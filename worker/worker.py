"""Durable local Aegis workflow worker.

Secrets are accepted only through the process environment so Infisical can
inject them at runtime. Jobs are dispatched through an explicit handler
registry, leased atomically, heartbeated while running, and completed through
job-specific restricted database RPCs.
"""

from __future__ import annotations

import json
import logging
import os
import re
import socket
from pathlib import Path
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from base64 import b64decode, b64encode
from contextlib import AbstractContextManager
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt

try:  # imported as a package (tests)
    from . import gcp, servicenow, governance, plan_gate, native_analysis
except ImportError:  # run as /app/worker.py
    import gcp  # type: ignore[no-redef]
    import servicenow  # type: ignore[no-redef]
    import governance  # type: ignore[no-redef]
    import plan_gate  # type: ignore[no-redef]
    import native_analysis  # type: ignore[no-redef]


logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
LOGGER = logging.getLogger("aegis.worker")


class ConfigurationError(RuntimeError):
    pass


def required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ConfigurationError(f"{name} is required")
    return value


SUPABASE_URL = required_env("SUPABASE_URL").rstrip("/")
SUPABASE_SECRET_KEY = required_env("SUPABASE_SECRET_KEY")
OPA_URL = os.getenv("OPA_URL", "http://opa:8181").rstrip("/")
WORKER_ID = os.getenv("WORKER_ID", f"{socket.gethostname()}-{os.getpid()}")
POLL_SECONDS = max(1.0, float(os.getenv("POLL_SECONDS", "2")))
HEARTBEAT_SECONDS = max(10.0, min(float(os.getenv("HEARTBEAT_SECONDS", "30")), 90.0))
LEASE_EXTENSION_SECONDS = max(60, min(int(os.getenv("LEASE_EXTENSION_SECONDS", "120")), 600))
SNAPSHOT_SECONDS = max(300, int(os.getenv("SNAPSHOT_SECONDS", "900")))
AUDIT_EXPORT_SECONDS = max(30, int(os.getenv("AUDIT_EXPORT_SECONDS", "60")))


class NonRetryableJobError(RuntimeError):
    """A malformed or unsupported job that will not succeed after retrying."""


JobHandler = Any
JOB_HANDLERS: dict[str, JobHandler] = {}
_GITHUB_TOKEN: tuple[str, datetime] | None = None


def register_handler(job_type: str):
    if not re.fullmatch(r"[a-z][a-z0-9_]{2,63}", job_type):
        raise ValueError(f"Invalid job type: {job_type}")

    def decorator(handler: JobHandler) -> JobHandler:
        if job_type in JOB_HANDLERS:
            raise ValueError(f"Duplicate handler for job type: {job_type}")
        JOB_HANDLERS[job_type] = handler
        return handler

    return decorator


def registered_job_types() -> list[str]:
    return sorted(JOB_HANDLERS)


def request_json(url: str, *, method: str = "GET", body: dict[str, Any] | None = None, supabase: bool = False,
                 extra_headers: dict[str, str] | None = None) -> Any:
    headers = {"Accept": "application/json", "Content-Type": "application/json", **(extra_headers or {})}
    if supabase:
        headers.update({"apikey": SUPABASE_SECRET_KEY, "Authorization": f"Bearer {SUPABASE_SECRET_KEY}"})
    payload = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(url, data=payload, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            content = response.read()
            return json.loads(content) if content else None
    except urllib.error.HTTPError as exc:
        safe_body = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"HTTP {exc.code} from {url}: {safe_body}") from exc


def rpc(name: str, body: dict[str, Any]) -> Any:
    return request_json(f"{SUPABASE_URL}/rest/v1/rpc/{name}", method="POST", body=body, supabase=True)


def select(table: str, query: str) -> list[dict[str, Any]]:
    result = request_json(f"{SUPABASE_URL}/rest/v1/{table}?{query}", supabase=True)
    if not isinstance(result, list):
        raise RuntimeError(f"Unexpected response while reading {table}")
    return result


def load_request(job: dict[str, Any]) -> dict[str, Any]:
    runs = select("workflow_runs", f"select=action_request_id&id=eq.{job['workflow_run_id']}&limit=1")
    if not runs:
        raise RuntimeError("Workflow run does not exist")
    requests = select(
        "action_requests",
        "select=id,tenant_id,requester_id,beneficiary_id,service_id,capability,justification,status,resource,policy_decision,decided_at"
        f"&id=eq.{runs[0]['action_request_id']}&limit=1",
    )
    if not requests:
        raise RuntimeError("Action request does not exist")
    return requests[0]


def user_email(user_id: str | None) -> str:
    if not user_id or not re.fullmatch(r"[0-9a-f-]{36}", str(user_id)):
        raise NonRetryableJobError("Request has no valid user")
    user = request_json(f"{SUPABASE_URL}/auth/v1/admin/users/{user_id}", supabase=True)
    email = str(user.get("email") or "").strip().lower() if isinstance(user, dict) else ""
    if not re.fullmatch(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,63}", email):
        raise NonRetryableJobError("User has no valid authenticated email address")
    return email


def cloud_account(tenant_id: str) -> dict[str, Any]:
    rows = select("tenant_cloud_accounts",
                  f"select=project_id,environment,billing_table&tenant_id=eq.{tenant_id}&cloud=eq.gcp&enabled=eq.true&limit=2")
    if len(rows) != 1:
        raise NonRetryableJobError("Workspace has no single governed GCP project")
    return rows[0]


POLICY_PATHS = {
    "access_grant": "aegis/access_request",
    "incident_triage": "aegis/incident_triage",
    "label_fix": "aegis/label_fix",
}


def governance_context(action_request: dict[str, Any]) -> tuple[dict, dict, dict, dict | None]:
    tenant = str(action_request["tenant_id"])
    account = cloud_account(tenant)
    settings = select("governance_settings", f"tenant_id=eq.{tenant}&limit=1")
    policies = select("capability_policies", f"tenant_id=eq.{tenant}&capability=eq.{urllib.parse.quote(str(action_request['capability']), safe='')}&limit=1")
    if len(settings) != 1 or len(policies) != 1:
        raise NonRetryableJobError("Governance controls have not been configured")
    route = None
    if action_request["capability"] == "access_grant":
        resource = action_request.get("resource") or {}
        routes = select("service_entitlements", f"tenant_id=eq.{tenant}&service_id=eq.{action_request['service_id']}&cloud=eq.gcp&environment=eq.{urllib.parse.quote(str(resource.get('environment')), safe='')}&tier=eq.{urllib.parse.quote(str(resource.get('tier')), safe='')}&enabled=eq.true&limit=2")
        if len(routes) == 1:
            route = routes[0]
    if route and route.get("scope_type") == "bucket":
        if not any(item["name"] == route.get("scope_identifier") for item in gcp.list_buckets(account["project_id"])):
            raise NonRetryableJobError("Reviewed bucket scope is outside the governed project")
    return account, settings[0], policies[0], route


def policy_input(action_request: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return (OPA input, extra facts to keep with the decision)."""
    capability = action_request.get("capability")
    resource = action_request.get("resource") or {}
    justification = action_request.get("justification", "")
    if capability == "access_grant":
        return {"cloud": resource.get("cloud"), "environment": resource.get("environment"), "tier": resource.get("tier"),
                "ttl_hours": resource.get("ttl_hours"), "justification": justification}, {}
    if capability == "incident_triage":
        return {"cloud": resource.get("cloud"), "resource_hint": resource.get("resource_hint", ""),
                "time_window_hours": resource.get("time_window_hours"), "justification": justification}, {}
    if capability == "label_fix":
        # The live labels decide what gets proposed; the portal's view may be stale.
        bucket = str(resource.get("bucket") or "")
        project = cloud_account(str(action_request["tenant_id"]))["project_id"]
        owned = next((item for item in gcp.list_buckets(project) if item["name"] == bucket), None)
        if owned is None:
            raise NonRetryableJobError("The bucket does not belong to the governed project")
        current = gcp.bucket_labels(bucket)
        missing = gcp.missing_labels(current)
        return {"cloud": resource.get("cloud"), "environment": resource.get("environment"), "bucket": bucket,
                "cost_center": resource.get("cost_center", ""), "labels_to_add": missing,
                "justification": justification}, {"labels_to_add": missing, "labels_before": current}
    raise NonRetryableJobError(f"Unsupported capability: {capability}")


def policy_bundle_digest() -> str:
    directory = Path("/policies") if Path("/policies").is_dir() else Path(__file__).resolve().parents[1] / "policy"
    policies = {p.name: p.read_text() for p in sorted(directory.glob("*.rego")) if not p.name.endswith("_test.rego")}
    if not policies:
        raise ConfigurationError("No deployed policy bundle is available")
    return governance.digest(policies)


def evaluate_policy(action_request: dict[str, Any]) -> dict[str, Any]:
    capability = str(action_request.get("capability"))
    path = POLICY_PATHS.get(capability)
    if path is None:
        raise NonRetryableJobError(f"Unsupported capability: {capability}")
    account, settings, policy, route = governance_context(action_request)
    denials = governance.control_denials(action_request, account, settings, policy, route)
    if denials:
        return {"allowed": False, "reasons": denials, "risk_level": "critical" if account["environment"] == "prod" else "unknown", "policy_ids": ["AEGIS-CAPABILITY-001"]}
    request_input, facts = policy_input(action_request)
    request_input.update({"production_authorized": policy["production_allowed"] and settings["writes_enabled"], "production_max_ttl": policy["max_ttl_hours"]})
    facts["governance_revision"] = settings["revision"]
    facts["policy_bundle_sha256"] = policy_bundle_digest()
    if route:
        facts["reviewed_scope"] = {"project": account["project_id"], "environment": account["environment"], "role": route["role_identifier"], "type": route.get("scope_type", "project"), "resource": route.get("scope_identifier") or account["project_id"]}
    response = request_json(f"{OPA_URL}/v1/data/{path}/decision", method="POST", body={"input": {"request": request_input}})
    decision = response.get("result") if isinstance(response, dict) else None
    if not isinstance(decision, dict) or not isinstance(decision.get("allowed"), bool):
        raise RuntimeError("OPA returned no valid decision")
    return {**decision, **facts}


@register_handler("evaluate_policy")
def handle_policy_evaluation(job: dict[str, Any]) -> None:
    action_request = load_request(job)
    decision = evaluate_policy(action_request)
    if decision["allowed"] and action_request.get("capability") == "access_grant":
        hours = action_request["resource"]["ttl_hours"]
        decision["proposed_expires_at"] = (datetime.now(timezone.utc) + timedelta(hours=hours)).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")
    rpc("complete_policy_evaluation", {
        "p_job_id": job["id"],
        "p_worker_id": WORKER_ID,
        "p_allowed": decision["allowed"],
        "p_decision": decision,
    })
    LOGGER.info("policy_complete job=%s request=%s allowed=%s", job["id"], action_request["id"], decision["allowed"])


def github_config() -> tuple[str, str, str, str]:
    repo = required_env("GITHUB_REPO")
    app_id = required_env("GITHUB_APP_ID")
    installation_id = required_env("GITHUB_APP_INSTALLATION_ID")
    private_key = required_env("GITHUB_APP_PRIVATE_KEY").replace("\\n", "\n")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise NonRetryableJobError("GITHUB_REPO must be in owner/repository form")
    if not app_id.isdigit() or not installation_id.isdigit():
        raise NonRetryableJobError("GitHub App and installation IDs must be numeric")
    if "PRIVATE KEY-----" not in private_key:
        raise NonRetryableJobError("GITHUB_APP_PRIVATE_KEY is not a PEM private key")
    return repo, app_id, installation_id, private_key


def github_request(
    method: str,
    path: str,
    token: str,
    body: dict[str, Any] | None = None,
    *,
    allow_not_found: bool = False,
) -> Any:
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "User-Agent": "aegis-governed-delivery",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    payload = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(f"https://api.github.com{path}", data=payload, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            content = response.read()
            return json.loads(content) if content else None
    except urllib.error.HTTPError as exc:
        if allow_not_found and exc.code == 404:
            return None
        safe_body = exc.read().decode("utf-8", errors="replace")[:500]
        message = f"GitHub API returned {exc.code} for {method} {path}: {safe_body}"
        if 400 <= exc.code < 500 and exc.code not in (408, 409, 429):
            raise NonRetryableJobError(message) from exc
        raise RuntimeError(message) from exc


def github_installation_token() -> str:
    global _GITHUB_TOKEN
    now = datetime.now(timezone.utc)
    if _GITHUB_TOKEN and _GITHUB_TOKEN[1] > now + timedelta(minutes=2):
        return _GITHUB_TOKEN[0]
    _, app_id, installation_id, private_key = github_config()
    app_token = jwt.encode(
        {
            "iat": int((now - timedelta(seconds=60)).timestamp()),
            "exp": int((now + timedelta(minutes=9)).timestamp()),
            "iss": app_id,
        },
        private_key,
        algorithm="RS256",
    )
    response = github_request("POST", f"/app/installations/{installation_id}/access_tokens", app_token)
    expires_at = datetime.fromisoformat(response["expires_at"].replace("Z", "+00:00"))
    _GITHUB_TOKEN = (response["token"], expires_at)
    return response["token"]


def clean_pr_text(value: str, limit: int) -> str:
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", value).strip()[:limit]


def load_proposal_context(job: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], str]:
    return load_proposal_context_for(load_request(job))


def load_proposal_context_for(action_request: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], str]:
    if action_request.get("status") != "proposal_pending":
        raise NonRetryableJobError("Action request is not approved and proposal_pending")
    resource = action_request.get("resource") or {}
    for field in ("cloud", "environment", "tier", "ttl_hours"):
        if field not in resource:
            raise NonRetryableJobError(f"Request resource is missing {field}")
    routes = select(
        "service_entitlements",
        "select=id,cloud,environment,tier,entitlement_type,target_identifier,role_identifier,"
        "terraform_module_source,max_ttl_hours,scope_type,scope_identifier"
        f"&service_id=eq.{action_request['service_id']}&tenant_id=eq.{action_request['tenant_id']}"
        f"&cloud=eq.{urllib.parse.quote(str(resource['cloud']), safe='')}"
        f"&environment=eq.{urllib.parse.quote(str(resource['environment']), safe='')}"
        f"&tier=eq.{urllib.parse.quote(str(resource['tier']), safe='')}&enabled=eq.true&limit=2",
    )
    if len(routes) != 1:
        raise NonRetryableJobError("No unique reviewed IaC route exists for this request")
    ttl_hours = resource.get("ttl_hours")
    if not isinstance(ttl_hours, int) or ttl_hours < 1 or ttl_hours > int(routes[0]["max_ttl_hours"]):
        raise NonRetryableJobError("Requested duration exceeds the reviewed entitlement limit")
    email = user_email(action_request.get("beneficiary_id") or action_request.get("requester_id"))
    return action_request, routes[0], email


BASIC_GCP_ROLES = {"roles/owner", "roles/editor", "roles/viewer"}


def access_expires_at(action_request: dict[str, Any]) -> str:
    """Expiry = approval time + requested duration, so re-rendering is deterministic."""
    fixed = (action_request.get("policy_decision") or {}).get("proposed_expires_at")
    if fixed:
        value = datetime.fromisoformat(str(fixed).replace("Z", "+00:00"))
        if value.tzinfo is None:
            raise NonRetryableJobError("Proposed expiry has no timezone")
        return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    decided_at = str(action_request.get("decided_at") or "")
    try:
        approved = datetime.fromisoformat(decided_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise NonRetryableJobError("Approved request has no valid approval time") from exc
    if approved.tzinfo is None:
        raise NonRetryableJobError("Approval time has no timezone")
    ttl_hours = (action_request.get("resource") or {}).get("ttl_hours")
    if not isinstance(ttl_hours, int) or ttl_hours < 1:
        raise NonRetryableJobError("Request has no valid duration")
    expires = approved.astimezone(timezone.utc).replace(microsecond=0) + timedelta(hours=ttl_hours)
    return expires.strftime("%Y-%m-%dT%H:%M:%SZ")


def render_gcp_access_hcl(action_request: dict[str, Any], route: dict[str, Any], email: str) -> str:
    if route.get("cloud") != "gcp" or route.get("entitlement_type") != "gcp_iam_binding":
        raise NonRetryableJobError("Only reviewed GCP IAM binding routes are currently supported")
    project_id = str(route.get("target_identifier") or "")
    role = str(route.get("role_identifier") or "")
    module_source = str(route.get("terraform_module_source") or "")
    if not re.fullmatch(r"[a-z][a-z0-9-]{4,28}[a-z0-9]", project_id):
        raise NonRetryableJobError("Reviewed route has an invalid GCP project ID")
    if not re.fullmatch(r"roles/[A-Za-z0-9_.]+", role):
        raise NonRetryableJobError("Reviewed route has an invalid GCP IAM role")
    if role in BASIC_GCP_ROLES:
        raise NonRetryableJobError("Basic GCP roles cannot expire; the access level must use a predefined role")
    bucket = str(route.get("scope_identifier") or "") if route.get("scope_type") == "bucket" else None
    expected_module = "modules/gcp/bucket_iam_binding" if bucket else "modules/gcp/iam_binding"
    if bucket and not gcp._BUCKET.fullmatch(bucket):
        raise NonRetryableJobError("Invalid reviewed bucket scope")
    if module_source != expected_module:
        raise NonRetryableJobError("Reviewed route uses an unsupported Terraform module")
    request_id = str(action_request["id"])
    if not re.fullmatch(r"[0-9a-f-]{36}", request_id):
        raise NonRetryableJobError("Action request ID is not a UUID")
    module_name = f"aegis_request_{request_id.replace('-', '_')}"
    expires_at = access_expires_at(action_request)
    return (
        "# Generated by Aegis Governed Delivery. Do not edit manually.\n"
        f"# action_request_id: {request_id}\n"
        f"# GCP stops honouring this grant at {expires_at}.\n\n"
        f"module {json.dumps(module_name)} {{\n"
        f"  source     = {json.dumps('../../' + module_source)}\n"
        f"  project    = {json.dumps(project_id)}\n" +
        (f"  bucket     = {json.dumps(bucket)}\n" if bucket else "") +
        f"  role       = {json.dumps(role)}\n"
        f"  member     = {json.dumps('user:' + email)}\n"
        f"  expires_at = {json.dumps(expires_at)}\n"
        "}\n"
    )


def find_pull_request(repo: str, branch: str, token: str) -> dict[str, Any] | None:
    owner = repo.split("/", 1)[0]
    query = urllib.parse.urlencode({"state": "all", "head": f"{owner}:{branch}", "per_page": 10})
    pulls = github_request("GET", f"/repos/{repo}/pulls?{query}", token)
    return pulls[0] if pulls else None


def render_label_fix(action_request: dict[str, Any], project_id: str, requester: str) -> str:
    """The reviewed file the pipeline applies: only missing required labels."""
    resource = action_request.get("resource") or {}
    decision = action_request.get("policy_decision") or {}
    missing = decision.get("labels_to_add")
    if not isinstance(missing, list) or not missing or not set(missing) <= set(gcp.REQUIRED_LABELS):
        raise NonRetryableJobError("Approved request has no valid list of labels to add")
    values = {
        "owner": gcp.label_value(requester.replace("@", "-at-")),
        "cost-center": gcp.label_value(str(resource.get("cost_center") or "")),
        "environment": gcp.label_value(str(resource.get("environment") or "")),
    }
    bucket = str(resource.get("bucket") or "")
    if not gcp._BUCKET.fullmatch(bucket):
        raise NonRetryableJobError("Approved request has an invalid bucket name")
    document = {
        "action_request_id": str(action_request["id"]),
        "project": project_id,
        "bucket": bucket,
        "add_labels": {key: values[key] for key in sorted(missing)},
        "note": "Generated by Aegis. Only labels that are missing at apply time are set; existing labels are never changed.",
    }
    return json.dumps(document, indent=2, sort_keys=True) + "\n"


def proposal_files(action_request: dict[str, Any]) -> tuple[str, str, str, str]:
    """Return (branch, file_path, content, summary) for an approved request."""
    request_id = str(action_request["id"])
    if not re.fullmatch(r"[0-9a-f-]{36}", request_id):
        raise NonRetryableJobError("Action request ID is not a UUID")
    if action_request.get("status") != "proposal_pending":
        raise NonRetryableJobError("Action request is not approved and proposal_pending")
    if action_request.get("capability") == "access_grant":
        request, route, email = load_proposal_context_for(action_request)
        return (f"aegis/access-request-{request_id}", f"infra/access-grants/{request_id}.tf",
                render_gcp_access_hcl(request, route, email), f"Time-bound GCP access for `{email}`")
    if action_request.get("capability") == "label_fix":
        requester = user_email(action_request.get("requester_id"))
        project = cloud_account(str(action_request["tenant_id"]))["project_id"]
        content = render_label_fix(action_request, project, requester)
        bucket = (action_request.get("resource") or {}).get("bucket")
        return (f"aegis/label-fix-{request_id}", f"infra/label-fixes/{request_id}.json", content,
                f"Add missing required labels to bucket `{bucket}`")
    raise NonRetryableJobError("This capability does not produce a pull request")


def create_github_proposal(action_request: dict[str, Any], branch: str, file_path: str, content: str,
                           summary: str, change: dict[str, Any] | None = None) -> dict[str, Any]:
    account, settings, policy, route = governance_context(action_request)
    denials = governance.control_denials(action_request, account, settings, policy, route)
    receipts = select("proposal_receipts", f"action_request_id=eq.{action_request['id']}&limit=1")
    if denials or len(receipts) != 1:
        raise NonRetryableJobError("Proposal is not authorized: " + "; ".join(denials or ["missing immutable receipt"]))
    receipt = receipts[0]
    if receipt["artifact_path"] != file_path or receipt["artifact_sha256"] != governance.digest(content) or datetime.fromisoformat(receipt["expires_at"].replace("Z", "+00:00")) <= datetime.now(timezone.utc):
        raise NonRetryableJobError("Proposal content changed or its approval window expired")
    repo, _, _, _ = github_config()
    token = github_installation_token()
    request_id = str(action_request["id"])
    existing_pr = find_pull_request(repo, branch, token)
    if existing_pr:
        if existing_pr.get("state") == "closed" and not existing_pr.get("merged_at"):
            raise NonRetryableJobError("The idempotent proposal PR was closed without merge")
        ref = github_request("GET", f"/repos/{repo}/git/ref/heads/{urllib.parse.quote(branch, safe='/')}", token)
        return {
            "number": existing_pr["number"], "url": existing_pr["html_url"], "branch": branch,
            "commit_sha": ref["object"]["sha"], "file_path": file_path,
        }
    base_ref = github_request("GET", f"/repos/{repo}/git/ref/heads/main", token)
    branch_ref = github_request(
        "GET", f"/repos/{repo}/git/ref/heads/{urllib.parse.quote(branch, safe='/')}", token, allow_not_found=True
    )
    if branch_ref is None:
        github_request("POST", f"/repos/{repo}/git/refs", token, {"ref": f"refs/heads/{branch}", "sha": base_ref["object"]["sha"]})
    encoded_path = urllib.parse.quote(file_path, safe="/")
    existing_file = github_request(
        "GET", f"/repos/{repo}/contents/{encoded_path}?ref={urllib.parse.quote(branch, safe='')}", token, allow_not_found=True
    )
    if existing_file:
        existing_content = b64decode(existing_file["content"]).decode("utf-8")
        if existing_content != content:
            raise NonRetryableJobError("Existing idempotent proposal contains different Terraform")
    else:
        github_request(
            "PUT", f"/repos/{repo}/contents/{encoded_path}", token,
            {"message": f"Aegis: approved request {request_id}", "content": b64encode(content.encode()).decode(), "branch": branch},
        )
    pr = find_pull_request(repo, branch, token)
    if pr is None:
        justification = clean_pr_text(str(action_request.get("justification") or ""), 1500)
        approval = ""
        if change:
            approval = (f"\n\nApproved in ServiceNow: [{change.get('change_number')}]({change.get('change_url')})"
                        f" by {change.get('approver_email') or 'an approver'}.")
        pr = github_request(
            "POST", f"/repos/{repo}/pulls", token,
            {
                "title": f"Aegis request {request_id[:8]}: {clean_pr_text(summary, 80).replace('`', '')}",
                "head": branch, "base": "main",
                "body": f"{summary}.{approval}\n\nJustification:\n{justification}\n\nThis PR requires protected-branch review and cannot be merged by Aegis.",
            },
        )
    ref = github_request("GET", f"/repos/{repo}/git/ref/heads/{urllib.parse.quote(branch, safe='/')}", token)
    return {"number": pr["number"], "url": pr["html_url"], "branch": branch, "commit_sha": ref["object"]["sha"], "file_path": file_path}


def servicenow_reference(job: dict[str, Any]) -> dict[str, Any]:
    runs = select("workflow_runs", f"select=external_references&id=eq.{job['workflow_run_id']}&limit=1")
    return ((runs[0].get("external_references") or {}) if runs else {}).get("servicenow") or {}


@register_handler("create_github_proposal")
def handle_github_proposal(job: dict[str, Any]) -> None:
    action_request = load_request(job)
    controls = select("governance_settings", f"tenant_id=eq.{action_request['tenant_id']}&limit=1")
    if controls and controls[0].get("proposals_paused"):
        rpc("defer_governance_job", {"p_job": job["id"], "p_worker": WORKER_ID})
        return
    branch, file_path, content, summary = proposal_files(action_request)
    proposal = create_github_proposal(action_request, branch, file_path, content, summary, servicenow_reference(job))
    rpc("complete_github_proposal", {
        "p_job_id": job["id"], "p_worker_id": WORKER_ID, "p_pr_number": proposal["number"],
        "p_pr_url": proposal["url"], "p_branch": proposal["branch"], "p_commit_sha": proposal["commit_sha"],
        "p_file_path": proposal["file_path"],
    })
    LOGGER.info("github_proposal_complete job=%s request=%s pr=%s", job["id"], action_request["id"], proposal["number"])


APPLY_WORKFLOW_FILE = "terraform-apply.yml"
APPLY_JOB_NAMES = {"access_grant": "gcp-apply-access-grants", "label_fix": "gcp-apply-label-fixes"}
PR_RECHECK_SECONDS = 60
PIPELINE_RECHECK_SECONDS = 30
PIPELINE_START_GRACE = timedelta(minutes=30)


def load_github_reference(job: dict[str, Any]) -> dict[str, Any]:
    runs = select("workflow_runs", f"select=external_references&id=eq.{job['workflow_run_id']}&limit=1")
    github = ((runs[0].get("external_references") or {}) if runs else {}).get("github") or {}
    number = github.get("pull_request_number")
    if not isinstance(number, int) or number < 1:
        raise NonRetryableJobError("Workflow run has no GitHub pull request reference")
    return github


def check_delivery(repo: str, token: str, pr_number: int, now: datetime,
                   apply_job: str = "gcp-apply-access-grants") -> tuple[str, dict[str, Any], int]:
    """Return (state, details, recheck_seconds) for a governed proposal PR.

    States: pr_open, merged, applying (keep watching) or completed, failed,
    cancelled (final). Aegis only reads GitHub here; it never merges.
    """
    pr = github_request("GET", f"/repos/{repo}/pulls/{pr_number}", token)
    details: dict[str, Any] = {"pull_request_number": pr_number, "pull_request_url": pr.get("html_url")}
    if not pr.get("merged_at"):
        if pr.get("state") == "closed":
            return "cancelled", {**details, "reason": "Pull request was closed without merging"}, 0
        return "pr_open", details, PR_RECHECK_SECONDS

    sha = str(pr.get("merge_commit_sha") or "")
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise RuntimeError("Merged pull request has no merge commit yet")
    merged_at = datetime.fromisoformat(str(pr["merged_at"]).replace("Z", "+00:00"))
    details.update({"merged_at": pr["merged_at"], "merge_commit_sha": sha})

    query = urllib.parse.urlencode({"head_sha": sha, "event": "push", "per_page": 5})
    runs = github_request("GET", f"/repos/{repo}/actions/workflows/{APPLY_WORKFLOW_FILE}/runs?{query}", token)
    workflow_runs = (runs or {}).get("workflow_runs") or []
    if not workflow_runs:
        if now - merged_at > PIPELINE_START_GRACE:
            return "failed", {**details, "reason": "The apply pipeline did not start after the merge"}, 0
        return "merged", details, PIPELINE_RECHECK_SECONDS

    run = max(workflow_runs, key=lambda item: item.get("run_attempt") or 0)
    details.update({"workflow_run_id": run.get("id"), "workflow_run_url": run.get("html_url")})
    if run.get("status") != "completed":
        return "applying", details, PIPELINE_RECHECK_SECONDS

    jobs = github_request("GET", f"/repos/{repo}/actions/runs/{run['id']}/jobs?per_page=50", token)
    job_result = next((item for item in (jobs or {}).get("jobs") or [] if item.get("name") == apply_job), None)
    conclusion = (job_result or {}).get("conclusion")
    details["apply_conclusion"] = conclusion
    if conclusion == "success":
        return "completed", details, 0
    reason = (
        "The apply step was skipped; check the pipeline's GCP settings"
        if conclusion == "skipped"
        else f"The apply step finished with: {conclusion or 'no result'}"
    )
    return "failed", {**details, "reason": reason}, 0


@register_handler("track_github_delivery")
def handle_delivery_tracking(job: dict[str, Any]) -> None:
    action_request = load_request(job)
    github = load_github_reference(job)
    repo, _, _, _ = github_config()
    apply_job = APPLY_JOB_NAMES.get(str(action_request.get("capability")))
    if apply_job is None:
        raise NonRetryableJobError("This capability has no apply pipeline to follow")
    state, details, recheck = check_delivery(
        repo, github_installation_token(), int(github["pull_request_number"]), datetime.now(timezone.utc), apply_job
    )
    if state == "completed" and action_request.get("capability") == "access_grant":
        details["expires_at"] = access_expires_at(action_request)
    if state == "completed":
        verification = verify_delivered_change(action_request)
        if verification:
            details["verification_state"] = verification["state"]
    if state in ("completed", "failed", "cancelled"):
        note_servicenow_outcome(job, state, details)
    rpc("record_github_delivery", {
        "p_job_id": job["id"], "p_worker_id": WORKER_ID, "p_state": state,
        "p_details": details, "p_recheck_seconds": recheck or PR_RECHECK_SECONDS,
    })
    LOGGER.info("delivery_checked job=%s request=%s state=%s", job["id"], action_request["id"], state)


def upsert(table: str, row: dict[str, Any], conflict: str) -> None:
    request_json(f"{SUPABASE_URL}/rest/v1/{table}?on_conflict={urllib.parse.quote(conflict, safe=',')}", method="POST", body=row, supabase=True, extra_headers={"Prefer": "resolution=merge-duplicates"})


def verify_delivered_change(action_request: dict[str, Any]) -> dict:
    try:
        account = cloud_account(str(action_request["tenant_id"]))
        receipts = select("proposal_receipts", f"action_request_id=eq.{action_request['id']}&limit=1")
        manifest = receipts[0].get("manifest") if receipts else None
        if manifest is not None and (not isinstance(manifest, dict) or receipts[0].get("manifest_sha256") != governance.digest(manifest)):
            raise NonRetryableJobError("Approved verification manifest is invalid")
        if action_request["capability"] == "label_fix":
            requester = manifest["requester"] if manifest else user_email(action_request["requester_id"])
            expected = json.loads(render_label_fix(action_request, account["project_id"], requester))
            result = governance.verify_labels(expected["add_labels"], gcp.bucket_labels(expected["bucket"]))
        else:
            scope = manifest["scope"] if manifest else (action_request.get("policy_decision") or {}).get("reviewed_scope")
            if scope:
                project, role = scope["project"], scope["role"]
                bucket = scope["resource"] if scope["type"] == "bucket" else None
            else:  # Legacy read-back only; legacy proposals cannot enter the execution gate.
                _, _, _, route = governance_context(action_request)
                if not route:
                    raise NonRetryableJobError("The reviewed entitlement is no longer available")
                project, role = account["project_id"], route["role_identifier"]
                bucket = route.get("scope_identifier") if route.get("scope_type") == "bucket" else None
            observed = gcp.iam_policy(project, bucket)
            beneficiary = manifest["beneficiary"] if manifest else user_email(action_request.get("beneficiary_id") or action_request["requester_id"])
            expiry = manifest["grant_expires_at"] if manifest else access_expires_at(action_request)
            result = governance.verify_iam(observed, role, "user:" + beneficiary, expiry)
    except Exception as exc:
        result = {"state": "unavailable", "details": {"reason": type(exc).__name__, "scope": "Cloud read-back could not finish; CI success is not treated as verification"}}
    upsert("change_verifications", {"action_request_id": action_request["id"], "tenant_id": action_request["tenant_id"], **result, "checked_at": datetime.now(timezone.utc).isoformat()}, "action_request_id")
    return result


@register_handler("verify_change")
def handle_change_verification(job: dict[str, Any]) -> None:
    request = load_request(job)
    if request["status"] != "completed" or request["capability"] not in ("access_grant", "label_fix"):
        raise NonRetryableJobError("Only delivered cloud changes can be verified")
    verify_delivered_change(request)
    rpc("complete_change_verification", {"p_job": job["id"], "p_worker": WORKER_ID})


@register_handler("analyze_access")
def handle_native_access_analysis(job: dict[str, Any]) -> None:
    request = load_request(job)
    if request["capability"] != "access_grant":
        raise NonRetryableJobError("Native access analysis requires an access request")
    account = None
    try:
        account, _, _, route = governance_context(request)
        approved_scope = (request.get("policy_decision") or {}).get("reviewed_scope")
        if approved_scope:
            if approved_scope.get("project") != account["project_id"]:
                route = None
            else:
                route = {"role_identifier": approved_scope["role"], "scope_type": approved_scope["type"], "scope_identifier": approved_scope["resource"]}
                if route["scope_type"] == "bucket" and not any(b["name"] == route["scope_identifier"] for b in gcp.list_buckets(account["project_id"])):
                    route = None
        report = native_analysis.assess(gcp, account, route, user_email(request.get("beneficiary_id") or request["requester_id"]))
    except Exception as exc:
        report = native_analysis.assess(gcp, {}, None, "")
        report["analyzer"].update(summary="Reviewed context or identity could not be read. No access conclusion was made.", error_type=type(exc).__name__)
    rpc("complete_native_access_analysis", {"p_job": job["id"], "p_worker": WORKER_ID, "p_report": report})
    state = report["analyzer"]["state"]
    if account:
        source_health(request["tenant_id"], account["project_id"], "native_iam", "healthy" if state in ("available", "partial") else "unavailable", report["analyzer"]["summary"])


def note_servicenow_outcome(job: dict[str, Any], state: str, details: dict[str, Any]) -> None:
    """Best effort: the change record shows how delivery ended. Never blocks delivery."""
    change = servicenow_reference(job)
    if not change.get("change_sys_id"):
        return
    lines = {
        "completed": f"The apply pipeline finished successfully. Separate cloud read-back: {details.get('verification_state', 'not recorded')}. This is not an assessment of inherited or other access grants.",
        "failed": f"Delivery failed: {details.get('reason', 'unknown reason')}.",
        "cancelled": "The pull request was closed without merging; nothing was changed.",
    }[state]
    if details.get("pull_request_url"):
        lines += f" Pull request: {details['pull_request_url']}"
    if details.get("workflow_run_url"):
        lines += f" Pipeline run: {details['workflow_run_url']}"
    if details.get("expires_at"):
        lines += f" Access ends automatically at {details['expires_at']}."
    try:
        servicenow.add_change_note(servicenow.ServiceNowConfig.from_env(), change["change_sys_id"], lines)
    except Exception:
        LOGGER.exception("servicenow_note_failed job=%s", job.get("id"))


def describe_request(action_request: dict[str, Any], requester: str, project_id: str) -> tuple[str, str]:
    resource = action_request.get("resource") or {}
    decision = action_request.get("policy_decision") or {}
    capability = action_request.get("capability")
    if capability == "access_grant":
        title = f"Aegis: {resource.get('tier')} GCP access for {requester} ({resource.get('ttl_hours')}h)"
        what = (f"Grant {resource.get('tier')} access on GCP project {project_id} ({resource.get('environment')}) "
                f"to {requester} for {resource.get('ttl_hours')} hours. GCP removes it automatically afterwards.")
    else:
        labels = ", ".join(decision.get("labels_to_add") or [])
        title = f"Aegis: add missing labels to bucket {resource.get('bucket')}"
        what = (f"Add the missing required labels ({labels}) to GCP bucket {resource.get('bucket')} in project {project_id}. "
                "Existing labels are not changed.")
    body = (f"{what}\n\nRequested by: {requester}\nJustification: {action_request.get('justification', '')}\n"
            f"Policy: allowed ({', '.join(decision.get('policy_ids') or [])}), risk {decision.get('risk_level', 'unknown')}\n"
            f"Aegis request: {action_request['id']}\n\n"
            "Approving here lets Aegis open a pull request. A person must still merge it before anything changes.")
    return title, body


@register_handler("servicenow_approval")
def handle_servicenow_approval(job: dict[str, Any]) -> None:
    action_request = load_request(job)
    controls = select("governance_settings", f"tenant_id=eq.{action_request['tenant_id']}&limit=1")
    if controls and controls[0].get("proposals_paused"):
        rpc("defer_governance_job", {"p_job": job["id"], "p_worker": WORKER_ID})
        return
    config = servicenow.ServiceNowConfig.from_env()
    requester = user_email(action_request.get("requester_id"))
    project = cloud_account(str(action_request["tenant_id"]))["project_id"]
    account, settings, policy, route = governance_context(action_request)
    denials = governance.control_denials(action_request, account, settings, policy, route)
    if denials:
        raise NonRetryableJobError("; ".join(denials))
    _, file_path, content, _ = proposal_files({**action_request, "status": "proposal_pending"})
    existing = select("proposal_receipts", f"action_request_id=eq.{action_request['id']}&limit=1")
    if existing:
        receipt = existing[0]
        if receipt["artifact_sha256"] != governance.digest(content):
            raise NonRetryableJobError("The proposal differs from the immutable approval receipt")
    else:
        expiry = datetime.now(timezone.utc) + timedelta(minutes=settings["approval_ttl_minutes"])
        if action_request["capability"] == "access_grant":
            expiry = min(expiry, datetime.fromisoformat(access_expires_at(action_request).replace("Z", "+00:00")))
        receipt = {"action_request_id": action_request["id"], "tenant_id": action_request["tenant_id"], "project_id": project, "artifact_path": file_path, "artifact_sha256": governance.digest(content), "policy_sha256": governance.digest(action_request.get("policy_decision") or {}), "expires_at": expiry.isoformat()}
        manifest = governance.approval_manifest(action_request, account, route, requester, user_email(action_request.get("beneficiary_id") or action_request["requester_id"]))
        receipt.update({"manifest": manifest, "manifest_sha256": governance.digest(manifest)})
        insert("proposal_receipts", receipt)
    if datetime.fromisoformat(receipt["expires_at"].replace("Z", "+00:00")) <= datetime.now(timezone.utc):
        raise NonRetryableJobError("The approval window expired; submit a fresh request")
    title, body = describe_request(action_request, requester, project)
    body += f"\n\nApproval manifest SHA-256: {receipt.get('manifest_sha256', 'Legacy receipt')}\nArtifact: {file_path}\nSHA-256: {receipt['artifact_sha256']}\nPolicy SHA-256: {receipt['policy_sha256']}\nApproval expires: {receipt['expires_at']}"
    change = servicenow.open_change_for_approval(config, request_id=str(action_request["id"]), requester_email=requester,
                                                 short_description=title, description=body)
    decision = servicenow.approval_decision(config, change["change_sys_id"], requester)
    rpc("record_servicenow_approval", {
        "p_job_id": job["id"], "p_worker_id": WORKER_ID, "p_state": decision["state"],
        "p_details": {**change, "approver_email": decision["approver_email"], "decided_at": decision["decided_at"]},
        "p_recheck_seconds": 30,
    })
    LOGGER.info("servicenow_approval job=%s request=%s change=%s state=%s",
                job["id"], action_request["id"], change["change_number"], decision["state"])


@register_handler("search_logs")
def handle_log_search(job: dict[str, Any]) -> None:
    action_request = load_request(job)
    resource = action_request.get("resource") or {}
    project = cloud_account(str(action_request["tenant_id"]))["project_id"]
    hint = str(resource.get("resource_hint") or "")
    hours = resource.get("time_window_hours")
    findings = gcp.search_logs(project, hint, hours)
    rpc("record_log_search", {
        "p_job_id": job["id"], "p_worker_id": WORKER_ID,
        "p_summary": gcp.summarize_findings(findings, hint, hours), "p_findings": findings,
    })
    try:
        changes = gcp.related_change_events(project, hint, hours)
        context_state = "available"
    except Exception:
        changes, context_state = [], "unavailable"
    upsert("triage_change_context", {"action_request_id": action_request["id"], "tenant_id": action_request["tenant_id"], "changes": changes, "state": context_state, "coverage": {"window_hours": hours, "log_row_limit": 50, "returned_log_rows": len(findings), "audit_row_limit": 20, "statement": "Temporal matches are evidence for investigation, not proof of causation"}}, "action_request_id")
    LOGGER.info("log_search job=%s request=%s findings=%s", job["id"], action_request["id"], len(findings))


@register_handler("create_servicenow_incident")
def handle_servicenow_incident(job: dict[str, Any]) -> None:
    action_request = load_request(job)
    runs = select("workflow_runs", f"select=external_references&id=eq.{job['workflow_run_id']}&limit=1")
    triage = ((runs[0].get("external_references") or {}) if runs else {}).get("triage") or {}
    resource = action_request.get("resource") or {}
    requester = user_email(action_request.get("requester_id"))
    project = cloud_account(str(action_request["tenant_id"]))["project_id"]
    incident = servicenow.open_incident(
        servicenow.ServiceNowConfig.from_env(), request_id=str(action_request["id"]),
        short_description=f"Aegis triage: {resource.get('resource_hint')} on GCP {project}",
        description=(f"Raised by {requester} through Aegis.\nJustification: {action_request.get('justification', '')}\n"
                     f"Search: last {resource.get('time_window_hours')} hours, warnings and above.\n\n"
                     f"Findings: {triage.get('summary', 'none recorded')}"),
    )
    rpc("record_servicenow_incident", {"p_job_id": job["id"], "p_worker_id": WORKER_ID, "p_details": incident})
    LOGGER.info("servicenow_incident job=%s request=%s incident=%s", job["id"], action_request["id"], incident["incident_number"])


class LeaseHeartbeat(AbstractContextManager["LeaseHeartbeat"]):
    def __init__(self, job_id: str):
        self.job_id = job_id
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name=f"heartbeat-{job_id[:8]}", daemon=True)

    def __enter__(self) -> "LeaseHeartbeat":
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self._stop.set()
        self._thread.join(timeout=2)

    def _run(self) -> None:
        while not self._stop.wait(HEARTBEAT_SECONDS):
            try:
                rpc("heartbeat_workflow_job", {
                    "p_job_id": self.job_id,
                    "p_worker_id": WORKER_ID,
                    "p_extend_seconds": LEASE_EXTENSION_SECONDS,
                })
            except Exception:
                # The foreground completion RPC remains the authoritative lease
                # check; a transient heartbeat failure should not kill the job.
                LOGGER.exception("job_heartbeat_failed job=%s", self.job_id)


def process(job: dict[str, Any]) -> None:
    job_type = str(job.get("job_type") or "").strip()
    handler = JOB_HANDLERS.get(job_type)
    if handler is None:
        raise NonRetryableJobError(f"No worker handler is registered for job type: {job_type or '<missing>'}")
    with LeaseHeartbeat(str(job["id"])):
        handler(job)


def error_code(exc: Exception) -> str:
    name = re.sub(r"(?<!^)(?=[A-Z])", "_", exc.__class__.__name__).lower()
    return re.sub(r"[^a-z0-9_]+", "_", name)[:64] or "worker_error"


def is_retryable(exc: Exception) -> bool:
    if isinstance(exc, NonRetryableJobError):
        return False
    return bool(getattr(exc, "retryable", True))


def persist_failure(job: dict[str, Any], exc: Exception) -> None:
    rpc("fail_workflow_job", {
        "p_job_id": job["id"],
        "p_worker_id": WORKER_ID,
        "p_error": str(exc),
        "p_error_code": error_code(exc),
        "p_retryable": is_retryable(exc),
    })


def source_health(tenant: str, project: str, source: str, state: str, summary: str) -> None:
    upsert("cloud_source_health", {"tenant_id": tenant, "project_id": project, "source": source, "state": state, "summary": summary[:400], "checked_at": datetime.now(timezone.utc).isoformat()}, "tenant_id,project_id,source")


def refresh_inventory(tenant: str, project: str) -> None:
    try:
        assets = gcp.resource_inventory(project)
        previous = select("cloud_snapshots", f"tenant_id=eq.{tenant}&project_id=eq.{project}&kind=eq.inventory&order=captured_at.desc&limit=1")
        if previous:
            before = {a["name"]: a for a in previous[0]["data"].get("assets", [])}
            after = {a["name"]: a for a in assets}
            changes = [{"name": name, "change": "added" if name not in before else "removed" if name not in after else "metadata_changed", "before": before.get(name), "after": after.get(name)} for name in sorted(before.keys() | after.keys()) if before.get(name) != after.get(name)]
            insert("cloud_snapshots", {"tenant_id": tenant, "project_id": project, "kind": "observed_drift", "data": {"baseline_captured_at": previous[0]["captured_at"], "changes": changes, "scope": "Observed inventory metadata changes, not approved-IaC drift"}})
        insert("cloud_snapshots", {"tenant_id": tenant, "project_id": project, "kind": "inventory", "data": {"assets": assets, "total": len(assets)}})
        source_health(tenant, project, "inventory", "healthy", f"Read {len(assets)} resources from Cloud Asset Inventory")
    except Exception as exc:
        source_health(tenant, project, "inventory", "error", f"Inventory read failed ({type(exc).__name__}); verify Cloud Asset API and reader permissions")


def insert(table: str, row: dict[str, Any]) -> None:
    request_json(f"{SUPABASE_URL}/rest/v1/{table}", method="POST", body=row, supabase=True)


def refresh_snapshots(now: datetime | None = None) -> None:
    """Record label compliance (and cost, once billing export exists) per workspace."""
    now = now or datetime.now(timezone.utc)
    for account in select("tenant_cloud_accounts", "select=tenant_id,project_id,billing_table&cloud=eq.gcp&enabled=eq.true"):
        tenant, project = account["tenant_id"], account["project_id"]
        refresh_inventory(tenant, project)
        try:
            buckets = gcp.list_buckets(project)
            items = [{"bucket": b["name"], "location": b["location"], "labels": b["labels"],
                      "missing": gcp.missing_labels(b["labels"])} for b in buckets]
            insert("cloud_snapshots", {"tenant_id": tenant, "kind": "label_compliance", "project_id": project, "data": {
                "required": list(gcp.REQUIRED_LABELS), "buckets": items,
                "compliant": sum(1 for item in items if not item["missing"]), "total": len(items)}})
            source_health(tenant, project, "labels", "healthy", f"Read labels for {len(items)} buckets")
        except Exception:
            LOGGER.exception("label_snapshot_failed tenant=%s", tenant)
            source_health(tenant, project, "labels", "error", "Bucket metadata read failed")
        if not account.get("billing_table"):
            source_health(tenant, project, "billing", "unavailable", "No billing export table is configured")
            continue
        month = now.strftime("%Y%m")
        try:
            rows = gcp.cost_by_cost_center(project, account["billing_table"], month)
            total = round(sum(float(row["net_cost"] or 0) for row in rows), 2)
            untagged = round(sum(float(row["net_cost"] or 0) for row in rows if not row["cost_center"]), 2)
            insert("cloud_snapshots", {"tenant_id": tenant, "kind": "cost", "project_id": project, "data": {
                "month": month, "currency": next((row["currency"] for row in rows if row.get("currency")), None),
                "total": total, "untagged": untagged, "rows": rows}})
            source_health(tenant, project, "billing", "healthy", f"Read {len(rows)} billing groups")
        except Exception:
            LOGGER.exception("cost_snapshot_failed tenant=%s", tenant)
            source_health(tenant, project, "billing", "error", "Billing export could not be queried")


def export_audit_events() -> int:
    """Copy new audit events to Cloud Logging (`aegis-audit`) for SIEM ingestion."""
    exported = 0
    for account in select("tenant_cloud_accounts", "select=tenant_id,project_id&cloud=eq.gcp&enabled=eq.true"):
        tenant = account["tenant_id"]
        state = select("audit_export_state", f"select=last_event_id&tenant_id=eq.{tenant}&limit=1")
        last = int(state[0]["last_event_id"]) if state else 0
        events = select("audit_events", "select=id,tenant_id,actor_id,action,object_type,object_id,correlation_id,metadata,occurred_at"
                                        f"&tenant_id=eq.{tenant}&id=gt.{last}&order=id.asc&limit=200")
        if not events:
            continue
        gcp.write_audit_entries(account["project_id"], events)
        newest = events[-1]["id"]
        request_json(f"{SUPABASE_URL}/rest/v1/audit_export_state?on_conflict=tenant_id", method="POST",
                     body={"tenant_id": tenant, "last_event_id": newest, "updated_at": datetime.now(timezone.utc).isoformat()},
                     supabase=True, extra_headers={"Prefer": "resolution=merge-duplicates"})
        exported += len(events)
    return exported


class Every:
    def __init__(self, seconds: float):
        self.seconds = seconds
        self.next_at = 0.0

    def due(self, now: float) -> bool:
        if now < self.next_at:
            return False
        self.next_at = now + self.seconds
        return True


def run_periodic(snapshots: "Every", audit: "Every") -> None:
    now = time.monotonic()
    if snapshots.due(now):
        try:
            refresh_snapshots()
        except Exception:
            LOGGER.exception("snapshots_failed")
    if audit.due(now):
        try:
            count = export_audit_events()
            if count:
                LOGGER.info("audit_exported events=%s", count)
        except Exception:
            LOGGER.exception("audit_export_failed")


def run() -> None:
    LOGGER.info("worker_started worker_id=%s job_types=%s", WORKER_ID, ",".join(registered_job_types()))
    snapshots, audit = Every(SNAPSHOT_SECONDS), Every(AUDIT_EXPORT_SECONDS)
    while True:
        run_periodic(snapshots, audit)
        try:
            jobs = rpc("claim_workflow_jobs", {
                "p_worker_id": WORKER_ID,
                "p_limit": 5,
                "p_job_types": registered_job_types(),
            }) or []
            if not jobs:
                time.sleep(POLL_SECONDS)
                continue
            for job in jobs:
                try:
                    process(job)
                except Exception as exc:  # job failure must be persisted before continuing
                    LOGGER.exception("job_failed job=%s", job.get("id"))
                    try:
                        persist_failure(job, exc)
                    except Exception:
                        LOGGER.exception("job_failure_persist_failed job=%s", job.get("id"))
        except KeyboardInterrupt:
            LOGGER.info("worker_stopped")
            return
        except Exception:
            LOGGER.exception("claim_failed")
            time.sleep(POLL_SECONDS)


def start_health_server() -> None:
    """Cloud Run requires an HTTP port; answer health checks only."""
    port = os.getenv("PORT")
    if not port:
        return
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class Health(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - stdlib name
            body = json.dumps({"status": "ok", "worker_id": WORKER_ID, "job_types": registered_job_types()}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:  # noqa: N802
            status, result = 403, {"error": "Pipeline authentication required"}
            try:
                expected = required_env("AEGIS_PIPELINE_CALLER")
                audience = required_env("AEGIS_GATE_AUDIENCE")
                from google.oauth2 import id_token
                import google.auth.transport.requests
                token = self.headers.get("X-Aegis-Token", "")
                identity = id_token.verify_oauth2_token(token, google.auth.transport.requests.Request(), audience=audience)
                if identity.get("email") != expected or not identity.get("email_verified") or self.path != "/governance/plan":
                    raise ValueError("Caller or route is not authorized")
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 100000:
                    raise ValueError("Invalid request size")
                payload = json.loads(self.rfile.read(size))
                status, result = plan_gate.authorize(payload, sys.modules[__name__])
            except Exception as exc:
                LOGGER.warning("pipeline_gate_failed type=%s", type(exc).__name__)
                result = {"error": "The execution gate could not validate this request"}
            body = json.dumps(result).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: Any) -> None:
            return

    server = ThreadingHTTPServer(("0.0.0.0", int(port)), Health)
    threading.Thread(target=server.serve_forever, name="health", daemon=True).start()


if __name__ == "__main__":
    start_health_server()
    run()
