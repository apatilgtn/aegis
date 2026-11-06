#!/usr/bin/env python3
"""Prepare and apply an exact, metageneration-bound label patch in the pipeline."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import re
import subprocess
import urllib.parse
import urllib.request
from pathlib import Path
from apply_label_fixes import load


def token() -> str:
    return subprocess.run(["gcloud", "auth", "print-access-token"], check=True, capture_output=True, text=True).stdout.strip()


def api(url: str, body: dict | None = None) -> dict:
    request = urllib.request.Request(url, data=None if body is None else json.dumps(body).encode(), headers={"Authorization": "Bearer " + token(), "Content-Type": "application/json"}, method="GET" if body is None else "PATCH")
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)


def bucket_url(name: str) -> str:
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{1,220}[a-z0-9]", name):
        raise ValueError("Invalid bucket")
    return "https://storage.googleapis.com/storage/v1/b/" + urllib.parse.quote(name, safe="")


def prepare(project: str, base: str, head: str) -> dict:
    if not all(re.fullmatch(r"[a-f0-9]{40}", value) for value in (base, head)):
        raise ValueError("Valid before/after commit hashes are required")
    files = subprocess.run(["git", "diff", "--name-only", "--diff-filter=AM", base, head, "--", "infra/label-fixes"], check=True, capture_output=True, text=True).stdout.splitlines()
    number = api("https://cloudresourcemanager.googleapis.com/v1/projects/" + project)["projectNumber"]
    changes, artifacts = [], {}
    for name in files:
        path = Path(name)
        if path.suffix != ".json":
            continue
        doc = load(path)
        if doc["project"] != project or doc["action_request_id"] != path.stem:
            raise ValueError("Label file project or request ID mismatch")
        observed = api(bucket_url(doc["bucket"]))
        if str(observed["projectNumber"]) != str(number):
            raise ValueError("Bucket belongs to another project")
        existing = observed.get("labels") or {}
        additions = {k: v for k, v in doc["add_labels"].items() if k not in existing}
        if additions:
            changes.append({"request_id": path.stem, "bucket": doc["bucket"], "project": project, "metageneration": observed["metageneration"], "before_labels": existing, "add_labels": additions})
            artifacts[path.stem] = hashlib.sha256(path.read_bytes()).hexdigest()
    return {"version": 1, "project": project, "changes": changes, "artifacts": artifacts}


def apply(plan: dict, project: str) -> None:
    if plan.get("version") != 1 or plan.get("project") != project:
        raise ValueError("Invalid label plan")
    for change in plan["changes"]:
        path = Path("infra/label-fixes") / (change["request_id"] + ".json")
        if hashlib.sha256(path.read_bytes()).hexdigest() != plan["artifacts"][change["request_id"]]:
            raise ValueError("Label artifact changed after approval")
        # A concurrent metadata update returns 412; no existing value is overwritten.
        merged = {**change["before_labels"], **change["add_labels"]}
        api(bucket_url(change["bucket"]) + "?ifMetagenerationMatch=" + str(int(change["metageneration"])), {"labels": merged})
        print(f"Applied approved missing-label patch to {change['bucket']}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepare", type=Path)
    parser.add_argument("--apply", type=Path)
    args = parser.parse_args()
    project = os.environ["GCP_PROJECT_ID"]
    if args.prepare:
        args.prepare.write_text(json.dumps(prepare(project, os.environ["GITHUB_BEFORE"], os.environ["GITHUB_SHA"]), sort_keys=True, indent=2))
    elif args.apply:
        apply(json.loads(args.apply.read_text()), project)
    else:
        parser.error("Choose --prepare or --apply")
