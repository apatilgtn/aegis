"""
Mocked Azure Monitor / Log Analytics query — stands in for a live Azure
Monitor Logs (KQL) call since this environment has no Azure tenant
credentials. Real deployment swaps this module's internals for a real
query behind the same function signature; callers don't change (see
mcp_servers/gcp_logging/log_lookup.py for the real GCP equivalent this
mirrors).

Read-only, matching the propose-don't-mutate design.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from contracts.capability_contract import LogFinding

_MOCK_FINDINGS_BY_HINT = {
    "payments-api": [
        ("ERROR", "Connection timeout to payments-db after 30000ms"),
        ("WARNING", "Retry attempt 2/3 for downstream call to ledger-service"),
        ("ERROR", "Connection timeout to payments-db after 30000ms"),
        ("INFO", "Health check recovered after 3 failed attempts"),
    ],
    "checkout-vm": [
        ("CRITICAL", "Disk usage at 97% on /var/log"),
        ("WARNING", "CPU throttling detected for 45s"),
    ],
}


def search_logs(resource_hint: str, time_window_hours: int, min_severity: str = "WARNING") -> list[LogFinding]:
    now = datetime.now(timezone.utc)
    scenario = _MOCK_FINDINGS_BY_HINT.get(resource_hint.strip().lower(), [])

    return [
        LogFinding(timestamp=now - timedelta(minutes=5 * i), severity=severity, message=message)
        for i, (severity, message) in enumerate(scenario)
    ]
