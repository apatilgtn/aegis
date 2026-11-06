"""ServiceNow client for the Aegis worker.

Authenticates as the `aegis.integration` user through an OAuth
client-credentials grant (no username or password) and uses the Table API
for exactly four things: create a change request with approvals, read the
approval decision, create an incident, and add a closing note to a change.

Requests are idempotent: every record carries the Aegis request ID in
`correlation_id`, and creation first looks the record up by that ID.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

_TOKEN: tuple[str, datetime] | None = None
_SYS_ID = re.compile(r"[0-9a-f]{32}")


class ServiceNowError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool = True):
        super().__init__(message)
        self.retryable = retryable


@dataclass(frozen=True)
class ServiceNowConfig:
    instance_url: str
    client_id: str
    client_secret: str
    approver_group: str

    @classmethod
    def from_env(cls) -> "ServiceNowConfig":
        values = {name: os.getenv(name, "").strip() for name in (
            "SN_INSTANCE_URL", "SN_OAUTH_CLIENT_ID", "SN_OAUTH_CLIENT_SECRET", "SN_APPROVER_GROUP")}
        missing = [name for name, value in values.items() if not value]
        if missing:
            raise ServiceNowError(f"Missing ServiceNow settings: {', '.join(missing)}", retryable=False)
        url = values["SN_INSTANCE_URL"].rstrip("/")
        if not re.fullmatch(r"https://[a-z0-9-]+\.service-now\.com", url):
            raise ServiceNowError("SN_INSTANCE_URL must be https://<instance>.service-now.com", retryable=False)
        return cls(url, values["SN_OAUTH_CLIENT_ID"], values["SN_OAUTH_CLIENT_SECRET"], values["SN_APPROVER_GROUP"])


def _http(method: str, url: str, *, headers: dict[str, str], data: bytes | None = None) -> Any:
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            content = response.read()
            return json.loads(content) if content else None
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:300]
        retryable = exc.code in (408, 409, 429) or exc.code >= 500
        raise ServiceNowError(f"ServiceNow returned {exc.code} for {method} {urllib.parse.urlsplit(url).path}: {body}",
                              retryable=retryable) from exc


def access_token(config: ServiceNowConfig) -> str:
    global _TOKEN
    now = datetime.now(timezone.utc)
    if _TOKEN and _TOKEN[1] > now + timedelta(seconds=60):
        return _TOKEN[0]
    body = urllib.parse.urlencode({
        "grant_type": "client_credentials",
        "client_id": config.client_id,
        "client_secret": config.client_secret,
    }).encode()
    result = _http("POST", f"{config.instance_url}/oauth_token.do", data=body,
                   headers={"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"})
    token = (result or {}).get("access_token")
    if not token:
        raise ServiceNowError("ServiceNow OAuth response had no access token", retryable=False)
    _TOKEN = (token, now + timedelta(seconds=max(int(result.get("expires_in", 300)), 60)))
    return token


def table(config: ServiceNowConfig, method: str, path: str, *, query: dict[str, str] | None = None,
          body: dict[str, Any] | None = None) -> Any:
    url = f"{config.instance_url}/api/now/table/{path}"
    # Reference fields come back as plain sys_ids rather than {link, value}.
    query = {**(query or {}), "sysparm_exclude_reference_link": "true"}
    url += "?" + urllib.parse.urlencode(query)
    result = _http(method, url, data=None if body is None else json.dumps(body).encode(), headers={
        "Accept": "application/json", "Content-Type": "application/json",
        "Authorization": f"Bearer {access_token(config)}",
    })
    return (result or {}).get("result")


def record_url(config: ServiceNowConfig, record_table: str, sys_id: str) -> str:
    return f"{config.instance_url}/nav_to.do?uri={record_table}.do?sys_id={sys_id}"


def _correlation(request_id: str) -> str:
    if not re.fullmatch(r"[0-9a-f-]{36}", request_id):
        raise ServiceNowError("Aegis request ID must be a UUID", retryable=False)
    return f"aegis:{request_id}"


def _clean(value: str, limit: int) -> str:
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", value).strip()[:limit]


def _find(config: ServiceNowConfig, record_table: str, correlation_id: str, fields: str) -> dict[str, Any] | None:
    rows = table(config, "GET", record_table, query={
        "sysparm_query": f"correlation_id={correlation_id}", "sysparm_fields": fields, "sysparm_limit": "1"})
    return rows[0] if rows else None


def approver_group_members(config: ServiceNowConfig) -> list[dict[str, str]]:
    rows = table(config, "GET", "sys_user_grmember", query={
        "sysparm_query": f"group.name={config.approver_group}^user.active=true",
        "sysparm_fields": "user.sys_id,user.email", "sysparm_limit": "50"})
    members = [{"sys_id": row["user.sys_id"], "email": (row.get("user.email") or "").lower()} for row in rows or []]
    if not members:
        raise ServiceNowError(f"Approver group '{config.approver_group}' has no active members", retryable=False)
    return members


def open_change_for_approval(config: ServiceNowConfig, *, request_id: str, requester_email: str,
                             short_description: str, description: str, revision: str | None = None, excluded_emails: list[str] | None = None) -> dict[str, str]:
    """Create (or find) the change request and one approval per approver.

    The requester is never added as an approver, so they cannot approve
    their own request inside ServiceNow either.
    """
    correlation_id = _correlation(request_id)
    if revision:
        if not re.fullmatch(r"[a-f0-9]{64}", revision):
            raise ServiceNowError("Invalid plan revision", retryable=False)
        correlation_id = f"aegis-plan:{request_id}:{revision[:32]}"
    change = _find(config, "change_request", correlation_id, "sys_id,number")
    if change is None:
        change = table(config, "POST", "change_request", query={"sysparm_fields": "sys_id,number"}, body={
            "type": "normal",
            "short_description": _clean(short_description, 160),
            "description": _clean(description, 4000),
            "correlation_id": correlation_id,
            "correlation_display": "Aegis",
        })
    sys_id = change["sys_id"]
    if not _SYS_ID.fullmatch(sys_id):
        raise ServiceNowError("ServiceNow returned an invalid change sys_id", retryable=False)

    existing = {row["approver"] for row in table(config, "GET", "sysapproval_approver", query={
        "sysparm_query": f"sysapproval={sys_id}", "sysparm_fields": "approver", "sysparm_limit": "50"}) or []}
    excluded = {requester_email.lower(), *((email or "").lower() for email in (excluded_emails or []))}
    approvers = [member for member in approver_group_members(config) if member["email"] not in excluded]
    if not approvers:
        raise ServiceNowError("No approver other than the requester is available", retryable=False)
    for member in approvers:
        if member["sys_id"] in existing:
            continue
        table(config, "POST", "sysapproval_approver", query={"sysparm_fields": "sys_id"}, body={
            "sysapproval": sys_id, "source_table": "change_request", "document_id": sys_id,
            "approver": member["sys_id"], "state": "requested",
        })
    return {"change_sys_id": sys_id, "change_number": change["number"],
            "change_url": record_url(config, "change_request", sys_id)}


def approval_decision(config: ServiceNowConfig, change_sys_id: str, requester_email: str) -> dict[str, Any]:
    """Return {'state': requested|approved|rejected, 'approver_email': ...}.

    Any rejection wins. An approval only counts if it came from someone
    other than the requester (separation of duties is re-checked here, not
    only when approvers were assigned).
    """
    if not _SYS_ID.fullmatch(change_sys_id):
        raise ServiceNowError("Invalid change sys_id", retryable=False)
    rows = table(config, "GET", "sysapproval_approver", query={
        "sysparm_query": f"sysapproval={change_sys_id}",
        "sysparm_fields": "state,approver.email,comments,sys_updated_on", "sysparm_limit": "50"}) or []
    requester = requester_email.lower()
    allowed = {member["email"] for member in approver_group_members(config)}
    for row in rows:
        if row.get("state") == "rejected":
            return {"state": "rejected", "approver_email": (row.get("approver.email") or "").lower(),
                    "decided_at": row.get("sys_updated_on")}
    for row in rows:
        email = (row.get("approver.email") or "").lower()
        if row.get("state") == "approved" and email in allowed and email != requester:
            return {"state": "approved", "approver_email": email, "decided_at": row.get("sys_updated_on")}
    return {"state": "requested", "approver_email": None, "decided_at": None}


def add_change_note(config: ServiceNowConfig, change_sys_id: str, note: str) -> None:
    if not _SYS_ID.fullmatch(change_sys_id):
        raise ServiceNowError("Invalid change sys_id", retryable=False)
    table(config, "PATCH", f"change_request/{change_sys_id}", query={"sysparm_fields": "sys_id"},
          body={"work_notes": _clean(note, 4000)})


def open_incident(config: ServiceNowConfig, *, request_id: str, short_description: str, description: str,
                  urgency: str = "3") -> dict[str, str]:
    if urgency not in {"1", "2", "3"}:
        raise ServiceNowError("Urgency must be 1, 2 or 3", retryable=False)
    correlation_id = _correlation(request_id)
    incident = _find(config, "incident", correlation_id, "sys_id,number")
    if incident is None:
        incident = table(config, "POST", "incident", query={"sysparm_fields": "sys_id,number"}, body={
            "short_description": _clean(short_description, 160),
            "description": _clean(description, 4000),
            "urgency": urgency, "impact": "3",
            "correlation_id": correlation_id, "correlation_display": "Aegis",
        })
    return {"incident_sys_id": incident["sys_id"], "incident_number": incident["number"],
            "incident_url": record_url(config, "incident", incident["sys_id"])}
