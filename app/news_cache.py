"""SerpApi-backed news search with in-memory TTL cache.

This module intentionally does not persist results to DB. It centralizes all
SerpApi usage for downstream trading clients so repeated same-query requests hit
one 1-3 hour cache and API keys rotate across cache misses.
"""

from __future__ import annotations

import logging
import os
import time
from threading import Lock
from typing import Any

import httpx

DEFAULT_TTL_SEC = int(os.getenv("SERPAPI_NEWS_CACHE_TTL_SEC", "7200"))
MIN_TTL_SEC = 3600
MAX_TTL_SEC = 10800
MIN_INTERVAL_SEC = float(os.getenv("SERPAPI_MIN_INTERVAL_SEC", "30"))
REQUEST_TIMEOUT_SEC = float(os.getenv("SERPAPI_TIMEOUT_SEC", "6"))
FAILURE_COOLDOWN_SEC = float(os.getenv("SERPAPI_FAILURE_COOLDOWN_SEC", "900"))
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


def _timeout() -> httpx.Timeout:
    timeout = max(1.0, REQUEST_TIMEOUT_SEC)
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
    start = int(_state.get("next_index") or 0) % len(keys)
    order = list(range(start, len(keys))) + list(range(0, start))
    request_timeout = _timeout() if timeout_sec is None else httpx.Timeout(max(1.0, float(timeout_sec)), connect=min(3.0, max(1.0, float(timeout_sec))))

    for idx in order:
        key = keys[idx]
        with _lock:
            now = time.time()
            failed_until = float(_state_dict("failed_until_by_key").get(idx, 0))
            if failed_until > now:
                errors.append({"key_index": idx, "error": "cooldown"})
                continue
            wait = _request_wait_locked(idx)
            if wait > 0:
                errors.append({"key_index": idx, "error": "rate_limited"})
                continue

        attempted += 1
        try:
            response = httpx.get(
                SERPAPI_URL,
                params={"engine": engine, "q": query, "api_key": key},
                timeout=request_timeout,
            )
            with _lock:
                _record_request_locked(idx)
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
            }
        except Exception as exc:
            with _lock:
                _record_request_locked(idx)
                _record_failure_locked(idx)
                _state["next_index"] = (idx + 1) % len(keys)
            errors.append({"key_index": idx, "error": _error_summary(exc)})

    reason = "no_key_available" if attempted == 0 else "all_keys_failed"
    status = "skipped" if attempted == 0 else "error"
    return {"status": status, "reason": reason, "query": query, "items": [], "errors": errors}
