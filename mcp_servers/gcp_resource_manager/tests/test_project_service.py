import pytest

from mcp_servers.gcp_resource_manager.project_service import ProjectNotFoundError, resolve_project


def test_resolves_a_known_business_unit():
    # Value comes from GCP_PROJECT_PAYMENTS_NONPROD in .env — just check it
    # resolves to *something* non-empty rather than pinning the exact
    # project ID, so this test doesn't churn every time the mapped project changes.
    assert resolve_project("payments")


def test_unknown_business_unit_fails_closed():
    with pytest.raises(ProjectNotFoundError):
        resolve_project("unknown-bu")
