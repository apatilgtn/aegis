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
    account = {'project_id':'project-a','environment':'prod'}
    route = {'role_identifier':'roles/logging.viewer','scope_type':'project'}
    manifest = governance.approval_manifest(request, account, route, 'alice@example.com', 'alice@example.com')
    rows = {
        'tenant_memberships': [{'status':'active'}],
        'action_requests': [request],
        'proposal_receipts': [{'project_id': 'project-a', 'artifact_sha256': 'a'*64, 'policy_sha256': governance.digest(policy), 'manifest':manifest, 'manifest_sha256':governance.digest(manifest), 'expires_at': FUTURE}],
        'workflow_runs': [{'external_references': {'servicenow': {'change_sys_id': 'original'}}}],
        'plan_approvals': [{'id': 'approval', 'request_ids': [RID], 'expires_at': FUTURE, 'change_sys_id': 'exact', 'change_number': 'CHG123', 'manifest_sha256s':{RID:governance.digest(manifest)}}],
    }
    decisions = {'original': {'state': 'approved', 'approver_email': 'bob@example.com'}, 'exact': {'state': 'approved', 'approver_email': 'bob@example.com'}}
    settings = {'revision': 2, 'executions_paused': False}
    worker = SimpleNamespace(
        select=lambda table, query: rows[table],
        governance_context=lambda request: (account, settings, {}, route),
        governance=SimpleNamespace(digest=governance.digest, approval_manifest=governance.approval_manifest, compare_manifest=governance.compare_manifest, control_denials=lambda *args: []),
        insert=lambda *args: None,
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

@pytest.mark.parametrize('changed', ['beneficiary','requester_email','role','scope','grant_expiry','manifest_hash','plan_manifest'])
def test_exact_approval_cannot_be_reused_for_changed_authorization_facts(gate, changed):
    worker,payload,rows,_,_=gate
    request=rows['action_requests'][0]
    route=worker.governance_context(request)[3]
    if changed=='beneficiary':request['beneficiary_id']='someone-else'
    elif changed=='requester_email':worker.user_email=lambda user:'changed@example.com'
    elif changed=='role':route['role_identifier']='roles/storage.objectUser'
    elif changed=='scope':route.update(scope_type='bucket',scope_identifier='different-bucket')
    elif changed=='grant_expiry':request['policy_decision']['proposed_expires_at']='2100-01-01T00:00:00Z'
    elif changed=='manifest_hash':rows['proposal_receipts'][0]['manifest_sha256']='0'*64
    elif changed=='plan_manifest':rows['plan_approvals'][0]['manifest_sha256s']={}
    recorded=[];worker.insert=lambda table,row:recorded.append((table,row))
    assert plan_gate.authorize(payload,worker)[0]==403
    assert recorded[0][0]=='execution_check_events'
    assert recorded[0][1]['verdict']=='denied'
    assert recorded[0][1]['reason']


def test_pre_apply_authorization_is_recorded_as_a_checkpoint_not_an_apply(gate):
    worker,payload,*_=gate
    payload['checkpoint']='pre_apply'
    recorded=[];worker.insert=lambda table,row:recorded.append(row)
    assert plan_gate.authorize(payload,worker)[0]==200
    assert recorded[0]['checkpoint']=='pre_apply'
    assert recorded[0]['verdict']=='authorized'
    assert all(c['state']=='matched' for c in recorded[0]['checks'])


def test_missing_evidence_storage_blocks_authorization(gate):
    worker,payload,*_=gate
    def offline(*args):raise RuntimeError('Evidence storage unavailable')
    worker.insert=offline
    with pytest.raises(RuntimeError,match='Evidence storage'):
        plan_gate.authorize(payload,worker)


def test_offboarded_identity_cannot_use_previously_approved_plan(gate):
    worker,payload,rows,*_=gate
    rows['tenant_memberships']=[]
    recorded=[]
    worker.insert=lambda table,row:recorded.append(row)
    status,result=plan_gate.authorize(payload,worker)
    assert status==403 and 'active workspace member' in result['error']
    assert recorded[0]['verdict']=='denied'
    assert recorded[0]['checks'][0]['key']=='active_membership'
