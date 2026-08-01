"""
Mocked Azure resource tag lookup — stands in for a live Azure Resource
Graph / Management API call (GET .../resourceGroups/{name}?$expand=tags)
since this environment has no live Azure tenant credentials. Real
deployment swaps this module's internals for that API call behind the same
function; callers don't change.

Read-only, matching the propose-don't-mutate design.
"""

from __future__ import annotations

_MOCK_CURRENT_TAGS: dict[str, dict[str, str]] = {
    "payments-nonprod-rg": {"owner": "payments-team@example.com"},  # missing cost-center, environment
}


def current_tags(resource_id: str) -> dict[str, str]:
    return dict(_MOCK_CURRENT_TAGS.get(resource_id, {}))
