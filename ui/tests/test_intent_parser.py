import pytest

from contracts.capability_contract import Environment, Tier
from ui.intent_parser import IntentParseError, parse_intent

KNOWN_BUSINESS_UNITS = {"payments"}


def test_parses_the_canonical_demo_phrase():
    parsed = parse_intent(
        "give me temporary Tier-2 access to payments non-prod for a debugging task",
        KNOWN_BUSINESS_UNITS,
    )

    assert parsed.business_unit == "payments"
    assert parsed.environment == Environment.NONPROD
    assert parsed.tier == Tier.TIER2
    assert parsed.ttl_hours == 8  # default, no explicit duration mentioned


def test_parses_an_explicit_ttl():
    parsed = parse_intent(
        "tier 1 access to payments nonprod for 24 hours, reviewing configs",
        KNOWN_BUSINESS_UNITS,
    )

    assert parsed.ttl_hours == 24
    assert parsed.tier == Tier.TIER1


def test_rejects_unknown_business_unit():
    with pytest.raises(IntentParseError):
        parse_intent("tier 2 access to marketing non-prod for a campaign review", KNOWN_BUSINESS_UNITS)


def test_rejects_missing_tier():
    with pytest.raises(IntentParseError):
        parse_intent("access to payments non-prod for a debugging task", KNOWN_BUSINESS_UNITS)


def test_rejects_missing_environment():
    with pytest.raises(IntentParseError):
        parse_intent("tier 2 access to payments for a debugging task", KNOWN_BUSINESS_UNITS)
