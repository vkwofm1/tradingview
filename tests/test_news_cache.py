from app import news_cache


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


def test_search_news_uses_ttl_cache_and_rotates_keys(monkeypatch):
    news_cache._cache.clear()
    news_cache._state["next_index"] = 0
    news_cache._state["last_request_at"] = 0.0
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
