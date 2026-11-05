-- Deployment-time, self-cleaning verification of the durable request path.
do $verification$
declare
  t uuid:=gen_random_uuid(); s uuid:=gen_random_uuid(); r uuid:=gen_random_uuid();
  u uuid:='e19e4007-11e5-4856-86d2-d469b29dab75'; j public.workflow_jobs%rowtype;
  rs text; ws text; js text; ec int; ac int; blocked boolean:=false;
begin
  insert into public.tenants(id,name,slug) values(t,'Aegis verification','aegis-verification-'||replace(t::text,'-',''));
  insert into public.services(id,tenant_id,name,slug,lifecycle) values(s,t,'Verification service','verification-service','experimental');
  insert into public.action_requests(id,tenant_id,requester_id,beneficiary_id,service_id,capability,justification,status,risk_level,resource,idempotency_key,submitted_at)
  values(r,t,u,u,s,'access_grant','Automated durable workflow integration verification','awaiting_policy','unknown','{"cloud":"gcp","environment":"nonprod","tier":"tier1","ttl_hours":8}',gen_random_uuid()::text,now());
  perform set_config('request.jwt.claim.role','service_role',true);
  select * into j from public.claim_workflow_jobs('migration-verifier',1) where tenant_id=t;
  if j.id is null then raise exception 'Verification failed: job was not claimable'; end if;
  perform public.complete_policy_evaluation(j.id,'migration-verifier',true,'{"allowed":true,"reasons":[],"policy_ids":["VERIFY"],"risk_level":"low"}');
  select status into rs from public.action_requests where id=r;
  select status into ws from public.workflow_runs where action_request_id=r;
  select status into js from public.workflow_jobs where id=j.id;
  select count(*) into ec from public.workflow_events e join public.workflow_runs w on w.id=e.workflow_run_id where w.action_request_id=r and e.event_type in('request.submitted','policy.allowed');
  select count(*) into ac from public.audit_events where tenant_id=t and action in('service.created','action_request.created','policy.allowed');
  if rs<>'awaiting_approval' or ws<>'waiting_external' or js<>'succeeded' or ec<>2 or ac<>3 then raise exception 'Verification failed: transition or evidence invariant mismatch'; end if;
  begin
    insert into public.approvals(tenant_id,action_request_id,approver_id,decision) values(t,r,u,'approved');
  exception when raise_exception then
    if position('Separation of duties' in sqlerrm)>0 or position('cannot approve their own' in sqlerrm)>0 then blocked:=true; else raise; end if;
  end;
  if not blocked then raise exception 'Verification failed: self approval was not blocked'; end if;
  alter table public.audit_events disable trigger audit_events_append_only;
  alter table public.workflow_events disable trigger workflow_events_append_only;
  delete from public.audit_events where tenant_id=t; delete from public.workflow_events where tenant_id=t;
  delete from public.workflow_jobs where tenant_id=t; delete from public.workflow_runs where tenant_id=t;
  delete from public.approvals where tenant_id=t; delete from public.action_requests where tenant_id=t;
  delete from public.services where tenant_id=t; delete from public.tenants where id=t;
  alter table public.audit_events enable trigger audit_events_append_only;
  alter table public.workflow_events enable trigger workflow_events_append_only;
end
$verification$;
