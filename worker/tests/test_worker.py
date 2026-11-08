import os
from datetime import datetime, timedelta, timezone

import pytest


os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SECRET_KEY", "test-secret")

from worker import worker


@pytest.fixture(autouse=True)
def isolate_workflow_integrations(monkeypatch):
    """Existing handler tests stub new governance reads; security rules have their own tests."""
    account = {"project_id": "project-4f0a7dac-0503-4e87-abe", "environment": "nonprod"}
    settings = {"writes_enabled": True, "proposals_paused": False, "executions_paused": False, "approval_ttl_minutes": 60, "revision": 1}
    policy = {"enabled": True, "production_allowed": False, "max_ttl_hours": 72, "allowed_roles": ["roles/logging.viewer", "roles/storage.objectUser"]}
    monkeypatch.setattr(worker, "governance_context", lambda r: (account, settings, policy, proposal_route()))
    def read(table, query):
        if table == "governance_settings": return [settings]
        if table == "proposal_receipts": return []
        if table == "tenant_cloud_accounts": return [account]
        if table == "service_entitlements": return [proposal_route()]
        raise AssertionError(f"Unexpected integration read: {table}")
    monkeypatch.setattr(worker, "select", read)
    monkeypatch.setattr(worker, "insert", lambda *a: None)
    monkeypatch.setattr(worker, "upsert", lambda *a: None)
    monkeypatch.setattr(worker.gcp, "related_change_events", lambda *a: [])
    monkeypatch.setattr(worker, "source_health", lambda *a: None)
    monkeypatch.setattr(worker, "refresh_inventory", lambda *a: None)
    monkeypatch.setattr(worker, "verify_delivered_change", lambda *a: None)
    monkeypatch.setattr(worker.gcp, "list_buckets", lambda project: [{"name": "team-exports", "labels": {}, "location": "US"}])


class NoopHeartbeat:
    def __init__(self, job_id: str):
        self.job_id = job_id

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return None


def test_policy_handler_is_registered():
    assert worker.registered_job_types() == [
        "analyze_access", "create_github_proposal", "create_servicenow_incident", "evaluate_policy",
        "search_logs", "servicenow_approval", "track_github_delivery", "verify_change",
    ]


def test_process_dispatches_by_job_type(monkeypatch):
    seen = []
    monkeypatch.setattr(worker, "LeaseHeartbeat", NoopHeartbeat)
    monkeypatch.setitem(worker.JOB_HANDLERS, "test_handler", lambda job: seen.append(job["id"]))

    worker.process({"id": "job-1", "job_type": "test_handler"})

    assert seen == ["job-1"]


def test_process_rejects_unknown_job_without_retry(monkeypatch):
    monkeypatch.setattr(worker, "LeaseHeartbeat", NoopHeartbeat)

    with pytest.raises(worker.NonRetryableJobError, match="registered for job type"):
        worker.process({"id": "job-2", "job_type": "future_job"})


def test_persist_failure_marks_permanent_errors_non_retryable(monkeypatch):
    calls = []
    monkeypatch.setattr(worker, "rpc", lambda name, body: calls.append((name, body)))

    worker.persist_failure({"id": "job-3"}, worker.NonRetryableJobError("invalid job"))

    assert calls[0][0] == "fail_workflow_job"
    assert calls[0][1]["p_retryable"] is False
    assert calls[0][1]["p_error_code"] == "non_retryable_job_error"


def test_policy_handler_completes_with_structured_decision(monkeypatch):
    calls = []
    monkeypatch.setattr(worker, "load_request", lambda job: {"id": "request-1", "resource": {}, "justification": "valid reason"})
    monkeypatch.setattr(worker, "evaluate_policy", lambda request: {"allowed": True, "risk_level": "low", "reasons": []})
    monkeypatch.setattr(worker, "rpc", lambda name, body: calls.append((name, body)))

    worker.handle_policy_evaluation({"id": "job-4"})

    assert calls == [("complete_policy_evaluation", {
        "p_job_id": "job-4",
        "p_worker_id": worker.WORKER_ID,
        "p_allowed": True,
        "p_decision": {"allowed": True, "risk_level": "low", "reasons": []},
    })]


def proposal_request(**overrides):
    request = {
        "id": "11111111-2222-3333-4444-555555555555",
        "tenant_id": "153810f1-c175-46a4-8636-74918d9b70c5",
        "requester_id": "22222222-3333-4444-5555-666666666666",
        "service_id": "33333333-4444-5555-6666-777777777777",
        "capability": "access_grant",
        "status": "proposal_pending",
        "resource": {"cloud": "gcp", "environment": "nonprod", "tier": "tier1", "ttl_hours": 24},
        "justification": "Need temporary production support access.",
        "decided_at": "2026-11-06T01:30:15.123456+00:00",
    }
    request.update(overrides)
    return request


def proposal_route(**overrides):
    route = {
        "cloud": "gcp", "environment": "nonprod", "entitlement_type": "gcp_iam_binding", "target_identifier": "project-4f0a7dac-0503-4e87-abe",
        "role_identifier": "roles/logging.viewer", "terraform_module_source": "modules/gcp/iam_binding", "max_ttl_hours": 72,
    }
    route.update(overrides)
    return route


def test_render_gcp_access_hcl_uses_only_reviewed_identifiers():
    rendered = worker.render_gcp_access_hcl(proposal_request(), proposal_route(), "person@example.com")

    assert 'project    = "project-4f0a7dac-0503-4e87-abe"' in rendered
    assert 'role       = "roles/logging.viewer"' in rendered
    assert 'member     = "user:person@example.com"' in rendered
    assert "temporary production support" not in rendered


def test_render_sets_expiry_from_approval_time_plus_duration():
    rendered = worker.render_gcp_access_hcl(proposal_request(), proposal_route(), "person@example.com")

    # Approved 01:30:15 + 24h, seconds kept, fraction dropped.
    assert 'expires_at = "2026-11-07T01:30:15Z"' in rendered


def test_render_is_deterministic_for_idempotent_retries():
    first = worker.render_gcp_access_hcl(proposal_request(), proposal_route(), "person@example.com")
    second = worker.render_gcp_access_hcl(proposal_request(), proposal_route(), "person@example.com")

    assert first == second


@pytest.mark.parametrize("role", ["roles/viewer", "roles/editor", "roles/owner"])
def test_render_rejects_basic_roles_that_cannot_expire(role):
    with pytest.raises(worker.NonRetryableJobError, match="Basic GCP roles"):
        worker.render_gcp_access_hcl(proposal_request(), proposal_route(role_identifier=role), "person@example.com")


@pytest.mark.parametrize("decided_at", [None, "", "not-a-date", "2026-11-06T01:30:15"])
def test_render_requires_a_valid_approval_time(decided_at):
    with pytest.raises(worker.NonRetryableJobError):
        worker.render_gcp_access_hcl(proposal_request(decided_at=decided_at), proposal_route(), "person@example.com")


@pytest.mark.parametrize("field,value", [
    ("target_identifier", 'project\"\nresource \"bad\" \"x\" {}'),
    ("role_identifier", "roles/logging.viewer\nresource.bad"),
    ("terraform_module_source", "../../attacker/module"),
])
def test_render_gcp_access_hcl_rejects_unreviewed_route_values(field, value):
    with pytest.raises(worker.NonRetryableJobError):
        worker.render_gcp_access_hcl(proposal_request(), proposal_route(**{field: value}), "person@example.com")


def test_github_handler_persists_external_reference(monkeypatch):
    calls = []
    request = proposal_request()
    monkeypatch.setattr(worker, "load_request", lambda job: request)
    monkeypatch.setattr(worker, "load_proposal_context_for", lambda r: (r, proposal_route(), "person@example.com"))
    monkeypatch.setattr(worker, "servicenow_reference", lambda job: {"change_number": "CHG0030001"})
    monkeypatch.setattr(worker, "create_github_proposal", lambda *args: {
        "number": 42, "url": "https://github.com/apatilgtn/aegis/pull/42", "branch": "aegis/access-request-1",
        "commit_sha": "abc123", "file_path": "infra/access-grants/request.tf",
    })
    monkeypatch.setattr(worker, "rpc", lambda name, body: calls.append((name, body)))

    worker.handle_github_proposal({"id": "job-5", "workflow_run_id": "run-1"})

    assert calls[0][0] == "complete_github_proposal"
    assert calls[0][1]["p_pr_number"] == 42
    assert calls[0][1]["p_commit_sha"] == "abc123"


NOW = worker.datetime(2026, 11, 6, 3, 0, tzinfo=worker.timezone.utc)
SHA = "a" * 40


def fake_github(responses):
    """Route GET paths to canned GitHub responses by prefix."""
    calls = []

    def request(method, path, token, body=None, **kwargs):
        calls.append((method, path))
        assert method == "GET", "delivery tracking must only read GitHub"
        for prefix, response in responses.items():
            if path.startswith(prefix):
                return response
        raise AssertionError(f"unexpected GitHub call {path}")

    return request, calls


def pr(**overrides):
    value = {"html_url": "https://github.com/apatilgtn/aegis/pull/7", "state": "open", "merged_at": None}
    value.update(overrides)
    return value


def merged_pr():
    return pr(state="closed", merged_at="2026-11-06T02:50:00Z", merge_commit_sha=SHA)


def run(status="completed", **overrides):
    value = {"id": 99, "html_url": "https://github.com/apatilgtn/aegis/actions/runs/99", "status": status, "run_attempt": 1}
    value.update(overrides)
    return value


def check(monkeypatch, responses):
    request, calls = fake_github(responses)
    monkeypatch.setattr(worker, "github_request", request)
    state, details, recheck = worker.check_delivery("apatilgtn/aegis", "token", 7, NOW)
    return state, details, recheck, calls


def test_delivery_open_pr_keeps_watching(monkeypatch):
    state, details, recheck, _ = check(monkeypatch, {"/repos/apatilgtn/aegis/pulls/7": pr()})

    assert state == "pr_open"
    assert recheck == worker.PR_RECHECK_SECONDS
    assert details["pull_request_url"].endswith("/pull/7")


def test_delivery_closed_without_merge_is_cancelled(monkeypatch):
    state, details, _, _ = check(monkeypatch, {"/repos/apatilgtn/aegis/pulls/7": pr(state="closed")})

    assert state == "cancelled"
    assert "without merging" in details["reason"]


def test_delivery_merged_waits_for_pipeline_to_start(monkeypatch):
    state, _, recheck, calls = check(monkeypatch, {
        "/repos/apatilgtn/aegis/pulls/7": merged_pr(),
        "/repos/apatilgtn/aegis/actions/workflows/terraform-apply.yml/runs": {"workflow_runs": []},
    })

    assert state == "merged"
    assert recheck == worker.PIPELINE_RECHECK_SECONDS
    assert f"head_sha={SHA}" in calls[1][1]


def test_delivery_fails_if_pipeline_never_starts(monkeypatch):
    stale = merged_pr() | {"merged_at": "2026-11-06T02:00:00Z"}
    state, details, _, _ = check(monkeypatch, {
        "/repos/apatilgtn/aegis/pulls/7": stale,
        "/repos/apatilgtn/aegis/actions/workflows/terraform-apply.yml/runs": {"workflow_runs": []},
    })

    assert state == "failed"
    assert "did not start" in details["reason"]


def test_delivery_running_pipeline_is_applying(monkeypatch):
    state, details, _, _ = check(monkeypatch, {
        "/repos/apatilgtn/aegis/pulls/7": merged_pr(),
        "/repos/apatilgtn/aegis/actions/workflows/terraform-apply.yml/runs": {"workflow_runs": [run("in_progress")]},
    })

    assert state == "applying"
    assert details["workflow_run_url"].endswith("/runs/99")


def test_delivery_completes_only_when_access_grant_job_succeeds(monkeypatch):
    state, details, _, _ = check(monkeypatch, {
        "/repos/apatilgtn/aegis/pulls/7": merged_pr(),
        "/repos/apatilgtn/aegis/actions/workflows/terraform-apply.yml/runs": {"workflow_runs": [run()]},
        "/repos/apatilgtn/aegis/actions/runs/99/jobs": {"jobs": [
            {"name": "gcp-apply-tag-remediations", "conclusion": "failure"},
            {"name": "gcp-apply-access-grants", "conclusion": "success"},
        ]},
    })

    assert state == "completed"
    assert details["apply_conclusion"] == "success"


@pytest.mark.parametrize("conclusion,reason", [
    ("skipped", "skipped"),
    ("failure", "failure"),
    (None, "no result"),
])
def test_delivery_fails_when_apply_job_does_not_succeed(monkeypatch, conclusion, reason):
    jobs = [] if conclusion is None else [{"name": "gcp-apply-access-grants", "conclusion": conclusion}]
    state, details, _, _ = check(monkeypatch, {
        "/repos/apatilgtn/aegis/pulls/7": merged_pr(),
        "/repos/apatilgtn/aegis/actions/workflows/terraform-apply.yml/runs": {"workflow_runs": [run()]},
        "/repos/apatilgtn/aegis/actions/runs/99/jobs": {"jobs": jobs},
    })

    assert state == "failed"
    assert reason in details["reason"]


def test_delivery_handler_reports_expiry_on_completion(monkeypatch):
    calls = []
    monkeypatch.setattr(worker, "load_request", lambda job: proposal_request())
    monkeypatch.setattr(worker, "load_github_reference", lambda job: {"pull_request_number": 7})
    monkeypatch.setattr(worker, "github_config", lambda: ("apatilgtn/aegis", "1", "2", "key"))
    monkeypatch.setattr(worker, "github_installation_token", lambda: "token")
    monkeypatch.setattr(worker, "check_delivery", lambda *args: ("completed", {"pull_request_number": 7}, 0))
    monkeypatch.setattr(worker, "note_servicenow_outcome", lambda *args: None)
    monkeypatch.setattr(worker, "rpc", lambda name, body: calls.append((name, body)))

    worker.handle_delivery_tracking({"id": "job-6"})

    assert calls[0][0] == "record_github_delivery"
    assert calls[0][1]["p_state"] == "completed"
    assert calls[0][1]["p_details"]["expires_at"] == "2026-11-07T01:30:15Z"


# --- ServiceNow approval, incident triage, label fixes ----------------------------------------

def capture_rpc(monkeypatch):
    calls = []
    monkeypatch.setattr(worker, "rpc", lambda name, body: calls.append((name, body)))
    return calls


def common(monkeypatch, request):
    monkeypatch.setattr(worker, "load_request", lambda job: request)
    monkeypatch.setattr(worker, "user_email", lambda user_id: "requester@example.com")
    monkeypatch.setattr(worker, "cloud_account", lambda tenant: {"project_id": "project-4f0a7dac-0503-4e87-abe"})
    monkeypatch.setattr(worker.servicenow.ServiceNowConfig, "from_env", classmethod(lambda cls: "config"))


def label_request(**overrides):
    values = {"capability": "label_fix",
              "resource": {"cloud": "gcp", "environment": "nonprod", "bucket": "team-exports", "cost_center": "PMT 01"},
              "policy_decision": {"allowed": True, "labels_to_add": ["owner", "cost-center"], "policy_ids": ["AEGIS-LABEL-001"]}}
    values.update(overrides)
    return proposal_request(**values)


def test_label_fix_policy_input_uses_live_labels(monkeypatch):
    monkeypatch.setattr(worker.gcp, "bucket_labels", lambda bucket: {"environment": "nonprod", "team": "x"})
    request_input, facts = worker.policy_input(label_request())

    assert request_input["labels_to_add"] == ["owner", "cost-center"]
    assert facts["labels_before"] == {"environment": "nonprod", "team": "x"}


def test_policy_routes_each_capability_to_its_package(monkeypatch):
    seen = []
    monkeypatch.setattr(worker.gcp, "bucket_labels", lambda bucket: {})
    monkeypatch.setattr(worker, "request_json", lambda url, **kw: seen.append(url) or {"result": {"allowed": True}})
    for capability, request in [("access_grant", proposal_request()),
                                ("incident_triage", proposal_request(capability="incident_triage",
                                                                     resource={"cloud": "gcp", "resource_hint": "api", "time_window_hours": 4})),
                                ("label_fix", label_request())]:
        worker.evaluate_policy(request)
    assert [url.rsplit("/v1/data/", 1)[1] for url in seen] == [
        "aegis/access_request/decision", "aegis/incident_triage/decision", "aegis/label_fix/decision"]


def test_unknown_capability_is_not_retried():
    with pytest.raises(worker.NonRetryableJobError):
        worker.evaluate_policy(proposal_request(capability="delete_everything"))


def test_label_fix_file_adds_only_missing_labels_with_valid_values():
    import json
    content = worker.render_label_fix(label_request(), "project-4f0a7dac-0503-4e87-abe", "Jane.Doe@example.com")
    document = json.loads(content)

    assert document["bucket"] == "team-exports"
    assert document["add_labels"] == {"cost-center": "pmt-01", "owner": "jane-doe-at-example-com"}
    assert "environment" not in document["add_labels"]


@pytest.mark.parametrize("labels", [[], ["owner", "team"], None])
def test_label_fix_rejects_invalid_label_lists(labels):
    request = label_request(policy_decision={"labels_to_add": labels})
    with pytest.raises(worker.NonRetryableJobError):
        worker.render_label_fix(request, "project-4f0a7dac-0503-4e87-abe", "a@example.com")


def test_proposal_files_route_by_capability(monkeypatch):
    common(monkeypatch, label_request())
    branch, path, _, summary = worker.proposal_files(label_request())

    assert branch == "aegis/label-fix-11111111-2222-3333-4444-555555555555"
    assert path == "infra/label-fixes/11111111-2222-3333-4444-555555555555.json"
    assert "team-exports" in summary


def test_proposal_requires_approval_first():
    with pytest.raises(worker.NonRetryableJobError, match="proposal_pending"):
        worker.proposal_files(proposal_request(status="awaiting_approval"))


def test_servicenow_approval_handler_reports_decision(monkeypatch):
    request = proposal_request(status="awaiting_approval", policy_decision={"policy_ids": ["AEGIS-ACCESS-001"], "risk_level": "low", "proposed_expires_at": (datetime.now(timezone.utc)+timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")})
    common(monkeypatch, request)
    calls = capture_rpc(monkeypatch)
    opened = {}

    def open_change(config, **kwargs):
        opened.update(kwargs)
        return {"change_sys_id": "a" * 32, "change_number": "CHG0030001", "change_url": "https://x"}

    monkeypatch.setattr(worker.servicenow, "open_change_for_approval", open_change)
    monkeypatch.setattr(worker.servicenow, "approval_decision", lambda config, sys_id, requester: {
        "state": "approved", "approver_email": "abel.tuter@example.com", "decided_at": "2026-11-06 01:00:00"})

    worker.handle_servicenow_approval({"id": "job-7"})

    assert opened["requester_email"] == "requester@example.com"
    assert "removes it automatically" in opened["description"]
    assert calls[0][0] == "record_servicenow_approval"
    assert calls[0][1]["p_state"] == "approved"
    assert calls[0][1]["p_details"]["change_number"] == "CHG0030001"
    assert calls[0][1]["p_details"]["approver_email"] == "abel.tuter@example.com"


def test_log_search_handler_records_summary(monkeypatch):
    request = proposal_request(capability="incident_triage", status="executing",
                               resource={"cloud": "gcp", "resource_hint": "cilium-agent", "time_window_hours": 24})
    common(monkeypatch, request)
    calls = capture_rpc(monkeypatch)
    findings = [{"timestamp": "t", "severity": "ERROR", "source": "cilium-agent", "message": "boom"}] * 3
    monkeypatch.setattr(worker.gcp, "search_logs", lambda project, hint, hours: findings)

    worker.handle_log_search({"id": "job-8"})

    assert calls[0][0] == "record_log_search"
    assert "Found 3 log entries in the last 24 hours (3 error)" in calls[0][1]["p_summary"]
    assert calls[0][1]["p_findings"] == findings


def test_incident_handler_uses_triage_summary(monkeypatch):
    request = proposal_request(capability="incident_triage", status="executing",
                               resource={"cloud": "gcp", "resource_hint": "cilium-agent", "time_window_hours": 24})
    common(monkeypatch, request)
    calls = capture_rpc(monkeypatch)
    monkeypatch.setattr(worker, "select", lambda table, query: [{"external_references": {"triage": {"summary": "Found 3 errors"}}}])
    sent = {}
    monkeypatch.setattr(worker.servicenow, "open_incident", lambda config, **kw: sent.update(kw) or {
        "incident_sys_id": "b" * 32, "incident_number": "INC0010001", "incident_url": "https://x"})

    worker.handle_servicenow_incident({"id": "job-9", "workflow_run_id": "run-1"})

    assert "Found 3 errors" in sent["description"]
    assert calls[0] == ("record_servicenow_incident", {"p_job_id": "job-9", "p_worker_id": worker.WORKER_ID, "p_details": {
        "incident_sys_id": "b" * 32, "incident_number": "INC0010001", "incident_url": "https://x"}})


def test_servicenow_config_errors_are_not_retried():
    assert worker.is_retryable(worker.servicenow.ServiceNowError("missing settings", retryable=False)) is False
    assert worker.is_retryable(worker.gcp.GcpError("503", retryable=True)) is True
    assert worker.is_retryable(RuntimeError("network")) is True


def test_delivery_follows_the_capabilitys_own_apply_job(monkeypatch):
    seen = {}
    monkeypatch.setattr(worker, "load_request", lambda job: label_request(status="pr_open"))
    monkeypatch.setattr(worker, "load_github_reference", lambda job: {"pull_request_number": 7})
    monkeypatch.setattr(worker, "github_config", lambda: ("apatilgtn/aegis", "1", "2", "key"))
    monkeypatch.setattr(worker, "github_installation_token", lambda: "token")
    monkeypatch.setattr(worker, "check_delivery", lambda *args: seen.setdefault("job", args[4]) and ("pr_open", {}, 60))
    capture_rpc(monkeypatch)

    worker.handle_delivery_tracking({"id": "job-10"})

    assert seen["job"] == "gcp-apply-label-fixes"


def test_snapshot_records_label_compliance(monkeypatch):
    rows = []
    monkeypatch.setattr(worker, "select", lambda table, query: [{"tenant_id": "t1", "project_id": "project-4f0a7dac-0503-4e87-abe", "billing_table": None}])
    monkeypatch.setattr(worker, "insert", lambda table, row: rows.append((table, row)))
    monkeypatch.setattr(worker.gcp, "list_buckets", lambda project: [
        {"name": "ok", "location": "US", "labels": {"owner": "a", "cost-center": "b", "environment": "c"}},
        {"name": "bad", "location": "US", "labels": {"owner": "a"}}])

    worker.refresh_snapshots()

    table, row = rows[0]
    assert table == "cloud_snapshots" and row["kind"] == "label_compliance"
    assert row["data"]["compliant"] == 1 and row["data"]["total"] == 2
    assert row["data"]["buckets"][1]["missing"] == ["cost-center", "environment"]


def test_audit_export_advances_cursor_after_writing(monkeypatch):
    order = []

    def fake_select(table, query):
        if table == "tenant_cloud_accounts":
            return [{"tenant_id": "t1", "project_id": "project-4f0a7dac-0503-4e87-abe"}]
        if table == "audit_export_state":
            return [{"last_event_id": 4}]
        assert "id=gt.4" in query
        return [{"id": 5, "action": "a"}, {"id": 6, "action": "b"}]

    monkeypatch.setattr(worker, "select", fake_select)
    monkeypatch.setattr(worker.gcp, "write_audit_entries", lambda project, events: order.append(("write", len(events))))
    monkeypatch.setattr(worker, "request_json", lambda url, **kw: order.append(("cursor", kw["body"]["last_event_id"])))

    assert worker.export_audit_events() == 2
    assert order == [("write", 2), ("cursor", 6)]
