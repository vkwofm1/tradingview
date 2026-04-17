"""External API monitoring helpers for operational dashboards."""

from __future__ import annotations

import os
import time
from typing import Any

import httpx

from app.collectors import bithumb, crypto, naver_stocks, stocks, upbit

API_DEPENDENCIES = [
    {
        "name": "CoinGecko API",
        "collector": "crypto",
        "method": "GET",
        "url": f"{crypto.COINGECKO_URL}/ping",
        "params": None,
        "headers": None,
        "required_json_paths": [("gecko_says",)],
        "schema_risk": "low",
    },
    {
        "name": "Yahoo Finance API",
        "collector": "stocks",
        "method": "GET",
        "url": f"{stocks.YF_URL}/AAPL",
        "params": {"range": "1d", "interval": "5m"},
        "headers": {"User-Agent": "tradingview-crawl/0.1"},
        "required_json_paths": [("chart", "result")],
        "schema_risk": "medium",
    },
    {
        "name": "Naver Finance API",
        "collector": "naver_stocks",
        "method": "GET",
        "url": naver_stocks.QUOTE_URL.format(code="005930"),
        "params": None,
        "headers": naver_stocks.HEADERS,
        "required_json_paths": [("result", "areas"), ("result", "areas", 0, "datas"), ("result", "areas", 0, "datas", 0, "nv")],
        "schema_risk": "high",
    },
    {
        "name": "Upbit API",
        "collector": "upbit",
        "method": "GET",
        "url": upbit.UPBIT_URL,
        "params": {"market": "KRW-BTC", "count": 1},
        "headers": None,
        "required_json_paths": [(0, "trade_price")],
        "schema_risk": "low",
    },
    {
        "name": "Bithumb API",
        "collector": "bithumb",
        "method": "GET",
        "url": bithumb.BITHUMB_URL.format(symbol="BTC"),
        "params": None,
        "headers": None,
        "required_json_paths": [("status",), ("data",)],
        "schema_risk": "low",
    },
]


def _read_json_path(payload: Any, path: tuple[Any, ...]) -> Any:
    current = payload
    for key in path:
        if isinstance(key, int):
            if not isinstance(current, list) or key >= len(current):
                return None
            current = current[key]
            continue
        if not isinstance(current, dict):
            return None
        current = current.get(key)
        if current is None:
            return None
    return current


def _alert_channels() -> list[dict[str, Any]]:
    email_targets = [value.strip() for value in os.environ.get("ALERT_EMAIL_TO", "").split(",") if value.strip()]
    slack_webhook = os.environ.get("ALERT_SLACK_WEBHOOK_URL", "").strip()
    return [
        {
            "channel": "email",
            "configured": bool(email_targets),
            "targets": email_targets,
        },
        {
            "channel": "slack",
            "configured": bool(slack_webhook),
            "targets": ["webhook"] if slack_webhook else [],
        },
    ]


async def check_api_dependencies() -> list[dict[str, Any]]:
    """Run lightweight health checks against every upstream API dependency."""
    results: list[dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=15) as client:
        for dependency in API_DEPENDENCIES:
            started = time.perf_counter()
            status = "healthy"
            schema_status = "ok"
            detail = None
            missing_paths: list[str] = []
            try:
                response = await client.request(
                    dependency["method"],
                    dependency["url"],
                    params=dependency["params"],
                    headers=dependency["headers"],
                )
                response.raise_for_status()
                payload = response.json()
                for path in dependency["required_json_paths"]:
                    if _read_json_path(payload, path) is None:
                        missing_paths.append(".".join(str(part) for part in path))
                if missing_paths:
                    schema_status = "changed"
                    if dependency["collector"] == "naver_stocks":
                        status = "degraded"
                        detail = f"필수 응답 필드 누락: {', '.join(missing_paths)}"
                    else:
                        detail = f"예상 필드 누락: {', '.join(missing_paths)}"
                if dependency["collector"] == "bithumb" and payload.get("status") != "0000":
                    status = "failing"
                    detail = f"Bithumb status={payload.get('status')}"
            except Exception as exc:  # noqa: BLE001
                status = "failing"
                schema_status = "unknown"
                detail = str(exc)
            latency_ms = round((time.perf_counter() - started) * 1000, 2)
            results.append(
                {
                    "name": dependency["name"],
                    "collector": dependency["collector"],
                    "status": status,
                    "schema_status": schema_status,
                    "schema_risk": dependency["schema_risk"],
                    "latency_ms": latency_ms,
                    "detail": detail,
                    "missing_paths": missing_paths,
                }
            )
    return results


async def build_operations_dashboard(
    *,
    failure_rate_threshold_pct: float,
    job_failure_rates: list[dict[str, Any]],
) -> dict[str, Any]:
    api_checks = await check_api_dependencies()
    alerts: list[dict[str, Any]] = []

    for item in api_checks:
        if item["status"] == "failing":
            alerts.append(
                {
                    "severity": "high",
                    "code": "api_unreachable",
                    "collector": item["collector"],
                    "message": f"{item['name']} 헬스체크가 실패했습니다.",
                }
            )
        elif item["schema_status"] == "changed":
            alerts.append(
                {
                    "severity": "high" if item["collector"] == "naver_stocks" else "medium",
                    "code": "api_schema_changed",
                    "collector": item["collector"],
                    "message": f"{item['name']} 응답 스키마가 예상과 다릅니다.",
                }
            )

    for item in job_failure_rates:
        if item["alert"]:
            alerts.append(
                {
                    "severity": "high",
                    "code": "job_failure_rate_high",
                    "collector": item["collector"],
                    "message": (
                        f"{item['collector']} 최근 24시간 실패율이 "
                        f"{failure_rate_threshold_pct}% 임계치를 초과했습니다."
                    ),
                }
            )

    healthy_apis = sum(1 for item in api_checks if item["status"] == "healthy")
    degraded_apis = sum(1 for item in api_checks if item["status"] == "degraded")
    failing_apis = sum(1 for item in api_checks if item["status"] == "failing")

    return {
        "summary": {
            "api_count": len(api_checks),
            "healthy_apis": healthy_apis,
            "degraded_apis": degraded_apis,
            "failing_apis": failing_apis,
            "collectors_over_failure_threshold": sum(1 for item in job_failure_rates if item["alert"]),
            "alert_count": len(alerts),
        },
        "apis": api_checks,
        "job_failure_rates": job_failure_rates,
        "alert_channels": _alert_channels(),
        "alerts": alerts,
    }
