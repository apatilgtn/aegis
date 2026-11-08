"""Read-only Google Cloud calls for the Aegis worker.

On Cloud Run the worker runs as its own service account, so
`google.auth.default()` returns that identity with no key file. Everything
here only reads; changes to GCP still go through a reviewed pull request
and the protected pipeline.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

SEVERITIES = ("DEFAULT", "DEBUG", "INFO", "NOTICE", "WARNING", "ERROR", "CRITICAL", "ALERT", "EMERGENCY")
REQUIRED_LABELS = ("owner", "cost-center", "environment")
_PROJECT = re.compile(r"[a-z][a-z0-9-]{4,28}[a-z0-9]")
_BUCKET = re.compile(r"[a-z0-9][a-z0-9._-]{1,220}[a-z0-9]")
_LABEL_VALUE = re.compile(r"[a-z0-9_-]{0,63}")


class GcpError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool = True):
        super().__init__(message)
        self.retryable = retryable


def _default_token() -> str:
    import google.auth
    import google.auth.transport.requests

    credentials, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    credentials.refresh(google.auth.transport.requests.Request())
    return credentials.token


# Replaced in tests; on Cloud Run this is the attached service account.
token_provider: Callable[[], str] = _default_token


def _call(method: str, url: str, body: dict[str, Any] | None = None) -> Any:
    request = urllib.request.Request(url, method=method, data=None if body is None else json.dumps(body).encode(),
                                     headers={"Authorization": f"Bearer {token_provider()}",
                                              "Content-Type": "application/json", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            content = response.read()
            return json.loads(content) if content else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        retryable = exc.code in (408, 429) or exc.code >= 500
        raise GcpError(f"GCP returned {exc.code} for {urllib.parse.urlsplit(url).path}: {detail}",
                       retryable=retryable) from exc


def _require_project(project_id: str) -> str:
    if not _PROJECT.fullmatch(project_id or ""):
        raise GcpError("Invalid GCP project ID", retryable=False)
    return project_id


# --- Incident triage: Cloud Logging -------------------------------------------------------------

def logging_filter(hint: str, since: datetime, min_severity: str) -> str:
    hint = hint.strip()
    if not hint or len(hint) > 120 or any(ord(char) < 32 for char in hint):
        raise GcpError("Service or resource must be 1-120 printable characters", retryable=False)
    if min_severity not in SEVERITIES:
        raise GcpError("Unsupported log severity", retryable=False)
    # JSON encoding keeps the hint inside one string literal, so it cannot add filter clauses.
    literal = json.dumps(hint)
    fields = ("textPayload", "jsonPayload.message", "resource.labels.service_name",
              "resource.labels.container_name", "resource.labels.namespace_name", "logName")
    match = " OR ".join(f"{field}:{literal}" for field in fields)
    return f'timestamp >= "{since.strftime("%Y-%m-%dT%H:%M:%SZ")}" AND severity >= {min_severity} AND ({match})'


def _entry_message(entry: dict[str, Any]) -> str:
    if entry.get("textPayload"):
        return str(entry["textPayload"])
    payload = entry.get("jsonPayload") or {}
    if isinstance(payload, dict) and payload.get("message"):
        return str(payload["message"])
    if payload:
        return json.dumps(payload, sort_keys=True)
    request = entry.get("httpRequest") or {}
    if request:
        return f"HTTP {request.get('status', '?')} {request.get('requestMethod', '')} {request.get('requestUrl', '')}".strip()
    return "(no message)"


def search_logs(project_id: str, hint: str, hours: int, *, min_severity: str = "WARNING",
                now: datetime | None = None, limit: int = 50) -> list[dict[str, str]]:
    if not isinstance(hours, int) or isinstance(hours, bool) or not 1 <= hours <= 168:
        raise GcpError("Time window must be 1-168 hours", retryable=False)
    since = (now or datetime.now(timezone.utc)) - timedelta(hours=hours)
    result = _call("POST", "https://logging.googleapis.com/v2/entries:list", {
        "resourceNames": [f"projects/{_require_project(project_id)}"],
        "filter": logging_filter(hint, since, min_severity),
        "orderBy": "timestamp desc",
        "pageSize": max(1, min(limit, 100)),
    })
    findings = []
    for entry in result.get("entries") or []:
        resource = entry.get("resource") or {}
        labels = resource.get("labels") or {}
        findings.append({
            "timestamp": entry.get("timestamp", ""),
            "severity": entry.get("severity", "DEFAULT"),
            "source": labels.get("container_name") or labels.get("service_name") or resource.get("type", ""),
            "message": _entry_message(entry)[:500],
        })
    return findings


def summarize_findings(findings: list[dict[str, str]], hint: str, hours: int) -> str:
    """Plain-language summary built from the findings themselves (no model)."""
    if not findings:
        return f"No warnings or errors mentioning '{hint}' in the last {hours} hours."
    by_severity = Counter(item["severity"] for item in findings)
    severity_text = ", ".join(f"{count} {severity.lower()}" for severity, count in by_severity.most_common())
    top_message, top_count = Counter(item["message"] for item in findings).most_common(1)[0]
    sources = sorted({item["source"] for item in findings if item["source"]})
    text = f"Found {len(findings)} log entries in the last {hours} hours ({severity_text})."
    if sources:
        text += f" Sources: {', '.join(sources[:5])}."
    if top_count > 1:
        text += f" Most frequent ({top_count} times): {top_message[:200]}"
    else:
        text += f" Most recent: {findings[0]['message'][:200]}"
    return text


# --- Tag / label compliance: Cloud Storage ------------------------------------------------------

def list_buckets(project_id: str) -> list[dict[str, Any]]:
    items = []
    token = None
    while True:
        query = {"project": _require_project(project_id), "fields": "items(name,labels,location),nextPageToken", "maxResults": 200}
        if token:
            query["pageToken"] = token
        result = _call("GET", "https://storage.googleapis.com/storage/v1/b?" + urllib.parse.urlencode(query))
        items.extend({"name": item["name"], "labels": item.get("labels") or {}, "location": item.get("location", "")} for item in result.get("items") or [])
        token = result.get("nextPageToken")
        if not token:
            return items


def bucket_labels(bucket: str) -> dict[str, str]:
    if not _BUCKET.fullmatch(bucket or ""):
        raise GcpError("Invalid bucket name", retryable=False)
    result = _call("GET", f"https://storage.googleapis.com/storage/v1/b/{urllib.parse.quote(bucket, safe='')}"
                          "?fields=name,labels")
    return result.get("labels") or {}


def missing_labels(labels: dict[str, str], required: tuple[str, ...] = REQUIRED_LABELS) -> list[str]:
    return [key for key in required if key not in labels]


def label_value(value: str) -> str:
    """GCP label values: lowercase letters, digits, '_' and '-', max 63."""
    cleaned = re.sub(r"[^a-z0-9_-]+", "-", value.strip().lower()).strip("-")[:63]
    if not cleaned or not _LABEL_VALUE.fullmatch(cleaned):
        raise GcpError(f"'{value}' cannot be turned into a valid GCP label value", retryable=False)
    return cleaned


# --- Cost: BigQuery billing export --------------------------------------------------------------

def cost_by_cost_center(project_id: str, billing_table: str, month: str) -> list[dict[str, Any]]:
    """Month-to-date cost grouped by the cost-center label (null = untagged)."""
    if not re.fullmatch(r"[a-z][a-z0-9-]{4,28}[a-z0-9]\.[A-Za-z0-9_]+\.gcp_billing_export(_resource)?_v1_[0-9A-F_]+",
                        billing_table or ""):
        raise GcpError("Billing table must be <project>.<dataset>.gcp_billing_export_v1_<ACCOUNT>", retryable=False)
    if not re.fullmatch(r"20[0-9]{2}(0[1-9]|1[0-2])", month):
        raise GcpError("Month must be YYYYMM", retryable=False)
    query = (
        "SELECT (SELECT value FROM UNNEST(labels) WHERE key = 'cost-center') AS cost_center, "
        "service.description AS service, ROUND(SUM(cost) + SUM(IFNULL((SELECT SUM(c.amount) FROM UNNEST(credits) c), 0)), 2) AS net_cost, "
        "ANY_VALUE(currency) AS currency "
        f"FROM `{billing_table}` WHERE invoice.month = @month AND project.id = @project "
        "GROUP BY cost_center, service ORDER BY net_cost DESC LIMIT 200"
    )
    result = _call("POST", f"https://bigquery.googleapis.com/bigquery/v2/projects/{_require_project(project_id)}/queries", {
        "query": query, "useLegacySql": False, "timeoutMs": 30000,
        "queryParameters": [
            {"name": "month", "parameterType": {"type": "STRING"}, "parameterValue": {"value": month}},
            {"name": "project", "parameterType": {"type": "STRING"}, "parameterValue": {"value": project_id}},
        ],
    })
    if not result.get("jobComplete"):
        raise GcpError("Billing query did not finish in time")
    names = [field["name"] for field in result["schema"]["fields"]]
    return [dict(zip(names, [cell.get("v") for cell in row["f"]])) for row in result.get("rows") or []]


# --- Audit export: Cloud Logging ----------------------------------------------------------------

def write_audit_entries(project_id: str, events: list[dict[str, Any]]) -> None:
    """Append Aegis audit events to the `aegis-audit` log for SIEM ingestion."""
    if not events:
        return
    log_name = f"projects/{_require_project(project_id)}/logs/aegis-audit"
    _call("POST", "https://logging.googleapis.com/v2/entries:write", {
        "logName": log_name,
        "resource": {"type": "global", "labels": {"project_id": project_id}},
        "entries": [{
            "severity": "NOTICE",
            "timestamp": event.get("occurred_at"),
            "insertId": f"aegis-{event['id']}",
            "labels": {"aegis_action": str(event.get("action", ""))[:63], "aegis_object_type": str(event.get("object_type", ""))[:63]},
            "jsonPayload": event,
        } for event in events],
    })


def iam_policy(project_id: str, bucket: str | None = None) -> dict[str, Any]:
    if bucket:
        if not _BUCKET.fullmatch(bucket):
            raise GcpError("Invalid bucket name", retryable=False)
        return _call("GET", f"https://storage.googleapis.com/storage/v1/b/{urllib.parse.quote(bucket, safe='')}/iam?optionsRequestedPolicyVersion=3")
    return _call("POST", f"https://cloudresourcemanager.googleapis.com/v1/projects/{_require_project(project_id)}:getIamPolicy", {"options": {"requestedPolicyVersion": 3}})


def resource_inventory(project_id: str) -> list[dict[str, Any]]:
    assets = []
    token = None
    while True:
        query = {"pageSize": 500}
        if token:
            query["pageToken"] = token
        result = _call("GET", f"https://cloudasset.googleapis.com/v1/projects/{_require_project(project_id)}:searchAllResources?" + urllib.parse.urlencode(query))
        assets.extend({"name": a.get("name"), "type": a.get("assetType"), "display_name": a.get("displayName"), "location": a.get("location"), "labels": a.get("labels") or {}, "state": a.get("state")} for a in result.get("results", []))
        token = result.get("nextPageToken")
        if not token:
            return assets


def related_change_events(project_id: str, hint: str, hours: int) -> list[dict[str, str]]:
    if not isinstance(hours, int) or isinstance(hours, bool) or not 1 <= hours <= 168:
        raise GcpError("Invalid investigation window", retryable=False)
    if not hint or len(hint) > 120 or any(ord(c) < 32 for c in hint):
        raise GcpError("Invalid resource hint", retryable=False)
    project = _require_project(project_id)
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
    literal = json.dumps(hint)
    filter_ = f'logName={json.dumps("projects/" + project + "/logs/cloudaudit.googleapis.com%2Factivity")} AND timestamp>="{since}" AND (protoPayload.resourceName:{literal} OR resource.labels.service_name:{literal})'
    result = _call("POST", "https://logging.googleapis.com/v2/entries:list", {"resourceNames": ["projects/" + project], "filter": filter_, "orderBy": "timestamp desc", "pageSize": 20})
    return [{"timestamp": e.get("timestamp", ""), "method": str((e.get("protoPayload") or {}).get("methodName", ""))[:200], "resource": str((e.get("protoPayload") or {}).get("resourceName", ""))[:300], "service": str((e.get("protoPayload") or {}).get("serviceName", ""))[:100]} for e in result.get("entries", [])]


def analyze_access(project_id: str, member: str, role: str, bucket: str | None = None) -> dict[str, Any]:
    """Project-bounded native allow-policy evidence; never an authorization verdict."""
    project_id = _require_project(project_id)
    if not re.fullmatch(r'user:[^\s@]+@[^\s@]+', member) or not re.fullmatch(r'roles/[A-Za-z0-9_.]+', role):
        raise GcpError('Invalid reviewed identity or role', retryable=False)
    if bucket and not _BUCKET.fullmatch(bucket):
        raise GcpError('Invalid reviewed bucket', retryable=False)
    if bucket:
        resource = '//storage.googleapis.com/buckets/' + bucket
    else:
        resource = '//cloudresourcemanager.googleapis.com/projects/' + project_id
    query = {'analysisQuery.identitySelector.identity': member,
             'analysisQuery.resourceSelector.fullResourceName': resource,
             'analysisQuery.accessSelector.roles': role,
             'analysisQuery.options.expandGroups': 'false',
             'analysisQuery.options.expandRoles': 'true', 'executionTimeout': '10s'}
    result = _call('GET', f'https://cloudasset.googleapis.com/v1/projects/{project_id}:analyzeIamPolicy?' + urllib.parse.urlencode(query))
    analysis = result.get('mainAnalysis') or {}
    if not isinstance(result.get('mainAnalysis'), dict) or not isinstance(analysis.get('analysisResults', []), list):
        raise GcpError('Native analysis response was incomplete', retryable=False)
    rows = analysis.get('analysisResults') or []
    bindings = []
    conditions = set()
    for row in rows[:50]:
        binding = row.get('iamBinding') or {}
        for acl in row.get('accessControlLists') or []:
            if binding.get('condition'):
                conditions.add((acl.get('conditionEvaluation') or {}).get('evaluationValue', 'UNKNOWN'))
        bindings.append({'attached_to': row.get('attachedResourceFullName'), 'role': binding.get('role'),
                         'conditional': bool(binding.get('condition')), 'fully_explored': row.get('fullyExplored') is True})
    errors = [str(e.get('code', 'UNKNOWN')) for e in analysis.get('nonCriticalErrors') or []]
    complete = result.get('fullyExplored') is True and analysis.get('fullyExplored') is True
    partial = not complete or bool(errors) or bool(conditions - {'TRUE', 'FALSE'}) or len(rows) > 50
    return {'state': 'partial' if partial else 'available', 'fully_explored': complete, 'identity': member,
            'role': role, 'resource': resource, 'binding_count': len(rows), 'bindings': bindings,
            'condition_evaluations': sorted(conditions), 'error_codes': errors, 'truncated': len(rows) > 50,
            'summary': f'Google returned {len(rows)} matching or potentially matching allow-policy bindings.',
            'limitations': ['Current-state allow-policy query, not a simulation of the proposed grant.',
                            'Query scope is this project; ancestor policies outside it are not analyzed.',
                            'Group expansion, deny policies and principal access boundaries are not assessed.',
                            'No matching result does not prove that the identity has no other access.']}
