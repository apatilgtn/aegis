-- Follow each governed proposal after the PR opens: merge, apply pipeline,
-- and final result. Aegis only reads GitHub; humans still merge.

-- 1. Opening a proposal now also queues a delivery-tracking job.
create or replace function public.complete_github_proposal(
  p_job_id uuid,
  p_worker_id text,
  p_pr_number bigint,
  p_pr_url text,
  p_branch text,
  p_commit_sha text,
  p_file_path text
) returns void language plpgsql security definer set search_path='' as $$
declare
  j public.workflow_jobs%rowtype;
  w public.workflow_runs%rowtype;
  r public.action_requests%rowtype;
  seq bigint;
  refs jsonb;
begin
  if coalesce(auth.role(),'')<>'service_role' then raise exception 'service_role required'; end if;
  if p_pr_number<1 or p_pr_url!~'^https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/pull/[0-9]+$' then
    raise exception 'Invalid GitHub pull request reference';
  end if;
  if p_branch!~'^aegis/(access-request|label-fix)-[0-9a-f-]{36}$'
     or p_commit_sha!~'^[0-9a-f]{40}$'
     or p_file_path!~'^infra/(access-grants/[0-9a-f-]{36}\.tf|label-fixes/[0-9a-f-]{36}\.json)$' then
    raise exception 'Invalid governed proposal reference';
  end if;
  select * into j from public.workflow_jobs where id=p_job_id for update;
  if not found or j.status<>'leased' or j.locked_by<>p_worker_id or j.job_type<>'create_github_proposal' then
    raise exception 'GitHub proposal job lease is not owned by this worker';
  end if;
  select * into w from public.workflow_runs where id=j.workflow_run_id for update;
  select * into r from public.action_requests where id=w.action_request_id for update;
  if r.status<>'proposal_pending' then raise exception 'Action request is not proposal_pending'; end if;
  refs=jsonb_build_object(
    'github',jsonb_build_object('pull_request_number',p_pr_number,'pull_request_url',p_pr_url,
      'branch',p_branch,'commit_sha',p_commit_sha,'file_path',p_file_path)
  );
  update public.workflow_jobs set status='succeeded',lease_until=null,locked_by=null,updated_at=now() where id=j.id;
  update public.workflow_runs set status='waiting_external',external_references=external_references||refs,
    last_error_code=null,last_error_summary=null,next_attempt_at=null,updated_at=now() where id=w.id;
  update public.action_requests set status='pr_open',updated_at=now() where id=r.id;
  insert into public.workflow_jobs(tenant_id,workflow_run_id,job_type,idempotency_key,payload,available_at)
  values(r.tenant_id,w.id,'track_github_delivery','track_github_delivery:'||r.id::text,
    jsonb_build_object('action_request_id',r.id),now()+interval '30 seconds')
  on conflict(tenant_id,idempotency_key) do nothing;
  seq:=aegis_private.next_workflow_sequence(w.id);
  insert into public.workflow_events(tenant_id,workflow_run_id,sequence_number,event_type,payload)
  values(r.tenant_id,w.id,seq,'github.proposal_created',(refs->'github')||jsonb_build_object('action_request_id',r.id));
  insert into public.audit_events(tenant_id,action,object_type,object_id,correlation_id,metadata)
  values(r.tenant_id,'github.proposal_created','action_request',r.id::text,w.id,refs->'github');
end $$;
revoke all on function public.complete_github_proposal(uuid,text,bigint,text,text,text,text) from public,anon,authenticated;
grant execute on function public.complete_github_proposal(uuid,text,bigint,text,text,text,text) to service_role;

-- 2. The worker reports what it saw. Non-final states re-queue the job
--    without using up retry attempts; final states close the workflow.
create function public.record_github_delivery(
  p_job_id uuid,
  p_worker_id text,
  p_state text,
  p_details jsonb,
  p_recheck_seconds integer default 60
) returns void language plpgsql security definer set search_path='' as $$
declare
  j public.workflow_jobs%rowtype;
  w public.workflow_runs%rowtype;
  r public.action_requests%rowtype;
  final boolean;
  seq bigint;
  v_payload jsonb;
begin
  if coalesce(auth.role(),'')<>'service_role' then raise exception 'service_role required'; end if;
  if p_state not in ('pr_open','merged','applying','completed','failed','cancelled') then
    raise exception 'Unknown delivery state %', p_state;
  end if;
  if jsonb_typeof(coalesce(p_details,'{}'::jsonb))<>'object' then raise exception 'Delivery details must be an object'; end if;
  select * into j from public.workflow_jobs where id=p_job_id for update;
  if not found or j.status<>'leased' or j.locked_by<>p_worker_id or j.job_type<>'track_github_delivery' then
    raise exception 'Delivery tracking job lease is not owned by this worker';
  end if;
  select * into w from public.workflow_runs where id=j.workflow_run_id for update;
  select * into r from public.action_requests where id=w.action_request_id for update;
  if r.status not in ('pr_open','merged','applying') then
    raise exception 'Action request is not awaiting delivery';
  end if;
  final:=p_state in ('completed','failed','cancelled');
  v_payload:=coalesce(p_details,'{}'::jsonb)||jsonb_build_object('action_request_id',r.id,'state',p_state);

  if final then
    update public.workflow_jobs set status='succeeded',lease_until=null,locked_by=null,updated_at=now() where id=j.id;
  else
    update public.workflow_jobs set status='queued',attempts=0,lease_until=null,locked_by=null,
      available_at=now()+make_interval(secs=>greatest(30,least(coalesce(p_recheck_seconds,60),3600))),
      last_error=null,updated_at=now()
    where id=j.id;
  end if;

  update public.workflow_runs set
    status=case p_state when 'completed' then 'succeeded' when 'failed' then 'failed'
                        when 'cancelled' then 'cancelled' else 'waiting_external' end,
    external_references=external_references||jsonb_build_object('delivery',v_payload),
    completed_at=case when final then now() else null end,
    last_error_code=case when p_state='failed' then 'delivery_failed' else null end,
    last_error_summary=case when p_state='failed' then left(coalesce(v_payload->>'reason','Delivery failed'),500) else null end,
    updated_at=now()
  where id=w.id;

  -- Only a change of state is recorded, so polling does not flood the log.
  if r.status<>p_state then
    update public.action_requests set status=p_state,
      expires_at=case when p_state='completed' and v_payload->>'expires_at' ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:]{8}Z$'
                      then (v_payload->>'expires_at')::timestamptz else expires_at end,
      updated_at=now()
    where id=r.id;
    seq:=aegis_private.next_workflow_sequence(w.id);
    insert into public.workflow_events(tenant_id,workflow_run_id,sequence_number,event_type,payload)
    values(r.tenant_id,w.id,seq,'delivery.'||p_state,v_payload);
    insert into public.audit_events(tenant_id,action,object_type,object_id,correlation_id,metadata)
    values(r.tenant_id,'delivery.'||p_state,'action_request',r.id::text,w.id,v_payload);
  end if;
end $$;
revoke all on function public.record_github_delivery(uuid,text,text,jsonb,integer) from public,anon,authenticated;
grant execute on function public.record_github_delivery(uuid,text,text,jsonb,integer) to service_role;

-- 3. Start tracking proposals that were already open before this change.
insert into public.workflow_jobs(tenant_id,workflow_run_id,job_type,idempotency_key,payload)
select r.tenant_id,w.id,'track_github_delivery','track_github_delivery:'||r.id::text,
       jsonb_build_object('action_request_id',r.id)
from public.action_requests r
join public.workflow_runs w on w.action_request_id=r.id
where r.status='pr_open'
on conflict(tenant_id,idempotency_key) do nothing;
