import pytest

from mcp_servers.azure_entra.directory_service import (
    PrincipalNotFoundError,
    is_group_member,
    resolve_principal,
)


def test_resolves_a_known_principal():
    assert resolve_principal("jane.doe@example.com") == "11111111-1111-1111-1111-111111111111"


def test_unknown_principal_fails_closed():
    with pytest.raises(PrincipalNotFoundError):
        resolve_principal("nobody@example.com")


def test_membership_check_reflects_mock_state():
    tier1_group = "00000000-0000-0000-0000-000000000102"
    assert is_group_member(tier1_group, "22222222-2222-2222-2222-222222222222") is True
    assert is_group_member(tier1_group, "11111111-1111-1111-1111-111111111111") is False
