from mcp_servers.azure_resource_tags.resource_tags import current_tags


def test_known_resource_returns_canned_tags():
    tags = current_tags("payments-nonprod-rg")
    assert tags == {"owner": "payments-team@example.com"}


def test_unknown_resource_returns_empty_not_an_error():
    assert current_tags("nothing-like-this-exists") == {}
