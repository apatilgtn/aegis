#!/usr/bin/env python3
"""Submit a saved plan hash for independent approval; fail closed on every error."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
MODULE = re.compile(r"(?:^|\.)module\.aegis_request_([a-f0-9_]{36})(?:\.|\[)")

def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def terraform_payload(binary: Path, document: dict, project: str, commit: str) -> dict:
    ids = set()
    summary = []
    for item in document.get("resource_changes", []):
        actions = item.get("change", {}).get("actions", [])
        if not actions or all(a in ("no-op", "read") for a in actions) or item.get("mode") == "data":
            continue
        match = MODULE.search(item.get("address", ""))
        if not match:
            raise ValueError("Plan contains a change outside an Aegis request module")
        request_id = match[1].replace("_", "-")
        if not re.fullmatch(UUID, request_id):
            raise ValueError("Invalid request module ID")
        ids.add(request_id)
        summary.append({"resource": item["address"], "actions": actions})
    return {"project_id": project, "commit_sha": commit, "plan_sha256": sha(binary), "request_ids": sorted(ids), "artifacts": {i: sha(Path("infra/access-grants") / (i + ".tf")) for i in ids}, "summary": json.dumps(summary)[:1600]}


def call_gate(payload: dict, url: str, token: str) -> tuple[int, dict]:
    if not url.startswith("https://"):
        raise ValueError("The gate must use HTTPS")
    request = urllib.request.Request(url.rstrip("/") + "/governance/plan", data=json.dumps(payload).encode(), headers={"Authorization": "Bearer " + token, "X-Aegis-Token": token, "Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=60) as result:
            return result.status, json.load(result)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"Execution authorization denied (HTTP {exc.code})") from exc


def smoke_gate(url: str, token: str, project: str = "") -> None:
    """Verify IAM/audience/caller checks with a deliberately invalid empty plan.

    This fails before any approval record or ServiceNow change can be created.
    """
    if not url.startswith("https://"):
        raise ValueError("The gate must use HTTPS")
    request = urllib.request.Request(url.rstrip("/") + "/governance/plan", data=json.dumps({"request_ids": [], "smoke": True, "project_id": project}).encode(), headers={"Authorization": "Bearer " + token, "X-Aegis-Token": token, "Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=30):
            raise RuntimeError("The gate accepted an invalid smoke-test plan")
    except urllib.error.HTTPError as exc:
        if exc.code != 400:
            raise RuntimeError(f"Gate authentication or reachability check failed (HTTP {exc.code})") from exc
        result = json.load(exc)
        if not str(result.get("error", "")).startswith("A plan must identify"):
            raise RuntimeError("The endpoint did not return the expected governance validation error")
    print("Authenticated execution gate verified; no approval or mutation requested")


def await_approval(payload: dict, wait: bool) -> bool:
    if not payload["request_ids"]:
        return False
    url, token = os.environ["AEGIS_GATE_URL"], os.environ["AEGIS_GATE_TOKEN"]
    deadline = time.monotonic() + (420 if wait else 0)
    announced = False
    while True:
        status, result = call_gate(payload, url, token)
        if status == 200 and result.get("state") == "approved" and result.get("plan_sha256") == payload["plan_sha256"]:
            return True
        if status != 202 or not wait or time.monotonic() >= deadline:
            raise RuntimeError("The exact saved execution plan has not been independently approved")
        if not announced:
            print(f"Waiting for exact-plan approval: {result.get('change_number', 'ServiceNow change')} (up to 7 minutes)", flush=True)
            announced = True
        time.sleep(10)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--terraform-plan", type=Path)
    parser.add_argument("--terraform-json", type=Path)
    parser.add_argument("--label-plan", type=Path)
    parser.add_argument("--wait", action="store_true")
    parser.add_argument("--smoke", action="store_true")
    args = parser.parse_args()
    if args.smoke:
        smoke_gate(os.environ["AEGIS_GATE_URL"], os.environ["AEGIS_GATE_TOKEN"], os.environ["GCP_PROJECT_ID"])
        return
    project, commit = os.environ["GCP_PROJECT_ID"], os.environ["GITHUB_SHA"]
    if args.label_plan:
        plan = json.loads(args.label_plan.read_text())
        payload = {"project_id": project, "commit_sha": commit, "plan_sha256": sha(args.label_plan), "request_ids": sorted(plan["artifacts"]), "artifacts": plan["artifacts"], "summary": json.dumps([{ "bucket": x["bucket"], "labels": sorted(x["add_labels"])} for x in plan["changes"]])[:1600]}
    else:
        if not args.terraform_plan or not args.terraform_json:
            raise ValueError("Provide a saved Terraform plan and its JSON representation")
        payload = terraform_payload(args.terraform_plan, json.loads(args.terraform_json.read_text()), project, commit)
    payload["checkpoint"] = "approval" if args.wait else "pre_apply"
    changed = await_approval(payload, args.wait)
    if os.getenv("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as output:
            output.write(f"changed={'true' if changed else 'false'}\n")
    print("Exact plan authorized" if changed else "No changes to execute")


if __name__ == "__main__":
    main()
