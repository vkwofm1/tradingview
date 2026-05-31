"""tradingview cron 진입점 — 일일 수집 job CLI.

K8s CronJob에서 호출. 매 호출이 새 프로세스 → 신규 상장 자동 포함.

사용:
    python scripts/collect_jobs.py krw-1m
    python scripts/collect_jobs.py us-stocks-1m [--batch-size 50]
    python scripts/collect_jobs.py kr-stocks-1m
    python scripts/collect_jobs.py krw-1m-single --targets bithumb:BTC/KRW,upbit:ETH/KRW
    python scripts/collect_jobs.py stocks-1m-single --market kr --symbols 005930,105560
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from pathlib import Path

# /app or repo root에서 임포트 가능하게
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import db
from app.collectors import exchange_1m
from app.runner import run_collector


def _print(p):
    print(json.dumps(p, ensure_ascii=False, indent=2), flush=True)


async def cmd_krw_1m(args):
    db.init_db()
    async def _fn(jid, _s):
        return await exchange_1m.collect_krw_1m(
            jid, ["bithumb", "upbit"], lookback_minutes=args.lookback_minutes,
        )
    job = await run_collector("krw_1m", _fn, None)
    _print(job)
    return 0 if (job or {}).get("status") != "failed" else 1


async def cmd_us_stocks_1m(args):
    db.init_db()
    async def _fn(jid, _s):
        return await exchange_1m.collect_us_stocks_1m(jid, None, batch_size=args.batch_size)
    job = await run_collector("us_stocks_1m", _fn, None)
    _print(job)
    return 0 if (job or {}).get("status") != "failed" else 1


async def cmd_kr_stocks_1m(args):
    db.init_db()
    async def _fn(jid, _s):
        return await exchange_1m.collect_kr_stocks_1m(jid, None, bars_per_call=args.bars_per_call)
    job = await run_collector("kr_stocks_1m", _fn, None)
    _print(job)
    return 0 if (job or {}).get("status") != "failed" else 1


async def cmd_krw_1m_single(args):
    db.init_db()
    targets = [t.strip() for t in args.targets.split(",") if t.strip()]
    async def _fn(jid, _s):
        return await exchange_1m.collect_krw_1m_until_now(jid, targets)
    job = await run_collector("krw_1m_until_now", _fn, None)
    _print(job)
    return 0


async def cmd_stocks_1m_single(args):
    db.init_db()
    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    if args.market == "us":
        async def _fn(jid, _s):
            return await exchange_1m.collect_us_stocks_1m_until_now(jid, symbols)
        name = "us_stocks_1m_until_now"
    else:
        async def _fn(jid, _s):
            return await exchange_1m.collect_kr_stocks_1m_until_now(jid, symbols)
        name = "kr_stocks_1m_until_now"
    job = await run_collector(name, _fn, None)
    _print(job)
    return 0


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    p1 = sub.add_parser("krw-1m", help="빗썸+업비트 KRW 전체 1m")
    p1.add_argument("--lookback-minutes", type=int, default=1440)
    p1.set_defaults(fn=cmd_krw_1m)

    p2 = sub.add_parser("us-stocks-1m", help="미장 SP500+NDX100 1m")
    p2.add_argument("--batch-size", type=int, default=50)
    p2.set_defaults(fn=cmd_us_stocks_1m)

    p3 = sub.add_parser("kr-stocks-1m", help="국장 KIS universe 1m")
    p3.add_argument("--bars-per-call", type=int, default=30)
    p3.set_defaults(fn=cmd_kr_stocks_1m)

    p4 = sub.add_parser("krw-1m-single", help="빗썸/업비트 즉시 수집")
    p4.add_argument("--targets", required=True,
                    help="콤마구분, e.g. bithumb:BTC/KRW,upbit:ETH/KRW")
    p4.set_defaults(fn=cmd_krw_1m_single)

    p5 = sub.add_parser("stocks-1m-single", help="국장/미장 즉시 수집")
    p5.add_argument("--market", choices=["kr", "us"], required=True)
    p5.add_argument("--symbols", required=True, help="콤마구분")
    p5.set_defaults(fn=cmd_stocks_1m_single)

    args = ap.parse_args()
    return asyncio.run(args.fn(args))


if __name__ == "__main__":
    raise SystemExit(main())
