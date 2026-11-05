create or replace function public.fail_workflow_job(p_job_id uuid,p_worker_id text,p_error text) returns void language plpgsql security definer set search_path='' as $$
declare j public.workflow_jobs%rowtype; w public.workflow_runs%rowtype; r public.action_requests%rowtype; terminal boolean; retry_at timestamptz; seq bigint; event_name text;
begin
 if coalesce(auth.role(),'')<>'service_role' then raise exception 'service_role required'; end if;
 select * into j from public.workflow_jobs where id=p_job_id for update;
 if not found or j.status<>'leased' or j.locked_by<>p_worker_id then raise exception 'Job lease is not owned by this worker'; end if;
 select * into w from public.workflow_runs where id=j.workflow_run_id for update;
 select * into r from public.action_requests where id=w.action_request_id for update;
 terminal:=j.attempts>=j.max_attempts;
 retry_at:=case when terminal then null else now()+make_interval(secs=>least(300,power(2,j.attempts)::integer*5)) end;
 event_name:=case when terminal then 'workflow.failed' else 'workflow.retry_scheduled' end;
 update public.workflow_jobs set status=case when terminal then 'failed' else 'queued' end,available_at=coalesce(retry_at,available_at),lease_until=null,locked_by=null,last_error=left(coalesce(p_error,'Unknown worker error'),1000),updated_at=now() where id=p_job_id;
 update public.workflow_runs set status=case when terminal then 'failed' else 'pending' end,last_error_code='policy_worker_error',last_error_summary=left(coalesce(p_error,'Unknown worker error'),500),next_attempt_at=retry_at,completed_at=case when terminal then now() else null end,updated_at=now() where id=w.id;
 if terminal then update public.action_requests set status='failed',decided_at=now(),updated_at=now() where id=r.id; end if;
 seq:=aegis_private.next_workflow_sequence(w.id);
 insert into public.workflow_events(tenant_id,workflow_run_id,sequence_number,event_type,actor_id,payload) values(r.tenant_id,w.id,seq,event_name,null,jsonb_build_object('attempt',j.attempts,'max_attempts',j.max_attempts,'retry_at',retry_at,'error_code','policy_worker_error'));
 insert into public.audit_events(tenant_id,actor_id,action,object_type,object_id,correlation_id,metadata) values(r.tenant_id,null,event_name,'action_request',r.id::text,w.id,jsonb_build_object('attempt',j.attempts,'terminal',terminal,'error_code','policy_worker_error'));
end $$;
revoke all on function public.fail_workflow_job(uuid,text,text) from public,anon,authenticated;
grant execute on function public.fail_workflow_job(uuid,text,text) to service_role;
