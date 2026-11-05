import os

import pytest


os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SECRET_KEY", "test-secret")

from worker import worker


class NoopHeartbeat:
    def __init__(self, job_id: str):
        self.job_id = job_id

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return None


def test_policy_handler_is_registered():
    assert worker.registered_job_types() == ["create_github_proposal", "evaluate_policy"]


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
        "resource": {"cloud": "gcp", "environment": "nonprod", "tier": "tier1", "ttl_hours": 24},
        "justification": "Need temporary production support access.",
    }
    request.update(overrides)
    return request


def proposal_route(**overrides):
    route = {
        "cloud": "gcp", "entitlement_type": "gcp_iam_binding", "target_identifier": "ual-demo-af168",
        "role_identifier": "roles/viewer", "terraform_module_source": "modules/gcp/iam_binding", "max_ttl_hours": 72,
    }
    route.update(overrides)
    return route


def test_render_gcp_access_hcl_uses_only_reviewed_identifiers():
    rendered = worker.render_gcp_access_hcl(proposal_request(), proposal_route(), "person@example.com")

    assert 'project = "ual-demo-af168"' in rendered
    assert 'role    = "roles/viewer"' in rendered
    assert 'member  = "user:person@example.com"' in rendered
    assert "temporary production support" not in rendered


@pytest.mark.parametrize("field,value", [
    ("target_identifier", 'project\"\nresource \"bad\" \"x\" {}'),
    ("role_identifier", "roles/viewer\nresource.bad"),
    ("terraform_module_source", "../../attacker/module"),
])
def test_render_gcp_access_hcl_rejects_unreviewed_route_values(field, value):
    with pytest.raises(worker.NonRetryableJobError):
        worker.render_gcp_access_hcl(proposal_request(), proposal_route(**{field: value}), "person@example.com")


def test_github_handler_persists_external_reference(monkeypatch):
    calls = []
    request = proposal_request()
    route = proposal_route()
    monkeypatch.setattr(worker, "load_proposal_context", lambda job: (request, route, "person@example.com"))
    monkeypatch.setattr(worker, "create_github_proposal", lambda *args: {
        "number": 42, "url": "https://github.com/apatilgtn/aegis/pull/42", "branch": "aegis/access-request-1",
        "commit_sha": "abc123", "file_path": "infra/access-grants/request.tf",
    })
    monkeypatch.setattr(worker, "rpc", lambda name, body: calls.append((name, body)))

    worker.handle_github_proposal({"id": "job-5"})

    assert calls[0][0] == "complete_github_proposal"
    assert calls[0][1]["p_pr_number"] == 42
    assert calls[0][1]["p_commit_sha"] == "abc123"
