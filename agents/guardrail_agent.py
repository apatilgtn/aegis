"""
Guardrail agent: the deterministic allow/deny gate for capability requests.

This module calls the Rego policy directly via regopy (Regorus). The LLM
orchestrator calls `evaluate_access_grant` and may only *explain* the
resulting PolicyDecision to the user — it never overrides `allowed`.

Note: the reference design (see solution doc) calls for the real `opa` CLI /
server. `regopy` is a drop-in substitute used here because this dev sandbox's
network policy blocks the `opa` binary download. Both evaluate the same
policy/access_grant.rego file; swapping the engine later requires no change
to this module's public interface.
"""

from __future__ import annotations

import json
from pathlib import Path

import regopy

from contracts.capability_contract import AccessGrantRequest, PolicyDecision
from contracts.entitlement_catalog import EntitlementCatalogEntry

_POLICY_PATH = Path(__file__).parents[1] / "policy" / "access_grant.rego"
_POLICY_ID = "aegis.access_grant"


def evaluate_access_grant(
    request: AccessGrantRequest, catalog_entry: EntitlementCatalogEntry
) -> PolicyDecision:
    rego = regopy.Interpreter()
    rego.add_module("access_grant", _POLICY_PATH.read_text())
    rego.set_input(
        {
            "request": {
                "environment": request.environment.value,
                "tier": request.tier.value,
                "justification": request.justification,
                "ttl_hours": request.ttl_hours,
            },
            "catalog_entry": {
                "id": catalog_entry.id,
                "environment": catalog_entry.environment.value,
                "max_ttl_hours": catalog_entry.max_ttl_hours,
            },
        }
    )
    result = json.loads(str(rego.query(f"data.{_POLICY_ID}")))["expressions"][0]

    return PolicyDecision(
        allowed=result["allow"],
        policy_ids=[_POLICY_ID],
        reasons=result["deny"],
    )
