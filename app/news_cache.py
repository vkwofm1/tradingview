"""SerpApi-backed news search with monthly quota guard and TTL cache.

SerpApi usage is centralized here so downstream trading clients share one
cache, one key-rotation policy, and one quota guard. Results are not stored
in the market DB; only monthly usage counters are persisted to a small JSON
file so pod restarts do not reset API-budget accounting.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any

import httpx

DEFAULT_TTL_SEC = int(os.getenv("SERPAPI_NEWS_CACHE_TTL_SEC", "10800"))
MIN_TTL_SEC = 3600
MAX_TTL_SEC = 10800
MIN_INTERVAL_SEC = float(os.getenv("SERPAPI_MIN_INTERVAL_SEC", "10800"))
REQUEST_TIMEOUT_SEC = float(os.getenv("SERPAPI_TIMEOUT_SEC", "6"))
FAILURE_COOLDOWN_SEC = float(os.getenv("SERPAPI_FAILURE_COOLDOWN_SEC", "900"))
MONTHLY_QUOTA_PER_KEY = int(os.getenv("SERPAPI_MONTHLY_QUOTA_PER_KEY", "250"))
USAGE_STATE_PATH = os.getenv("SERPAPI_USAGE_STATE_PATH", "/app/data/serpapi_usage.json")
SERPAPI_URL = "https://serpapi.com/search.json"

_cache: dict[tuple[str, str], dict[str, Any]] = {}
_state = {"next_index": 0, "last_request_at_by_key": {}, "failed_until_by_key": {}}
_lock = Lock()

# httpx INFO logs include the full request URL, which would expose api_key.
logging.getLogger("httpx").setLevel(logging.WARNING)


def _split_keys(value: str) -> list[str]:
    return [item.strip() for item in value.replace(";", ",").split(",") if item.strip()]


def load_serpapi_keys() -> list[str]:
    keys: list[str] = []
    keys.extend(_split_keys(os.getenv("SERPAPI_API_KEYS", "")))
    single = os.getenv("SERPAPI_API_KEY", "").strip()
    if single:
        keys.append(single)

    seen: set[str] = set()
    out: list[str] = []
    for key in keys:
        if key and key not in seen:
            out.append(key)
            seen.add(key)
    return out


def _clamp_ttl(ttl_sec: int | None) -> int:
    value = DEFAULT_TTL_SEC if ttl_sec is None else int(ttl_sec)
    return max(MIN_TTL_SEC, min(MAX_TTL_SEC, value))


def _month_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def _key_id(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def _usage_path() -> Path:
    return Path(USAGE_STATE_PATH)


def _empty_usage() -> dict[str, Any]:
    return {"month": _month_id(), "keys": {}}


def _load_usage_locked() -> dict[str, Any]:
    path = _usage_path()
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        body = _empty_usage()
    if not isinstance(body, dict) or body.get("month") != _month_id():
        return _empty_usage()
    if not isinstance(body.get("keys"), dict):
        body["keys"] = {}
    return body


def _save_usage_locked(usage: dict[str, Any]) -> None:
    path = _usage_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(usage, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def _usage_record(usage: dict[str, Any], key: str) -> dict[str, Any]:
    records = usage.setdefault("keys", {})
    key_record = records.setdefault(_key_id(key), {"count": 0, "last_request_at": 0.0})
    if not isinstance(key_record, dict):
        key_record = {"count": 0, "last_request_at": 0.0}
        records[_key_id(key)] = key_record
    return key_record


def _remaining_quota_locked(key: str) -> int:
    if MONTHLY_QUOTA_PER_KEY <= 0:
        return 0
    usage = _load_usage_locked()
    record = _usage_record(usage, key)
    return max(0, MONTHLY_QUOTA_PER_KEY - int(record.get("count") or 0))


def _record_external_attempt_locked(key: str) -> int:
    usage = _load_usage_locked()
    record = _usage_record(usage, key)
    record["count"] = int(record.get("count") or 0) + 1
    record["last_request_at"] = time.time()
    _save_usage_locked(usage)
    return max(0, MONTHLY_QUOTA_PER_KEY - int(record["count"]))


def _extract_items(payload: dict[str, Any], limit: int) -> list[dict[str, Any]]:
    results = payload.get("news_results") or payload.get("organic_results") or []
    out: list[dict[str, Any]] = []
    for item in results[:limit]:
        if not isinstance(item, dict):
            continue
        out.append({
            "title": item.get("title", ""),
            "date": item.get("date", ""),
            "source": (item.get("source") or {}).get("name") if isinstance(item.get("source"), dict) else item.get("source", ""),
            "link": item.get("link", ""),
        })
    return out


def _cache_get(engine: str, query: str, ttl_sec: int) -> dict[str, Any] | None:
    entry = _cache.get((engine, query))
    if not entry:
        return None
    if time.time() - float(entry.get("cached_at", 0)) > ttl_sec:
        _cache.pop((engine, query), None)
        return None
    return entry


def _cache_set(engine: str, query: str, payload: dict[str, Any], items: list[dict[str, Any]]) -> None:
    _cache[(engine, query)] = {
        "cached_at": time.time(),
        "payload": payload,
        "items": items,
    }


def _state_dict(name: str) -> dict[int, float]:
    value = _state.setdefault(name, {})
    if not isinstance(value, dict):
        value = {}
        _state[name] = value
    return value


def _request_wait_locked(key_index: int) -> float:
    if MIN_INTERVAL_SEC <= 0:
        return 0.0
    last_request_at = float(_state_dict("last_request_at_by_key").get(key_index, 0))
    return max(0.0, MIN_INTERVAL_SEC - (time.time() - last_request_at))


def _record_request_locked(key_index: int) -> None:
    _state_dict("last_request_at_by_key")[key_index] = time.time()


def _record_failure_locked(key_index: int) -> None:
    if FAILURE_COOLDOWN_SEC > 0:
        _state_dict("failed_until_by_key")[key_index] = time.time() + FAILURE_COOLDOWN_SEC


def _error_summary(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        return f"HTTP {exc.response.status_code}"
    if isinstance(exc, httpx.TimeoutException):
        return "timeout"
    if isinstance(exc, httpx.RequestError):
        return exc.__class__.__name__
    return exc.__class__.__name__


def _timeout(timeout_sec: int | float | None = None) -> httpx.Timeout:
    timeout = max(1.0, float(REQUEST_TIMEOUT_SEC if timeout_sec is None else timeout_sec))
    return httpx.Timeout(timeout, connect=min(3.0, timeout))


def search_news(query: str, *, ttl_sec: int | None = None, limit: int = 10, timeout_sec: int | None = None) -> dict[str, Any]:
    query = str(query or "").strip()
    if not query:
        return {"status": "skipped", "reason": "empty_query", "query": query, "items": []}

    engine = "google_news"
    ttl = _clamp_ttl(ttl_sec)
    cached = _cache_get(engine, query, ttl)
    if cached is not None:
        return {
            "status": "ok",
            "query": query,
            "engine": engine,
            "cache_hit": True,
            "ttl_sec": ttl,
            "cached_at": cached["cached_at"],
            "items": cached["items"][:limit],
        }

    keys = load_serpapi_keys()
    if not keys:
        return {"status": "skipped", "reason": "missing_SERPAPI_API_KEY", "query": query, "items": []}

    errors: list[dict[str, Any]] = []
    attempted = 0
    quota_exhausted = 0
    start = int(_state.get("next_index") or 0) % len(keys)
    order = list(range(start, len(keys))) + list(range(0, start))

    for idx in order:
        key = keys[idx]
        with _lock:
            remaining = _remaining_quota_locked(key)
            if remaining <= 0:
                quota_exhausted += 1
                errors.append({"key_index": idx, "error": "monthly_quota_exhausted"})
                continue
            now = time.time()
            failed_until = float(_state_dict("failed_until_by_key").get(idx, 0))
            if failed_until > now:
                errors.append({"key_index": idx, "error": "cooldown"})
                continue
            wait = _request_wait_locked(idx)
            if wait > 0:
                errors.append({"key_index": idx, "error": "rate_limited"})
                continue
            remaining_after_attempt = _record_external_attempt_locked(key)
            _record_request_locked(idx)

        attempted += 1
        try:
            response = httpx.get(
                SERPAPI_URL,
                params={"engine": engine, "q": query, "api_key": key},
                timeout=_timeout(timeout_sec),
            )
            response.raise_for_status()
            payload = response.json()
            items = _extract_items(payload, limit)
            with _lock:
                _cache_set(engine, query, payload, items)
                _state["next_index"] = (idx + 1) % len(keys)
            return {
                "status": "ok",
                "query": query,
                "engine": engine,
                "cache_hit": False,
                "ttl_sec": ttl,
                "items": items,
                "serpapi_key_index": idx,
                "serpapi_key_count": len(keys),
                "serpapi_monthly_remaining_for_key": remaining_after_attempt,
            }
        except Exception as exc:
            with _lock:
                _record_failure_locked(idx)
                _state["next_index"] = (idx + 1) % len(keys)
            errors.append({"key_index": idx, "error": _error_summary(exc)})

    if attempted == 0 and quota_exhausted == len(keys):
        reason = "monthly_quota_exhausted"
    else:
        reason = "no_key_available" if attempted == 0 else "all_keys_failed"
    status = "skipped" if attempted == 0 else "error"
    return {"status": status, "reason": reason, "query": query, "items": [], "errors": errors}
