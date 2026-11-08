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
    return {"state": "failed" if missing else "verified", "details": {"checked_keys": sorted(expected), "mismatched_keys": missing, "scope": "Requested label values only", "comparison": [{"key": key, "expected": value, "observed": observed.get(key), "present": key in observed, "matched": observed.get(key) == value} for key, value in sorted(expected.items())]}}


def verify_iam(policy: dict, role: str, member: str, expiry: str, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    if datetime.fromisoformat(expiry.replace("Z", "+00:00")) <= now:
        return {"state": "expired", "details": {"expires_at": expiry, "scope": "Grant window has ended"}}
    expression = f'request.time < timestamp("{expiry}")'
    candidates = [b for b in policy.get("bindings", []) if b.get("role") == role and member in b.get("members", [])]
    found = any((b.get("condition") or {}).get("expression") == expression for b in candidates)
    return {"state": "verified" if found else "failed", "details": {"role": role, "member": member, "expires_at": expiry, "scope": "Requested conditional binding only; inherited and other grants are not assessed", "expected_condition": expression, "observed_conditions": [(b.get("condition") or {}).get("expression") for b in candidates[:10]], "matching_role_member_bindings": len(candidates), "observations_truncated": len(candidates) > 10}}


def approval_manifest(request: dict, account: dict, route: dict | None, requester: str, beneficiary: str) -> dict:
    """Stable authorization facts; no model-generated interpretation or wall clock."""
    decision = request.get('policy_decision') or {}
    resource = request.get('resource') or {}
    scope = {'project': account['project_id'], 'environment': account.get('environment'),
             'type': route.get('scope_type', 'project') if route else 'bucket',
             'resource': (route.get('scope_identifier') or account['project_id']) if route else resource.get('bucket'),
             'role': route.get('role_identifier') if route else None}
    return {'version': 1, 'requester_id': request['requester_id'], 'requester': requester.lower(),
            'beneficiary_id': request.get('beneficiary_id') or request['requester_id'], 'beneficiary': beneficiary.lower(),
            'capability': request['capability'], 'scope': scope,
            'grant_expires_at': decision.get('proposed_expires_at'),
            'policy_bundle_sha256': decision.get('policy_bundle_sha256'),
            'governance_revision': decision.get('governance_revision')}


def compare_manifest(approved: dict, current: dict) -> list[dict]:
    labels = {'requester_id': 'Requester identity', 'requester': 'Requester email', 'beneficiary_id': 'Beneficiary identity',
              'beneficiary': 'Beneficiary email', 'capability': 'Capability', 'scope': 'Cloud scope and role',
              'grant_expires_at': 'Grant expiry', 'policy_bundle_sha256': 'Policy bundle', 'governance_revision': 'Policy revision'}
    return [{'key': key, 'label': label, 'state': 'matched' if approved.get(key) == current.get(key) else 'changed',
             'approved': approved.get(key), 'current': current.get(key)} for key, label in labels.items()]
