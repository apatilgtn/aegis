"""
Real, read-only lookup of a user's current GCP IAM roles on the project —
backs the "what access do I have" capability. It uses Application Default
Credentials instead of a static service-account key. Never writes; that only
ever happens via the governed change path (PR -> approval -> merge -> CI/CD).
"""

from __future__ import annotations

import os

import google.auth
from google.auth.exceptions import DefaultCredentialsError
from google.cloud import resourcemanager_v3

_PROJECT_ID = os.getenv("GCP_PROJECT_PAYMENTS_NONPROD", "ual-demo-af168")


class GcpLookupConfigError(Exception):
    pass


def list_current_roles(email: str) -> list[str]:
    try:
        credentials, _ = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform.read-only"]
        )
    except DefaultCredentialsError as exc:
        raise GcpLookupConfigError("GCP Application Default Credentials are unavailable") from exc
    client = resourcemanager_v3.ProjectsClient(credentials=credentials)
    policy = client.get_iam_policy(request={"resource": f"projects/{_PROJECT_ID}"})

    member = f"user:{email}"
    return [b.role for b in policy.bindings if member in b.members]
