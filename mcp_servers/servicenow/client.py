"""
Real ServiceNow client — Layer 4 of the Aegis architecture (the governed
change path). Unlike the Layer 3 tool servers (azure_entra, gcp_resource_manager)
which are strictly read-only, this one legitimately writes — but only ever
creates the approval ask itself (a Service Catalog Request + an explicit
approval task). It never touches cloud infrastructure; that only happens
after a human approves and CI/CD runs `terraform apply`.

Object model: a bare Table API insert into sc_request does NOT trigger
ServiceNow's workflow engine (verified empirically — the request sits at
approval="not requested" with no task in anyone's approval queue). To get a
real, actionable approval, we explicitly create a sysapproval_approver
record tied to the request. That record's own `state` field is the reliable
source of truth for status — the parent request's `approval` rollup field
was observed to report stale/incorrect values on this instance, so callers
must poll the approval task, not the request.

Credentials are read from environment variables (see .env.example), loaded
via python-dotenv — never hardcoded, never logged.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import requests
from dotenv import load_dotenv
from pydantic import BaseModel

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

_REQUIRED_ENV_VARS = ("SN_INSTANCE_URL", "SN_USERNAME", "SN_PASSWORD")


class ServiceNowConfigError(Exception):
    pass


class ServiceNowRequestError(Exception):
    def __init__(self, status_code: int, body: str):
        self.status_code = status_code
        super().__init__(f"ServiceNow API returned {status_code}: {body[:300]}")


class AccessRequestRef(BaseModel):
    request_number: str
    request_sys_id: str
    approval_sys_id: str
    approval_state: str


class IncidentRecordRef(BaseModel):
    number: str
    sys_id: str


def _config() -> tuple[str, str, str]:
    missing = [name for name in _REQUIRED_ENV_VARS if not os.getenv(name)]
    if missing:
        raise ServiceNowConfigError(f"missing required environment variables: {', '.join(missing)}")
    return os.environ["SN_INSTANCE_URL"], os.environ["SN_USERNAME"], os.environ["SN_PASSWORD"]


def _get(path: str, params: dict) -> dict:
    instance_url, username, password = _config()
    response = requests.get(
        f"{instance_url}/api/now/table/{path}",
        auth=(username, password),
        headers={"Accept": "application/json"},
        params=params,
        timeout=15,
    )
    if response.status_code != 200:
        raise ServiceNowRequestError(response.status_code, response.text)
    return response.json()["result"]


def _post(path: str, body: dict) -> dict:
    instance_url, username, password = _config()
    response = requests.post(
        f"{instance_url}/api/now/table/{path}",
        auth=(username, password),
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        json=body,
        timeout=15,
    )
    if response.status_code not in (200, 201):
        raise ServiceNowRequestError(response.status_code, response.text)
    return response.json()["result"]


@lru_cache(maxsize=8)
def _resolve_approver_sys_id(username: str) -> str:
    results = _get("sys_user", {"sysparm_query": f"user_name={username}", "sysparm_fields": "sys_id", "sysparm_limit": 1})
    if not results:
        raise ServiceNowConfigError(f"no ServiceNow user found for SN_APPROVER_USERNAME={username!r}")
    return results[0]["sys_id"]


def create_access_request(short_description: str, description: str, justification: str) -> AccessRequestRef:
    """Raises the ServiceNow approval ask for a proposed access grant: a
    Service Catalog Request plus an explicit approval task, so a real human
    has something to act on in their ServiceNow approval queue.
    """
    approver_username = os.getenv("SN_APPROVER_USERNAME", "admin")
    approver_sys_id = _resolve_approver_sys_id(approver_username)

    request = _post(
        "sc_request",
        {"short_description": short_description, "description": f"{description}\n\nJustification: {justification}"},
    )

    approval_task = _post(
        "sysapproval_approver",
        {
            "sysapproval": request["sys_id"],
            "approver": approver_sys_id,
            "state": "requested",
            "short_description": short_description,
        },
    )

    return AccessRequestRef(
        request_number=request["number"],
        request_sys_id=request["sys_id"],
        approval_sys_id=approval_task["sys_id"],
        approval_state=approval_task["state"],
    )


def create_incident(short_description: str, description: str, urgency: str = "3") -> IncidentRecordRef:
    """Raises a ServiceNow incident directly — unlike create_access_request,
    there is no approval task to create, because raising the incident IS the
    action (investigating/tracking an issue), not a request for permission
    to change something. urgency follows ServiceNow's native scale:
    "1" (high) .. "3" (low).
    """
    result = _post(
        "incident",
        {"short_description": short_description, "description": description, "urgency": urgency},
    )
    return IncidentRecordRef(number=result["number"], sys_id=result["sys_id"])


def get_approval_state(approval_sys_id: str) -> str:
    """Read-only status check against the approval task itself (not the
    request's rollup field — see module docstring for why that's unreliable).
    Returns one of ServiceNow's native values: requested, approved,
    rejected, cancelled, not requested.
    """
    result = _get("sysapproval_approver", {"sysparm_query": f"sys_id={approval_sys_id}", "sysparm_fields": "state", "sysparm_limit": 1})
    if not result:
        raise ServiceNowRequestError(404, f"no approval task found for sys_id={approval_sys_id}")
    return result[0]["state"]
