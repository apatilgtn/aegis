"""
Mocked Entra ID directory — stands in for live Microsoft Graph calls
(GET /users/{upn}, GET /groups/{id}/members) since this environment has no
live Azure tenant credentials. Real deployment swaps this module's internals
for Graph API calls behind the same two functions; callers (the MCP tool
layer and the capability agent) don't change.

Read-only, matching the propose-don't-mutate design — there is no
add-member function here.
"""

from __future__ import annotations


class PrincipalNotFoundError(Exception):
    def __init__(self, upn: str):
        super().__init__(f"no Entra ID principal found for {upn!r}")


_MOCK_PRINCIPALS = {
    "jane.doe@example.com": "11111111-1111-1111-1111-111111111111",
    "anand.patil2@cognizant.com": "22222222-2222-2222-2222-222222222222",
}

_MOCK_GROUP_MEMBERS: dict[str, set[str]] = {
    "00000000-0000-0000-0000-000000000101": set(),  # payments-nonprod-tier2, empty
    "00000000-0000-0000-0000-000000000102": {"22222222-2222-2222-2222-222222222222"},  # tier1
}


def resolve_principal(upn: str) -> str:
    try:
        return _MOCK_PRINCIPALS[upn]
    except KeyError:
        raise PrincipalNotFoundError(upn) from None


def is_group_member(group_object_id: str, principal_object_id: str) -> bool:
    return principal_object_id in _MOCK_GROUP_MEMBERS.get(group_object_id, set())
