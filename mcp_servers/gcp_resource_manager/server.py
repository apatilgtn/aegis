"""
MCP tool server fronting GCP Resource Manager (mocked) — Layer 3 of the
Aegis architecture. Read-only: resolves a business unit to its GCP project.
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from mcp_servers.gcp_resource_manager.project_service import (
    ProjectNotFoundError,
    resolve_project,
)

mcp = FastMCP("aegis-gcp-resource-manager")


@mcp.tool()
def resolve_project_id(business_unit: str) -> str:
    """Resolve a business unit to its GCP project ID."""
    try:
        return resolve_project(business_unit)
    except ProjectNotFoundError as exc:
        raise ValueError(str(exc)) from exc


if __name__ == "__main__":
    mcp.run()
