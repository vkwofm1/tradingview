"""TradingView crawling server — FastAPI app on port 8509."""

import asyncio
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel

from app import db, adoption_metrics
from app.collectors import bithumb, crypto, dart_disclosures, naver_stocks, stocks, upbit
from app.db_monitoring import get_database_health, get_database_stats, get_migration_readiness
from app.mcp_server import build_mcp
from app.monitoring import build_operations_dashboard
from app.news_cache import search_news
from app.runner import run_collector
from app.scheduler import Scheduler
from app.adoption_scheduler import AdoptionMetricsScheduler

COLLECTORS = {
    "crypto": crypto.collect,
    "stocks": stocks.collect,
    "naver_stocks": naver_stocks.collect,
    "upbit": upbit.collect,
    "bithumb": bithumb.collect,
    "dart_disclosures": dart_disclosures.collect,
}

mcp = build_mcp()
mcp_app = mcp.streamable_http_app()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.init_db()
    scheduler = Scheduler(COLLECTORS)
    adoption_scheduler = AdoptionMetricsScheduler()
    await scheduler.start()
    await adoption_scheduler.start()
    # MCP session manager must be running for the mounted streamable-HTTP
    # transport to handle requests. We enter its lifespan here.
    async with mcp.session_manager.run():
        try:
            yield
        finally:
            await scheduler.stop()
            await adoption_scheduler.stop()


app = FastAPI(title="TradingView Crawl", version="0.2.0", lifespan=lifespan)
app.mount("/mcp", mcp_app)


class CollectRequest(BaseModel):
    collector: str
    symbols: list[str] | None = None


class CollectionPolicyRequest(BaseModel):
    collector: str
    include_symbols: list[str] | None = None
    exclude_symbols: list[str] | None = None
    include_fields: list[str] | None = None
    exclude_fields: list[str] | None = None
    notes: str = ""
    source: str = "manual"
    requested_by: str = ""
    active: bool = True


class SurveyResponse(BaseModel):
    respondent_id: str
    survey_type: str
    score: int
    feedback: str | None = None
    survey_date: str | None = None


class SystemLogEntry(BaseModel):
    user_id: str
    action_type: str
    decision_id: str | None = None
    metadata: dict | None = None


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


@app.get("/health/db")
def database_health():
    health = get_database_health()
    status = "ok" if health.get("healthy") else "unhealthy"
    return {
        "status": status,
        "database": health,
    }


@app.get("/health/db/stats")
def database_stats():
    stats = get_database_stats()
    return stats


@app.get("/health/db/readiness")
def database_readiness():
    readiness = get_migration_readiness()
    return {
        "ready": readiness["ready"],
        "database": readiness["database"],
        "checks": readiness["checks"],
        "recommendation": readiness["recommendation"],
    }


@app.get("/news/search")
def news_search(
    q: str = Query(..., min_length=1, max_length=200),
    ttl_sec: int = Query(7200, ge=3600, le=10800),
    limit: int = Query(10, ge=1, le=20),
):
    """Return SerpApi Google News results through a 1-3h in-memory cache.

    Results are not stored in the market DB. API keys rotate only on cache
    misses, so repeated same-query callers share one external request.
    """
    return search_news(q, ttl_sec=ttl_sec, limit=limit)


@app.post("/collect")
async def start_collection(req: CollectRequest):
    if req.collector not in COLLECTORS:
        raise HTTPException(400, f"Unknown collector: {req.collector}. Available: {list(COLLECTORS)}")
    job_id = uuid.uuid4().hex[:12]
    job = db.create_job(job_id, req.collector)
    resolved_symbols = db.resolve_collection_symbols(req.collector, req.symbols)

    async def _run():
        try:
            count = await COLLECTORS[req.collector](job_id, resolved_symbols)
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


@app.get("/collection-policies")
def list_collection_policies():
    return db.list_collection_policies()


@app.get("/collection-policies/{collector}")
def get_collection_policy(collector: str):
    policy = db.get_collection_policy(collector)
    if not policy:
        raise HTTPException(404, "Collection policy not found")
    return policy


@app.post("/collection-policies")
def upsert_collection_policy(req: CollectionPolicyRequest):
    if req.collector not in COLLECTORS:
        raise HTTPException(400, f"Unknown collector: {req.collector}. Available: {list(COLLECTORS)}")
    return db.upsert_collection_policy(
        collector=req.collector,
        include_symbols=req.include_symbols,
        exclude_symbols=req.exclude_symbols,
        include_fields=req.include_fields,
        exclude_fields=req.exclude_fields,
        notes=req.notes,
        source=req.source,
        requested_by=req.requested_by,
        active=req.active,
    )


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


@app.post("/adoption/surveys")
def submit_survey(survey: SurveyResponse):
    if not 1 <= survey.score <= 5:
        raise HTTPException(400, "Score must be between 1 and 5")
    adoption_metrics.record_survey_response(
        respondent_id=survey.respondent_id,
        survey_type=survey.survey_type,
        score=survey.score,
        feedback=survey.feedback,
        survey_date=survey.survey_date,
    )
    return {
        "status": "success",
        "message": "Survey response recorded",
        "respondent_id": survey.respondent_id,
    }


@app.post("/adoption/log")
def log_action(log_entry: SystemLogEntry):
    adoption_metrics.log_system_action(
        user_id=log_entry.user_id,
        action_type=log_entry.action_type,
        decision_id=log_entry.decision_id,
        metadata=log_entry.metadata,
    )
    return {
        "status": "success",
        "message": "Action logged",
        "user_id": log_entry.user_id,
    }


@app.get("/adoption/metrics/daily")
def get_daily_metrics(limit: int = Query(30, ge=1, le=365)):
    metrics = adoption_metrics.get_daily_metrics(limit=limit)
    return {
        "period": "daily",
        "limit": limit,
        "count": len(metrics),
        "data": metrics,
    }


@app.get("/adoption/metrics/weekly")
def get_weekly_metrics(limit: int = Query(12, ge=1, le=52)):
    metrics = adoption_metrics.get_weekly_metrics(limit=limit)
    return {
        "period": "weekly",
        "limit": limit,
        "count": len(metrics),
        "data": metrics,
    }


@app.get("/adoption/metrics/monthly")
def get_monthly_metrics(limit: int = Query(12, ge=1, le=60)):
    metrics = adoption_metrics.get_monthly_metrics(limit=limit)
    return {
        "period": "monthly",
        "limit": limit,
        "count": len(metrics),
        "data": metrics,
    }


@app.post("/adoption/metrics/calculate/daily")
def calculate_daily_metrics_endpoint(date: str | None = Query(None)):
    result = adoption_metrics.calculate_daily_metrics(target_date=date)
    return {
        "status": "success",
        "message": "Daily metrics calculated",
        **result,
    }


@app.post("/adoption/metrics/calculate/weekly")
def calculate_weekly_metrics_endpoint(week_start: str | None = Query(None)):
    result = adoption_metrics.calculate_weekly_metrics(week_start=week_start)
    return {
        "status": "success",
        "message": "Weekly metrics calculated",
        **result,
    }


@app.post("/adoption/metrics/calculate/monthly")
def calculate_monthly_metrics_endpoint(month_start: str | None = Query(None)):
    result = adoption_metrics.calculate_monthly_metrics(month_start=month_start)
    return {
        "status": "success",
        "message": "Monthly metrics calculated",
        **result,
    }


@app.post("/adoption/archival")
def archive_feedback(days_to_keep: int = Query(90, ge=1)):
    result = adoption_metrics.archive_old_feedback(days_to_keep=days_to_keep)
    return {
        "status": "success",
        "message": "Feedback archival completed",
        **result,
    }


@app.post("/adoption/reports/generate")
def generate_report(month_start: str = Query(...)):
    result = adoption_metrics.generate_monthly_report(month_start)
    return {
        "status": "success",
        "message": "Monthly report generated",
        "report": result,
    }


@app.get("/adoption/reports")
def get_reports(report_type: str = Query("monthly"), limit: int = Query(12, ge=1, le=60)):
    reports = adoption_metrics.get_archival_reports(report_type=report_type, limit=limit)
    return {
        "report_type": report_type,
        "limit": limit,
        "count": len(reports),
        "data": reports,
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=8509, reload=True)
