"""Approval must bind the exact tenant, artifact, policy version and plan."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import pytest
from worker import governance, plan_gate

RID = '11111111-2222-4333-8444-555555555555'
TENANT = 'workspace-a'
FUTURE = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()

@pytest.fixture
def gate():
    policy = {'governance_revision': 2, 'policy_bundle_sha256': 'bundle'}
    request = {'id': RID, 'tenant_id': TENANT, 'requester_id': 'alice', 'capability': 'access_grant', 'status': 'pr_open', 'policy_decision': policy}
    rows = {
        'action_requests': [request],
        'proposal_receipts': [{'project_id': 'project-a', 'artifact_sha256': 'a'*64, 'policy_sha256': governance.digest(policy), 'expires_at': FUTURE}],
        'workflow_runs': [{'external_references': {'servicenow': {'change_sys_id': 'original'}}}],
        'plan_approvals': [{'id': 'approval', 'request_ids': [RID], 'expires_at': FUTURE, 'change_sys_id': 'exact', 'change_number': 'CHG123'}],
    }
    decisions = {'original': {'state': 'approved', 'approver_email': 'bob@example.com'}, 'exact': {'state': 'approved', 'approver_email': 'bob@example.com'}}
    settings = {'revision': 2, 'executions_paused': False}
    worker = SimpleNamespace(
        select=lambda table, query: rows[table],
        governance_context=lambda request: ({'project_id': 'project-a'}, settings, {}, None),
        governance=SimpleNamespace(digest=governance.digest, control_denials=lambda *args: []),
        policy_bundle_digest=lambda: 'bundle',
        user_email=lambda user: user+'@example.com',
        servicenow=SimpleNamespace(ServiceNowConfig=SimpleNamespace(from_env=lambda: None), approval_decision=lambda config, change, requester: decisions[change]),
        request_json=lambda *args, **kwargs: None, SUPABASE_URL='https://example.test',
    )
    payload = {'request_ids': [RID], 'project_id': 'project-a', 'plan_sha256': 'b'*64, 'commit_sha': 'c'*40, 'artifacts': {RID: 'a'*64}}
    return worker, payload, rows, decisions, settings

def test_exact_plan_with_independent_approval_is_authorized(gate):
    worker, payload, *_ = gate
    status, result = plan_gate.authorize(payload, worker)
    assert status == 200 and result['plan_sha256'] == payload['plan_sha256']

@pytest.mark.parametrize('mutation', ['artifact', 'project', 'receipt', 'policy', 'revision', 'expired', 'paused', 'requester_approval', 'revoked'])
def test_changed_or_unapproved_execution_fails_closed(gate, mutation):
    worker, payload, rows, decisions, settings = gate
    if mutation == 'artifact': payload['artifacts'][RID] = 'd'*64
    elif mutation == 'project': payload['project_id'] = 'other-project'
    elif mutation == 'receipt': rows['proposal_receipts'] = []
    elif mutation == 'policy': worker.policy_bundle_digest = lambda: 'new-bundle'
    elif mutation == 'revision': settings['revision'] = 3
    elif mutation == 'expired': rows['proposal_receipts'][0]['expires_at'] = '2000-01-01T00:00:00Z'
    elif mutation == 'paused': settings['executions_paused'] = True
    elif mutation == 'requester_approval': decisions['exact']['approver_email'] = 'alice@example.com'
    elif mutation == 'revoked': decisions['original']['state'] = 'requested'
    assert plan_gate.authorize(payload, worker)[0] == 403

def test_pending_exact_approval_never_authorizes(gate):
    worker, payload, _, decisions, _ = gate
    decisions['exact']['state'] = 'requested'
    assert plan_gate.authorize(payload, worker)[0] == 202

def test_duplicate_requests_rejected_before_database_access(gate):
    worker, payload, *_ = gate
    payload['request_ids'].append(RID)
    assert plan_gate.authorize(payload, worker)[0] == 400
