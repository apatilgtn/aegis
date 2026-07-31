"""
MCP tool server fronting ServiceNow — Layer 4 of the Aegis architecture
(the governed change path). See client.py for why this server legitimately
writes, unlike the read-only Layer 3 tool servers.
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from mcp_servers.servicenow.client import (
    ServiceNowConfigError,
    ServiceNowRequestError,
    create_change_request,
    get_change_request_state,
)

mcp = FastMCP("aegis-servicenow")


@mcp.tool()
def raise_change_request(short_description: str, description: str, justification: str) -> dict:
    """Raise a ServiceNow change request as the approval ask for a proposed access grant."""
    try:
        ref = create_change_request(short_description, description, justification)
    except (ServiceNowConfigError, ServiceNowRequestError) as exc:
        raise ValueError(str(exc)) from exc
    return ref.model_dump()


@mcp.tool()
def check_change_request_state(sys_id: str) -> str:
    """Check whether a previously raised change request is still pending, approved, or rejected."""
    try:
        return get_change_request_state(sys_id)
    except (ServiceNowConfigError, ServiceNowRequestError) as exc:
        raise ValueError(str(exc)) from exc


if __name__ == "__main__":
    mcp.run()
