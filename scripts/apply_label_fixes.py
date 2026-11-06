#!/usr/bin/env python3
"""Apply merged Aegis label fixes: add missing required labels only.

Runs in GitHub Actions as aegis-pipeline after a human merge. Reads each
infra/label-fixes/*.json, checks it strictly, and calls
`gcloud storage buckets update --update-labels` for labels the bucket does
not already have. Re-running is safe: labels already present are skipped.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ALLOWED_KEYS = {"owner", "cost-center", "environment"}
PROJECT = re.compile(r"[a-z][a-z0-9-]{4,28}[a-z0-9]")
BUCKET = re.compile(r"[a-z0-9][a-z0-9._-]{1,220}[a-z0-9]")
VALUE = re.compile(r"[a-z0-9_-]{1,63}")
FILE = re.compile(r"[0-9a-f-]{36}\.json")


def load(path: Path) -> dict:
    if not FILE.fullmatch(path.name):
        raise ValueError(f"{path.name}: unexpected file name")
    doc = json.loads(path.read_text())
    if not PROJECT.fullmatch(str(doc.get("project", ""))) or not BUCKET.fullmatch(str(doc.get("bucket", ""))):
        raise ValueError(f"{path.name}: invalid project or bucket")
    labels = doc.get("add_labels")
    if not isinstance(labels, dict) or not labels or not set(labels) <= ALLOWED_KEYS:
        raise ValueError(f"{path.name}: add_labels must only contain {sorted(ALLOWED_KEYS)}")
    if not all(isinstance(v, str) and VALUE.fullmatch(v) for v in labels.values()):
        raise ValueError(f"{path.name}: label values must be lowercase letters, digits, '_' or '-'")
    return doc


def gcloud(*args: str) -> str:
    return subprocess.run(["gcloud", *args], check=True, capture_output=True, text=True).stdout


def current_labels(project: str, bucket: str) -> dict:
    out = gcloud("storage", "buckets", "describe", f"gs://{bucket}", f"--project={project}", "--format=json(labels)")
    described = json.loads(out or "null")  # gcloud prints null for a bucket with no labels
    return (described or {}).get("labels") or {}


def main(directory: str = "infra/label-fixes", dry_run: bool = False) -> int:
    files = sorted(Path(directory).glob("*.json"))
    docs = [load(path) for path in files]  # validate everything before touching anything
    for doc in docs:
        existing = current_labels(doc["project"], doc["bucket"])
        to_add = {k: v for k, v in sorted(doc["add_labels"].items()) if not existing.get(k)}
        if not to_add:
            print(f"{doc['bucket']}: nothing to add (request {doc['action_request_id']})")
            continue
        spec = ",".join(f"{k}={v}" for k, v in to_add.items())
        print(f"{doc['bucket']}: adding {spec} (request {doc['action_request_id']})")
        if not dry_run:
            gcloud("storage", "buckets", "update", f"gs://{doc['bucket']}", f"--project={doc['project']}", f"--update-labels={spec}")
    print(f"{len(docs)} label-fix file(s) processed")
    return 0


if __name__ == "__main__":
    sys.exit(main(dry_run="--dry-run" in sys.argv))
