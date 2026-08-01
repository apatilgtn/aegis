"""
Real GitHub client — the "propose" half of the governed change path (Layer 4).
Opens a branch + commit + pull request containing the generated Terraform
diff. It never merges anything itself: a human merges, and only that merge
(a push to main) triggers the GitHub Actions workflow that actually applies
(see .github/workflows/terraform-apply.yml).

Credentials are read from environment variables (see .env.example), loaded
via python-dotenv — never hardcoded, never logged.
"""

from __future__ import annotations

import base64
import os
from pathlib import Path

import requests
from dotenv import load_dotenv
from pydantic import BaseModel

from contracts.capability_contract import PullRequestRef

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

_REQUIRED_ENV_VARS = ("GITHUB_REPO", "GITHUB_PAT")
_API_ROOT = "https://api.github.com"


class GitHubConfigError(Exception):
    pass


class GitHubRequestError(Exception):
    def __init__(self, status_code: int, body: str):
        self.status_code = status_code
        super().__init__(f"GitHub API returned {status_code}: {body[:300]}")


def _config() -> tuple[str, str]:
    missing = [name for name in _REQUIRED_ENV_VARS if not os.getenv(name)]
    if missing:
        raise GitHubConfigError(f"missing required environment variables: {', '.join(missing)}")
    return os.environ["GITHUB_REPO"], os.environ["GITHUB_PAT"]


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}


def _request(method: str, path: str, token: str, **kwargs) -> dict:
    response = requests.request(method, f"{_API_ROOT}{path}", headers=_headers(token), timeout=15, **kwargs)
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
    repo, token = _config()

    base_ref = _request("GET", f"/repos/{repo}/git/ref/heads/{base_branch}", token)
    base_sha = base_ref["object"]["sha"]

    _request(
        "POST",
        f"/repos/{repo}/git/refs",
        token,
        json={"ref": f"refs/heads/{branch_name}", "sha": base_sha},
    )

    _request(
        "PUT",
        f"/repos/{repo}/contents/{file_path}",
        token,
        json={
            "message": f"Aegis: {pr_title}",
            "content": base64.b64encode(file_content.encode()).decode(),
            "branch": branch_name,
        },
    )

    pr = _request(
        "POST",
        f"/repos/{repo}/pulls",
        token,
        json={"title": pr_title, "body": pr_body, "head": branch_name, "base": base_branch},
    )

    return PullRequestRef(number=pr["number"], html_url=pr["html_url"], branch=branch_name)


def get_pull_request_state(pr_number: int) -> str:
    """Read-only status check: 'open', 'closed' (merged or not — check merged_at)."""
    repo, token = _config()
    pr = _request("GET", f"/repos/{repo}/pulls/{pr_number}", token)
    if pr["state"] == "closed" and pr.get("merged_at"):
        return "merged"
    return pr["state"]


def merge_pull_request(pr_number: int) -> str:
    """The one write action a human explicitly triggers — via an in-chat
    confirmation after ServiceNow approval, per user decision. Returns the
    merge commit SHA."""
    repo, token = _config()
    result = _request("PUT", f"/repos/{repo}/pulls/{pr_number}/merge", token, json={"merge_method": "squash"})
    return result["sha"]


def get_merge_commit_sha(pr_number: int) -> str | None:
    """Returns the merge commit SHA once merged, else None — used to look up
    the GitHub Actions run that resulted from this specific PR."""
    repo, token = _config()
    pr = _request("GET", f"/repos/{repo}/pulls/{pr_number}", token)
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
    repo, token = _config()
    result = _request("GET", f"/repos/{repo}/actions/runs?head_sha={commit_sha}", token)

    for run in result.get("workflow_runs", []):
        if run.get("path", "").endswith(workflow_filename):
            status = run.get("conclusion") or run.get("status")
            return WorkflowRunStatus(run_id=run["id"], status=status, log_url=run["html_url"])
    return None
