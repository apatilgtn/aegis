"""
The access_check capability: "what access do I currently have" — read-only,
reads live identity state via the same MCP tool servers the access_grant
capability uses, and reverse-maps whatever it finds back to catalog entries
so the answer is in terms of "Tier 2 Access", not a raw role string.
"""

from __future__ import annotations

from contracts.capability_contract import Cloud
from contracts.entitlement_catalog import EntitlementCatalog, EntitlementCatalogEntry
from mcp_servers.azure_entra.directory_service import PrincipalNotFoundError, is_group_member, resolve_principal
from mcp_servers.gcp_resource_manager.iam_lookup import list_current_roles


def check_current_access(email: str, cloud: Cloud, catalog: EntitlementCatalog) -> list[EntitlementCatalogEntry]:
    entries = [e for e in catalog.all_entries() if e.cloud == cloud]

    if cloud == Cloud.GCP:
        current_roles = set(list_current_roles(email))
        return [e for e in entries if e.resource_id in current_roles]

    # cloud == Cloud.AZURE
    try:
        object_id = resolve_principal(email)
    except PrincipalNotFoundError:
        return []
    return [e for e in entries if is_group_member(e.resource_id, object_id)]
