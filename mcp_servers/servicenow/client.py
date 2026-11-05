"""ServiceNow OAuth client for governed, Flow-owned integration endpoints.

Aegis never writes ServiceNow tables directly. Each operation calls an
administrator-owned Flow Designer REST endpoint with a short-lived OAuth
client-credentials token. The Flow owns record creation, approval routing,
ACL enforcement, validation, and response shaping.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv
from pydantic import BaseModel

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

_REQUIRED_ENV_VARS = (
    "SN_INSTANCE_URL",
    "SN_OAUTH_CLIENT_ID",
    "SN_OAUTH_CLIENT_SECRET",
    "SN_ACCESS_FLOW_PATH",
    "SN_STATUS_FLOW_PATH",
    "SN_INCIDENT_FLOW_PATH",
)
_token_cache: tuple[str, datetime] | None = None


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


def _config() -> dict[str, str]:
    missing = [name for name in _REQUIRED_ENV_VARS if not os.getenv(name)]
    if missing:
        raise ServiceNowConfigError(f"missing required environment variables: {', '.join(missing)}")
    instance_url = os.environ["SN_INSTANCE_URL"].rstrip("/")
    if not instance_url.startswith("https://"):
        raise ServiceNowConfigError("SN_INSTANCE_URL must use https")
    for name in ("SN_ACCESS_FLOW_PATH", "SN_STATUS_FLOW_PATH", "SN_INCIDENT_FLOW_PATH"):
        if not os.environ[name].startswith("/") or "://" in os.environ[name]:
            raise ServiceNowConfigError(f"{name} must be a relative path beginning with '/'")
    return {
        "instance_url": instance_url,
        "client_id": os.environ["SN_OAUTH_CLIENT_ID"],
        "client_secret": os.environ["SN_OAUTH_CLIENT_SECRET"],
        "access_path": os.environ["SN_ACCESS_FLOW_PATH"],
        "status_path": os.environ["SN_STATUS_FLOW_PATH"],
        "incident_path": os.environ["SN_INCIDENT_FLOW_PATH"],
    }


def _access_token(config: dict[str, str]) -> str:
    global _token_cache
    now = datetime.now(timezone.utc)
    if _token_cache and _token_cache[1] > now + timedelta(seconds=60):
        return _token_cache[0]

    response = requests.post(
        f"{config['instance_url']}/oauth_token.do",
        headers={"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"},
        data={
            "grant_type": "client_credentials",
            "client_id": config["client_id"],
            "client_secret": config["client_secret"],
        },
        timeout=15,
    )
    if response.status_code != 200:
        raise ServiceNowRequestError(response.status_code, response.text)
    payload = response.json()
    token = payload.get("access_token")
    if not token:
        raise ServiceNowRequestError(502, "OAuth response did not contain access_token")
    expires_in = max(int(payload.get("expires_in", 300)), 60)
    _token_cache = (token, now + timedelta(seconds=expires_in))
    return token


def _invoke_flow(path_key: str, body: dict) -> dict:
    config = _config()
    response = requests.post(
        f"{config['instance_url']}{config[path_key]}",
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {_access_token(config)}",
        },
        json=body,
        timeout=30,
    )
    if response.status_code not in (200, 201, 202):
        raise ServiceNowRequestError(response.status_code, response.text)
    payload = response.json()
    result = payload.get("result", payload)
    if not isinstance(result, dict):
        raise ServiceNowRequestError(502, "Flow response must contain a JSON object")
    return result


def _required(result: dict, *keys: str) -> tuple[str, ...]:
    missing = [key for key in keys if not isinstance(result.get(key), str) or not result[key]]
    if missing:
        raise ServiceNowRequestError(502, f"Flow response missing fields: {', '.join(missing)}")
    return tuple(result[key] for key in keys)


def create_access_request(short_description: str, description: str, justification: str) -> AccessRequestRef:
    """Ask the configured access-request Flow to create and route approval."""
    result = _invoke_flow(
        "access_path",
        {
            "short_description": short_description,
            "description": description,
            "justification": justification,
        },
    )
    request_number, request_sys_id, approval_sys_id, approval_state = _required(
        result, "request_number", "request_sys_id", "approval_sys_id", "approval_state"
    )
    return AccessRequestRef(
        request_number=request_number,
        request_sys_id=request_sys_id,
        approval_sys_id=approval_sys_id,
        approval_state=approval_state,
    )


def create_incident(short_description: str, description: str, urgency: str = "3") -> IncidentRecordRef:
    """Ask the configured incident Flow to create a governed incident."""
    result = _invoke_flow(
        "incident_path",
        {"short_description": short_description, "description": description, "urgency": urgency},
    )
    number, sys_id = _required(result, "number", "sys_id")
    return IncidentRecordRef(number=number, sys_id=sys_id)


def get_approval_state(approval_sys_id: str) -> str:
    """Ask the status Flow for the current approval state."""
    result = _invoke_flow("status_path", {"approval_sys_id": approval_sys_id})
    (state,) = _required(result, "state")
    if state not in {"requested", "approved", "rejected", "cancelled", "not requested"}:
        raise ServiceNowRequestError(502, f"Flow returned unsupported approval state: {state!r}")
    return state
