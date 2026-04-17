"""Shopping-Auto KPI recalculation helpers."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def _to_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def load_json_rows(path: str | Path) -> list[dict[str, Any]]:
    source = Path(path)
    payload = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError(f"{source} must contain a JSON array")
    return [row for row in payload if isinstance(row, dict)]


def check_naver_credentials() -> dict[str, Any]:
    client_id = os.getenv("NAVER_CLIENT_ID", "").strip()
    client_secret = os.getenv("NAVER_CLIENT_SECRET", "").strip()
    return {
        "available": bool(client_id and client_secret),
        "missing": [
            name
            for name, value in (
                ("NAVER_CLIENT_ID", client_id),
                ("NAVER_CLIENT_SECRET", client_secret),
            )
            if not value
        ],
    }


def compute_kpis(
    daily_stats: list[dict[str, Any]],
    transactions: list[dict[str, Any]],
) -> dict[str, Any]:
    impressions = sum(_to_float(row.get("impressions")) for row in daily_stats)
    orders = sum(_to_float(row.get("orders")) for row in daily_stats)
    revenue = sum(_to_float(row.get("revenue")) for row in daily_stats)
    ad_cost = sum(_to_float(row.get("ad_cost")) for row in daily_stats)
    cogs = sum(_to_float(row.get("cogs")) for row in daily_stats)

    profit = revenue - ad_cost - cogs
    profit_rate_pct = (profit / revenue * 100.0) if revenue else 0.0
    conversion_rate_pct = (orders / impressions * 100.0) if impressions else 0.0
    average_margin_rate_pct = ((revenue - cogs) / revenue * 100.0) if revenue else 0.0
    average_margin_multiple_x = (revenue / cogs) if cogs else 0.0

    by_category: dict[str, float] = {}
    for tx in transactions:
        category = str(tx.get("category", "unknown"))
        by_category[category] = by_category.get(category, 0.0) + _to_float(tx.get("revenue"))

    total_transaction_revenue = sum(by_category.values())
    category_mix = sorted(
        [
            {
                "category": category,
                "revenue": round(value, 2),
                "share_pct": round((value / total_transaction_revenue * 100.0), 2)
                if total_transaction_revenue
                else 0.0,
            }
            for category, value in by_category.items()
        ],
        key=lambda row: row["revenue"],
        reverse=True,
    )

    return {
        "summary": {
            "daily_stats_rows": len(daily_stats),
            "transactions_rows": len(transactions),
            "impressions": int(impressions),
            "orders": int(orders),
            "revenue": round(revenue, 2),
            "ad_cost": round(ad_cost, 2),
            "cogs": round(cogs, 2),
            "profit": round(profit, 2),
        },
        "kpis": {
            "profit_rate_pct": round(profit_rate_pct, 2),
            "conversion_rate_pct": round(conversion_rate_pct, 2),
            "average_margin_rate_pct": round(average_margin_rate_pct, 2),
            "average_margin_multiple_x": round(average_margin_multiple_x, 2),
            "category_mix": category_mix,
        },
    }
