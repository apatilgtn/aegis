"""
Real, read-only log search backing the incident_triage capability. Uses
Application Default Credentials so local development, workload identity, and
attached runtime identities work without a static key. Never writes; raising
the actual incident is a separate, explicit action (see mcp_servers/servicenow).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os

import google.auth
from google.auth.exceptions import DefaultCredentialsError
from google.cloud import logging_v2

from contracts.capability_contract import LogFinding

_PROJECT_ID = os.getenv("GCP_PROJECT_PAYMENTS_NONPROD", "ual-demo-af168")

# Standard Cloud Logging severity values (LogSeverity enum) — hardcoded
# because the generated client returns a bare int, not an enum wrapper.
_SEVERITY_NAMES = {
    0: "DEFAULT",
    100: "DEBUG",
    200: "INFO",
    300: "NOTICE",
    400: "WARNING",
    500: "ERROR",
    600: "CRITICAL",
    700: "ALERT",
    800: "EMERGENCY",
}
_ALLOWED_SEVERITIES = frozenset(_SEVERITY_NAMES.values())


class GcpLoggingConfigError(Exception):
    pass


def _client() -> logging_v2.services.logging_service_v2.LoggingServiceV2Client:
    try:
        credentials, _ = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform.read-only"]
        )
    except DefaultCredentialsError as exc:
        raise GcpLoggingConfigError("GCP Application Default Credentials are unavailable") from exc
    return logging_v2.services.logging_service_v2.LoggingServiceV2Client(credentials=credentials)


def search_logs(resource_hint: str, time_window_hours: int, min_severity: str = "WARNING") -> list[LogFinding]:
    resource_hint = resource_hint.strip()
    if (
        not resource_hint
        or len(resource_hint) > 255
        or any(ord(char) < 32 for char in resource_hint)
    ):
        raise ValueError("resource_hint must contain 1-255 printable characters")
    if (
        not isinstance(time_window_hours, int)
        or isinstance(time_window_hours, bool)
        or not 1 <= time_window_hours <= 168
    ):
        raise ValueError("time_window_hours must be an integer between 1 and 168")
    min_severity = min_severity.upper()
    if min_severity not in _ALLOWED_SEVERITIES:
        raise ValueError("min_severity is not a supported Cloud Logging severity")

    client = _client()
    since = datetime.now(timezone.utc) - timedelta(hours=time_window_hours)
    # Cloud Logging string literals use JSON-compatible escaping. Encoding once
    # prevents a hint from terminating the literal and adding filter clauses.
    hint_literal = json.dumps(resource_hint)

    filter_str = (
        f'timestamp >= "{since.isoformat()}" '
        f"AND severity >= {min_severity} "
        f"AND (textPayload:{hint_literal} OR jsonPayload.message:{hint_literal} "
        f"OR resource.labels.service_name:{hint_literal} OR labels.build_trigger_name:{hint_literal})"
    )

    entries = client.list_log_entries(
        request={
            "resource_names": [f"projects/{_PROJECT_ID}"],
            "filter": filter_str,
            "order_by": "timestamp desc",
            "page_size": 50,
        }
    )

    findings = []
    for entry in entries:
        if entry.text_payload:
            message = entry.text_payload
        elif entry.json_payload:
            message = str(dict(entry.json_payload))
        else:
            message = "(no payload)"
        findings.append(
            LogFinding(
                timestamp=entry.timestamp,
                severity=_SEVERITY_NAMES.get(int(entry.severity), str(entry.severity)),
                message=message[:500],
            )
        )
    return findings
