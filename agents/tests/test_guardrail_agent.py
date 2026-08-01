from pathlib import Path

import pytest

from agents.guardrail_agent import evaluate_access_grant, evaluate_tag_remediation
from contracts.capability_contract import AccessGrantRequest, Cloud, Environment, TagComplianceRequest, Tier
from contracts.entitlement_catalog import EntitlementCatalog

CATALOG_PATH = Path(__file__).parents[2] / "data" / "entitlement_catalog.yaml"


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


def test_allows_a_valid_request(catalog, valid_request):
    entry = catalog.resolve("payments", Environment.NONPROD, Tier.TIER2, Cloud.AZURE)

    decision = evaluate_access_grant(valid_request, entry)

    assert decision.allowed is True
    assert decision.reasons == []
    assert "aegis.access_grant" in decision.policy_ids


def test_denies_ttl_beyond_catalog_max(catalog, valid_request):
    entry = catalog.resolve("payments", Environment.NONPROD, Tier.TIER2, Cloud.AZURE)
    over_ttl_request = valid_request.model_copy(update={"ttl_hours": entry.max_ttl_hours + 1})

    decision = evaluate_access_grant(over_ttl_request, entry)

    assert decision.allowed is False
    assert any("longer than the maximum allowed" in reason for reason in decision.reasons)


def test_same_request_evaluates_consistently_for_azure_and_gcp(catalog, valid_request):
    azure_entry = catalog.resolve("payments", Environment.NONPROD, Tier.TIER2, Cloud.AZURE)
    gcp_entry = catalog.resolve("payments", Environment.NONPROD, Tier.TIER2, Cloud.GCP)

    azure_decision = evaluate_access_grant(valid_request, azure_entry)
    gcp_decision = evaluate_access_grant(valid_request, gcp_entry)

    assert azure_decision.allowed is True
    assert gcp_decision.allowed is True


@pytest.fixture
def valid_tag_request() -> TagComplianceRequest:
    return TagComplianceRequest(
        requester="jane.doe@example.com",
        cloud=Cloud.GCP,
        environment=Environment.NONPROD,
        resource_id="aegis-demo-payments-bucket",
        cost_center="PMT-01",
        justification="quarterly tag compliance sweep for payments buckets",
    )


def test_evaluate_tag_remediation_allows_a_valid_request(valid_tag_request):
    decision = evaluate_tag_remediation(valid_tag_request)

    assert decision.allowed is True
    assert decision.reasons == []
    assert "aegis.tag_remediation" in decision.policy_ids


def test_evaluate_tag_remediation_denies_prod_environment(valid_tag_request):
    prod_request = valid_tag_request.model_copy(update={"environment": Environment.PROD})

    decision = evaluate_tag_remediation(prod_request)

    assert decision.allowed is False
    assert any("not remediated" in reason for reason in decision.reasons)


def test_evaluate_tag_remediation_denies_short_cost_center(valid_tag_request):
    short_cost_center_request = valid_tag_request.model_copy(update={"cost_center": "x"})

    decision = evaluate_tag_remediation(short_cost_center_request)

    assert decision.allowed is False
    assert any("cost_center" in reason for reason in decision.reasons)
