"""Pure governance decisions and read-only post-apply checks."""
from __future__ import annotations
import hashlib
import json
from datetime import datetime, timezone
from typing import Any


def digest(value: Any) -> str:
    encoded = value if isinstance(value, str) else json.dumps(value, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def control_denials(request: dict, account: dict, settings: dict, policy: dict, route: dict | None = None) -> list[str]:
    capability = request.get("capability")
    resource = request.get("resource") or {}
    reasons = []
    if not policy.get("enabled"):
        reasons.append("This capability is disabled for the workspace")
    if capability == "incident_triage":
        if account.get("environment") == "prod" and not policy.get("production_allowed"):
            reasons.append("Log investigations are not enabled for production")
        return reasons
    if not settings.get("writes_enabled"):
        reasons.append("Governed writes are disabled")
    if settings.get("proposals_paused"):
        reasons.append("New proposals are paused by an administrator")
    if resource.get("environment") != account.get("environment"):
        reasons.append("Request environment does not match the governed project")
    if account.get("environment") == "prod" and not policy.get("production_allowed"):
        reasons.append("This capability is not enabled for production")
    if capability == "access_grant":
        if route is None or route.get("target_identifier") != account.get("project_id") or route.get("environment") != account.get("environment"):
            reasons.append("No reviewed route matches this project's environment and scope")
        elif route.get("role_identifier") not in policy.get("allowed_roles", []):
            reasons.append("The IAM role is not allowed by the capability policy")
        ttl = resource.get("ttl_hours")
        if not isinstance(ttl, int) or isinstance(ttl, bool) or ttl < 1:
            reasons.append("Access duration must be a positive whole number")
        elif route and ttl > min(int(route.get("max_ttl_hours", 0)), int(policy.get("max_ttl_hours", 0))):
            reasons.append("Access duration exceeds the reviewed policy or entitlement limit")
    return reasons


def verify_labels(expected: dict[str, str], observed: dict[str, str]) -> dict:
    missing = [key for key, value in expected.items() if observed.get(key) != value]
    return {"state": "failed" if missing else "verified", "details": {"checked_keys": sorted(expected), "mismatched_keys": missing, "scope": "Requested label values only"}}


def verify_iam(policy: dict, role: str, member: str, expiry: str, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    if datetime.fromisoformat(expiry.replace("Z", "+00:00")) <= now:
        return {"state": "expired", "details": {"expires_at": expiry, "scope": "Grant window has ended"}}
    expression = f'request.time < timestamp("{expiry}")'
    found = any(b.get("role") == role and member in b.get("members", []) and (b.get("condition") or {}).get("expression") == expression for b in policy.get("bindings", []))
    return {"state": "verified" if found else "failed", "details": {"role": role, "member": member, "expires_at": expiry, "scope": "Requested conditional binding only; inherited and other grants are not assessed"}}
