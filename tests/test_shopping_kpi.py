import json
from pathlib import Path

from app.shopping_analytics_api import compute_kpis, load_json_rows


def test_compute_kpis_from_operational_snapshot():
    daily_stats = load_json_rows("vector_store/dashboard/daily_stats.json")
    transactions = load_json_rows("vector_store/dashboard/transactions.json")

    result = compute_kpis(daily_stats=daily_stats, transactions=transactions)

    assert result["summary"]["revenue"] == 55_000_000.0
    assert result["kpis"]["profit_rate_pct"] == 29.27
    assert result["kpis"]["conversion_rate_pct"] == 3.16
    assert result["kpis"]["average_margin_rate_pct"] == 41.45
    assert result["kpis"]["average_margin_multiple_x"] == 1.71

    category_mix = result["kpis"]["category_mix"]
    assert category_mix[0]["category"] == "전문공구"
    assert category_mix[0]["share_pct"] == 36.36
    assert round(sum(row["share_pct"] for row in category_mix), 2) == 100.0


def test_load_json_rows_requires_array(tmp_path: Path):
    source = tmp_path / "bad.json"
    source.write_text(json.dumps({"a": 1}), encoding="utf-8")

    try:
        load_json_rows(source)
    except ValueError as exc:
        assert "must contain a JSON array" in str(exc)
    else:
        raise AssertionError("expected ValueError for non-array payload")
