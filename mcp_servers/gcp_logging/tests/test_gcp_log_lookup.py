from __future__ import annotations

import pytest

from mcp_servers.gcp_logging import log_lookup


class _FakeClient:
    def __init__(self) -> None:
        self.request = None

    def list_log_entries(self, *, request):
        self.request = request
        return []


@pytest.mark.parametrize("hint", ["", "   ", "bad\nclause", "x" * 256])
def test_search_logs_rejects_invalid_resource_hints(monkeypatch, hint):
    monkeypatch.setattr(log_lookup, "_client", lambda: pytest.fail("client must not be created"))
    with pytest.raises(ValueError, match="resource_hint"):
        log_lookup.search_logs(hint, 1)


@pytest.mark.parametrize("hours", [True, 0, 169, 1.5])
def test_search_logs_rejects_invalid_time_window(monkeypatch, hours):
    monkeypatch.setattr(log_lookup, "_client", lambda: pytest.fail("client must not be created"))
    with pytest.raises(ValueError, match="time_window_hours"):
        log_lookup.search_logs("checkout-api", hours)


def test_search_logs_rejects_unrecognised_severity(monkeypatch):
    monkeypatch.setattr(log_lookup, "_client", lambda: pytest.fail("client must not be created"))
    with pytest.raises(ValueError, match="min_severity"):
        log_lookup.search_logs("checkout-api", 1, "WARNING OR severity>=DEFAULT")


def test_search_logs_escapes_filter_string(monkeypatch):
    client = _FakeClient()
    monkeypatch.setattr(log_lookup, "_client", lambda: client)

    assert log_lookup.search_logs('checkout" OR severity>=DEFAULT OR textPayload:"', 1) == []

    generated_filter = client.request["filter"]
    assert '\\" OR severity>=DEFAULT' in generated_filter
    assert 'textPayload:"checkout" OR severity>=DEFAULT' not in generated_filter


