"""
Rule-based intent parser — a stand-in for the real LLM-driven orchestrator
described in the solution doc (§5: orchestrator agent interprets free-text
intent). Wiring an actual LLM here only needs an API key; this parser exists
so the rest of the pipeline (guardrail -> MCP resolution -> Terraform diff)
can be demoed end-to-end without one.

It deliberately refuses to guess: if a required field (business unit, tier,
environment) can't be identified with confidence, it raises rather than
defaulting to something that might not match what the user meant — the same
fail-closed posture as the entitlement catalog and guardrail policy.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from contracts.capability_contract import Environment, Tier

_TIER_PATTERN = re.compile(r"tier[\s-]?([123])", re.IGNORECASE)
_TTL_PATTERN = re.compile(r"(\d+)\s*(?:hours?|hrs?|h)\b", re.IGNORECASE)
_DEFAULT_TTL_HOURS = 8


class IntentParseError(Exception):
    pass


@dataclass
class ParsedIntent:
    business_unit: str
    environment: Environment
    tier: Tier
    justification: str
    ttl_hours: int


def parse_intent(text: str, known_business_units: set[str]) -> ParsedIntent:
    lowered = text.lower()

    business_unit = next((bu for bu in known_business_units if bu.lower() in lowered), None)
    if business_unit is None:
        raise IntentParseError(
            "couldn't identify a known business unit in your request "
            f"(known: {', '.join(sorted(known_business_units))})"
        )

    tier_match = _TIER_PATTERN.search(lowered)
    if not tier_match:
        raise IntentParseError("couldn't identify a tier (e.g. 'Tier 2') in your request")
    tier = Tier(f"tier{tier_match.group(1)}")

    if "non-prod" in lowered or "nonprod" in lowered or "non prod" in lowered:
        environment = Environment.NONPROD
    elif "prod" in lowered:
        environment = Environment.PROD
    else:
        raise IntentParseError("couldn't identify an environment ('prod' or 'non-prod') in your request")

    ttl_match = _TTL_PATTERN.search(lowered)
    ttl_hours = int(ttl_match.group(1)) if ttl_match else _DEFAULT_TTL_HOURS

    justification = text.strip()
    if len(justification) < 10:
        raise IntentParseError("please include a brief justification (at least 10 characters)")

    return ParsedIntent(business_unit, environment, tier, justification, ttl_hours)
