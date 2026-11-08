import os
os.environ.setdefault('SUPABASE_URL','https://example.supabase.co')
os.environ.setdefault('SUPABASE_SECRET_KEY','test-secret')
from worker import worker,governance


def test_readback_uses_approved_identity_scope_and_expiry_after_profile_change(monkeypatch):
    manifest={'scope':{'project':'approved-project','role':'roles/logging.viewer','type':'project'},'beneficiary':'approved@example.com','grant_expires_at':'2100-01-01T00:00:00Z'}
    monkeypatch.setattr(worker,'cloud_account',lambda _: {'project_id':'current-project'})
    monkeypatch.setattr(worker,'select',lambda *args:[{'manifest':manifest,'manifest_sha256':governance.digest(manifest)}])
    monkeypatch.setattr(worker,'user_email',lambda _: 'changed@example.com')
    monkeypatch.setattr(worker,'upsert',lambda *args: None)
    seen=[]
    monkeypatch.setattr(worker.gcp,'iam_policy',lambda project,bucket:seen.append((project,bucket)) or {'bindings':[{'role':'roles/logging.viewer','members':['user:approved@example.com'],'condition':{'expression':'request.time < timestamp("2100-01-01T00:00:00Z")'}}]})
    result=worker.verify_delivered_change({'id':'request','tenant_id':'tenant','capability':'access_grant','requester_id':'user'})
    assert result['state']=='verified'
    assert seen==[('approved-project',None)]
    assert result['details']['member']=='user:approved@example.com'


def test_invalid_snapshot_never_yields_verified_readback(monkeypatch):
    monkeypatch.setattr(worker,'cloud_account',lambda _: {'project_id':'current-project'})
    monkeypatch.setattr(worker,'select',lambda *args:[{'manifest':{'beneficiary':'changed@example.com'},'manifest_sha256':'invalid'}])
    monkeypatch.setattr(worker,'upsert',lambda *args: None)
    result=worker.verify_delivered_change({'id':'request','tenant_id':'tenant','capability':'access_grant'})
    assert result['state']=='unavailable'
