"""TradingView crawling server — FastAPI app on port 8509."""

import asyncio
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel

from app import db
from app.collectors import bithumb, crypto, naver_stocks, stocks, upbit
from app.mcp_server import build_mcp
from app.monitoring import build_operations_dashboard
from app.runner import run_collector
from app.scheduler import Scheduler

COLLECTORS = {
    "crypto": crypto.collect,
    "stocks": stocks.collect,
    "naver_stocks": naver_stocks.collect,
    "upbit": upbit.collect,
    "bithumb": bithumb.collect,
}

mcp = build_mcp()
mcp_app = mcp.streamable_http_app()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.init_db()
    scheduler = Scheduler(COLLECTORS)
    await scheduler.start()
    # MCP session manager must be running for the mounted streamable-HTTP
    # transport to handle requests. We enter its lifespan here.
    async with mcp.session_manager.run():
        try:
            yield
        finally:
            await scheduler.stop()


app = FastAPI(title="TradingView Crawl", version="0.2.0", lifespan=lifespan)
app.mount("/mcp", mcp_app)


class CollectRequest(BaseModel):
    collector: str
    symbols: list[str] | None = None


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/health/apis")
async def api_health(
    failure_rate_threshold_pct: float = Query(10.0, ge=0.1, le=100.0),
):
    body = await build_operations_dashboard(
        failure_rate_threshold_pct=failure_rate_threshold_pct,
        job_failure_rates=db.get_job_failure_rates(
            lookback_hours=24,
            failure_rate_threshold_pct=failure_rate_threshold_pct,
        ),
    )
    return {
        "status": "ok" if body["summary"]["failing_apis"] == 0 else "degraded",
        **body,
    }


@app.post("/collect")
async def start_collection(req: CollectRequest):
    if req.collector not in COLLECTORS:
        raise HTTPException(400, f"Unknown collector: {req.collector}. Available: {list(COLLECTORS)}")
    job_id = uuid.uuid4().hex[:12]
    job = db.create_job(job_id, req.collector)

    async def _run():
        try:
            count = await COLLECTORS[req.collector](job_id, req.symbols)
            db.finish_job(job_id, count)
        except Exception as exc:
            db.finish_job(job_id, 0, str(exc))

    asyncio.create_task(_run())
    return job


@app.post("/collect/sync")
async def start_collection_sync(req: CollectRequest):
    """Run a collector synchronously and return the completed job."""
    if req.collector not in COLLECTORS:
        raise HTTPException(400, f"Unknown collector: {req.collector}. Available: {list(COLLECTORS)}")
    return await run_collector(req.collector, COLLECTORS[req.collector], req.symbols)


@app.get("/jobs")
def list_jobs(limit: int = Query(20, ge=1, le=100)):
    return db.list_jobs(limit)


@app.get("/jobs/{job_id}")
def get_job(job_id: str):
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return job


@app.get("/data")
def query_data(
    collector: str | None = None,
    symbol: str | None = None,
    limit: int = Query(50, ge=1, le=500),
):
    return db.query_market_data(collector, symbol, limit)


@app.get("/candles")
def query_candles(
    collector: str | None = None,
    symbol: str | None = None,
    interval: str | None = Query("1m"),
    limit: int = Query(60, ge=1, le=500),
):
    return db.query_market_candles(collector, symbol, interval, limit)


@app.get("/dashboard/risk")
def risk_dashboard(
    stale_after_sec: int = Query(7200, ge=60, le=604800),
    lookback_minutes: int = Query(60, ge=5, le=240),
    drawdown_alert_pct: float = Query(5.0, ge=0.1, le=100.0),
):
    return db.get_risk_dashboard(
        stale_after_sec=stale_after_sec,
        lookback_minutes=lookback_minutes,
        drawdown_alert_pct=drawdown_alert_pct,
    )


@app.get("/dashboard/operations")
async def operations_dashboard(
    failure_rate_threshold_pct: float = Query(10.0, ge=0.1, le=100.0),
):
    body = await build_operations_dashboard(
        failure_rate_threshold_pct=failure_rate_threshold_pct,
        job_failure_rates=db.get_job_failure_rates(
            lookback_hours=24,
            failure_rate_threshold_pct=failure_rate_threshold_pct,
        ),
    )
    return body


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=8509, reload=True)
