begin;
create function public.defer_governance_job(p_job uuid,p_worker text) returns void language plpgsql security definer set search_path='' as $$
begin
 if coalesce(auth.role(),'')<>'service_role' then raise exception 'Worker required'; end if;
 update public.workflow_jobs set status='queued',available_at=now()+interval '60 seconds',locked_by=null,lease_until=null,attempts=greatest(attempts-1,0),updated_at=now() where id=p_job and status='leased' and locked_by=p_worker;
 if not found then raise exception 'Job lease not owned'; end if;
end $$;
revoke all on function public.defer_governance_job(uuid,text) from public,anon,authenticated;
grant execute on function public.defer_governance_job(uuid,text) to service_role;
create function public.retry_read_job(p_job uuid) returns void language plpgsql security definer set search_path='' as $$
declare j public.workflow_jobs; r uuid;
begin
 select * into j from public.workflow_jobs where id=p_job for update;
 if not found or not exists(select 1 from public.tenant_memberships where tenant_id=j.tenant_id and user_id=(select auth.uid()) and status='active' and role='platform_admin') then raise exception 'Platform administrator required'; end if;
 if j.status not in ('failed','dead_letter') or j.job_type not in ('search_logs','track_github_delivery','verify_change') then raise exception 'Only failed read-only steps can be retried; submit a fresh proposal for mutations'; end if;
 select action_request_id into r from public.workflow_runs where id=j.workflow_run_id;
 if j.job_type='search_logs' then update public.action_requests set status='executing',updated_at=now() where id=r and status='failed'; end if;
 if j.job_type='track_github_delivery' then update public.action_requests set status='pr_open',updated_at=now() where id=r and status='failed'; end if;
 update public.workflow_jobs set status='queued',attempts=0,available_at=now(),locked_by=null,lease_until=null,last_error=null,updated_at=now() where id=p_job;
 insert into public.audit_events(tenant_id,actor_id,action,object_type,object_id,metadata) values(j.tenant_id,(select auth.uid()),'workflow.read_step_retried','workflow_job',p_job::text,jsonb_build_object('job_type',j.job_type));
end $$;
revoke all on function public.retry_read_job(uuid) from public,anon;
grant execute on function public.retry_read_job(uuid) to authenticated;
commit;
