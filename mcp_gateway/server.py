"""Aegis MCP gateway: one governed, read-only entry point to GCP tools.

Runs on Cloud Run with IAM-only access (no public invoker), as the
read-only aegis-reader identity. MCP-capable clients (Claude Code, IDEs,
other agents) connect over streamable HTTP. Every tool only reads; changes
still go through Aegis requests, ServiceNow approval and a merged PR.
Each call is logged as structured JSON, which Cloud Logging keeps.
"""

from __future__ import annotations

import json
import os
import sys
import time
from typing import Any

from mcp.server.fastmcp import FastMCP

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gcp  # noqa: E402  (copied next to this file in the image)

PROJECT_ID = os.environ.get("GCP_PROJECT_ID", "")
BILLING_TABLE = os.environ.get("BILLING_TABLE", "")

mcp = FastMCP("aegis-gcp", host="0.0.0.0", port=int(os.environ.get("PORT", "8080")), stateless_http=True)


def audited(tool: str, args: dict[str, Any], call):
    started = time.monotonic()
    outcome = "ok"
    try:
        return call()
    except gcp.GcpError as exc:
        outcome = "error"
        raise ValueError(str(exc)) from exc
    finally:
        print(json.dumps({"severity": "NOTICE", "message": "aegis_mcp_tool_call", "tool": tool, "args": args,
                          "outcome": outcome, "ms": round((time.monotonic() - started) * 1000)}), flush=True)


@mcp.tool()
def search_logs(resource_hint: str, time_window_hours: int = 24, min_severity: str = "WARNING") -> dict[str, Any]:
    """Search recent Cloud Logging entries (WARNING and above by default) mentioning a service or resource."""
    def run():
        findings = gcp.search_logs(PROJECT_ID, resource_hint, time_window_hours, min_severity=min_severity.upper())
        return {"summary": gcp.summarize_findings(findings, resource_hint, time_window_hours), "findings": findings[:20]}
    return audited("search_logs", {"resource_hint": resource_hint, "time_window_hours": time_window_hours}, run)


@mcp.tool()
def bucket_label_compliance() -> dict[str, Any]:
    """List the project's buckets and which required labels (owner, cost-center, environment) each is missing."""
    def run():
        buckets = gcp.list_buckets(PROJECT_ID)
        return {"required": list(gcp.REQUIRED_LABELS),
                "buckets": [{"bucket": b["name"], "missing": gcp.missing_labels(b["labels"])} for b in buckets]}
    return audited("bucket_label_compliance", {}, run)


@mcp.tool()
def cost_by_cost_center(month: str) -> dict[str, Any]:
    """Net cost for a month (YYYYMM) grouped by the cost-center label; null means untagged spend."""
    def run():
        if not BILLING_TABLE:
            raise gcp.GcpError("Billing export is not connected for this project", retryable=False)
        return {"rows": gcp.cost_by_cost_center(PROJECT_ID, BILLING_TABLE, month)}
    return audited("cost_by_cost_center", {"month": month}, run)


if __name__ == "__main__":
    if not PROJECT_ID:
        raise SystemExit("GCP_PROJECT_ID is required")
    mcp.run(transport="streamable-http")
