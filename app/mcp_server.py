"""MCP server exposing the collectors and stored market data.

Mounts at `/mcp` on the main FastAPI app via `streamable_http_app()`. Tools
re-use the existing collector functions and `db` helpers, so MCP and the
REST API see the same data through the same code paths.
"""

import os

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from app import db
from app.collectors import bithumb, crypto, naver_stocks, stocks, upbit
from app.runner import run_collector


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
    async def collect_kr_stocks(symbols: list[str] | None = None) -> dict:
        """Collect Korean stock prices from Naver Finance.

        If `symbols` is omitted, fetches the KOSPI top-100 by market cap.
        """
        return await run_collector("naver_stocks", naver_stocks.collect, symbols)

    @mcp.tool()
    async def collect_upbit(markets: list[str] | None = None) -> dict:
        """Collect Upbit KRW ticker data. Pass markets like ['KRW-BTC', 'KRW-ETH']."""
        return await run_collector("upbit", upbit.collect, markets)

    @mcp.tool()
    async def collect_bithumb(symbols: list[str] | None = None) -> dict:
        """Collect Bithumb KRW ticker data. Pass symbols like ['BTC', 'ETH']."""
        return await run_collector("bithumb", bithumb.collect, symbols)

    @mcp.tool()
    async def collect_us_stocks(symbols: list[str] | None = None) -> dict:
        """Collect US stocks from Yahoo Finance."""
        return await run_collector("stocks", stocks.collect, symbols)

    @mcp.tool()
    async def collect_global_crypto(ids: list[str] | None = None) -> dict:
        """Collect global crypto prices from CoinGecko (e.g. ['bitcoin', 'ethereum'])."""
        return await run_collector("crypto", crypto.collect, ids)

    @mcp.tool()
    def query_market_data(
        collector: str | None = None,
        symbol: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        """Query stored market data records, optionally filtered by collector / symbol."""
        return db.query_market_data(collector, symbol, limit)

    @mcp.tool()
    def query_market_candles(
        collector: str | None = None,
        symbol: str | None = None,
        interval: str | None = "1m",
        limit: int = 60,
    ) -> list[dict]:
        """Query stored candle records, optionally filtered by collector / symbol / interval."""
        return db.query_market_candles(collector, symbol, interval, limit)

    @mcp.tool()
    def list_jobs(limit: int = 20) -> list[dict]:
        """Return the most recent collection jobs."""
        return db.list_jobs(limit)

    @mcp.tool()
    def get_job(job_id: str) -> dict | None:
        """Return a single job record by id."""
        return db.get_job(job_id)

    @mcp.resource("market://latest/{collector}/{symbol}")
    def latest_record(collector: str, symbol: str) -> dict:
        """Return the most recent market_data record for a (collector, symbol) pair."""
        rows = db.query_market_data(collector, symbol, 1)
        return rows[0] if rows else {}

    @mcp.resource("market://jobs")
    def jobs_resource() -> list[dict]:
        """Return the 50 most recent collection jobs."""
        return db.list_jobs(50)

    return mcp


if __name__ == "__main__":
    # Allow running the MCP server stand-alone over stdio for local CLI clients.
    db.init_db()
    build_mcp().run()
