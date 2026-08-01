from pathlib import Path

import pytest

from agents.access_grant_agent import (
    AccessGrantDenied,
    AlreadyGrantedError,
    ApprovalRejected,
    MergeNotAllowed,
    advance_pipeline,
    check_approval_status,
    check_pull_request_status,
    handle_access_grant,
    merge_access_grant,
)
from contracts.capability_contract import AccessGrantRequest, Cloud, Environment, PullRequestRef, Tier
from contracts.entitlement_catalog import EntitlementCatalog, EntitlementNotFoundError
from mcp_servers.azure_entra.directory_service import PrincipalNotFoundError
from mcp_servers.gcp_resource_manager.project_service import resolve_project
from mcp_servers.servicenow.client import AccessRequestRef

CATALOG_PATH = Path(__file__).parents[2] / "data" / "entitlement_catalog.yaml"

FAKE_PR = PullRequestRef(number=9999, html_url="https://github.com/example/repo/pull/9999", branch="fake-branch")


@pytest.fixture(autouse=True)
def stub_servicenow(monkeypatch):
    """The real client is verified separately via a live call (see
    mcp_servers/servicenow/client.py) — unit tests must not create real
    ServiceNow tickets on every run."""

    def fake_create_access_request(short_description, description, justification):
        return AccessRequestRef(
            request_number="REQ9999999",
            request_sys_id="fake-request-sys-id",
            approval_sys_id="fake-approval-sys-id",
            approval_state="requested",
        )

    monkeypatch.setattr("agents.access_grant_agent.create_access_request", fake_create_access_request)


@pytest.fixture(autouse=True)
def stub_github(monkeypatch):
    """The real client is verified separately via a live call (see
    mcp_servers/github/client.py) — unit tests must not open real PRs on
    every run."""

    def fake_open_pull_request(**kwargs):
        return FAKE_PR

    monkeypatch.setattr("agents.access_grant_agent.open_pull_request", fake_open_pull_request)


@pytest.fixture(scope="module")
def catalog() -> EntitlementCatalog:
    return EntitlementCatalog.load(CATALOG_PATH)


@pytest.fixture
def valid_request() -> AccessGrantRequest:
    return AccessGrantRequest(
        requester="jane.doe@example.com",
        business_unit="payments",
        environment=Environment.NONPROD,
        tier=Tier.TIER2,
        justification="debugging a payments reconciliation ticket",
        ttl_hours=8,
    )


def approve(monkeypatch):
    monkeypatch.setattr("agents.access_grant_agent.get_approval_state", lambda approval_sys_id: "approved")


def test_allowed_request_produces_azure_diff_with_no_pr_yet(catalog, valid_request):
    result = handle_access_grant(valid_request, Cloud.AZURE, catalog)

    assert result.policy_decision.allowed is True
    assert result.entitlement.cloud == Cloud.AZURE
    assert result.entitlement.catalog_entry_id == "azure-payments-nonprod-tier2"
    assert result.pull_request is None  # not raised until approved
    assert "azuread" not in result.iac_diff  # module call site, not the module internals
    assert 'source           = "../../modules/azure/entra_group_membership"' in result.iac_diff
    assert "jane.doe@example.com" in result.iac_diff  # header metadata
    assert "11111111-1111-1111-1111-111111111111" in result.iac_diff  # resolved object ID, not the UPN


def test_allowed_request_produces_gcp_diff_with_no_pr_yet(catalog, valid_request):
    result = handle_access_grant(valid_request, Cloud.GCP, catalog)

    assert result.entitlement.cloud == Cloud.GCP
    assert result.entitlement.catalog_entry_id == "gcp-payments-nonprod-tier2"
    assert result.pull_request is None
    assert "roles/editor" in result.iac_diff
    assert resolve_project("payments") in result.iac_diff  # resolved GCP project


def test_azure_request_for_unknown_principal_fails_closed(catalog, valid_request):
    unknown_requester_request = valid_request.model_copy(update={"requester": "ghost@example.com"})

    with pytest.raises(PrincipalNotFoundError):
        handle_access_grant(unknown_requester_request, Cloud.AZURE, catalog)


def test_azure_request_for_existing_member_is_a_noop(catalog, valid_request):
    # tier1 catalog entry's mock group already has this principal as a member.
    existing_member_request = valid_request.model_copy(
        update={"requester": "anand.patil2@cognizant.com", "tier": Tier.TIER1}
    )

    with pytest.raises(AlreadyGrantedError):
        handle_access_grant(existing_member_request, Cloud.AZURE, catalog)


def test_denied_request_raises_and_produces_no_diff(catalog, valid_request):
    prod_request = valid_request.model_copy(update={"environment": Environment.PROD})

    # No prod catalog entry exists at all, so this should fail closed at
    # resolution — before the guardrail is even consulted.
    with pytest.raises(EntitlementNotFoundError):
        handle_access_grant(prod_request, Cloud.AZURE, catalog)


def test_guardrail_denial_is_surfaced_as_access_grant_denied(catalog, valid_request):
    over_ttl_request = valid_request.model_copy(update={"ttl_hours": 100})

    with pytest.raises(AccessGrantDenied) as exc_info:
        handle_access_grant(over_ttl_request, Cloud.AZURE, catalog)

    assert "longer than the maximum allowed" in str(exc_info.value)


def test_check_approval_status_polls_the_approval_task_not_the_request(catalog, valid_request, monkeypatch):
    result = handle_access_grant(valid_request, Cloud.AZURE, catalog)

    monkeypatch.setattr(
        "agents.access_grant_agent.get_approval_state",
        lambda approval_sys_id: "approved" if approval_sys_id == "fake-approval-sys-id" else "wrong-id-used",
    )

    assert check_approval_status(result.approval) == "approved"


def test_advance_pipeline_does_not_raise_pr_while_pending(catalog, valid_request, monkeypatch):
    result = handle_access_grant(valid_request, Cloud.AZURE, catalog)
    monkeypatch.setattr("agents.access_grant_agent.get_approval_state", lambda approval_sys_id: "requested")

    advanced = advance_pipeline(result)

    assert advanced.pull_request is None


def test_advance_pipeline_raises_pr_once_approved(catalog, valid_request, monkeypatch):
    result = handle_access_grant(valid_request, Cloud.AZURE, catalog)
    approve(monkeypatch)

    advanced = advance_pipeline(result)

    assert advanced.pull_request == FAKE_PR


def test_advance_pipeline_never_raises_pr_when_rejected(catalog, valid_request, monkeypatch):
    result = handle_access_grant(valid_request, Cloud.AZURE, catalog)
    monkeypatch.setattr("agents.access_grant_agent.get_approval_state", lambda approval_sys_id: "rejected")

    with pytest.raises(ApprovalRejected):
        advance_pipeline(result)


def test_advance_pipeline_is_a_noop_once_pr_already_exists(catalog, valid_request, monkeypatch):
    result = handle_access_grant(valid_request, Cloud.AZURE, catalog)
    approve(monkeypatch)
    first = advance_pipeline(result)

    monkeypatch.setattr(
        "agents.access_grant_agent.open_pull_request",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("should not open a second PR")),
    )

    second = advance_pipeline(first)

    assert second.pull_request == FAKE_PR


def test_check_pull_request_status_polls_the_pr_number(catalog, valid_request, monkeypatch):
    result = handle_access_grant(valid_request, Cloud.AZURE, catalog)
    approve(monkeypatch)
    result = advance_pipeline(result)

    monkeypatch.setattr(
        "agents.access_grant_agent.get_pull_request_state",
        lambda pr_number: "merged" if pr_number == 9999 else "wrong-pr-used",
    )

    assert check_pull_request_status(result.pull_request) == "merged"


def test_merge_access_grant_refuses_when_not_approved(catalog, valid_request, monkeypatch):
    result = handle_access_grant(valid_request, Cloud.AZURE, catalog)
    approve(monkeypatch)
    result = advance_pipeline(result)

    monkeypatch.setattr("agents.access_grant_agent.get_approval_state", lambda approval_sys_id: "requested")

    with pytest.raises(MergeNotAllowed):
        merge_access_grant(result.approval, result.pull_request)


def test_merge_access_grant_merges_when_approved(catalog, valid_request, monkeypatch):
    result = handle_access_grant(valid_request, Cloud.AZURE, catalog)
    approve(monkeypatch)
    result = advance_pipeline(result)

    monkeypatch.setattr(
        "agents.access_grant_agent.merge_pull_request",
        lambda pr_number: "abc123sha" if pr_number == 9999 else "wrong-pr-used",
    )

    assert merge_access_grant(result.approval, result.pull_request) == "abc123sha"
