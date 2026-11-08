"""Exact-plan human approval gate. Only the configured pipeline may call it."""
from __future__ import annotations
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any

_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_SHA = re.compile(r"[0-9a-f]{64}")


def authorize(payload: dict[str, Any], worker: Any) -> tuple[int, dict]:
    observations = []
    try:
        status, result = _authorize(payload, worker, observations)
    except Exception as exc:
        status, result = 403, {"error": f"Checkpoint could not complete ({type(exc).__name__}); no authorization issued"}
    # Recording failure raises: authorization must never succeed without evidence.
    for observation in observations:
        worker.insert("execution_check_events", {**observation, "plan_sha256": payload["plan_sha256"],
                      "commit_sha": payload["commit_sha"], "checkpoint": payload.get("checkpoint", "approval"),
                      "verdict": "authorized" if status == 200 else "pending" if status == 202 else "denied",
                      "reason": result.get("error")})
    return status, result


def _authorize(payload: dict[str, Any], worker: Any, observations: list) -> tuple[int, dict]:
    now = datetime.now(timezone.utc)
    if not isinstance(payload, dict):
        return 400, {"error": "Expected an execution plan object"}
    if payload.get("checkpoint", "approval") not in ("approval", "pre_apply"):
        return 400, {"error": "Invalid execution checkpoint"}
    if payload.get("smoke") is True:
        project = payload.get("project_id", "")
        if isinstance(project, str) and re.fullmatch(r"[a-z][a-z0-9-]{4,28}[a-z0-9]", project):
            accounts = worker.select("tenant_cloud_accounts", f"project_id=eq.{project}&cloud=eq.gcp&enabled=eq.true&limit=2")
            if len(accounts) == 1:
                worker.source_health(accounts[0]["tenant_id"], project, "execution_gate", "healthy", "Authenticated pipeline reached the gate; no approval or mutation requested")
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
    manifests = {}
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
        observation = {"tenant_id": request["tenant_id"], "action_request_id": request_id, "checks": []}
        observations.append(observation)
        for identity in {request["requester_id"], request.get("beneficiary_id") or request["requester_id"]}:
            membership = worker.select("tenant_memberships", f"tenant_id=eq.{request['tenant_id']}&user_id=eq.{identity}&status=eq.active&limit=1")
            if not membership:
                observation["checks"].append({"key": "active_membership", "label": "Active workspace identity", "state": "changed", "approved": identity, "current": "No active workspace membership"})
                return 403, {"error": "Requester or beneficiary is no longer an active workspace member"}
            observation["checks"].append({"key": "membership_" + identity, "label": "Active workspace identity", "state": "matched", "approved": identity, "current": "Active"})
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
        approved = receipt.get("manifest")
        if not isinstance(approved, dict) or type(approved.get("version")) is not int or approved.get("version") != 1 or receipt.get("manifest_sha256") != worker.governance.digest(approved):
            return 403, {"error": "Missing or invalid approval manifest; submit a fresh request"}
        current = worker.governance.approval_manifest(request, account, route, worker.user_email(request["requester_id"]), worker.user_email(request.get("beneficiary_id") or request["requester_id"]))
        current["policy_bundle_sha256"] = worker.policy_bundle_digest()
        current["governance_revision"] = settings["revision"]
        checks = worker.governance.compare_manifest(approved, current)
        checks.append({"key": "artifact", "label": "Source artifact", "state": "matched" if receipt["artifact_sha256"] == artifacts[request_id] else "changed", "approved": receipt["artifact_sha256"], "current": artifacts[request_id]})
        checks.append({"key": "approval_expiry", "label": "Approval window", "state": "expired" if datetime.fromisoformat(receipt["expires_at"].replace("Z", "+00:00")) <= now else "matched", "approved": receipt["expires_at"], "current": now.isoformat()})
        observation["checks"].extend(checks)
        manifests[request_id] = receipt["manifest_sha256"]
        if any(c["state"] != "matched" for c in checks):
            changed = ", ".join(c["label"] for c in checks if c["state"] != "matched")
            return 403, {"error": f"Approved facts no longer match: {changed}. Submit a fresh request."}
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
        observation["checks"].append({"key": "initial_approval", "label": "Original ServiceNow approval", "state": "matched" if decision["state"] == "approved" else "changed", "approved": "Independent active approver required", "current": {"state": decision["state"], "approver": decision.get("approver_email")}})
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
            description=f"Approve this exact saved execution plan, after human merge.\nProject: {payload['project_id']}\nPlan SHA-256: {sha}\nCommit: {commit}\nRequests: {', '.join(ids)}\nApproval manifests: {json.dumps(manifests, sort_keys=True)}\nSummary: {summary}\nNo other plan hash is authorized by this approval.",
            revision=sha, excluded_emails=excluded,
        )
        expiry = min(now + timedelta(minutes=settings["approval_ttl_minutes"]), *(datetime.fromisoformat(worker.select("proposal_receipts", f"action_request_id=eq.{i}&limit=1")[0]["expires_at"].replace("Z", "+00:00")) for i in ids))
        worker.request_json(f"{worker.SUPABASE_URL}/rest/v1/plan_approvals?on_conflict=tenant_id,plan_sha256,commit_sha", method="POST", supabase=True, extra_headers={"Prefer": "resolution=ignore-duplicates"}, body={"tenant_id": tenant, "plan_sha256": sha, "commit_sha": commit, "request_ids": sorted(ids), "manifest_sha256s": manifests, "change_sys_id": change["change_sys_id"], "change_number": change["change_number"], "expires_at": expiry.isoformat()})
        records = worker.select("plan_approvals", query)
    record = records[0]
    if record.get("manifest_sha256s") != manifests:
        return 403, {"error": "The execution-plan approval does not cover these approval manifests"}
    if sorted(record["request_ids"]) != sorted(ids) or datetime.fromisoformat(record["expires_at"].replace("Z", "+00:00")) <= now:
        return 403, {"error": "Plan approval scope changed or expired"}
    config = worker.servicenow.ServiceNowConfig.from_env()
    # Reject approvals by any requester, not only the requester of the first file.
    decision = worker.servicenow.approval_decision(config, record["change_sys_id"], worker.user_email(requests[0]["requester_id"]))
    for observation in observations:
        observation["checks"].append({"key": "plan_approval", "label": "Exact-plan ServiceNow approval", "state": "matched" if decision["state"] == "approved" else "pending" if decision["state"] == "requested" else "changed", "approved": sha, "current": {"state": decision["state"], "approver": decision.get("approver_email"), "change_number": record["change_number"]}})
    if str(decision.get("approver_email") or "").lower() in {worker.user_email(r["requester_id"]).lower() for r in requests}:
        return 403, {"error": "A requester cannot approve the execution plan"}
    worker.request_json(f"{worker.SUPABASE_URL}/rest/v1/plan_approvals?id=eq.{record['id']}", method="PATCH", supabase=True, body={"state": decision["state"], "approver_email": decision.get("approver_email")})
    if decision["state"] == "rejected":
        return 403, {"error": "The exact execution plan was rejected"}
    if decision["state"] != "approved":
        return 202, {"state": "requested", "change_number": record["change_number"], "plan_sha256": sha}
    return 200, {"state": "approved", "change_number": record["change_number"], "plan_sha256": sha, "expires_at": record["expires_at"]}
