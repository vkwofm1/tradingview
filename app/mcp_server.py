"""MCP server exposing the collectors and stored market data.

Mounts at `/mcp` on the main FastAPI app via `streamable_http_app()`. Tools
re-use the existing collector functions and `db` helpers, so MCP and the
REST API see the same data through the same code paths.
"""

import os
from typing import Any

from pydantic import BaseModel, ConfigDict
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from app import db
from app.collectors import (
    bithumb, crypto, exchange_1m, market_archive,
    naver_stocks, stocks, upbit,
)
from app.runner import run_collector


class ListResponse(BaseModel):
    """Wrapper for list responses to work with Pydantic/MCP serialization."""
    model_config = ConfigDict(arbitrary_types_allowed=True)
    result: list[Any]


def build_mcp() -> FastMCP:
    # stateless_http=True keeps each MCP request self-contained — simpler when
    # mounted under FastAPI and easier for tools like the MCP Inspector.
    # streamable_http_path="/" so that when mounted at /mcp on FastAPI the
    # final URL is /mcp (not /mcp/mcp).
    #
    # DNS rebinding protection is on by default and rejects unknown Host
    # headers (including the FastAPI TestClient `testserver`). We disable it
    # for non-production by default and let operators re-enable via env vars.
    enable_dns_rebind = os.environ.get("MCP_DNS_REBIND_PROTECTION") == "1"
    allowed_hosts = [
        h.strip()
        for h in os.environ.get("MCP_ALLOWED_HOSTS", "").split(",")
        if h.strip()
    ]
    transport_security = TransportSecuritySettings(
        enable_dns_rebinding_protection=enable_dns_rebind,
        allowed_hosts=allowed_hosts,
    )

    mcp = FastMCP(
        "tradingview-crawl",
        stateless_http=True,
        streamable_http_path="/",
        transport_security=transport_security,
    )

    @mcp.tool()
    async def collect_kr_stocks(symbols: list[str] | None = None) -> Any:
        """Collect Korean stock prices from Naver Finance.

        If `symbols` is omitted, fetches the KOSPI top-100 by market cap.
        """
        return await run_collector("naver_stocks", naver_stocks.collect, symbols)

    @mcp.tool()
    async def collect_upbit(markets: list[str] | None = None) -> Any:
        """Collect Upbit KRW ticker data. Pass markets like ['KRW-BTC', 'KRW-ETH']."""
        return await run_collector("upbit", upbit.collect, markets)

    @mcp.tool()
    async def collect_bithumb(symbols: list[str] | None = None) -> Any:
        """Collect Bithumb KRW ticker data. Pass symbols like ['BTC', 'ETH']."""
        return await run_collector("bithumb", bithumb.collect, symbols)

    @mcp.tool()
    async def collect_us_stocks(symbols: list[str] | None = None) -> Any:
        """Collect US stocks from Yahoo Finance."""
        return await run_collector("stocks", stocks.collect, symbols)

    @mcp.tool()
    async def collect_global_crypto(ids: list[str] | None = None) -> Any:
        """Collect global crypto prices from CoinGecko (e.g. ['bitcoin', 'ethereum'])."""
        return await run_collector("crypto", crypto.collect, ids)

    @mcp.tool()
    def query_market_data(
        collector: str | None = None,
        symbol: str | None = None,
        limit: int = 50,
    ) -> ListResponse:
        """Query stored market data records, optionally filtered by collector / symbol."""
        return ListResponse(result=db.query_market_data(collector, symbol, limit))

    @mcp.tool()
    def query_market_candles(
        collector: str | None = None,
        symbol: str | None = None,
        interval: str | None = "1m",
        limit: int = 60,
    ) -> ListResponse:
        """Query stored candle records, optionally filtered by collector / symbol / interval."""
        return ListResponse(result=db.query_market_candles(collector, symbol, interval, limit))

    # ── Market Archive (aibitcoin 1분봉 archive) ─────────────────────────────
    # invest-lead 회의 시점에 LLM이 분단위 가격을 inspect 하기 위한 tool.
    # aibitcoin의 market-archive 서비스(K8s `market-archive:8510`)를 통해 조회.

    @mcp.tool()
    async def market_archive_collect_until_now(
        targets: list[str],
        lookback_minutes_if_empty: int = 1440,
    ) -> Any:
        """Market Archive에 즉시 1분봉 수집 요청 + 결과 메타 반환.

        Args:
            targets: ["exchange:symbol", ...] 예) ["bithumb:BTC/KRW", "kis:005930"]
            lookback_minutes_if_empty: DB에 데이터가 전혀 없을 때 fallback 시작 (분)

        지원 거래소: bithumb, upbit, kis
        결과는 fetched_rows, last_ts_before/after 등 수집 메타.
        실제 분단위 candles는 market-archive DB에 있고 별도 query tool로 조회.
        """
        parsed = [
            {"exchange": t.split(":", 1)[0], "symbol": t.split(":", 1)[1],
             "lookback_minutes": lookback_minutes_if_empty}
            for t in targets if ":" in t
        ]
        return await run_collector("market_archive", market_archive.collect, parsed)

    @mcp.tool()
    async def market_archive_last_ts(
        exchange: str,
        symbol: str,
        timeframe: str = "1m",
    ) -> Any:
        """Market Archive에 쌓인 단일 (거래소, 종목, 타임프레임)의 마지막 ts 반환.

        Args:
            exchange: bithumb | upbit | kis
            symbol: "BTC/KRW" / "005930" 등
            timeframe: "1m" (기본)
        """
        return await market_archive.fetch_last_ts(exchange, symbol, timeframe)

    @mcp.tool()
    async def market_archive_stats() -> Any:
        """Market Archive 전체 수집 통계 (거래소/타임프레임별 row 수, 시간 범위)."""
        return await market_archive.fetch_stats()

    # ── Exchange 1m native collectors (2026-05-31 tradingview-native) ────────
    # 빗썸/업비트 KRW + KIS 국장 + 미장. invest-lead 회의에서 분단위 검증용.
    # 데이터: tradingview 자체 SQLite (market_candles 테이블).

    @mcp.tool()
    async def collect_krw_1m_until_now(targets: list[str]) -> Any:
        """빗썸/업비트 KRW 코인 1m 즉시 수집.

        Args:
            targets: ["bithumb:BTC/KRW", "upbit:ETH/KRW", ...]
        """
        return await run_collector(
            "krw_1m_until_now",
            lambda jid, _s: exchange_1m.collect_krw_1m_until_now(jid, targets),
            None,
        )

    @mcp.tool()
    async def collect_krw_1m_full(
        exchanges: list[str] | None = None,
        lookback_minutes: int = 1440,
    ) -> Any:
        """빗썸/업비트 KRW 전체 코인 1m 일일 수집 (오래 걸림 — cron 권장)."""
        async def _run(jid, _s):
            return await exchange_1m.collect_krw_1m(
                jid, exchanges, lookback_minutes=lookback_minutes,
            )
        return await run_collector("krw_1m_full", _run, None)

    @mcp.tool()
    async def collect_us_stocks_1m_until_now(symbols: list[str]) -> Any:
        """미장 단일 종목 1m 즉시 수집 (yfinance)."""
        async def _run(jid, _s):
            return await exchange_1m.collect_us_stocks_1m_until_now(jid, symbols)
        return await run_collector("us_stocks_1m_until_now", _run, None)

    @mcp.tool()
    async def collect_us_stocks_1m_full(
        symbols: list[str] | None = None,
        batch_size: int = 50,
    ) -> Any:
        """미장 S&P500+NASDAQ100 union 1m 일일 수집 (cron 권장)."""
        async def _run(jid, _s):
            return await exchange_1m.collect_us_stocks_1m(jid, symbols, batch_size=batch_size)
        return await run_collector("us_stocks_1m_full", _run, None)

    @mcp.tool()
    async def collect_kr_stocks_1m_until_now(symbols: list[str]) -> Any:
        """국장 단일 종목 1m 즉시 수집 (KIS API)."""
        async def _run(jid, _s):
            return await exchange_1m.collect_kr_stocks_1m_until_now(jid, symbols)
        return await run_collector("kr_stocks_1m_until_now", _run, None)

    @mcp.tool()
    async def collect_kr_stocks_1m_full(
        symbols: list[str] | None = None,
        bars_per_call: int = 30,
    ) -> Any:
        """국장 universe 1m 일일 수집 (cron 권장)."""
        async def _run(jid, _s):
            return await exchange_1m.collect_kr_stocks_1m(
                jid, symbols, bars_per_call=bars_per_call,
            )
        return await run_collector("kr_stocks_1m_full", _run, None)

    # ── 하위호환 (aibitcoin market-archive 호출 — deprecated) ────────────────
    @mcp.tool()
    async def market_archive_collect_stock(
        market: str,
        symbol: str,
        lookback_days_if_empty: int = 1,
    ) -> Any:
        """주식(국장 KIS / 미장 yfinance) 단일 종목 1분봉 즉시 수집.

        Args:
            market: "kr" (국장) | "us" (미장)
            symbol: "005930" (kr) | "AAPL" (us)
            lookback_days_if_empty: us 전용. yfinance 1m 7일 한도.

        포트폴리오 선정 시 LLM이 본 tool로 분단위 데이터를 inspect.
        """
        import httpx
        async with httpx.AsyncClient(
            timeout=market_archive.DEFAULT_TIMEOUT,
            headers=market_archive._headers(),
        ) as client:
            r = await client.post(
                f"{market_archive.DEFAULT_BASE_URL.rstrip('/')}/api/collect_stock_until_now",
                params={
                    "market": market, "symbol": symbol,
                    "lookback_days_if_empty": str(lookback_days_if_empty),
                },
            )
            r.raise_for_status()
            return r.json()

    @mcp.tool()
    def list_jobs(limit: int = 20) -> ListResponse:
        """Return the most recent collection jobs."""
        return ListResponse(result=db.list_jobs(limit))

    @mcp.tool()
    def get_job(job_id: str) -> Any:
        """Return a single job record by id."""
        return db.get_job(job_id)

    @mcp.resource("market://latest/{collector}/{symbol}")
    def latest_record(collector: str, symbol: str) -> Any:
        """Return the most recent market_data record for a (collector, symbol) pair."""
        rows = db.query_market_data(collector, symbol, 1)
        return rows[0] if rows else {}

    @mcp.resource("market://jobs")
    def jobs_resource() -> ListResponse:
        """Return the 50 most recent collection jobs."""
        return ListResponse(result=db.list_jobs(50))

    return mcp


if __name__ == "__main__":
    # Allow running the MCP server stand-alone over stdio for local CLI clients.
    db.init_db()
    build_mcp().run()
