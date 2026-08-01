"""
Real, read-only log search backing the incident_triage capability. Uses the
same aegis-reader service account as the other GCP read lookups (already
has roles/logging.viewer — no new grant needed). Never writes; raising the
actual incident is a separate, explicit action (see mcp_servers/servicenow).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from google.cloud import logging_v2
from google.oauth2 import service_account

from contracts.capability_contract import LogFinding

_PROJECT_ID = "ual-demo-af168"
_KEY_PATH = Path(__file__).resolve().parents[2] / ".secrets" / "aegis-reader-key.json"

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


class GcpLoggingConfigError(Exception):
    pass


def _client() -> logging_v2.services.logging_service_v2.LoggingServiceV2Client:
    if not _KEY_PATH.exists():
        raise GcpLoggingConfigError(f"no credentials file at {_KEY_PATH}")
    credentials = service_account.Credentials.from_service_account_file(str(_KEY_PATH))
    return logging_v2.services.logging_service_v2.LoggingServiceV2Client(credentials=credentials)


def search_logs(resource_hint: str, time_window_hours: int, min_severity: str = "WARNING") -> list[LogFinding]:
    client = _client()
    since = datetime.now(timezone.utc) - timedelta(hours=time_window_hours)

    filter_str = (
        f'timestamp >= "{since.isoformat()}" '
        f"AND severity >= {min_severity} "
        f'AND (textPayload:"{resource_hint}" OR jsonPayload.message:"{resource_hint}" '
        f'OR resource.labels.service_name:"{resource_hint}" OR labels.build_trigger_name:"{resource_hint}")'
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
