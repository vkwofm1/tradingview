"""TradingView crawling server — FastAPI app on port 8509."""

import asyncio
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel

from app import db
from app.collectors import crypto, stocks

COLLECTORS = {
    "crypto": crypto.collect,
    "stocks": stocks.collect,
}


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.init_db()
    yield


app = FastAPI(title="TradingView Crawl", version="0.1.0", lifespan=lifespan)


class CollectRequest(BaseModel):
    collector: str
    symbols: list[str] | None = None


@app.get("/health")
def health():
    return {"status": "ok"}


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


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=8509, reload=True)
