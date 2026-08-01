"""
The tag_remediation capability agent: checks a resource's current tags
against the required-tags policy and — only if non-compliant and allowed by
the guardrail — produces the IaC diff that fills in the missing keys.

The pull request is deliberately NOT opened here, for the same reason as
access_grant: a request that gets declined must never have had a PR
proposing its change on GitHub in the first place (see advance_pipeline).

Only missing required keys get a value filled in; any existing tag (required
or not, correct or not) is preserved as-is — this capability closes gaps, it
does not correct wrong values, which is out of scope for this MVP.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from agents.guardrail_agent import evaluate_tag_remediation
from agents.tag_render import render_tag_remediation_diff, tag_remediation_slug
from contracts.capability_contract import (
    ApprovalRef,
    Cloud,
    PolicyDecision,
    PullRequestRef,
    TagComplianceRequest,
    TagRef,
    TagRemediationResult,
)
from contracts.tag_policy import required_tag_keys
from mcp_servers.azure_resource_tags.resource_tags import current_tags as read_azure_tags
from mcp_servers.gcp_storage.bucket_labels import PROJECT_ID, current_labels as read_gcp_labels
from mcp_servers.github.client import (
    GitHubConfigError,
    GitHubRequestError,
    WorkflowRunStatus,
    get_merge_commit_sha,
    get_pull_request_state,
    get_workflow_run_for_commit,
    merge_pull_request,
    open_pull_request,
)
from mcp_servers.servicenow.client import (
    ServiceNowConfigError,
    ServiceNowRequestError,
    create_access_request,
    get_approval_state,
)


class TagRemediationDenied(Exception):
    def __init__(self, decision: PolicyDecision):
        self.decision = decision
        super().__init__("; ".join(decision.reasons) or "denied by guardrail policy")


class AlreadyCompliantError(Exception):
    def __init__(self, resource_id: str):
        super().__init__(f"{resource_id!r} already has every required tag — no change to propose")


class ApprovalRequestFailed(Exception):
    def __init__(self, reason: str):
        super().__init__(f"couldn't raise the ServiceNow approval: {reason}")


class PullRequestFailed(Exception):
    def __init__(self, reason: str):
        super().__init__(f"couldn't open the Terraform pull request: {reason}")


class ApprovalRejected(Exception):
    def __init__(self, state: str):
        self.state = state
        super().__init__(f"the request was not approved (ServiceNow state: {state!r}) — no pull request was raised")


def _gcp_label_safe(value: str) -> str:
    """GCP labels only permit lowercase letters, digits, hyphens, and
    underscores (max 63 chars) — sanitizes a free-text value (e.g. an email
    or a typed cost-center code) into a valid label value deterministically,
    rather than letting `terraform apply` fail on it later in CI."""
    return re.sub(r"[^a-z0-9_-]", "-", value.lower())[:63]


def _default_values(request: TagComplianceRequest) -> dict[str, str]:
    values = {
        "owner": request.requester,
        "cost-center": request.cost_center,
        "environment": request.environment.value,
    }
    if request.cloud == Cloud.GCP:
        values = {key: _gcp_label_safe(value) for key, value in values.items()}
    return values


def check_tag_compliance(cloud: Cloud, resource_id: str) -> tuple[dict[str, str], list[str]]:
    """Read-only preview of current tags and which required keys are
    missing. Used both by the chat UI (to decide whether to keep asking
    questions) and by handle_tag_remediation itself, so there's a single
    source of truth for what "compliant" means."""
    current = read_gcp_labels(resource_id) if cloud == Cloud.GCP else read_azure_tags(resource_id)
    missing_keys = [key for key in required_tag_keys() if key not in current]
    return current, missing_keys


def handle_tag_remediation(request: TagComplianceRequest) -> TagRemediationResult:
    decision = evaluate_tag_remediation(request)
    if not decision.allowed:
        raise TagRemediationDenied(decision)

    current, missing_keys = check_tag_compliance(request.cloud, request.resource_id)
    if not missing_keys:
        raise AlreadyCompliantError(request.resource_id)

    defaults = _default_values(request)
    proposed_values = dict(current)
    for key in missing_keys:
        proposed_values[key] = defaults[key]
    proposed_tags = [TagRef(key=key, value=value) for key, value in sorted(proposed_values.items())]

    project_id = PROJECT_ID if request.cloud == Cloud.GCP else None
    iac_diff = render_tag_remediation_diff(request, proposed_tags, project_id=project_id)

    slug = tag_remediation_slug(request)
    unique_suffix = int(datetime.now(timezone.utc).timestamp())
    branch_name = f"tag-remediation/{slug}-{unique_suffix}"
    file_path = f"infra/tag-remediations/{slug}-{unique_suffix}.tf"
    pr_title = f"Tag remediation: {request.resource_id}"
    pr_body = (
        f"**Requester:** {request.requester}\n"
        f"**Justification:** {request.justification}\n"
        f"**Missing tags being added:** {', '.join(missing_keys)}\n\n"
        "Propose-don't-mutate: this PR only exists because ServiceNow already approved the "
        "request. It still takes effect only once a human merges it, which triggers the "
        "GitHub Actions pipeline to run `terraform apply`."
    )

    try:
        access_request = create_access_request(
            short_description=f"Aegis tag remediation — {request.resource_id}",
            description=(
                f"Requested by: {request.requester}\n"
                f"Justification: {request.justification}\n"
                f"Missing tags: {', '.join(missing_keys)}\n\n"
                f"Proposed Terraform change (a pull request will be opened only once this is "
                f"approved):\n{iac_diff}"
            ),
            justification=request.justification,
        )
    except (ServiceNowConfigError, ServiceNowRequestError) as exc:
        raise ApprovalRequestFailed(str(exc)) from exc

    return TagRemediationResult(
        cloud=request.cloud,
        resource_id=request.resource_id,
        current_tags=[TagRef(key=k, value=v) for k, v in sorted(current.items())],
        missing_keys=missing_keys,
        proposed_tags=proposed_tags,
        iac_diff=iac_diff,
        branch_name=branch_name,
        file_path=file_path,
        pr_title=pr_title,
        pr_body=pr_body,
        policy_decision=decision,
        approval=ApprovalRef(
            reference=access_request.request_number,
            request_sys_id=access_request.request_sys_id,
            approval_sys_id=access_request.approval_sys_id,
            state=access_request.approval_state,
        ),
        pull_request=None,
    )


def advance_pipeline(result: TagRemediationResult) -> TagRemediationResult:
    """Polls ServiceNow and raises the PR the moment — and only the moment —
    approval is confirmed. Never raises a PR for anything pending or
    declined, and never re-raises one that already exists (safe to call on
    every refresh click)."""
    if result.pull_request is not None:
        return result

    approval_state = check_approval_status(result.approval)

    if approval_state in ("rejected", "cancelled"):
        raise ApprovalRejected(approval_state)

    if approval_state != "approved":
        return result

    try:
        pull_request = open_pull_request(
            branch_name=result.branch_name,
            file_path=result.file_path,
            file_content=result.iac_diff,
            pr_title=result.pr_title,
            pr_body=result.pr_body,
        )
    except (GitHubConfigError, GitHubRequestError) as exc:
        raise PullRequestFailed(str(exc)) from exc

    return result.model_copy(update={"pull_request": pull_request})


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


class MergeNotAllowed(Exception):
    def __init__(self, approval_state: str):
        super().__init__(f"refusing to merge — ServiceNow approval state is {approval_state!r}, not 'approved'")


def merge_tag_remediation(approval: ApprovalRef, pull_request: PullRequestRef) -> str:
    """The one explicit human-triggered write in this whole flow: merging the
    PR, which is what actually lets the CI/CD pipeline apply the change.
    Re-checks the live approval state itself rather than trusting a stale
    value the caller might be holding — never merges an unapproved request."""
    approval_state = check_approval_status(approval)
    if approval_state != "approved":
        raise MergeNotAllowed(approval_state)
    return merge_pull_request(pull_request.number)
