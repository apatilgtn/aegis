"""Per-user portal calls. Credentials stay in request headers, never tool arguments."""
from __future__ import annotations
import json
import os
import re
import urllib.error
import urllib.request
from typing import Any


def portal_call(ctx: Any, path: str, body: dict | None = None, idempotency_key: str | None = None) -> dict:
    request = ctx.request_context.request
    if request is None:
        raise ValueError("Governed requests require authenticated HTTP user context")
    token = request.headers.get("x-aegis-user-token", "")
    workspace = request.headers.get("x-aegis-workspace", "")
    if not token or not re.fullmatch(r"[0-9a-f-]{36}", workspace):
        raise ValueError("X-Aegis-User-Token and X-Aegis-Workspace headers are required")
    base = os.environ.get("AEGIS_PORTAL_URL", "").rstrip("/")
    if not base.startswith("https://"):
        raise ValueError("The HTTPS portal API has not been configured")
    headers = {"Authorization": "Bearer " + token, "X-Aegis-Workspace": workspace, "Content-Type": "application/json"}
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    req = urllib.request.Request(base + "/api/v1/" + path, data=None if body is None else json.dumps(body).encode(), headers=headers, method="GET" if body is None else "POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        raise ValueError(f"Portal request denied or unavailable (HTTP {exc.code})") from exc
