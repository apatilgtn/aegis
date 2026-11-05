"""Run the destructive-boundary P0 workflow check against hosted Supabase.

The check creates two synthetic users, proves that requester self-approval is
rejected, obtains an independent approval, and waits for the local worker to
open a governed GitHub pull request. It never merges the pull request. Test
memberships are disabled and sessions are signed out before exit so the
synthetic identities retain no workspace access.
"""

from __future__ import annotations

import json
import os
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import UTC, datetime
from typing import Any


SUPABASE_URL = os.environ["SUPABASE_URL"].rstrip("/")
SECRET_KEY = os.environ["SUPABASE_SECRET_KEY"]
PUBLISHABLE_KEY = os.environ["SUPABASE_PUBLISHABLE_KEY"]


def request_json(
    path: str,
    *,
    method: str = "GET",
    body: Any | None = None,
    token: str | None = None,
    admin: bool = False,
    prefer: str | None = None,
    expected: tuple[int, ...] = (200,),
) -> tuple[int, Any]:
    credential = SECRET_KEY if admin else (token or PUBLISHABLE_KEY)
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "apikey": SECRET_KEY if admin else PUBLISHABLE_KEY,
        "Authorization": f"Bearer {credential}",
    }
    if prefer:
        headers["Prefer"] = prefer
    payload = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        f"{SUPABASE_URL}{path}", data=payload, headers=headers, method=method
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read()
            result = json.loads(raw) if raw else None
            if response.status not in expected:
                raise RuntimeError(f"Unexpected HTTP {response.status} for {method} {path}")
            return response.status, result
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        if exc.code in expected:
            try:
                return exc.code, json.loads(raw)
            except json.JSONDecodeError:
                return exc.code, {"message": raw[:300]}
        raise RuntimeError(f"HTTP {exc.code} for {method} {path}: {raw[:300]}") from exc


def create_user(email: str, password: str) -> str:
    _, payload = request_json(
        "/auth/v1/admin/users",
        method="POST",
        admin=True,
        body={"email": email, "password": password, "email_confirm": True},
        expected=(200, 201),
    )
    return str(payload["id"])


def sign_in(email: str, password: str) -> str:
    _, payload = request_json(
        "/auth/v1/token?grant_type=password",
        method="POST",
        body={"email": email, "password": password},
        expected=(200,),
    )
    return str(payload["access_token"])


def insert_membership(tenant_id: str, user_id: str, role: str) -> None:
    request_json(
        "/rest/v1/tenant_memberships",
        method="POST",
        admin=True,
        body={"tenant_id": tenant_id, "user_id": user_id, "role": role, "status": "active"},
        prefer="return=minimal",
        expected=(201,),
    )


def remove_membership(user_id: str) -> None:
    encoded = urllib.parse.quote(user_id, safe="")
    request_json(
        f"/rest/v1/tenant_memberships?user_id=eq.{encoded}",
        method="DELETE",
        admin=True,
        prefer="return=minimal",
        expected=(204,),
    )


def sign_out(token: str) -> None:
    request_json(
        "/auth/v1/logout?scope=global",
        method="POST",
        token=token,
        expected=(204,),
    )


def remove_stale_test_memberships() -> None:
    """Remove workspace access left by any interrupted earlier P0 run."""
    _, payload = request_json(
        "/auth/v1/admin/users?page=1&per_page=1000",
        admin=True,
    )
    for user in payload.get("users", []):
        if str(user.get("email", "")).startswith("aegis-p0-"):
            remove_membership(str(user["id"]))


def get_one(path: str, token: str | None = None, *, admin: bool = False) -> dict[str, Any]:
    _, payload = request_json(path, token=token, admin=admin)
    if not isinstance(payload, list) or len(payload) != 1:
        raise RuntimeError(f"Expected exactly one row for {path}")
    return payload[0]


def poll_request(request_id: str, token: str, states: set[str], timeout: int) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    encoded = urllib.parse.quote(request_id, safe="")
    while time.monotonic() < deadline:
        row = get_one(
            f"/rest/v1/action_requests?select=id,status,risk_level,policy_decision&id=eq.{encoded}",
            token,
        )
        if row["status"] in states:
            return row
        if row["status"] in {"failed", "rejected", "cancelled", "expired"}:
            raise RuntimeError(f"Workflow reached terminal state {row['status']}: {row.get('policy_decision')}")
        time.sleep(2)
    raise TimeoutError(f"Request did not reach {sorted(states)} within {timeout}s")


def main() -> None:
    remove_stale_test_memberships()
    suffix = uuid.uuid4().hex[:12]
    requester_email = f"aegis-p0-requester-{suffix}@example.com"
    approver_email = f"aegis-p0-approver-{suffix}@example.com"
    requester_password = secrets.token_urlsafe(32)
    approver_password = secrets.token_urlsafe(32)
    requester_id = ""
    approver_id = ""
    requester_token = ""
    approver_token = ""

    service = get_one(
        "/rest/v1/services?select=id,tenant_id,slug&slug=eq.aegis-control-plane&limit=1",
        admin=True,
    )
    tenant_id = str(service["tenant_id"])
    service_id = str(service["id"])

    try:
        requester_id = create_user(requester_email, requester_password)
        approver_id = create_user(approver_email, approver_password)
        # The deployed tenant role constraint currently permits the platform
        # roles used by the portal, rather than a separate "requester" role.
        # Request authority still comes from the authenticated user ID and RLS;
        # approval authority is independently checked and SoD is enforced by ID.
        insert_membership(tenant_id, requester_id, "platform_admin")
        insert_membership(tenant_id, approver_id, "platform_admin")
        requester_token = sign_in(requester_email, requester_password)
        approver_token = sign_in(approver_email, approver_password)

        request_body = {
            "tenant_id": tenant_id,
            "requester_id": requester_id,
            "beneficiary_id": requester_id,
            "service_id": service_id,
            "capability": "access_grant",
            "justification": "P0 independent approval and governed delivery verification.",
            "status": "awaiting_policy",
            "risk_level": "unknown",
            "resource": {
                "cloud": "gcp",
                "environment": "nonprod",
                "tier": "tier1",
                "ttl_hours": 1,
                "service_slug": "aegis-control-plane",
            },
            "idempotency_key": str(uuid.uuid4()),
            "submitted_at": datetime.now(UTC).isoformat(),
        }
        _, created = request_json(
            "/rest/v1/action_requests?select=id,status",
            method="POST",
            token=requester_token,
            body=request_body,
            prefer="return=representation",
            expected=(201,),
        )
        request_id = str(created[0]["id"])
        policy_result = poll_request(request_id, requester_token, {"awaiting_approval"}, 60)

        self_status, _ = request_json(
            "/rest/v1/approvals",
            method="POST",
            token=requester_token,
            body={
                "tenant_id": tenant_id,
                "action_request_id": request_id,
                "approver_id": requester_id,
                "decision": "approved",
                "reason": "This must be rejected by separation of duties.",
            },
            prefer="return=minimal",
            expected=(400, 401, 403),
        )

        request_json(
            "/rest/v1/approvals",
            method="POST",
            token=approver_token,
            body={
                "tenant_id": tenant_id,
                "action_request_id": request_id,
                "approver_id": approver_id,
                "decision": "approved",
                "reason": "Independent P0 verification approval.",
            },
            prefer="return=minimal",
            expected=(201,),
        )
        final_request = poll_request(request_id, requester_token, {"pr_open"}, 120)
        run = get_one(
            "/rest/v1/workflow_runs?select=status,external_references&action_request_id=eq."
            + urllib.parse.quote(request_id, safe=""),
            admin=True,
        )
        github = (run.get("external_references") or {}).get("github") or {}
        pr_url = str(github.get("pull_request_url") or "")
        if not pr_url.startswith("https://github.com/"):
            raise RuntimeError("Workflow reached pr_open without a valid GitHub pull request URL")

        _, audit_rows = request_json(
            "/rest/v1/audit_events?select=action&object_id=eq."
            + urllib.parse.quote(request_id, safe="")
            + "&order=occurred_at.asc",
            admin=True,
        )
        actions = [row["action"] for row in audit_rows]
        required_actions = {
            "action_request.created",
            "policy.allowed",
            "action_request.approved",
            "github.proposal_created",
        }
        if not required_actions.issubset(set(actions)):
            raise RuntimeError(f"Missing audit evidence: {sorted(required_actions - set(actions))}")

        print(
            json.dumps(
                {
                    "result": "passed",
                    "request_id": request_id,
                    "policy_status": policy_result["status"],
                    "final_status": final_request["status"],
                    "self_approval_http": self_status,
                    "independent_approval": "accepted",
                    "audit_actions": actions,
                    "pull_request_url": pr_url,
                },
                sort_keys=True,
            )
        )
    finally:
        for user_id in (requester_id, approver_id):
            if user_id:
                try:
                    remove_membership(user_id)
                except Exception as exc:  # cleanup must not hide the primary result
                    print(f"cleanup_warning=membership:{user_id[-8:]}:{type(exc).__name__}")
        for token in (requester_token, approver_token):
            if token:
                try:
                    sign_out(token)
                except Exception as exc:
                    print(f"cleanup_warning=session:{type(exc).__name__}")


if __name__ == "__main__":
    main()
