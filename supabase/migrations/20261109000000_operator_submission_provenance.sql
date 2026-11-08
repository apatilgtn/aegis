begin;
-- Attribute the authenticated initiator, not the request owner. Trusted operators
-- can submit on behalf of active members without claiming a human session.
create or replace function aegis_private.on_action_request_created() returns trigger language plpgsql security definer set search_path='' as $$
declare run_id uuid;initiator uuid;origin text;facts jsonb;
begin
 if auth.role()='authenticated' and auth.uid() is not null then
  initiator:=auth.uid();origin:='user_session';
 else
  initiator:=null;origin:='authorized_operator';
  if not exists(select 1 from public.tenant_memberships where tenant_id=new.tenant_id and user_id=new.requester_id and status='active') or
     not exists(select 1 from public.tenant_memberships where tenant_id=new.tenant_id and user_id=coalesce(new.beneficiary_id,new.requester_id) and status='active') then
   raise exception 'Operator submissions require active requester and beneficiary memberships';
  end if;
  if new.service_id is not null and not exists(select 1 from public.services where id=new.service_id and tenant_id=new.tenant_id) then raise exception 'Operator submission service does not match workspace';end if;
 end if;
 facts:=jsonb_build_object('status',new.status,'capability',new.capability,'submission_origin',origin,'request_owner_id',new.requester_id,'beneficiary_id',new.beneficiary_id,'source_role',auth.role());
 insert into public.workflow_runs(tenant_id,action_request_id,workflow_type,status,next_attempt_at) values(new.tenant_id,new.id,'access_request','pending',now()) returning id into run_id;
 insert into public.workflow_jobs(tenant_id,workflow_run_id,job_type,idempotency_key,payload) values(new.tenant_id,run_id,'evaluate_policy','evaluate_policy:'||new.id::text,jsonb_build_object('action_request_id',new.id));
 insert into public.workflow_events(tenant_id,workflow_run_id,sequence_number,event_type,actor_id,payload) values(new.tenant_id,run_id,1,'request.submitted',initiator,facts);
 insert into public.audit_events(tenant_id,actor_id,action,object_type,object_id,correlation_id,metadata) values(new.tenant_id,initiator,'action_request.created','action_request',new.id::text,run_id,facts);
 return new;
end $$;
commit;
