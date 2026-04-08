---
name: tradingview-crawl
description: Manage, extend, and debug the TradingView market data crawling server (port 8509)
---

# tradingview-crawl

TRIGGER when:
- User asks to add a new market data collector (e.g., forex, commodities, indices)
- User asks to modify collection logic, scheduling, or persistence
- User asks to debug or inspect crawling jobs or collected data
- User asks to extend the /collect, /jobs, or /data API endpoints
- User references the crawling server, port 8509, or market data collection
- User references Korean stocks / Naver Finance / KOSPI / Upbit / Bithumb / KRW markets
- User references MCP / Model Context Protocol / `/mcp` endpoint

DO NOT TRIGGER when:
- User asks about unrelated services or ports
- User asks about frontend/charting (that's TradingView UI, not this crawler)
- User asks about trading execution or order placement

## Execution Strategy

1. **Read before changing** — always read the relevant collector or endpoint file before editing.
2. **New collectors** go in `app/collectors/<name>.py` with an `async def collect(job_id, symbols=None) -> int` signature. Register in `COLLECTORS` dict in `app/main.py` AND add to scheduler defaults if it should auto-run.
3. **Persistence** uses SQLite via `app/db.py` — call `db.insert_market_data()` from collectors.
4. **Shared runner**: prefer `app.runner.run_collector(name, fn, symbols)` over hand-rolled job lifecycle code — REST `/collect/sync`, MCP tools, and the scheduler all share this helper.
5. **MCP server**: tools and resources are defined in `app/mcp_server.py`. The MCP transport is mounted at `/mcp` on the same FastAPI app via streamable-HTTP. The MCP session manager lifespan is wired into the FastAPI lifespan in `app/main.py`.
6. **Scheduler**: `app/scheduler.py` runs every collector once per hour by default. Disable in tests with `SCHED_DISABLED=1`. Override interval with `SCHED_INTERVAL_SEC` or per-collector `SCHED_<NAME>_INTERVAL`.
7. **Tests** live in `tests/test_api.py` — add test cases for new endpoints, collectors, scheduler, or MCP tools using the existing `_setup_db` fixture, `respx` for HTTP mocking, and monkeypatched collectors.
8. **Run server**: `python -m app.main` (port 8509). MCP available at `http://localhost:8509/mcp/`.
9. **Run tests**: `pip install -e ".[dev]" && pytest`.

## Guardrails

- Never commit API keys or secrets — use env vars loaded at runtime.
- All collectors must handle HTTP errors gracefully (try/except → `db.finish_job` with error).
- Keep SQLite as the default store; migration to Postgres is a separate task.
- Prefer free/public APIs for new collectors (no paid keys for default config).

## Cross-references

- `app/main.py` — FastAPI app, endpoint definitions, collector registry, MCP mount, scheduler lifespan
- `app/db.py` — SQLite schema, job + market_data CRUD
- `app/runner.py` — shared `run_collector(name, fn, symbols)` helper used by REST + MCP + scheduler
- `app/scheduler.py` — asyncio background scheduler (1-hour default interval)
- `app/mcp_server.py` — FastMCP server (mounted at `/mcp` on port 8509, streamable-HTTP)
- `app/collectors/crypto.py` — CoinGecko (global crypto) collector
- `app/collectors/stocks.py` — Yahoo Finance (US stocks) collector
- `app/collectors/naver_stocks.py` — Naver Finance (Korean stocks, KOSPI top-100) collector
- `app/collectors/upbit.py` — Upbit KRW exchange collector
- `app/collectors/bithumb.py` — Bithumb KRW exchange collector
- `tests/test_api.py` — API + collector + scheduler + MCP test suite
- `Dockerfile` — container build (single port 8509 hosts both REST and MCP)
