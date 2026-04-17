#!/usr/bin/env python3
"""Recalculate Shopping-Auto KPI metrics from operational source files."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.shopping_analytics_api import (
    check_naver_credentials,
    compute_kpis,
    load_json_rows,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--daily-stats",
        default="vector_store/dashboard/daily_stats.json",
        help="Path to daily_stats.json",
    )
    parser.add_argument(
        "--transactions",
        default="vector_store/dashboard/transactions.json",
        help="Path to transactions.json",
    )
    parser.add_argument(
        "--output",
        default="vector_store/dashboard/kpi_snapshot.json",
        help="Path to write KPI snapshot JSON",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    daily_stats = load_json_rows(args.daily_stats)
    transactions = load_json_rows(args.transactions)
    kpi_payload = compute_kpis(daily_stats=daily_stats, transactions=transactions)
    kpi_payload["credentials"] = check_naver_credentials()

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(kpi_payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(f"[ok] KPI snapshot written: {output_path}")
    print(
        "[kpi] profit_rate_pct={profit_rate_pct} conversion_rate_pct={conversion_rate_pct} "
        "average_margin_rate_pct={average_margin_rate_pct} average_margin_multiple_x={average_margin_multiple_x}".format(
            **kpi_payload["kpis"]
        )
    )
    print(
        "[kpi] top_category={category} share_pct={share_pct}".format(
            category=kpi_payload["kpis"]["category_mix"][0]["category"]
            if kpi_payload["kpis"]["category_mix"]
            else "n/a",
            share_pct=kpi_payload["kpis"]["category_mix"][0]["share_pct"]
            if kpi_payload["kpis"]["category_mix"]
            else "n/a",
        )
    )
    if not kpi_payload["credentials"]["available"]:
        print(
            "[warn] Missing credential env vars: "
            + ", ".join(kpi_payload["credentials"]["missing"])
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
