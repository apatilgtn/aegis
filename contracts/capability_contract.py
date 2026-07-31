"""
Cloud-neutral capability contract for Aegis.

Capability agents only ever produce/consume these models. Nothing downstream
(MCP tool servers, Terraform generation, ServiceNow) is allowed to accept a
free-form dict instead — the contract is the cloud-neutrality boundary.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class Cloud(str, Enum):
    AZURE = "azure"
    GCP = "gcp"


class Environment(str, Enum):
    PROD = "prod"
    NONPROD = "nonprod"


class Tier(str, Enum):
    TIER1 = "tier1"
    TIER2 = "tier2"
    TIER3 = "tier3"


class PolicyDecision(BaseModel):
    """Output of the guardrail agent's call into OPA. Never produced by the LLM directly."""

    allowed: bool
    policy_ids: list[str] = Field(description="Rego rule IDs that were evaluated")
    reasons: list[str] = Field(default_factory=list, description="Human-readable deny reasons, empty if allowed")


class EntitlementRef(BaseModel):
    """A resolved, catalog-backed entitlement — never a synthesized/guessed name."""

    cloud: Cloud
    resource_type: Literal["entra_group", "iam_binding"]
    resource_id: str = Field(description="Exact Entra group object ID or GCP IAM binding role/member string")
    catalog_entry_id: str = Field(description="Foreign key into the entitlement catalog, for audit traceability")
    display_name: str = Field(description="Human-readable label from the catalog, for consumer-facing UI")


class PullRequestRef(BaseModel):
    """The Git PR carrying the proposed Terraform diff — never merged by the agent itself."""

    number: int
    html_url: str
    branch: str


class ApprovalRef(BaseModel):
    """The ITSM approval record raised for a proposed change — never the change itself.

    `approval_sys_id` (not `request_sys_id`) is what status checks must poll:
    the request's own rollup approval field was found to report stale values
    on ServiceNow instances without a workflow attached, so the explicit
    approval task is the only reliable source of truth (see
    mcp_servers/servicenow/client.py).
    """

    system: Literal["servicenow"] = "servicenow"
    reference: str = Field(description="Human-facing request number, e.g. REQ0010004")
    request_sys_id: str = Field(description="Internal ID of the Service Catalog Request record")
    approval_sys_id: str = Field(description="Internal ID of the approval task — poll this for status")
    state: str = Field(description="Approval task state at creation time, e.g. 'requested'")


# ---- Capability: access_grant ---------------------------------------------------

class AccessGrantRequest(BaseModel):
    capability: Literal["access_grant"] = "access_grant"
    requester: str = Field(description="Identity of the human who asked the agent, e.g. UPN or email")
    business_unit: str
    environment: Environment
    tier: Tier
    justification: str = Field(min_length=10)
    ttl_hours: int = Field(gt=0, le=168)


class AccessGrantResult(BaseModel):
    capability: Literal["access_grant"] = "access_grant"
    entitlement: EntitlementRef
    iac_diff: str = Field(description="Terraform HCL diff — not opened as a PR until approved")
    branch_name: str = Field(description="Branch the PR will be opened on, once approved")
    file_path: str = Field(description="Repo path the diff will be committed to, once approved")
    pr_title: str
    pr_body: str
    policy_decision: PolicyDecision
    approval: ApprovalRef
    pull_request: PullRequestRef | None = Field(
        default=None, description="Set only after ServiceNow approval — never exists before then"
    )
    expires_at: datetime


CapabilityRequest = AccessGrantRequest
CapabilityResult = AccessGrantResult
