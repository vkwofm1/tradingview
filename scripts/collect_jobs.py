"""TradingView cron collection entrypoint.

This file intentionally uses collectors available in this repository. Stock
collection is centralized in TradingView market_data via naver_stocks/stocks.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import db
from app.collectors import bithumb, crypto, naver_stocks, stocks, upbit
from app.runner import run_collector


def _json_default(value: object) -> str:
    """Serialize datetime values returned by PostgreSQL deterministically."""
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _print(payload: object) -> None:
    print(
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
            default=_json_default,
        ),
        flush=True,
    )


async def cmd_krw_1m(args):
    del args
    db.init_db()
    jobs = []
    jobs.append(await run_collector("bithumb", bithumb.collect, None))
    jobs.append(await run_collector("upbit", upbit.collect, None))
    jobs.append(await run_collector("crypto", crypto.collect, None))
    _print(jobs)
    return 1 if any((job or {}).get("status") == "failed" for job in jobs) else 0


async def cmd_us_stocks_1m(args):
    del args
    db.init_db()
    job = await run_collector("stocks", stocks.collect, None)
    _print(job)
    return 0 if (job or {}).get("status") != "failed" else 1


async def cmd_kr_stocks_1m(args):
    del args
    db.init_db()
    job = await run_collector("naver_stocks", naver_stocks.collect, None)
    _print(job)
    return 0 if (job or {}).get("status") != "failed" else 1


async def cmd_krw_1m_single(args):
    del args
    db.init_db()
    jobs = []
    jobs.append(await run_collector("bithumb", bithumb.collect, None))
    jobs.append(await run_collector("upbit", upbit.collect, None))
    _print(jobs)
    return 1 if any((job or {}).get("status") == "failed" for job in jobs) else 0


async def cmd_stocks_1m_single(args):
    db.init_db()
    symbols = [symbol.strip() for symbol in args.symbols.split(",") if symbol.strip()]
    if args.market == "us":
        job = await run_collector("stocks", stocks.collect, symbols)
    else:
        job = await run_collector("naver_stocks", naver_stocks.collect, symbols)
    _print(job)
    return 0 if (job or {}).get("status") != "failed" else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)

    p1 = sub.add_parser("krw-1m", help="빗썸+업비트+crypto 현재 수집")
    p1.add_argument("--lookback-minutes", type=int, default=1440)
    p1.set_defaults(fn=cmd_krw_1m)

    p2 = sub.add_parser("us-stocks-1m", help="미장 TradingView stocks 수집")
    p2.add_argument("--batch-size", type=int, default=50)
    p2.set_defaults(fn=cmd_us_stocks_1m)

    p3 = sub.add_parser("kr-stocks-1m", help="국장 TradingView naver_stocks 수집")
    p3.add_argument("--bars-per-call", type=int, default=30)
    p3.set_defaults(fn=cmd_kr_stocks_1m)

    p4 = sub.add_parser("krw-1m-single", help="빗썸/업비트 현재 수집")
    p4.add_argument("--targets", required=True)
    p4.set_defaults(fn=cmd_krw_1m_single)

    p5 = sub.add_parser("stocks-1m-single", help="국장/미장 지정 종목 수집")
    p5.add_argument("--market", choices=["kr", "us"], required=True)
    p5.add_argument("--symbols", required=True)
    p5.set_defaults(fn=cmd_stocks_1m_single)

    args = parser.parse_args()
    return asyncio.run(args.fn(args))


if __name__ == "__main__":
    raise SystemExit(main())
