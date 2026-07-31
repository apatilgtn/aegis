"""
MCP tool server fronting Entra ID (mocked) — Layer 3 of the Aegis
architecture. Exposes only read tools: resolving a principal and checking
group membership. There is no write tool here, by design — mutation only
ever happens through the governed change path (Terraform PR + ServiceNow +
CI/CD apply), never directly from an MCP tool call.
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from mcp_servers.azure_entra.directory_service import (
    PrincipalNotFoundError,
    is_group_member,
    resolve_principal,
)

mcp = FastMCP("aegis-azure-entra")


@mcp.tool()
def resolve_principal_object_id(upn: str) -> str:
    """Resolve a user principal name (email) to its Entra ID object ID."""
    try:
        return resolve_principal(upn)
    except PrincipalNotFoundError as exc:
        raise ValueError(str(exc)) from exc


@mcp.tool()
def check_group_membership(group_object_id: str, principal_object_id: str) -> bool:
    """Check whether a principal is already a member of an Entra ID group."""
    return is_group_member(group_object_id, principal_object_id)


if __name__ == "__main__":
    mcp.run()
