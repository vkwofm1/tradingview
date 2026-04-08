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

DO NOT TRIGGER when:
- User asks about unrelated services or ports
- User asks about frontend/charting (that's TradingView UI, not this crawler)
- User asks about trading execution or order placement

## Execution Strategy

1. **Read before changing** — always read the relevant collector or endpoint file before editing.
2. **New collectors** go in `app/collectors/<name>.py` with an `async def collect(job_id, symbols=None) -> int` signature. Register in `COLLECTORS` dict in `app/main.py`.
3. **Persistence** uses SQLite via `app/db.py` — call `db.insert_market_data()` from collectors.
4. **Tests** live in `tests/test_api.py` — add test cases for new endpoints or collectors using the existing `_setup_db` fixture and monkeypatched collectors.
5. **Run server**: `python -m app.main` (port 8509).
6. **Run tests**: `pip install -e ".[dev]" && pytest`.

## Guardrails

- Never commit API keys or secrets — use env vars loaded at runtime.
- All collectors must handle HTTP errors gracefully (try/except → `db.finish_job` with error).
- Keep SQLite as the default store; migration to Postgres is a separate task.
- Prefer free/public APIs for new collectors (no paid keys for default config).

## Cross-references

- `app/main.py` — FastAPI app, endpoint definitions, collector registry
- `app/db.py` — SQLite schema, job + market_data CRUD
- `app/collectors/crypto.py` — CoinGecko collector
- `app/collectors/stocks.py` — Yahoo Finance collector
- `tests/test_api.py` — API test suite
- `Dockerfile` — container build
