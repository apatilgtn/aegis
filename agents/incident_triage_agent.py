"""
The incident_triage capability agent: reads live log state and produces a
plain-language summary. Unlike access_grant, there is no policy gate here
(no infra risk, nothing to allow/deny) and no governed-change pipeline —
raising the incident is the action itself, done immediately once the human
confirms, not deferred behind an approval.
"""

from __future__ import annotations

from collections import Counter

from contracts.capability_contract import (
    Cloud,
    IncidentRef,
    IncidentTriageRequest,
    IncidentTriageResult,
    LogFinding,
)
from mcp_servers.azure_monitor.log_lookup import search_logs as search_azure_logs
from mcp_servers.gcp_logging.log_lookup import search_logs as search_gcp_logs
from mcp_servers.servicenow.client import ServiceNowConfigError, ServiceNowRequestError, create_incident


class IncidentRequestFailed(Exception):
    def __init__(self, reason: str):
        super().__init__(f"couldn't raise the ServiceNow incident: {reason}")


def summarize_findings(findings: list[LogFinding], resource_hint: str, time_window_hours: int) -> str:
    if not findings:
        return (
            f"No log entries matching '{resource_hint}' at WARNING severity or above were found "
            f"in the last {time_window_hours}h."
        )

    by_severity = Counter(f.severity for f in findings)
    severity_line = ", ".join(f"{count} {severity}" for severity, count in by_severity.most_common())

    by_message = Counter(f.message for f in findings)
    top_message, top_count = by_message.most_common(1)[0]

    lines = [f"Found {len(findings)} entries in the last {time_window_hours}h ({severity_line})."]
    if top_count > 1:
        lines.append(f"Most frequent ({top_count}x): {top_message}")
    else:
        lines.append(f"Most recent: {findings[0].message}")
    return " ".join(lines)


def triage_incident(request: IncidentTriageRequest) -> IncidentTriageResult:
    if request.cloud == Cloud.GCP:
        findings = search_gcp_logs(request.resource_hint, request.time_window_hours)
    else:
        findings = search_azure_logs(request.resource_hint, request.time_window_hours)

    summary = summarize_findings(findings, request.resource_hint, request.time_window_hours)

    return IncidentTriageResult(
        cloud=request.cloud,
        resource_hint=request.resource_hint,
        findings=findings,
        summary=summary,
        incident=None,
    )


def raise_incident(result: IncidentTriageResult, requester: str, justification: str) -> IncidentTriageResult:
    """The one explicit human-triggered write in this capability: creating
    the incident. No approval gate precedes it — investigating an issue
    carries no infrastructure risk, unlike access_grant's governed-change path."""
    try:
        incident = create_incident(
            short_description=f"Aegis triage: {result.resource_hint} ({result.cloud.value.upper()})",
            description=(
                f"Requested by: {requester}\nJustification: {justification}\n\nSummary: {result.summary}"
            ),
        )
    except (ServiceNowConfigError, ServiceNowRequestError) as exc:
        raise IncidentRequestFailed(str(exc)) from exc

    return result.model_copy(update={"incident": IncidentRef(number=incident.number, sys_id=incident.sys_id)})
