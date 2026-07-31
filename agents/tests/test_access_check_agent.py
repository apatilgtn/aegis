from pathlib import Path

import pytest

from agents.access_check_agent import check_current_access
from contracts.capability_contract import Cloud
from contracts.entitlement_catalog import EntitlementCatalog

CATALOG_PATH = Path(__file__).parents[2] / "data" / "entitlement_catalog.yaml"


@pytest.fixture(scope="module")
def catalog() -> EntitlementCatalog:
    return EntitlementCatalog.load(CATALOG_PATH)


def test_gcp_existing_access_is_reverse_mapped_to_catalog_entry(catalog, monkeypatch):
    monkeypatch.setattr("agents.access_check_agent.list_current_roles", lambda email: ["roles/viewer"])

    result = check_current_access("someone@example.com", Cloud.GCP, catalog)

    assert [e.id for e in result] == ["gcp-payments-nonprod-tier1"]


def test_gcp_no_current_access_returns_empty(catalog, monkeypatch):
    monkeypatch.setattr("agents.access_check_agent.list_current_roles", lambda email: [])

    assert check_current_access("nobody@example.com", Cloud.GCP, catalog) == []


def test_azure_existing_access_is_reverse_mapped_to_catalog_entry(catalog):
    # anand.patil2@cognizant.com is a mock member of the tier1 Azure group.
    result = check_current_access("anand.patil2@cognizant.com", Cloud.AZURE, catalog)

    assert [e.id for e in result] == ["azure-payments-nonprod-tier1"]


def test_azure_unknown_principal_returns_empty_not_an_error(catalog):
    assert check_current_access("ghost@example.com", Cloud.AZURE, catalog) == []
