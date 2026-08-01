from mcp_servers.azure_monitor.log_lookup import search_logs


def test_known_hint_returns_canned_findings():
    findings = search_logs("payments-api", 24)
    assert len(findings) == 4
    assert any(f.severity == "ERROR" for f in findings)


def test_unknown_hint_returns_empty_not_an_error():
    assert search_logs("nothing-like-this-exists", 24) == []
