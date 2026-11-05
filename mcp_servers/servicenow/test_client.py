from __future__ import annotations

import pytest

import mcp_servers.servicenow.client as client


class FakeResponse:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload
        self.text = str(payload)

    def json(self) -> dict:
        return self._payload


@pytest.fixture(autouse=True)
def servicenow_env(monkeypatch):
    values = {
        "SN_INSTANCE_URL": "https://example.service-now.com",
        "SN_OAUTH_CLIENT_ID": "client-id",
        "SN_OAUTH_CLIENT_SECRET": "client-secret",
        "SN_ACCESS_FLOW_PATH": "/api/x_aegis/flows/access",
        "SN_STATUS_FLOW_PATH": "/api/x_aegis/flows/status",
        "SN_INCIDENT_FLOW_PATH": "/api/x_aegis/flows/incident",
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    client._token_cache = None


def test_access_request_uses_oauth_and_flow_not_table_api(monkeypatch):
    calls = []

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        if url.endswith("/oauth_token.do"):
            return FakeResponse(200, {"access_token": "short-lived", "expires_in": 300})
        return FakeResponse(
            201,
            {
                "result": {
                    "request_number": "REQ001",
                    "request_sys_id": "request-id",
                    "approval_sys_id": "approval-id",
                    "approval_state": "requested",
                }
            },
        )

    monkeypatch.setattr(client.requests, "post", fake_post)
    result = client.create_access_request("Access", "Description", "Justification")

    assert result.request_number == "REQ001"
    assert len(calls) == 2
    assert all("/api/now/table/" not in url for url, _ in calls)
    assert "auth" not in calls[1][1]
    assert calls[1][1]["headers"]["Authorization"] == "Bearer short-lived"


def test_status_flow_rejects_unknown_state(monkeypatch):
    client._token_cache = ("cached", __import__("datetime").datetime.now(__import__("datetime").timezone.utc) + __import__("datetime").timedelta(minutes=5))
    monkeypatch.setattr(client.requests, "post", lambda *args, **kwargs: FakeResponse(200, {"result": {"state": "bypassed"}}))

    with pytest.raises(client.ServiceNowRequestError, match="unsupported approval state"):
        client.get_approval_state("approval-id")


def test_flow_path_cannot_escape_configured_instance(monkeypatch):
    monkeypatch.setenv("SN_ACCESS_FLOW_PATH", "https://attacker.example/steal")

    with pytest.raises(client.ServiceNowConfigError, match="relative path"):
        client.create_access_request("Access", "Description", "Justification")
