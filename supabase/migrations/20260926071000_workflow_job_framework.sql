-- Typed, multi-stage durable workflows with idempotent jobs and lease heartbeats.

alter table public.workflow_jobs
  add column job_type text,
  add column idempotency_key text;

update public.workflow_jobs
set job_type='evaluate_policy',
    idempotency_key='legacy:' || id::text
where job_type is null or idempotency_key is null;

alter table public.workflow_jobs
  alter column job_type set default 'evaluate_policy',
  alter column job_type set not null,
  alter column idempotency_key set not null,
  add constraint workflow_jobs_job_type_check check (job_type ~ '^[a-z][a-z0-9_]{2,63}$'),
  add constraint workflow_jobs_payload_object_check check (jsonb_typeof(payload)='object');

alter table public.workflow_jobs drop constraint workflow_jobs_workflow_run_id_key;
alter table public.workflow_jobs drop constraint workflow_jobs_status_check;
alter table public.workflow_jobs
  add constraint workflow_jobs_status_check check (status in ('queued','leased','succeeded','failed','dead_letter')),
  add constraint workflow_jobs_tenant_idempotency_key unique (tenant_id,idempotency_key);

drop index public.workflow_jobs_claim_idx;
create index workflow_jobs_claim_idx
  on public.workflow_jobs(job_type,status,available_at,lease_until)
  where status in ('queued','leased');
create index workflow_jobs_run_created_idx on public.workflow_jobs(workflow_run_id,created_at);

alter table public.action_requests drop constraint action_requests_status_check;
alter table public.action_requests add constraint action_requests_status_check check (status in (
  'draft','awaiting_policy','awaiting_approval','approved','proposal_pending','pr_open',
  'merged','executing','applying','completed','failed','rejected','cancelled','expired','revoked'
));

alter table public.workflow_runs drop constraint workflow_runs_status_check;
alter table public.workflow_runs add constraint workflow_runs_status_check check (status in (
  'pending','running','waiting_approval','waiting_delivery','waiting_external','reconciling',
  'succeeded','failed','cancelled'
));

create or replace function aegis_private.on_action_request_created() returns trigger language plpgsql security definer set search_path='' as $$
declare run_id uuid; begin
 insert into public.workflow_runs(tenant_id,action_request_id,workflow_type,status,next_attempt_at)
 values(new.tenant_id,new.id,'access_request','pending',now()) returning id into run_id;
 insert into public.workflow_jobs(tenant_id,workflow_run_id,job_type,idempotency_key,payload)
 values(new.tenant_id,run_id,'evaluate_policy','evaluate_policy:'||new.id::text,jsonb_build_object('action_request_id',new.id));
 insert into public.workflow_events(tenant_id,workflow_run_id,sequence_number,event_type,actor_id,payload)
 values(new.tenant_id,run_id,1,'request.submitted',new.requester_id,jsonb_build_object('status',new.status,'capability',new.capability));
 insert into public.audit_events(tenant_id,actor_id,action,object_type,object_id,correlation_id,metadata)
 values(new.tenant_id,new.requester_id,'action_request.created','action_request',new.id::text,run_id,jsonb_build_object('status',new.status,'capability',new.capability));
 return new;
end $$;

create or replace function aegis_private.on_approval_decided() returns trigger language plpgsql security definer set search_path='' as $$
declare run_id uuid; seq bigint; next_status text; begin
 next_status:=case when new.decision='approved' then 'proposal_pending' else 'rejected' end;
 update public.action_requests set status=next_status,decided_at=new.decided_at,updated_at=now() where id=new.action_request_id;
 select id into run_id from public.workflow_runs where action_request_id=new.action_request_id for update;
 if run_id is not null then
   update public.workflow_runs set
     status=case when new.decision='approved' then 'waiting_delivery' else 'failed' end,
     completed_at=case when new.decision='approved' then null else now() end,
     updated_at=now()
   where id=run_id;
   if new.decision='approved' then
     insert into public.workflow_jobs(tenant_id,workflow_run_id,job_type,idempotency_key,payload)
     values(new.tenant_id,run_id,'create_github_proposal','create_github_proposal:'||new.action_request_id::text,jsonb_build_object('action_request_id',new.action_request_id))
     on conflict(tenant_id,idempotency_key) do nothing;
   end if;
   seq:=aegis_private.next_workflow_sequence(run_id);
   insert into public.workflow_events(tenant_id,workflow_run_id,sequence_number,event_type,actor_id,payload)
   values(new.tenant_id,run_id,seq,'approval.'||new.decision,new.approver_id,jsonb_build_object('decision',new.decision,'reason',new.reason,'next_status',next_status));
 end if;
 insert into public.audit_events(tenant_id,actor_id,action,object_type,object_id,correlation_id,metadata)
 values(new.tenant_id,new.approver_id,'action_request.'||new.decision,'action_request',new.action_request_id::text,coalesce(run_id,gen_random_uuid()),jsonb_build_object('approval_id',new.id,'reason',new.reason,'next_status',next_status));
 return new;
end $$;

drop function public.claim_workflow_jobs(text,integer);
create function public.claim_workflow_jobs(
  p_worker_id text,
  p_limit integer default 5,
  p_job_types text[] default array['evaluate_policy']::text[]
) returns setof public.workflow_jobs language plpgsql security definer set search_path='' as $$
begin
 if coalesce(auth.role(),'')<>'service_role' then raise exception 'service_role required'; end if;
 if nullif(trim(p_worker_id),'') is null then raise exception 'worker id is required'; end if;
 if coalesce(cardinality(p_job_types),0)=0 then raise exception 'at least one job type is required'; end if;
 return query
 with candidates as (
   select id from public.workflow_jobs
   where job_type=any(p_job_types)
     and (status='queued' or (status='leased' and lease_until<now()))
     and available_at<=now() and attempts<max_attempts
   order by available_at,created_at
   for update skip locked
   limit greatest(1,least(p_limit,20))
 )
 update public.workflow_jobs j
 set status='leased',attempts=j.attempts+1,locked_by=p_worker_id,
     lease_until=now()+interval '2 minutes',updated_at=now()
 from candidates where j.id=candidates.id returning j.*;
end $$;
revoke all on function public.claim_workflow_jobs(text,integer,text[]) from public,anon,authenticated;
grant execute on function public.claim_workflow_jobs(text,integer,text[]) to service_role;

create function public.heartbeat_workflow_job(
  p_job_id uuid,
  p_worker_id text,
  p_extend_seconds integer default 120
) returns timestamptz language plpgsql security definer set search_path='' as $$
declare next_lease timestamptz; begin
 if coalesce(auth.role(),'')<>'service_role' then raise exception 'service_role required'; end if;
 if p_extend_seconds<30 or p_extend_seconds>600 then raise exception 'lease extension must be between 30 and 600 seconds'; end if;
 update public.workflow_jobs
 set lease_until=now()+make_interval(secs=>p_extend_seconds),updated_at=now()
 where id=p_job_id and status='leased' and locked_by=p_worker_id and lease_until>=now()-interval '5 seconds'
 returning lease_until into next_lease;
 if next_lease is null then raise exception 'Job lease is not owned by this worker'; end if;
 return next_lease;
end $$;
revoke all on function public.heartbeat_workflow_job(uuid,text,integer) from public,anon,authenticated;
grant execute on function public.heartbeat_workflow_job(uuid,text,integer) to service_role;

drop function public.fail_workflow_job(uuid,text,text);
create function public.fail_workflow_job(
  p_job_id uuid,
  p_worker_id text,
  p_error text,
  p_error_code text default 'worker_error',
  p_retryable boolean default true
) returns void language plpgsql security definer set search_path='' as $$
declare j public.workflow_jobs%rowtype; w public.workflow_runs%rowtype; r public.action_requests%rowtype;
  terminal boolean; retry_at timestamptz; seq bigint; event_name text;
begin
 if coalesce(auth.role(),'')<>'service_role' then raise exception 'service_role required'; end if;
 select * into j from public.workflow_jobs where id=p_job_id for update;
 if not found or j.status<>'leased' or j.locked_by<>p_worker_id then raise exception 'Job lease is not owned by this worker'; end if;
 select * into w from public.workflow_runs where id=j.workflow_run_id for update;
 select * into r from public.action_requests where id=w.action_request_id for update;
 terminal:=not p_retryable or j.attempts>=j.max_attempts;
 retry_at:=case when terminal then null else now()+make_interval(secs=>least(300,power(2,j.attempts)::integer*5)) end;
 event_name:=case when terminal then 'workflow.dead_lettered' else 'workflow.retry_scheduled' end;
 update public.workflow_jobs set
   status=case when terminal then 'dead_letter' else 'queued' end,
   available_at=coalesce(retry_at,available_at),lease_until=null,locked_by=null,
   last_error=left(coalesce(p_error,'Unknown worker error'),1000),updated_at=now()
 where id=p_job_id;
 update public.workflow_runs set
   status=case when terminal then 'failed' else 'pending' end,
   last_error_code=left(coalesce(nullif(p_error_code,''),'worker_error'),100),
   last_error_summary=left(coalesce(p_error,'Unknown worker error'),500),
   next_attempt_at=retry_at,completed_at=case when terminal then now() else null end,updated_at=now()
 where id=w.id;
 if terminal then update public.action_requests set status='failed',decided_at=now(),updated_at=now() where id=r.id; end if;
 seq:=aegis_private.next_workflow_sequence(w.id);
 insert into public.workflow_events(tenant_id,workflow_run_id,sequence_number,event_type,actor_id,payload)
 values(r.tenant_id,w.id,seq,event_name,null,jsonb_build_object('job_type',j.job_type,'attempt',j.attempts,'max_attempts',j.max_attempts,'retry_at',retry_at,'error_code',p_error_code));
 insert into public.audit_events(tenant_id,actor_id,action,object_type,object_id,correlation_id,metadata)
 values(r.tenant_id,null,event_name,'action_request',r.id::text,w.id,jsonb_build_object('job_type',j.job_type,'attempt',j.attempts,'terminal',terminal,'error_code',p_error_code));
end $$;
revoke all on function public.fail_workflow_job(uuid,text,text,text,boolean) from public,anon,authenticated;
grant execute on function public.fail_workflow_job(uuid,text,text,text,boolean) to service_role;

create or replace function public.complete_policy_evaluation(p_job_id uuid,p_worker_id text,p_allowed boolean,p_decision jsonb) returns void language plpgsql security definer set search_path='' as $$
declare j public.workflow_jobs%rowtype; w public.workflow_runs%rowtype; r public.action_requests%rowtype; seq bigint; begin
 if coalesce(auth.role(),'')<>'service_role' then raise exception 'service_role required'; end if;
 select * into j from public.workflow_jobs where id=p_job_id for update;
 if not found or j.status<>'leased' or j.locked_by<>p_worker_id or j.job_type<>'evaluate_policy' then raise exception 'Policy job lease is not owned by this worker'; end if;
 select * into w from public.workflow_runs where id=j.workflow_run_id for update;
 select * into r from public.action_requests where id=w.action_request_id for update;
 update public.action_requests set
   status=case when p_allowed then 'awaiting_approval' else 'rejected' end,
   risk_level=coalesce(nullif(p_decision->>'risk_level',''),'unknown'),policy_decision=p_decision,
   decided_at=case when p_allowed then null else now() end,updated_at=now()
 where id=r.id;
 update public.workflow_runs set
   status=case when p_allowed then 'waiting_approval' else 'failed' end,
   completed_at=case when p_allowed then null else now() end,updated_at=now()
 where id=w.id;
 update public.workflow_jobs set status='succeeded',lease_until=null,locked_by=null,updated_at=now() where id=j.id;
 seq:=aegis_private.next_workflow_sequence(w.id);
 insert into public.workflow_events(tenant_id,workflow_run_id,sequence_number,event_type,payload)
 values(r.tenant_id,w.id,seq,case when p_allowed then 'policy.allowed' else 'policy.denied' end,p_decision);
 insert into public.audit_events(tenant_id,action,object_type,object_id,correlation_id,metadata)
 values(r.tenant_id,case when p_allowed then 'policy.allowed' else 'policy.denied' end,'action_request',r.id::text,w.id,p_decision);
end $$;
revoke all on function public.complete_policy_evaluation(uuid,text,boolean,jsonb) from public,anon,authenticated;
grant execute on function public.complete_policy_evaluation(uuid,text,boolean,jsonb) to service_role;
