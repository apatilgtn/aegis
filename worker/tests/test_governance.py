from datetime import datetime, timezone
from copy import deepcopy
import pytest
from worker import governance, gcp

ACCOUNT={"project_id":"aegis-prod","environment":"prod"}
SETTINGS={"writes_enabled":True,"proposals_paused":False}
POLICY={"enabled":True,"production_allowed":True,"max_ttl_hours":4,"allowed_roles":["roles/logging.viewer"]}
ROUTE={"target_identifier":"aegis-prod","environment":"prod","role_identifier":"roles/logging.viewer","max_ttl_hours":4}
REQUEST={"capability":"access_grant","resource":{"environment":"prod","ttl_hours":1}}

def test_requires_all_production_controls_and_exact_scope():
 assert governance.control_denials(REQUEST,ACCOUNT,SETTINGS,POLICY,ROUTE)==[]
 for settings,policy,route in [({**SETTINGS,"writes_enabled":False},POLICY,ROUTE),({**SETTINGS,"proposals_paused":True},POLICY,ROUTE),(SETTINGS,{**POLICY,"production_allowed":False},ROUTE),(SETTINGS,POLICY,{**ROUTE,"target_identifier":"other-project"}),(SETTINGS,POLICY,{**ROUTE,"role_identifier":"roles/owner"})]:
  assert governance.control_denials(REQUEST,ACCOUNT,settings,policy,route)

def test_rejects_spoofed_environment_boolean_or_excessive_duration():
 for resource in [{"environment":"nonprod","ttl_hours":1},{"environment":"prod","ttl_hours":True},{"environment":"prod","ttl_hours":8}]:
  assert governance.control_denials({**REQUEST,"resource":resource},ACCOUNT,SETTINGS,POLICY,ROUTE)

def test_reads_do_not_require_write_permission_but_do_require_enabled_capability():
 request={"capability":"incident_triage","resource":{}}
 assert not governance.control_denials(request,ACCOUNT,{"writes_enabled":False},POLICY)
 assert governance.control_denials(request,ACCOUNT,SETTINGS,{**POLICY,"enabled":False})

def test_verification_requires_exact_condition_and_distinguishes_expiry():
 now=datetime(2026,11,6,tzinfo=timezone.utc); expiry="2026-11-06T02:00:00Z"
 binding={"role":"roles/logging.viewer","members":["user:person@example.com"],"condition":{"expression":f'request.time < timestamp("{expiry}")'}}
 assert governance.verify_iam({"bindings":[binding]},binding["role"],binding["members"][0],expiry,now)["state"]=="verified"
 wrong=deepcopy(binding);wrong.pop("condition")
 assert governance.verify_iam({"bindings":[wrong]},binding["role"],binding["members"][0],expiry,now)["state"]=="failed"
 assert governance.verify_iam({},binding["role"],binding["members"][0],"2026-11-05T00:00:00Z",now)["state"]=="expired"

def test_label_verification_does_not_treat_ci_success_as_cloud_success():
 assert governance.verify_labels({"owner":"ops"},{"owner":"ops"})["state"]=="verified"
 assert governance.verify_labels({"owner":"ops"},{"owner":"other"})["state"]=="failed"
 assert gcp.missing_labels({"owner":"","cost-center":"a","environment":"prod"})==[]

def test_bucket_inventory_follows_pagination(monkeypatch):
 pages=[{"items":[{"name":"a"}],"nextPageToken":"next"},{"items":[{"name":"b"}]}];urls=[]
 monkeypatch.setattr(gcp,"_call",lambda method,url: urls.append(url) or pages.pop(0))
 assert [b["name"] for b in gcp.list_buckets("aegis-prod")]==["a","b"]
 assert "pageToken=next" in urls[1]
