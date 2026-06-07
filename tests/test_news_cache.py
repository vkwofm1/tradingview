from pathlib import Path

import httpx

from app import news_cache


def _reset_news_cache_state(monkeypatch, tmp_path: Path):
    news_cache._cache.clear()
    news_cache._state["next_index"] = 0
    news_cache._state["last_request_at_by_key"] = {}
    news_cache._state["failed_until_by_key"] = {}
    monkeypatch.setattr(news_cache, "USAGE_STATE_PATH", str(tmp_path / "serpapi_usage.json"))
    monkeypatch.setattr(news_cache, "MONTHLY_QUOTA_PER_KEY", 250)


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


def test_search_news_uses_ttl_cache_and_rotates_keys(monkeypatch, tmp_path):
    _reset_news_cache_state(monkeypatch, tmp_path)
    monkeypatch.setenv("SERPAPI_API_KEYS", "key-one,key-two")
    monkeypatch.setattr(news_cache, "MIN_INTERVAL_SEC", 0)

    calls = []

    def fake_get(url, params, timeout):
        calls.append(params["api_key"])
        return FakeResponse({
            "news_results": [
                {"title": f"{params['q']} headline", "date": "today", "source": {"name": "source"}, "link": "https://example.com"}
            ]
        })

    monkeypatch.setattr(news_cache.httpx, "get", fake_get)

    first = news_cache.search_news("btc", ttl_sec=3600)
    second = news_cache.search_news("btc", ttl_sec=3600)
    third = news_cache.search_news("eth", ttl_sec=3600)

    assert first["status"] == "ok"
    assert first["cache_hit"] is False
    assert second["cache_hit"] is True
    assert third["cache_hit"] is False
    assert calls == ["key-one", "key-two"]


def test_search_news_clamps_ttl():
    assert news_cache._clamp_ttl(10) == 3600
    assert news_cache._clamp_ttl(20000) == 10800
    assert news_cache._clamp_ttl(7200) == 7200


def test_search_news_does_not_wait_between_different_keys(monkeypatch, tmp_path):
    _reset_news_cache_state(monkeypatch, tmp_path)
    monkeypatch.setenv("SERPAPI_API_KEYS", "rate-limited-key,working-key")
    monkeypatch.setattr(news_cache, "MIN_INTERVAL_SEC", 30)

    calls = []
    sleeps = []

    def fake_sleep(seconds):
        sleeps.append(seconds)

    def fake_get(url, params, timeout):
        calls.append(params["api_key"])
        if params["api_key"] == "rate-limited-key":
            request = httpx.Request("GET", "https://serpapi.com/search.json?api_key=rate-limited-key")
            response = httpx.Response(429, request=request)
            raise httpx.HTTPStatusError("rate limited api_key=rate-limited-key", request=request, response=response)
        return FakeResponse({"news_results": [{"title": "ok", "source": "source", "link": "https://example.com"}]})

    monkeypatch.setattr(news_cache.time, "sleep", fake_sleep)
    monkeypatch.setattr(news_cache.httpx, "get", fake_get)

    result = news_cache.search_news("btc", ttl_sec=3600)

    assert result["status"] == "ok"
    assert calls == ["rate-limited-key", "working-key"]
    assert sleeps == []


def test_search_news_error_summary_does_not_leak_api_key(monkeypatch, tmp_path):
    _reset_news_cache_state(monkeypatch, tmp_path)
    monkeypatch.setenv("SERPAPI_API_KEYS", "secret-key")
    monkeypatch.setattr(news_cache, "MIN_INTERVAL_SEC", 0)

    def fake_get(url, params, timeout):
        request = httpx.Request("GET", "https://serpapi.com/search.json?api_key=secret-key")
        response = httpx.Response(429, request=request)
        raise httpx.HTTPStatusError("rate limited api_key=secret-key", request=request, response=response)

    monkeypatch.setattr(news_cache.httpx, "get", fake_get)

    result = news_cache.search_news("btc", ttl_sec=3600)

    assert result["status"] == "error"
    assert result["errors"] == [{"key_index": 0, "error": "HTTP 429"}]
    assert "secret-key" not in str(result)


def test_search_news_returns_quickly_when_all_keys_are_cooling_down(monkeypatch, tmp_path):
    _reset_news_cache_state(monkeypatch, tmp_path)
    news_cache._state["last_request_at_by_key"] = {0: news_cache.time.time()}
    news_cache._state["failed_until_by_key"] = {1: news_cache.time.time() + 60}
    monkeypatch.setenv("SERPAPI_API_KEYS", "recent-key,cooling-key")
    monkeypatch.setattr(news_cache, "MIN_INTERVAL_SEC", 30)

    def fail_if_called(*args, **kwargs):
        raise AssertionError("external request should not be called")

    monkeypatch.setattr(news_cache.httpx, "get", fail_if_called)

    result = news_cache.search_news("btc", ttl_sec=3600)

    assert result["status"] == "skipped"
    assert result["reason"] == "no_key_available"


def test_search_news_enforces_monthly_quota_per_key(monkeypatch, tmp_path):
    _reset_news_cache_state(monkeypatch, tmp_path)
    monkeypatch.setenv("SERPAPI_API_KEYS", "key-one,key-two")
    monkeypatch.setattr(news_cache, "MONTHLY_QUOTA_PER_KEY", 1)
    monkeypatch.setattr(news_cache, "MIN_INTERVAL_SEC", 0)

    calls = []

    def fake_get(url, params, timeout):
        calls.append(params["api_key"])
        return FakeResponse({"news_results": [{"title": params["q"], "source": "source", "link": "https://example.com"}]})

    monkeypatch.setattr(news_cache.httpx, "get", fake_get)

    first = news_cache.search_news("btc", ttl_sec=3600)
    second = news_cache.search_news("eth", ttl_sec=3600)
    third = news_cache.search_news("xrp", ttl_sec=3600)

    assert first["status"] == "ok"
    assert first["serpapi_monthly_remaining_for_key"] == 0
    assert second["status"] == "ok"
    assert second["serpapi_monthly_remaining_for_key"] == 0
    assert third["status"] == "skipped"
    assert third["reason"] == "monthly_quota_exhausted"
    assert calls == ["key-one", "key-two"]
