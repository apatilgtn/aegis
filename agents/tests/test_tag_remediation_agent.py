import pytest

from agents.tag_remediation_agent import (
    AlreadyCompliantError,
    ApprovalRejected,
    TagRemediationDenied,
    _gcp_label_safe,
    advance_pipeline,
    check_approval_status,
    check_pull_request_status,
    handle_tag_remediation,
)
from agents.tag_render import UnsafeIaCOwnershipError, render_tag_remediation_diff
from contracts.capability_contract import Cloud, Environment, PullRequestRef, TagComplianceRequest, TagRef
from mcp_servers.servicenow.client import AccessRequestRef

FAKE_PR = PullRequestRef(number=8888, html_url="https://github.com/example/repo/pull/8888", branch="fake-branch")


@pytest.fixture(autouse=True)
def stub_servicenow(monkeypatch):
    """The real client is verified separately via a live call — unit tests
    must not create real ServiceNow tickets on every run."""

    def fake_create_access_request(short_description, description, justification):
        return AccessRequestRef(
            request_number="REQ8888888",
            request_sys_id="fake-request-sys-id",
            approval_sys_id="fake-approval-sys-id",
            approval_state="requested",
        )

    monkeypatch.setattr("agents.tag_remediation_agent.create_access_request", fake_create_access_request)


@pytest.fixture(autouse=True)
def stub_github(monkeypatch):
    """The real client is verified separately via a live call — unit tests
    must not open real PRs on every run."""

    def fake_open_pull_request(**kwargs):
        return FAKE_PR

    monkeypatch.setattr("agents.tag_remediation_agent.open_pull_request", fake_open_pull_request)


@pytest.fixture
def under_tagged_gcp_request(monkeypatch) -> TagComplianceRequest:
    monkeypatch.setattr("agents.tag_remediation_agent.read_gcp_labels", lambda resource_id: {})
    return TagComplianceRequest(
        requester="jane.doe@example.com",
        cloud=Cloud.GCP,
        environment=Environment.NONPROD,
        resource_id="aegis-demo-payments-bucket",
        cost_center="PMT-01",
        justification="quarterly tag compliance sweep for payments buckets",
    )


@pytest.fixture
def partially_tagged_azure_request(monkeypatch) -> TagComplianceRequest:
    monkeypatch.setattr(
        "agents.tag_remediation_agent.read_azure_tags",
        lambda resource_id: {"owner": "someone@example.com"},
    )
    return TagComplianceRequest(
        requester="jane.doe@example.com",
        cloud=Cloud.AZURE,
        environment=Environment.NONPROD,
        resource_id="payments-nonprod-rg",
        cost_center="PMT-01",
        justification="quarterly tag compliance sweep for payments resource groups",
    )


def approve(monkeypatch):
    monkeypatch.setattr("agents.tag_remediation_agent.get_approval_state", lambda approval_sys_id: "approved")


@pytest.mark.parametrize(
    "value, expected",
    [
        ("jane.doe@example.com", "jane-doe-example-com"),
        ("PMT-01", "pmt-01"),
        ("nonprod", "nonprod"),
        ("A" * 100, "a" * 63),
    ],
)
def test_gcp_label_safe_sanitizes_to_a_valid_label(value, expected):
    assert _gcp_label_safe(value) == expected


def test_under_tagged_gcp_resource_fails_closed_without_registered_iac_owner(under_tagged_gcp_request):
    with pytest.raises(UnsafeIaCOwnershipError) as exc_info:
        handle_tag_remediation(under_tagged_gcp_request)

    assert "owning Terraform state" in str(exc_info.value)
    assert "implicitly" in str(exc_info.value)


def test_partially_tagged_azure_resource_only_fills_the_gap(partially_tagged_azure_request):
    result = handle_tag_remediation(partially_tagged_azure_request)

    assert set(result.missing_keys) == {"cost-center", "environment"}
    proposed = {tag.key: tag.value for tag in result.proposed_tags}
    assert proposed["owner"] == "someone@example.com"  # preserved, not overwritten
    assert proposed["cost-center"] == "PMT-01"
    assert 'source              = "../../modules/azure/resource_tags"' in result.iac_diff


def test_fully_compliant_resource_raises_and_produces_no_diff(monkeypatch):
    monkeypatch.setattr(
        "agents.tag_remediation_agent.read_gcp_labels",
        lambda resource_id: {"owner": "x", "cost-center": "y", "environment": "nonprod"},
    )
    request = TagComplianceRequest(
        requester="jane.doe@example.com",
        cloud=Cloud.GCP,
        environment=Environment.NONPROD,
        resource_id="already-compliant-bucket",
        cost_center="PMT-01",
        justification="quarterly tag compliance sweep for payments buckets",
    )

    with pytest.raises(AlreadyCompliantError):
        handle_tag_remediation(request)


def test_guardrail_denial_is_surfaced_as_tag_remediation_denied(under_tagged_gcp_request):
    prod_request = under_tagged_gcp_request.model_copy(update={"environment": Environment.PROD})

    with pytest.raises(TagRemediationDenied) as exc_info:
        handle_tag_remediation(prod_request)

    assert "not remediated" in str(exc_info.value)


def test_advance_pipeline_does_not_raise_pr_while_pending(partially_tagged_azure_request, monkeypatch):
    result = handle_tag_remediation(partially_tagged_azure_request)
    monkeypatch.setattr("agents.tag_remediation_agent.get_approval_state", lambda approval_sys_id: "requested")

    advanced = advance_pipeline(result)

    assert advanced.pull_request is None


def test_advance_pipeline_raises_pr_once_approved(partially_tagged_azure_request, monkeypatch):
    result = handle_tag_remediation(partially_tagged_azure_request)
    approve(monkeypatch)

    advanced = advance_pipeline(result)

    assert advanced.pull_request == FAKE_PR


def test_advance_pipeline_never_raises_pr_when_rejected(partially_tagged_azure_request, monkeypatch):
    result = handle_tag_remediation(partially_tagged_azure_request)
    monkeypatch.setattr("agents.tag_remediation_agent.get_approval_state", lambda approval_sys_id: "rejected")

    with pytest.raises(ApprovalRejected):
        advance_pipeline(result)


def test_advance_pipeline_is_a_noop_once_pr_already_exists(partially_tagged_azure_request, monkeypatch):
    result = handle_tag_remediation(partially_tagged_azure_request)
    approve(monkeypatch)
    first = advance_pipeline(result)

    monkeypatch.setattr(
        "agents.tag_remediation_agent.open_pull_request",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("should not open a second PR")),
    )

    second = advance_pipeline(first)

    assert second.pull_request == FAKE_PR


def test_check_pull_request_status_polls_the_pr_number(partially_tagged_azure_request, monkeypatch):
    result = handle_tag_remediation(partially_tagged_azure_request)
    approve(monkeypatch)
    result = advance_pipeline(result)

    monkeypatch.setattr(
        "agents.tag_remediation_agent.get_pull_request_state",
        lambda pr_number: "merged" if pr_number == 8888 else "wrong-pr-used",
    )

    assert check_pull_request_status(result.pull_request) == "merged"


def test_tag_agent_exposes_no_merge_capability():
    import agents.tag_remediation_agent as agent
    import mcp_servers.github.client as github

    assert not hasattr(agent, "merge_tag_remediation")
    assert not hasattr(github, "merge_pull_request")


def test_tag_hcl_escapes_resource_names_keys_and_values(partially_tagged_azure_request):
    request = partially_tagged_azure_request.model_copy(update={"resource_id": 'rg"\nresource "x" "y" {}'})
    rendered = render_tag_remediation_diff(
        request,
        [TagRef(key='owner"\nkey', value='value"\nresource "x" "z" {}')],
    )

    assert '\\nresource \\"x\\"' in rendered
    assert '\nresource "x"' not in rendered
