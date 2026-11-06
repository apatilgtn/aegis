"""Exact-plan human approval gate. Only the configured pipeline may call it."""
from __future__ import annotations
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any

_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_SHA = re.compile(r"[0-9a-f]{64}")


def authorize(payload: dict[str, Any], worker: Any) -> tuple[int, dict]:
    now = datetime.now(timezone.utc)
    ids = payload.get("request_ids")
    if not isinstance(ids, list) or not 1 <= len(ids) <= 100 or any(not isinstance(i, str) or not _UUID.fullmatch(i) for i in ids) or len(set(ids)) != len(ids):
        return 400, {"error": "A plan must identify 1–100 distinct governed requests"}
    sha, commit = payload.get("plan_sha256", ""), payload.get("commit_sha", "")
    if not isinstance(sha, str) or not _SHA.fullmatch(sha) or not isinstance(commit, str) or not re.fullmatch(r"[a-f0-9]{40}", commit):
        return 400, {"error": "Plan and commit hashes are required"}
    artifacts = payload.get("artifacts")
    if not isinstance(artifacts, dict) or set(artifacts) != set(ids):
        return 400, {"error": "Every request must have an artifact hash"}
    requests = []
    tenant = None
    settings = None
    for request_id in sorted(ids):
        rows = worker.select("action_requests", f"id=eq.{request_id}&limit=1")
        if len(rows) != 1:
            return 403, {"error": "Request not found"}
        request = rows[0]
        if request["capability"] not in ("access_grant", "label_fix") or request["status"] not in ("pr_open", "merged", "applying"):
            return 403, {"error": "Every changed resource needs an active governed proposal"}
        if tenant and tenant != request["tenant_id"]:
            return 403, {"error": "A plan cannot cross workspace boundaries"}
        tenant = request["tenant_id"]
        account, settings, policy, route = worker.governance_context(request)
        if account["project_id"] != payload.get("project_id"):
            return 403, {"error": "Project mismatch"}
        denials = worker.governance.control_denials(request, account, settings, policy, route)
        if denials or settings["executions_paused"]:
            return 403, {"error": "; ".join(denials or ["Executions are paused"])}
        receipts = worker.select("proposal_receipts", f"action_request_id=eq.{request_id}&limit=1")
        if len(receipts) != 1:
            return 403, {"error": "Missing immutable proposal receipt; submit a fresh request"}
        receipt = receipts[0]
        if receipt["project_id"] != account["project_id"] or receipt["artifact_sha256"] != artifacts[request_id] or receipt["policy_sha256"] != worker.governance.digest(request.get("policy_decision") or {}):
            return 403, {"error": "The source proposal differs from the approved receipt"}
        if (request.get("policy_decision") or {}).get("policy_bundle_sha256") != worker.policy_bundle_digest():
            return 403, {"error": "Deployed policy bundle changed; submit a fresh request"}
        if (request.get("policy_decision") or {}).get("governance_revision") != settings["revision"]:
            return 403, {"error": "Governance policy changed; submit a fresh request for reassessment"}
        if datetime.fromisoformat(receipt["expires_at"].replace("Z", "+00:00")) <= now:
            return 403, {"error": "The proposal approval window expired"}
        runs = worker.select("workflow_runs", f"action_request_id=eq.{request_id}&limit=1")
        sn = ((runs[0].get("external_references") or {}).get("servicenow") or {}) if runs else {}
        if not sn.get("change_sys_id"):
            return 403, {"error": "Missing initial ServiceNow approval"}
        decision = worker.servicenow.approval_decision(worker.servicenow.ServiceNowConfig.from_env(), sn["change_sys_id"], worker.user_email(request["requester_id"]))
        if decision["state"] != "approved":
            return 403, {"error": "The original ServiceNow approval is no longer valid"}
        requests.append(request)
    query = f"tenant_id=eq.{tenant}&plan_sha256=eq.{sha}&commit_sha=eq.{commit}&limit=1"
    records = worker.select("plan_approvals", query)
    if not records:
        # Exclude every requester from the exact-plan approval group. Use the
        # first request for the idempotency key and the full plan hash for revision.
        excluded = [worker.user_email(r["requester_id"]).lower() for r in requests]
        summary = str(payload.get("summary", ""))[:1600]
        change = worker.servicenow.open_change_for_approval(
            worker.servicenow.ServiceNowConfig.from_env(), request_id=ids[0], requester_email=excluded[0],
            short_description=f"Aegis exact-plan approval: {sha[:12]}",
            description=f"Approve this exact saved execution plan, after human merge.\nProject: {payload['project_id']}\nPlan SHA-256: {sha}\nCommit: {commit}\nRequests: {', '.join(ids)}\nSummary: {summary}\nNo other plan hash is authorized by this approval.",
            revision=sha, excluded_emails=excluded,
        )
        expiry = min(now + timedelta(minutes=settings["approval_ttl_minutes"]), *(datetime.fromisoformat(worker.select("proposal_receipts", f"action_request_id=eq.{i}&limit=1")[0]["expires_at"].replace("Z", "+00:00")) for i in ids))
        worker.request_json(f"{worker.SUPABASE_URL}/rest/v1/plan_approvals?on_conflict=tenant_id,plan_sha256,commit_sha", method="POST", supabase=True, extra_headers={"Prefer": "resolution=ignore-duplicates"}, body={"tenant_id": tenant, "plan_sha256": sha, "commit_sha": commit, "request_ids": sorted(ids), "change_sys_id": change["change_sys_id"], "change_number": change["change_number"], "expires_at": expiry.isoformat()})
        records = worker.select("plan_approvals", query)
    record = records[0]
    if sorted(record["request_ids"]) != sorted(ids) or datetime.fromisoformat(record["expires_at"].replace("Z", "+00:00")) <= now:
        return 403, {"error": "Plan approval scope changed or expired"}
    config = worker.servicenow.ServiceNowConfig.from_env()
    # Reject approvals by any requester, not only the requester of the first file.
    decision = worker.servicenow.approval_decision(config, record["change_sys_id"], worker.user_email(requests[0]["requester_id"]))
    if str(decision.get("approver_email") or "").lower() in {worker.user_email(r["requester_id"]).lower() for r in requests}:
        return 403, {"error": "A requester cannot approve the execution plan"}
    worker.request_json(f"{worker.SUPABASE_URL}/rest/v1/plan_approvals?id=eq.{record['id']}", method="PATCH", supabase=True, body={"state": decision["state"], "approver_email": decision.get("approver_email")})
    if decision["state"] == "rejected":
        return 403, {"error": "The exact execution plan was rejected"}
    if decision["state"] != "approved":
        return 202, {"state": "requested", "change_number": record["change_number"], "plan_sha256": sha}
    return 200, {"state": "approved", "change_number": record["change_number"], "plan_sha256": sha, "expires_at": record["expires_at"]}
