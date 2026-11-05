-- Applied to aegis-dev. Durable request processing, SoD, audit, and Realtime.
create table public.workflow_jobs (
  id uuid primary key default gen_random_uuid(), tenant_id uuid not null references public.tenants(id) on delete cascade,
  workflow_run_id uuid not null unique references public.workflow_runs(id) on delete cascade,
  status text not null default 'queued' check (status in ('queued','leased','succeeded','failed')),
  attempts integer not null default 0 check (attempts >= 0), max_attempts integer not null default 5 check (max_attempts between 1 and 20),
  available_at timestamptz not null default now(), lease_until timestamptz, locked_by text,
  payload jsonb not null default '{}', last_error text, created_at timestamptz not null default now(), updated_at timestamptz not null default now()
);
create index workflow_jobs_claim_idx on public.workflow_jobs(status,available_at,lease_until) where status in ('queued','leased');
create index workflow_jobs_tenant_created_idx on public.workflow_jobs(tenant_id,created_at desc);
alter table public.workflow_jobs enable row level security; alter table public.workflow_jobs force row level security;
create policy workflow_jobs_select_for_admins on public.workflow_jobs for select to authenticated using (exists(select 1 from public.tenant_memberships m where m.tenant_id=workflow_jobs.tenant_id and m.user_id=(select auth.uid()) and m.status='active' and m.role in ('platform_admin','auditor')));
grant select on public.workflow_jobs to authenticated;
revoke insert,update,delete on public.workflow_jobs from anon,authenticated;

alter table public.action_requests add constraint action_requests_justification_length_check check(char_length(trim(justification)) between 10 and 2000);
alter table public.action_requests add constraint action_requests_resource_object_check check(jsonb_typeof(resource)='object');
drop policy action_requests_insert_for_requesters on public.action_requests;
create policy action_requests_insert_for_requesters on public.action_requests for insert to authenticated with check (
 requester_id=(select auth.uid()) and beneficiary_id=(select auth.uid()) and status='awaiting_policy' and risk_level='unknown' and policy_decision is null and decided_at is null and submitted_at is not null
 and exists(select 1 from public.tenant_memberships m where m.tenant_id=action_requests.tenant_id and m.user_id=(select auth.uid()) and m.status='active')
  and (service_id is null or exists(select 1 from public.services s where s.id=action_requests.service_id and s.tenant_id=action_requests.tenant_id))
);

create function aegis_private.next_workflow_sequence(p_run uuid) returns bigint language plpgsql security definer set search_path='' as $$
declare n bigint; begin perform pg_advisory_xact_lock(hashtextextended(p_run::text,0)); select coalesce(max(sequence_number),0)+1 into n from public.workflow_events where workflow_run_id=p_run; return n; end $$;
revoke all on function aegis_private.next_workflow_sequence(uuid) from public,anon,authenticated;

create function aegis_private.on_action_request_created() returns trigger language plpgsql security definer set search_path='' as $$
declare run_id uuid; begin
 insert into public.workflow_runs(tenant_id,action_request_id,workflow_type,status,next_attempt_at) values(new.tenant_id,new.id,'access_request','pending',now()) returning id into run_id;
 insert into public.workflow_jobs(tenant_id,workflow_run_id,payload) values(new.tenant_id,run_id,jsonb_build_object('action_request_id',new.id));
 insert into public.workflow_events(tenant_id,workflow_run_id,sequence_number,event_type,actor_id,payload) values(new.tenant_id,run_id,1,'request.submitted',new.requester_id,jsonb_build_object('status',new.status,'capability',new.capability));
 insert into public.audit_events(tenant_id,actor_id,action,object_type,object_id,correlation_id,metadata) values(new.tenant_id,new.requester_id,'action_request.created','action_request',new.id::text,run_id,jsonb_build_object('status',new.status,'capability',new.capability)); return new; end $$;
revoke all on function aegis_private.on_action_request_created() from public,anon,authenticated;
create trigger action_request_created_workflow after insert on public.action_requests for each row execute function aegis_private.on_action_request_created();

create function aegis_private.validate_approval() returns trigger language plpgsql security definer set search_path='' as $$
declare r public.action_requests%rowtype; begin select * into r from public.action_requests where id=new.action_request_id for update;
 if not found then raise exception 'Action request not found'; end if; if new.tenant_id<>r.tenant_id then raise exception 'Approval tenant does not match request tenant'; end if;
 if new.approver_id=r.requester_id or new.approver_id=r.beneficiary_id then raise exception 'Separation of duties prevents self-approval'; end if;
 if r.status<>'awaiting_approval' then raise exception 'Action request is not awaiting approval'; end if; return new; end $$;
revoke all on function aegis_private.validate_approval() from public,anon,authenticated;
create trigger validate_approval_before_insert before insert on public.approvals for each row execute function aegis_private.validate_approval();

create function aegis_private.on_approval_decided() returns trigger language plpgsql security definer set search_path='' as $$
declare run_id uuid; seq bigint; begin
 update public.action_requests set status=case when new.decision='approved' then 'approved' else 'rejected' end,decided_at=new.decided_at,updated_at=now() where id=new.action_request_id;
 select id into run_id from public.workflow_runs where action_request_id=new.action_request_id for update;
 if run_id is not null then update public.workflow_runs set status='succeeded',completed_at=now(),updated_at=now() where id=run_id; seq:=aegis_private.next_workflow_sequence(run_id);
 insert into public.workflow_events(tenant_id,workflow_run_id,sequence_number,event_type,actor_id,payload) values(new.tenant_id,run_id,seq,'approval.'||new.decision,new.approver_id,jsonb_build_object('decision',new.decision,'reason',new.reason)); end if;
 insert into public.audit_events(tenant_id,actor_id,action,object_type,object_id,correlation_id,metadata) values(new.tenant_id,new.approver_id,'action_request.'||new.decision,'action_request',new.action_request_id::text,coalesce(run_id,gen_random_uuid()),jsonb_build_object('approval_id',new.id,'reason',new.reason)); return new; end $$;
revoke all on function aegis_private.on_approval_decided() from public,anon,authenticated;
create trigger approval_decided_after_insert after insert on public.approvals for each row execute function aegis_private.on_approval_decided();

create function aegis_private.on_service_created() returns trigger language plpgsql security definer set search_path='' as $$ begin insert into public.audit_events(tenant_id,actor_id,action,object_type,object_id,metadata) values(new.tenant_id,new.created_by,'service.created','service',new.id::text,jsonb_build_object('name',new.name,'slug',new.slug)); return new; end $$;
revoke all on function aegis_private.on_service_created() from public,anon,authenticated;
create trigger service_created_audit after insert on public.services for each row execute function aegis_private.on_service_created();

create function public.claim_workflow_jobs(p_worker_id text,p_limit integer default 5) returns setof public.workflow_jobs language plpgsql security definer set search_path='' as $$ begin
 if coalesce(auth.role(),'')<>'service_role' then raise exception 'service_role required'; end if; if nullif(trim(p_worker_id),'') is null then raise exception 'worker id is required'; end if;
 return query with candidates as(select id from public.workflow_jobs where(status='queued' or(status='leased' and lease_until<now())) and available_at<=now() and attempts<max_attempts order by available_at,created_at for update skip locked limit greatest(1,least(p_limit,20)))
 update public.workflow_jobs j set status='leased',attempts=j.attempts+1,locked_by=p_worker_id,lease_until=now()+interval '2 minutes',updated_at=now() from candidates where j.id=candidates.id returning j.*; end $$;
revoke all on function public.claim_workflow_jobs(text,integer) from public,anon,authenticated; grant execute on function public.claim_workflow_jobs(text,integer) to service_role;

create function public.complete_policy_evaluation(p_job_id uuid,p_worker_id text,p_allowed boolean,p_decision jsonb) returns void language plpgsql security definer set search_path='' as $$
declare j public.workflow_jobs%rowtype; w public.workflow_runs%rowtype; r public.action_requests%rowtype; seq bigint; begin
 if coalesce(auth.role(),'')<>'service_role' then raise exception 'service_role required'; end if; select * into j from public.workflow_jobs where id=p_job_id for update;
 if not found or j.status<>'leased' or j.locked_by<>p_worker_id then raise exception 'Job lease is not owned by this worker'; end if;
 select * into w from public.workflow_runs where id=j.workflow_run_id for update; select * into r from public.action_requests where id=w.action_request_id for update;
 update public.action_requests set status=case when p_allowed then 'awaiting_approval' else 'rejected' end,risk_level=coalesce(nullif(p_decision->>'risk_level',''),'unknown'),policy_decision=p_decision,decided_at=case when p_allowed then null else now() end,updated_at=now() where id=r.id;
 update public.workflow_runs set status=case when p_allowed then 'waiting_external' else 'failed' end,completed_at=case when p_allowed then null else now() end,updated_at=now() where id=w.id;
 update public.workflow_jobs set status='succeeded',lease_until=null,locked_by=null,updated_at=now() where id=j.id; seq:=aegis_private.next_workflow_sequence(w.id);
 insert into public.workflow_events(tenant_id,workflow_run_id,sequence_number,event_type,payload) values(r.tenant_id,w.id,seq,case when p_allowed then 'policy.allowed' else 'policy.denied' end,p_decision);
 insert into public.audit_events(tenant_id,action,object_type,object_id,correlation_id,metadata) values(r.tenant_id,case when p_allowed then 'policy.allowed' else 'policy.denied' end,'action_request',r.id::text,w.id,p_decision); end $$;
revoke all on function public.complete_policy_evaluation(uuid,text,boolean,jsonb) from public,anon,authenticated; grant execute on function public.complete_policy_evaluation(uuid,text,boolean,jsonb) to service_role;

create function public.fail_workflow_job(p_job_id uuid,p_worker_id text,p_error text) returns void language plpgsql security definer set search_path='' as $$
declare j public.workflow_jobs%rowtype; terminal boolean; begin if coalesce(auth.role(),'')<>'service_role' then raise exception 'service_role required'; end if;
 select * into j from public.workflow_jobs where id=p_job_id for update; if not found or j.status<>'leased' or j.locked_by<>p_worker_id then raise exception 'Job lease is not owned by this worker'; end if; terminal:=j.attempts>=j.max_attempts;
 update public.workflow_jobs set status=case when terminal then 'failed' else 'queued' end,available_at=case when terminal then available_at else now()+make_interval(secs=>least(300,power(2,attempts)::integer*5)) end,lease_until=null,locked_by=null,last_error=left(coalesce(p_error,'Unknown worker error'),1000),updated_at=now() where id=p_job_id;
 update public.workflow_runs set status=case when terminal then 'failed' else 'pending' end,last_error_code='policy_worker_error',last_error_summary=left(coalesce(p_error,'Unknown worker error'),500),next_attempt_at=case when terminal then null else now()+make_interval(secs=>least(300,power(2,j.attempts)::integer*5)) end,completed_at=case when terminal then now() else null end,updated_at=now() where id=j.workflow_run_id; end $$;
revoke all on function public.fail_workflow_job(uuid,text,text) from public,anon,authenticated; grant execute on function public.fail_workflow_job(uuid,text,text) to service_role;

alter publication supabase_realtime add table public.services,public.action_requests,public.approvals,public.workflow_events,public.audit_events;
