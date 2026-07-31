"""
The access_grant capability agent: resolves the entitlement, runs it past the
guardrail, and — only if allowed — produces the IaC diff to be opened as a PR.

`cloud` is passed in by the orchestrator, not inferred by this agent from the
natural-language request: which cloud hosts a given business_unit/environment
is routing knowledge the orchestrator owns, not something to guess here.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from agents.guardrail_agent import evaluate_access_grant
from agents.terraform_render import access_grant_slug, render_access_grant_diff
from contracts.capability_contract import (
    AccessGrantRequest,
    AccessGrantResult,
    ApprovalRef,
    Cloud,
    PolicyDecision,
    PullRequestRef,
)
from contracts.entitlement_catalog import EntitlementCatalog
from mcp_servers.azure_entra.directory_service import is_group_member, resolve_principal
from mcp_servers.gcp_resource_manager.project_service import resolve_project
from mcp_servers.github.client import (
    GitHubConfigError,
    GitHubRequestError,
    WorkflowRunStatus,
    get_merge_commit_sha,
    get_pull_request_state,
    get_workflow_run_for_commit,
    open_access_grant_pr,
)
from mcp_servers.servicenow.client import (
    ServiceNowConfigError,
    ServiceNowRequestError,
    create_access_request,
    get_approval_state,
)


class AccessGrantDenied(Exception):
    def __init__(self, decision: PolicyDecision):
        self.decision = decision
        super().__init__("; ".join(decision.reasons) or "denied by guardrail policy")


class AlreadyGrantedError(Exception):
    def __init__(self, requester: str, catalog_entry_id: str):
        super().__init__(f"{requester} already holds entitlement {catalog_entry_id!r} — no change to propose")


class ApprovalRequestFailed(Exception):
    def __init__(self, reason: str):
        super().__init__(f"couldn't raise the ServiceNow approval: {reason}")


class PullRequestFailed(Exception):
    def __init__(self, reason: str):
        super().__init__(f"couldn't open the Terraform pull request: {reason}")


def handle_access_grant(
    request: AccessGrantRequest,
    cloud: Cloud,
    catalog: EntitlementCatalog,
) -> AccessGrantResult:
    catalog_entry = catalog.resolve(request.business_unit, request.environment, request.tier, cloud)

    decision = evaluate_access_grant(request, catalog_entry)
    if not decision.allowed:
        raise AccessGrantDenied(decision)

    member_object_id = None
    project_id = None

    if cloud == Cloud.AZURE:
        # Reads live identity state via the MCP-fronted directory before
        # proposing a change — never guesses the object ID from the UPN.
        member_object_id = resolve_principal(request.requester)
        if is_group_member(catalog_entry.resource_id, member_object_id):
            raise AlreadyGrantedError(request.requester, catalog_entry.id)
    elif cloud == Cloud.GCP:
        project_id = resolve_project(request.business_unit)

    iac_diff = render_access_grant_diff(
        request, catalog_entry, member_object_id=member_object_id, project_id=project_id
    )

    slug = access_grant_slug(request, catalog_entry)
    unique_suffix = int(datetime.now(timezone.utc).timestamp())
    branch_name = f"access-grant/{slug}-{unique_suffix}"
    file_path = f"infra/access-grants/{slug}-{unique_suffix}.tf"

    try:
        pull_request = open_access_grant_pr(
            branch_name=branch_name,
            file_path=file_path,
            file_content=iac_diff,
            pr_title=f"Access grant: {catalog_entry.display_name} for {request.requester}",
            pr_body=(
                f"**Requester:** {request.requester}\n"
                f"**Justification:** {request.justification}\n"
                f"**Expires:** {request.ttl_hours}h after approval\n\n"
                "Propose-don't-mutate: this PR only takes effect once a human merges it, "
                "which triggers the GitHub Actions pipeline to run `terraform apply`."
            ),
        )
    except (GitHubConfigError, GitHubRequestError) as exc:
        raise PullRequestFailed(str(exc)) from exc

    try:
        access_request = create_access_request(
            short_description=f"Aegis access grant — {catalog_entry.display_name} for {request.requester}",
            description=(
                f"Requested by: {request.requester}\n"
                f"Justification: {request.justification}\n"
                f"Pull request: {pull_request.html_url}\n\n"
                f"Proposed Terraform change:\n{iac_diff}"
            ),
            justification=request.justification,
        )
    except (ServiceNowConfigError, ServiceNowRequestError) as exc:
        raise ApprovalRequestFailed(str(exc)) from exc

    return AccessGrantResult(
        entitlement=catalog.to_entitlement_ref(catalog_entry),
        iac_diff=iac_diff,
        policy_decision=decision,
        approval=ApprovalRef(
            reference=access_request.request_number,
            request_sys_id=access_request.request_sys_id,
            approval_sys_id=access_request.approval_sys_id,
            state=access_request.approval_state,
        ),
        pull_request=pull_request,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=request.ttl_hours),
    )


def check_approval_status(approval: ApprovalRef) -> str:
    """Live poll of whether a previously raised approval is still pending,
    approved, or rejected — read-only, never grants anything itself."""
    return get_approval_state(approval.approval_sys_id)


def check_pull_request_status(pull_request: PullRequestRef) -> str:
    """Live poll of whether the PR is still open or has been merged —
    read-only; merging (and therefore applying) is a human action only."""
    return get_pull_request_state(pull_request.number)


def check_pipeline_status(pull_request: PullRequestRef) -> tuple[str, WorkflowRunStatus | None]:
    """Combined live status: PR state, plus the real GitHub Actions run once
    merged. Returns (pr_state, workflow_run_status_or_none)."""
    pr_state = get_pull_request_state(pull_request.number)
    if pr_state != "merged":
        return pr_state, None

    commit_sha = get_merge_commit_sha(pull_request.number)
    run = get_workflow_run_for_commit(commit_sha) if commit_sha else None
    return pr_state, run
