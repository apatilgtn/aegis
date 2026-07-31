from pathlib import Path

import pytest

from agents.guardrail_agent import evaluate_access_grant
from contracts.capability_contract import AccessGrantRequest, Cloud, Environment, Tier
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
