"""Tests for the cron collection CLI output contract."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from scripts import collect_jobs


@pytest.mark.asyncio
async def test_command_serializes_run_collector_datetime_as_iso8601(
    capsys,
    monkeypatch,
):
    collected_at = datetime(
        2026,
        7,
        14,
        9,
        29,
        30,
        123456,
        tzinfo=timezone(timedelta(hours=9)),
    )

    async def fake_run_collector(*_args, **_kwargs):
        return {"status": "completed", "created_at": collected_at}

    monkeypatch.setattr(collect_jobs.db, "init_db", lambda: None)
    monkeypatch.setattr(collect_jobs, "run_collector", fake_run_collector)

    exit_code = await collect_jobs.cmd_kr_stocks_1m(None)

    output = capsys.readouterr().out
    assert json.loads(output) == {
        "status": "completed",
        "created_at": "2026-07-14T09:29:30.123456+09:00",
    }
    assert output == (
        "{\n"
        '  "status": "completed",\n'
        '  "created_at": "2026-07-14T09:29:30.123456+09:00"\n'
        "}\n"
    )
    assert exit_code == 0


def test_print_preserves_primitive_json_output(capsys):
    payload = {
        "collector": "naver_stocks",
        "result_count": 5,
        "active": True,
        "error": None,
        "symbols": ["005930", "000660"],
    }

    collect_jobs._print(payload)

    output = capsys.readouterr().out
    assert json.loads(output) == payload
    assert output == json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


@pytest.mark.asyncio
async def test_krw_rotation_command_runs_selected_exchange(monkeypatch, capsys):
    calls = []

    async def fake_collect(job_id, **kwargs):
        calls.append((job_id, kwargs))
        return 42

    async def fake_run(name, fn, symbols):
        assert name == "upbit_1m_rotation"
        assert symbols is None
        count = await fn("job-upbit", None)
        return {"status": "completed", "result_count": count}

    monkeypatch.setattr(collect_jobs.db, "init_db", lambda: None)
    monkeypatch.setattr(collect_jobs.exchange_1m, "collect_krw_1m", fake_collect)
    monkeypatch.setattr(collect_jobs, "run_collector", fake_run)

    exit_code = await collect_jobs.cmd_krw_1m_rotate(
        SimpleNamespace(exchange="upbit", batch_size=30, lookback_minutes=3600)
    )

    assert exit_code == 0
    assert calls == [
        (
            "job-upbit",
            {
                "exchanges": ["upbit"],
                "lookback_minutes": 3600,
                "batch_size": 30,
            },
        )
    ]
    assert json.loads(capsys.readouterr().out) == {
        "status": "completed",
        "result_count": 42,
    }
