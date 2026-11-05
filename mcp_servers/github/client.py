"""
Real GitHub client — the "propose" half of the governed change path (Layer 4).
Opens a branch + commit + pull request containing the generated Terraform
diff. It never merges anything itself: a human merges, and only that merge
(a push to main) triggers the GitHub Actions workflow that actually applies
(see .github/workflows/terraform-apply.yml).

Authentication uses a short-lived GitHub App installation token. The App ID,
installation ID and PEM key are injected by Infisical and never logged.
"""

from __future__ import annotations

import base64
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import jwt
import requests
from dotenv import load_dotenv
from pydantic import BaseModel

from contracts.capability_contract import PullRequestRef

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

_REQUIRED_ENV_VARS = ("GITHUB_REPO", "GITHUB_APP_ID", "GITHUB_APP_INSTALLATION_ID", "GITHUB_APP_PRIVATE_KEY")
_API_ROOT = "https://api.github.com"
_cached_installation_token: tuple[str, datetime] | None = None


class GitHubConfigError(Exception):
    pass


class GitHubRequestError(Exception):
    def __init__(self, status_code: int, body: str):
        self.status_code = status_code
        super().__init__(f"GitHub API returned {status_code}: {body[:300]}")


def _config() -> str:
    missing = [name for name in _REQUIRED_ENV_VARS if not os.getenv(name)]
    if missing:
        raise GitHubConfigError(f"missing required environment variables: {', '.join(missing)}")
    return os.environ["GITHUB_REPO"]


def _headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _app_jwt() -> str:
    now = datetime.now(timezone.utc)
    private_key = os.environ["GITHUB_APP_PRIVATE_KEY"].replace("\\n", "\n")
    return jwt.encode(
        {"iat": int((now - timedelta(seconds=60)).timestamp()), "exp": int((now + timedelta(minutes=9)).timestamp()), "iss": os.environ["GITHUB_APP_ID"]},
        private_key,
        algorithm="RS256",
    )


def _installation_token() -> str:
    global _cached_installation_token
    now = datetime.now(timezone.utc)
    if _cached_installation_token and _cached_installation_token[1] > now + timedelta(minutes=2):
        return _cached_installation_token[0]

    response = requests.post(
        f"{_API_ROOT}/app/installations/{os.environ['GITHUB_APP_INSTALLATION_ID']}/access_tokens",
        headers=_headers(_app_jwt()),
        timeout=15,
    )
    if response.status_code != 201:
        raise GitHubRequestError(response.status_code, response.text)
    payload = response.json()
    expires_at = datetime.fromisoformat(payload["expires_at"].replace("Z", "+00:00"))
    _cached_installation_token = (payload["token"], expires_at)
    return payload["token"]


def _request(method: str, path: str, **kwargs) -> dict:
    response = requests.request(method, f"{_API_ROOT}{path}", headers=_headers(_installation_token()), timeout=15, **kwargs)
    if response.status_code not in (200, 201):
        raise GitHubRequestError(response.status_code, response.text)
    return response.json()


def open_pull_request(
    branch_name: str,
    file_path: str,
    file_content: str,
    pr_title: str,
    pr_body: str,
    base_branch: str = "main",
) -> PullRequestRef:
    """Opens a branch, commits the generated Terraform diff, and opens a PR —
    shared by every capability that proposes an IaC change (access_grant,
    tag_remediation, ...); nothing here is specific to one capability."""
    repo = _config()

    base_ref = _request("GET", f"/repos/{repo}/git/ref/heads/{base_branch}")
    base_sha = base_ref["object"]["sha"]

    _request(
        "POST",
        f"/repos/{repo}/git/refs",
        json={"ref": f"refs/heads/{branch_name}", "sha": base_sha},
    )

    _request(
        "PUT",
        f"/repos/{repo}/contents/{file_path}",
        json={
            "message": f"Aegis: {pr_title}",
            "content": base64.b64encode(file_content.encode()).decode(),
            "branch": branch_name,
        },
    )

    pr = _request(
        "POST",
        f"/repos/{repo}/pulls",
        json={"title": pr_title, "body": pr_body, "head": branch_name, "base": base_branch},
    )

    return PullRequestRef(number=pr["number"], html_url=pr["html_url"], branch=branch_name)


def get_pull_request_state(pr_number: int) -> str:
    """Read-only status check: 'open', 'closed' (merged or not — check merged_at)."""
    repo = _config()
    pr = _request("GET", f"/repos/{repo}/pulls/{pr_number}")
    if pr["state"] == "closed" and pr.get("merged_at"):
        return "merged"
    return pr["state"]


def get_merge_commit_sha(pr_number: int) -> str | None:
    """Returns the merge commit SHA once merged, else None — used to look up
    the GitHub Actions run that resulted from this specific PR."""
    repo = _config()
    pr = _request("GET", f"/repos/{repo}/pulls/{pr_number}")
    if pr.get("merged_at"):
        return pr.get("merge_commit_sha")
    return None


class WorkflowRunStatus(BaseModel):
    run_id: int
    status: str  # 'queued' | 'in_progress' | 'success' | 'failure' | 'cancelled' | 'timed_out'
    log_url: str


def get_workflow_run_for_commit(
    commit_sha: str, workflow_filename: str = "terraform-apply.yml"
) -> WorkflowRunStatus | None:
    """Read-only lookup of the GitHub Actions run triggered by this commit —
    never starts, cancels, or approves a run itself."""
    repo = _config()
    result = _request("GET", f"/repos/{repo}/actions/runs?head_sha={commit_sha}")

    for run in result.get("workflow_runs", []):
        if run.get("path", "").endswith(workflow_filename):
            status = run.get("conclusion") or run.get("status")
            return WorkflowRunStatus(run_id=run["id"], status=status, log_url=run["html_url"])
    return None
