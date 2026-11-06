begin;
create function public.request_change_verification(p_request uuid) returns void language plpgsql security definer set search_path='' as $$
declare r public.action_requests; run uuid;
begin
 select * into r from public.action_requests where id=p_request for update;
 if not found or not exists(select 1 from public.tenant_memberships where tenant_id=r.tenant_id and user_id=(select auth.uid()) and status='active' and role='platform_admin') then raise exception 'Platform administrator required'; end if;
 if r.status<>'completed' or r.capability not in ('access_grant','label_fix') then raise exception 'Only delivered cloud changes can be verified'; end if;
 select id into run from public.workflow_runs where action_request_id=r.id;
 if run is null then raise exception 'Workflow evidence is unavailable'; end if;
 if exists(select 1 from public.workflow_jobs where workflow_run_id=run and job_type='verify_change' and (status in ('queued','leased') or created_at>now()-interval '1 minute')) then raise exception 'Verification already queued or recently requested'; end if;
 insert into public.workflow_jobs(tenant_id,workflow_run_id,job_type,idempotency_key,payload)
 values(r.tenant_id,run,'verify_change','verify_change:'||gen_random_uuid()::text,jsonb_build_object('action_request_id',r.id));
 insert into public.audit_events(tenant_id,actor_id,action,object_type,object_id) values(r.tenant_id,(select auth.uid()),'verification.requested','action_request',r.id::text);
end $$;
revoke all on function public.request_change_verification(uuid) from public,anon;
grant execute on function public.request_change_verification(uuid) to authenticated;
create function public.complete_change_verification(p_job uuid,p_worker text) returns void language plpgsql security definer set search_path='' as $$
begin
 if coalesce(auth.role(),'')<>'service_role' then raise exception 'Worker required'; end if;
 update public.workflow_jobs set status='succeeded',lease_until=null,locked_by=null,updated_at=now() where id=p_job and status='leased' and locked_by=p_worker and job_type='verify_change';
 if not found then raise exception 'Verification lease not owned'; end if;
end $$;
revoke all on function public.complete_change_verification(uuid,text) from public,anon,authenticated;
grant execute on function public.complete_change_verification(uuid,text) to service_role;
commit;
