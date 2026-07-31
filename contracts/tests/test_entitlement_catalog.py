from pathlib import Path

import pytest

from contracts.capability_contract import Cloud, Environment, Tier
from contracts.entitlement_catalog import EntitlementCatalog, EntitlementNotFoundError

CATALOG_PATH = Path(__file__).parents[2] / "data" / "entitlement_catalog.yaml"


@pytest.fixture(scope="module")
def catalog() -> EntitlementCatalog:
    return EntitlementCatalog.load(CATALOG_PATH)


def test_resolves_azure_and_gcp_from_the_same_request(catalog):
    azure_entry = catalog.resolve("payments", Environment.NONPROD, Tier.TIER2, Cloud.AZURE)
    gcp_entry = catalog.resolve("payments", Environment.NONPROD, Tier.TIER2, Cloud.GCP)

    assert azure_entry.resource_type == "entra_group"
    assert gcp_entry.resource_type == "iam_binding"
    assert azure_entry.max_ttl_hours == gcp_entry.max_ttl_hours == 24


def test_unregistered_combination_fails_closed_instead_of_guessing(catalog):
    with pytest.raises(EntitlementNotFoundError):
        catalog.resolve("payments", Environment.PROD, Tier.TIER2, Cloud.AZURE)


def test_to_entitlement_ref_carries_catalog_id_for_audit(catalog):
    entry = catalog.resolve("payments", Environment.NONPROD, Tier.TIER1, Cloud.GCP)
    ref = catalog.to_entitlement_ref(entry)

    assert ref.catalog_entry_id == "gcp-payments-nonprod-tier1"
    assert ref.resource_id == entry.resource_id
