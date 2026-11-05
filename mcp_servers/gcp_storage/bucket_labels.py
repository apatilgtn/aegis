"""
Real, read-only lookup of a GCS bucket's current labels — backs the
tag_remediation capability's compliance check. It authenticates with
Application Default Credentials instead of a static service-account key.
Uses list_buckets() rather
than get_bucket() deliberately: the bucket-level get_bucket() call requires
storage.buckets.get, which aegis-reader (roles/storage.objectViewer) does not
have; list_buckets() only needs storage.buckets.list, which is included, and
still returns label metadata for every bucket. Never writes; that only ever
happens via the governed change path (PR -> approval -> merge -> CI/CD).
"""

from __future__ import annotations

import os

import google.auth
from google.auth.exceptions import DefaultCredentialsError
from google.cloud import storage

PROJECT_ID = os.getenv("GCP_PROJECT_PAYMENTS_NONPROD", "ual-demo-af168")


class GcpLookupConfigError(Exception):
    pass


def current_labels(bucket_name: str) -> dict[str, str]:
    """Returns the bucket's current labels, or {} if the bucket doesn't
    exist yet — a not-yet-created bucket is simply treated as missing every
    required tag, the same as an under-tagged one."""
    try:
        credentials, _ = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform.read-only"]
        )
    except DefaultCredentialsError as exc:
        raise GcpLookupConfigError("GCP Application Default Credentials are unavailable") from exc
    client = storage.Client(project=PROJECT_ID, credentials=credentials)

    for bucket in client.list_buckets():
        if bucket.name == bucket_name:
            return dict(bucket.labels or {})
    return {}
