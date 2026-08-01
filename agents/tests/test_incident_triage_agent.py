from datetime import datetime, timezone

import pytest

from agents.incident_triage_agent import (
    IncidentRequestFailed,
    raise_incident,
    summarize_findings,
    triage_incident,
)
from contracts.capability_contract import Cloud, IncidentTriageRequest, LogFinding
from mcp_servers.servicenow.client import IncidentRecordRef, ServiceNowRequestError


def make_finding(severity: str, message: str) -> LogFinding:
    return LogFinding(timestamp=datetime.now(timezone.utc), severity=severity, message=message)


def test_summarize_findings_empty():
    summary = summarize_findings([], "payments-api", 24)
    assert "No log entries" in summary
    assert "payments-api" in summary
    assert "24h" in summary


def test_summarize_findings_highlights_repeated_message():
    findings = [
        make_finding("ERROR", "Connection timeout"),
        make_finding("ERROR", "Connection timeout"),
        make_finding("WARNING", "Retry attempt 2/3"),
    ]
    summary = summarize_findings(findings, "payments-api", 24)
    assert "Found 3 entries" in summary
    assert "2 ERROR" in summary
    assert "Connection timeout" in summary


def test_summarize_findings_single_entry_shows_most_recent():
    findings = [make_finding("WARNING", "Disk usage high")]
    summary = summarize_findings(findings, "checkout-vm", 12)
    assert "Most recent: Disk usage high" in summary


@pytest.fixture(autouse=True)
def stub_servicenow(monkeypatch):
    """Automated tests must not create real ServiceNow incidents — the real
    client is verified separately via live calls."""

    def fake_create_incident(short_description, description, urgency="3"):
        return IncidentRecordRef(number="INC9999999", sys_id="fake-incident-sys-id")

    monkeypatch.setattr("agents.incident_triage_agent.create_incident", fake_create_incident)


def test_triage_incident_azure_uses_mocked_findings():
    request = IncidentTriageRequest(
        requester="jane.doe@example.com",
        cloud=Cloud.AZURE,
        resource_hint="payments-api",
        time_window_hours=24,
        justification="Investigating repeated timeouts reported by the team",
    )
    result = triage_incident(request)

    assert result.cloud == Cloud.AZURE
    assert len(result.findings) == 4
    assert result.incident is None


def test_triage_incident_gcp_calls_real_lookup(monkeypatch):
    monkeypatch.setattr(
        "agents.incident_triage_agent.search_gcp_logs",
        lambda resource_hint, hours: [make_finding("ERROR", f"synthetic error for {resource_hint}")],
    )
    request = IncidentTriageRequest(
        requester="jane.doe@example.com",
        cloud=Cloud.GCP,
        resource_hint="checkout-service",
        time_window_hours=6,
        justification="Investigating a spike in 500s on checkout",
    )
    result = triage_incident(request)

    assert len(result.findings) == 1
    assert "checkout-service" in result.findings[0].message


def test_raise_incident_populates_incident_ref():
    request = IncidentTriageRequest(
        requester="jane.doe@example.com",
        cloud=Cloud.AZURE,
        resource_hint="payments-api",
        time_window_hours=24,
        justification="Investigating repeated timeouts reported by the team",
    )
    result = triage_incident(request)

    raised = raise_incident(result, request.requester, request.justification)

    assert raised.incident.number == "INC9999999"
    assert raised.incident.sys_id == "fake-incident-sys-id"


def test_raise_incident_wraps_servicenow_failure(monkeypatch):
    def fake_create_incident(short_description, description, urgency="3"):
        raise ServiceNowRequestError(500, "instance unavailable")

    monkeypatch.setattr("agents.incident_triage_agent.create_incident", fake_create_incident)

    request = IncidentTriageRequest(
        requester="jane.doe@example.com",
        cloud=Cloud.AZURE,
        resource_hint="payments-api",
        time_window_hours=24,
        justification="Investigating repeated timeouts reported by the team",
    )
    result = triage_incident(request)

    with pytest.raises(IncidentRequestFailed):
        raise_incident(result, request.requester, request.justification)
