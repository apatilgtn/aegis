"""The execution input is the saved binary and exact reviewed source files."""
import hashlib
import importlib.util
from pathlib import Path
import pytest

spec=importlib.util.spec_from_file_location('pipeline_gate',Path(__file__).resolve().parents[2]/'scripts/pipeline_plan_gate.py')
gate=importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)
RID='11111111-2222-4333-8444-555555555555'

def test_plan_hashes_saved_binary_and_request_artifact(tmp_path,monkeypatch):
 monkeypatch.chdir(tmp_path)
 source=tmp_path/'infra/access-grants'/f'{RID}.tf';source.parent.mkdir(parents=True);source.write_text('reviewed source')
 binary=tmp_path/'tfplan';binary.write_bytes(b'exact binary')
 doc={'resource_changes':[{'address':f'module.aegis_request_{RID.replace("-","_")}.google_project_iam_member.this','change':{'actions':['create']}}]}
 result=gate.terraform_payload(binary,doc,'project-a','c'*40)
 assert result['plan_sha256']==hashlib.sha256(binary.read_bytes()).hexdigest()
 assert result['artifacts']=={RID:hashlib.sha256(source.read_bytes()).hexdigest()}
 binary.write_bytes(b'different resolved plan')
 assert gate.terraform_payload(binary,doc,'project-a','c'*40)['plan_sha256']!=result['plan_sha256']

def test_changes_outside_reviewed_request_modules_fail_closed(tmp_path):
 binary=tmp_path/'tfplan';binary.write_bytes(b'binary')
 with pytest.raises(ValueError,match='outside an Aegis'):
  gate.terraform_payload(binary,{'resource_changes':[{'address':'google_project_iam_policy.root','change':{'actions':['update']}}]},'project-a','c'*40)

def test_unchanged_plan_needs_no_execution_authorization(tmp_path):
 binary=tmp_path/'tfplan';binary.write_bytes(b'binary')
 result=gate.terraform_payload(binary,{'resource_changes':[{'address':'other','change':{'actions':['no-op']}}]},'project-a','c'*40)
 assert result['request_ids']==[]
 assert gate.await_approval(result,False) is False

@pytest.mark.parametrize('status,body,valid',[(400,b'{"error":"A plan must identify 1-100 distinct governed requests"}',True),(403,b'{"error":"Pipeline authentication required"}',False),(400,b'{"error":"Bad request"}',False)])
def test_smoke_requires_the_authenticated_governance_validation_response(monkeypatch,status,body,valid):
 import io,urllib.error
 def respond(request,timeout):
  raise urllib.error.HTTPError(request.full_url,status,'response',{},io.BytesIO(body))
 monkeypatch.setattr(gate.urllib.request,'urlopen',respond)
 if valid:gate.smoke_gate('https://gate.example.test','credential-not-logged')
 else:
  with pytest.raises(RuntimeError):gate.smoke_gate('https://gate.example.test','credential-not-logged')
