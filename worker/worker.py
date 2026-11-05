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


def request_json(url: str, *, method: str = "GET", body: dict[str, Any] | None = None, supabase: bool = False) -> Any:
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
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
        "select=id,tenant_id,requester_id,beneficiary_id,service_id,capability,justification,status,resource"
        f"&id=eq.{runs[0]['action_request_id']}&limit=1",
    )
    if not requests:
        raise RuntimeError("Action request does not exist")
    return requests[0]


def evaluate_policy(action_request: dict[str, Any]) -> dict[str, Any]:
    resource = action_request.get("resource") or {}
    policy_input = {
        "request": {
            "cloud": resource.get("cloud"),
            "environment": resource.get("environment"),
            "tier": resource.get("tier"),
            "ttl_hours": resource.get("ttl_hours"),
            "justification": action_request.get("justification", ""),
        }
    }
    response = request_json(f"{OPA_URL}/v1/data/aegis/access_request/decision", method="POST", body={"input": policy_input})
    decision = response.get("result") if isinstance(response, dict) else None
    if not isinstance(decision, dict) or not isinstance(decision.get("allowed"), bool):
        raise RuntimeError("OPA returned no valid decision")
    return decision


@register_handler("evaluate_policy")
def handle_policy_evaluation(job: dict[str, Any]) -> None:
    action_request = load_request(job)
    decision = evaluate_policy(action_request)
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
    action_request = load_request(job)
    if action_request.get("status") != "proposal_pending":
        raise NonRetryableJobError("Action request is not approved and proposal_pending")
    if action_request.get("capability") != "access_grant":
        raise NonRetryableJobError("Only access_grant proposals are supported")
    resource = action_request.get("resource") or {}
    for field in ("cloud", "environment", "tier", "ttl_hours"):
        if field not in resource:
            raise NonRetryableJobError(f"Request resource is missing {field}")
    routes = select(
        "service_entitlements",
        "select=id,cloud,environment,tier,entitlement_type,target_identifier,role_identifier,"
        "terraform_module_source,max_ttl_hours"
        f"&service_id=eq.{action_request['service_id']}"
        f"&cloud=eq.{urllib.parse.quote(str(resource['cloud']), safe='')}"
        f"&environment=eq.{urllib.parse.quote(str(resource['environment']), safe='')}"
        f"&tier=eq.{urllib.parse.quote(str(resource['tier']), safe='')}&enabled=eq.true&limit=2",
    )
    if len(routes) != 1:
        raise NonRetryableJobError("No unique reviewed IaC route exists for this request")
    ttl_hours = resource.get("ttl_hours")
    if not isinstance(ttl_hours, int) or ttl_hours < 1 or ttl_hours > int(routes[0]["max_ttl_hours"]):
        raise NonRetryableJobError("Requested duration exceeds the reviewed entitlement limit")
    beneficiary_id = action_request.get("beneficiary_id") or action_request.get("requester_id")
    user = request_json(f"{SUPABASE_URL}/auth/v1/admin/users/{beneficiary_id}", supabase=True)
    email = str(user.get("email") or "").strip().lower() if isinstance(user, dict) else ""
    if not re.fullmatch(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,63}", email):
        raise NonRetryableJobError("Beneficiary has no valid authenticated email address")
    return action_request, routes[0], email


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
    if module_source != "modules/gcp/iam_binding":
        raise NonRetryableJobError("Reviewed route uses an unsupported Terraform module")
    request_id = str(action_request["id"])
    if not re.fullmatch(r"[0-9a-f-]{36}", request_id):
        raise NonRetryableJobError("Action request ID is not a UUID")
    module_name = f"aegis_request_{request_id.replace('-', '_')}"
    return (
        "# Generated by Aegis Governed Delivery. Do not edit manually.\n"
        f"# action_request_id: {request_id}\n"
        f"# expires_after_hours: {action_request['resource']['ttl_hours']}\n\n"
        f"module {json.dumps(module_name)} {{\n"
        f"  source  = {json.dumps('../../' + module_source)}\n"
        f"  project = {json.dumps(project_id)}\n"
        f"  role    = {json.dumps(role)}\n"
        f"  member  = {json.dumps('user:' + email)}\n"
        "}\n"
    )


def find_pull_request(repo: str, branch: str, token: str) -> dict[str, Any] | None:
    owner = repo.split("/", 1)[0]
    query = urllib.parse.urlencode({"state": "all", "head": f"{owner}:{branch}", "per_page": 10})
    pulls = github_request("GET", f"/repos/{repo}/pulls?{query}", token)
    return pulls[0] if pulls else None


def create_github_proposal(action_request: dict[str, Any], route: dict[str, Any], email: str) -> dict[str, Any]:
    repo, _, _, _ = github_config()
    token = github_installation_token()
    request_id = str(action_request["id"])
    branch = f"aegis/access-request-{request_id}"
    file_path = f"infra/access-grants/{request_id}.tf"
    content = render_gcp_access_hcl(action_request, route, email)
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
            {"message": f"Aegis: approved access request {request_id}", "content": b64encode(content.encode()).decode(), "branch": branch},
        )
    pr = find_pull_request(repo, branch, token)
    if pr is None:
        justification = clean_pr_text(str(action_request.get("justification") or ""), 1500)
        pr = github_request(
            "POST", f"/repos/{repo}/pulls", token,
            {
                "title": f"Aegis access request {request_id[:8]}", "head": branch, "base": "main",
                "body": f"Governed access proposal for authenticated beneficiary `{email}`.\n\nJustification:\n{justification}\n\nThis PR requires protected-branch review and cannot be merged by Aegis.",
            },
        )
    ref = github_request("GET", f"/repos/{repo}/git/ref/heads/{urllib.parse.quote(branch, safe='/')}", token)
    return {"number": pr["number"], "url": pr["html_url"], "branch": branch, "commit_sha": ref["object"]["sha"], "file_path": file_path}


@register_handler("create_github_proposal")
def handle_github_proposal(job: dict[str, Any]) -> None:
    action_request, route, email = load_proposal_context(job)
    proposal = create_github_proposal(action_request, route, email)
    rpc("complete_github_proposal", {
        "p_job_id": job["id"], "p_worker_id": WORKER_ID, "p_pr_number": proposal["number"],
        "p_pr_url": proposal["url"], "p_branch": proposal["branch"], "p_commit_sha": proposal["commit_sha"],
        "p_file_path": proposal["file_path"],
    })
    LOGGER.info("github_proposal_complete job=%s request=%s pr=%s", job["id"], action_request["id"], proposal["number"])


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


def persist_failure(job: dict[str, Any], exc: Exception) -> None:
    rpc("fail_workflow_job", {
        "p_job_id": job["id"],
        "p_worker_id": WORKER_ID,
        "p_error": str(exc),
        "p_error_code": error_code(exc),
        "p_retryable": not isinstance(exc, NonRetryableJobError),
    })


def run() -> None:
    LOGGER.info("worker_started worker_id=%s job_types=%s", WORKER_ID, ",".join(registered_job_types()))
    while True:
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


if __name__ == "__main__":
    run()
