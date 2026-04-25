import pytest

from app.paperclip_drift_monitor import PaperclipDriftMonitor, run_drift_monitor


def test_get_issues_with_blockers_uses_issue_detail():
    monitor = PaperclipDriftMonitor("http://example.com/", "token", "company-1", "run-1")
    calls: list[tuple[str, str, dict]] = []

    def fake_api_request(method: str, path: str, **kwargs):
        calls.append((method, path, kwargs))
        if path == "/api/companies/company-1/issues":
            assert kwargs["params"] == {"status": "in_progress"}
            return [
                {"id": "ISSUE-1", "identifier": "GLMA-1", "status": "in_progress"},
                {"id": "ISSUE-2", "identifier": "GLMA-2", "status": "in_progress"},
            ]
        if path == "/api/issues/ISSUE-1":
            return {"id": "ISSUE-1", "blockedBy": [{"id": "BLOCK-1", "status": "blocked"}]}
        if path == "/api/issues/ISSUE-2":
            return {"id": "ISSUE-2", "blockedBy": []}
        raise AssertionError(f"unexpected call: {method} {path}")

    monitor._api_request = fake_api_request  # type: ignore[method-assign]

    issues = monitor.get_issues_with_blockers()

    assert len(issues) == 1
    assert issues[0]["identifier"] == "GLMA-1"
    assert issues[0]["blockedBy"][0]["id"] == "BLOCK-1"
    assert calls[0][1] == "/api/companies/company-1/issues"
    assert calls[1][1] == "/api/issues/ISSUE-1"
    assert calls[2][1] == "/api/issues/ISSUE-2"


def test_check_blocker_resolved_handles_string_blocker():
    monitor = PaperclipDriftMonitor("http://example.com", "token", "company-1")
    assert monitor.check_blocker_resolved("BLOCK-1") is False
    assert monitor.check_blocker_resolved({"id": "BLOCK-2", "status": "done"}) is True
    assert monitor.check_blocker_resolved({"id": "BLOCK-3", "status": "blocked"}) is False


def test_run_drift_monitor_requires_configuration(monkeypatch):
    monkeypatch.delenv("PAPERCLIP_API_URL", raising=False)
    monkeypatch.delenv("PAPERCLIP_API_KEY", raising=False)
    monkeypatch.delenv("PAPERCLIP_COMPANY_ID", raising=False)

    with pytest.raises(ValueError) as exc:
        run_drift_monitor(api_url="", api_key="", company_id="")

    assert "PAPERCLIP_API_KEY" in str(exc.value)
    assert "PAPERCLIP_COMPANY_ID" in str(exc.value)


def test_run_drift_monitor_explicit_empty_does_not_fallback_to_env(monkeypatch):
    monkeypatch.setenv("PAPERCLIP_API_URL", "https://example.paperclip.local")
    monkeypatch.setenv("PAPERCLIP_API_KEY", "env-key")
    monkeypatch.setenv("PAPERCLIP_COMPANY_ID", "env-company")

    with pytest.raises(ValueError) as exc:
        run_drift_monitor(api_url="", api_key="", company_id="")

    message = str(exc.value)
    assert "PAPERCLIP_API_URL" in message
    assert "PAPERCLIP_API_KEY" in message
    assert "PAPERCLIP_COMPANY_ID" in message
