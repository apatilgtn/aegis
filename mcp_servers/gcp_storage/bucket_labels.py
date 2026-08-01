"""
Real, read-only lookup of a GCS bucket's current labels — backs the
tag_remediation capability's compliance check. Uses list_buckets() rather
than get_bucket() deliberately: the bucket-level get_bucket() call requires
storage.buckets.get, which aegis-reader (roles/storage.objectViewer) does not
have; list_buckets() only needs storage.buckets.list, which is included, and
still returns label metadata for every bucket. Never writes; that only ever
happens via the governed change path (PR -> approval -> merge -> CI/CD).
"""

from __future__ import annotations

from pathlib import Path

from google.cloud import storage
from google.oauth2 import service_account

PROJECT_ID = "ual-demo-af168"
_KEY_PATH = Path(__file__).resolve().parents[2] / ".secrets" / "aegis-reader-key.json"


class GcpLookupConfigError(Exception):
    pass


def current_labels(bucket_name: str) -> dict[str, str]:
    """Returns the bucket's current labels, or {} if the bucket doesn't
    exist yet — a not-yet-created bucket is simply treated as missing every
    required tag, the same as an under-tagged one."""
    if not _KEY_PATH.exists():
        raise GcpLookupConfigError(f"no credentials file at {_KEY_PATH}")

    credentials = service_account.Credentials.from_service_account_file(str(_KEY_PATH))
    client = storage.Client(project=PROJECT_ID, credentials=credentials)

    for bucket in client.list_buckets():
        if bucket.name == bucket_name:
            return dict(bucket.labels or {})
    return {}
