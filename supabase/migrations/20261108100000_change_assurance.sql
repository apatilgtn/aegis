begin;
alter table public.proposal_receipts add column manifest jsonb;
alter table public.proposal_receipts add column manifest_sha256 text;
alter table public.proposal_receipts add constraint receipt_manifest_valid check ((manifest is null and manifest_sha256 is null) or (manifest is not null and manifest_sha256 is not null and jsonb_typeof(manifest)='object' and manifest_sha256 ~ '^[a-f0-9]{64}$'));
alter table public.plan_approvals add column manifest_sha256s jsonb not null default '{}';
create table public.execution_check_events (
 id uuid primary key default gen_random_uuid(),tenant_id uuid not null references public.tenants(id),action_request_id uuid not null references public.action_requests(id),
 plan_sha256 text not null check(plan_sha256 ~ '^[a-f0-9]{64}$'),commit_sha text not null check(commit_sha ~ '^[a-f0-9]{40}$'),
 checkpoint text not null check(checkpoint in ('approval','pre_apply')),verdict text not null check(verdict in ('authorized','pending','denied')),
 checks jsonb not null check(jsonb_typeof(checks)='array'),reason text,checked_at timestamptz not null default now()
);
create table public.native_access_assessments (
 id uuid primary key default gen_random_uuid(),tenant_id uuid not null references public.tenants(id),action_request_id uuid not null references public.action_requests(id),
 report jsonb not null check(jsonb_typeof(report)='object'),checked_at timestamptz not null default now()
);
do $$ declare tab text; begin
 foreach tab in array array['execution_check_events','native_access_assessments'] loop
  execute format('alter table public.%I enable row level security',tab);
  execute format('revoke all on public.%I from public,anon,authenticated',tab);
  execute format('grant select on public.%I to authenticated',tab);
  execute format('grant select,insert on public.%I to service_role',tab);
  execute format('create policy tenant_read on public.%I for select to authenticated using(exists(select 1 from public.tenant_memberships m where m.tenant_id=%I.tenant_id and m.user_id=(select auth.uid()) and m.status=''active''))',tab,tab);
  execute format('create trigger immutable_evidence before update or delete on public.%I for each row execute function aegis_private.reject_receipt_mutation()',tab);
 end loop;
end $$;
create function aegis_private.check_assurance_tenant() returns trigger language plpgsql security definer set search_path='' as $$ begin
 if not exists(select 1 from public.action_requests where id=new.action_request_id and tenant_id=new.tenant_id) then raise exception 'Evidence tenant does not match request';end if;return new;
end $$;
create trigger check_execution_tenant before insert on public.execution_check_events for each row execute function aegis_private.check_assurance_tenant();
create trigger check_native_tenant before insert on public.native_access_assessments for each row execute function aegis_private.check_assurance_tenant();
create index execution_check_request_time on public.execution_check_events(action_request_id,checked_at desc);
create index native_access_request_time on public.native_access_assessments(action_request_id,checked_at desc);
create function aegis_private.audit_execution_check() returns trigger language plpgsql security definer set search_path='' as $$ declare run uuid; begin
 insert into public.audit_events(tenant_id,action,object_type,object_id,metadata) values(new.tenant_id,'execution_check.'||new.verdict,'action_request',new.action_request_id::text,jsonb_build_object('evidence_id',new.id,'checkpoint',new.checkpoint,'plan_sha256',new.plan_sha256,'reason',new.reason));
 select id into run from public.workflow_runs where action_request_id=new.action_request_id;
 if run is not null then insert into public.workflow_events(tenant_id,workflow_run_id,sequence_number,event_type,payload) values(new.tenant_id,run,aegis_private.next_workflow_sequence(run),'execution_check.'||new.verdict,jsonb_build_object('checkpoint',new.checkpoint,'plan_sha256',new.plan_sha256));end if;
 return new;
end $$;
create trigger execution_check_audit after insert on public.execution_check_events for each row execute function aegis_private.audit_execution_check();
create function public.request_native_access_analysis(p_request uuid) returns void language plpgsql security definer set search_path='' as $$
declare r public.action_requests; run uuid;
begin
 select * into r from public.action_requests where id=p_request for update;
 if not found or not exists(select 1 from public.tenant_memberships where tenant_id=r.tenant_id and user_id=(select auth.uid()) and status='active' and (user_id=r.requester_id or role in ('platform_admin','auditor'))) then raise exception 'Requester, administrator or auditor required';end if;
 if r.capability<>'access_grant' then raise exception 'Native access analysis requires an access request';end if;
 select id into run from public.workflow_runs where action_request_id=r.id;
 if run is null then raise exception 'Workflow record unavailable';end if;
 if exists(select 1 from public.workflow_jobs where workflow_run_id=run and job_type='analyze_access' and (status in ('queued','leased') or created_at>now()-interval '1 minute')) then raise exception 'An analysis is already queued or was recently requested';end if;
 insert into public.workflow_jobs(tenant_id,workflow_run_id,job_type,idempotency_key,payload) values(r.tenant_id,run,'analyze_access','analyze_access:'||gen_random_uuid()::text,jsonb_build_object('action_request_id',r.id));
 insert into public.audit_events(tenant_id,actor_id,action,object_type,object_id) values(r.tenant_id,(select auth.uid()),'native_analysis.requested','action_request',r.id::text);
end $$;
revoke all on function public.request_native_access_analysis(uuid) from public,anon;
grant execute on function public.request_native_access_analysis(uuid) to authenticated;
create function public.complete_native_access_analysis(p_job uuid,p_worker text,p_report jsonb) returns void language plpgsql security definer set search_path='' as $$
declare j public.workflow_jobs;r public.action_requests;
begin
 if coalesce(auth.role(),'')<>'service_role' then raise exception 'Worker required';end if;
 select * into j from public.workflow_jobs where id=p_job for update;
 if not found or j.status<>'leased' or j.locked_by<>p_worker or j.job_type<>'analyze_access' then raise exception 'Analysis lease not owned';end if;
 select a.* into r from public.action_requests a join public.workflow_runs w on w.action_request_id=a.id where w.id=j.workflow_run_id;
 if r.capability<>'access_grant' or jsonb_typeof(p_report)<>'object' or pg_column_size(p_report)>250000 then raise exception 'Invalid analysis report';end if;
 insert into public.native_access_assessments(tenant_id,action_request_id,report) values(r.tenant_id,r.id,p_report);
 update public.workflow_jobs set status='succeeded',lease_until=null,locked_by=null,updated_at=now() where id=j.id;
 insert into public.audit_events(tenant_id,action,object_type,object_id,metadata) values(r.tenant_id,'native_analysis.completed','action_request',r.id::text,jsonb_build_object('analyzer_state',p_report->'analyzer'->>'state','simulator_state',p_report->'simulator'->>'state'));
 insert into public.workflow_events(tenant_id,workflow_run_id,sequence_number,event_type,payload) values(r.tenant_id,j.workflow_run_id,aegis_private.next_workflow_sequence(j.workflow_run_id),'native_analysis.completed',jsonb_build_object('analyzer_state',p_report->'analyzer'->>'state'));
end $$;
revoke all on function public.complete_native_access_analysis(uuid,text,jsonb) from public,anon,authenticated;
grant execute on function public.complete_native_access_analysis(uuid,text,jsonb) to service_role;
create function aegis_private.immutable_plan_context() returns trigger language plpgsql set search_path='' as $$ begin
 if tg_op='DELETE' then raise exception 'Execution-plan evidence is immutable';end if;
 if row(new.tenant_id,new.plan_sha256,new.commit_sha,new.request_ids,new.manifest_sha256s,new.change_sys_id,new.change_number,new.expires_at,new.created_at) is distinct from row(old.tenant_id,old.plan_sha256,old.commit_sha,old.request_ids,old.manifest_sha256s,old.change_sys_id,old.change_number,old.expires_at,old.created_at) then raise exception 'Execution-plan approval context cannot change';end if;return new;
end $$;
create trigger immutable_plan_context before update or delete on public.plan_approvals for each row execute function aegis_private.immutable_plan_context();
create or replace function aegis_private.audit_plan_approval() returns trigger language plpgsql security definer set search_path='' as $$ begin
 if tg_op='INSERT' or new.state is distinct from old.state or new.approver_email is distinct from old.approver_email then
 insert into public.audit_events(tenant_id,action,object_type,object_id,metadata) values(new.tenant_id,'execution_plan.'||new.state,'execution_plan',new.id::text,jsonb_build_object('plan_sha256',new.plan_sha256,'commit_sha',new.commit_sha,'request_ids',new.request_ids,'manifest_sha256s',new.manifest_sha256s,'change_number',new.change_number,'approver_email',new.approver_email,'expires_at',new.expires_at));end if;return new;
end $$;
commit;
