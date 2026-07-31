"""
Real, read-only lookup of a user's current GCP IAM roles on the project —
backs the "what access do I have" capability. Never writes; that only ever
happens via the governed change path (PR -> approval -> merge -> CI/CD).
"""

from __future__ import annotations

from pathlib import Path

from google.cloud import resourcemanager_v3
from google.oauth2 import service_account

_PROJECT_ID = "ual-demo-af168"
_KEY_PATH = Path(__file__).resolve().parents[2] / ".secrets" / "aegis-reader-key.json"


class GcpLookupConfigError(Exception):
    pass


def list_current_roles(email: str) -> list[str]:
    if not _KEY_PATH.exists():
        raise GcpLookupConfigError(f"no credentials file at {_KEY_PATH}")

    credentials = service_account.Credentials.from_service_account_file(str(_KEY_PATH))
    client = resourcemanager_v3.ProjectsClient(credentials=credentials)
    policy = client.get_iam_policy(request={"resource": f"projects/{_PROJECT_ID}"})

    member = f"user:{email}"
    return [b.role for b in policy.bindings if member in b.members]
