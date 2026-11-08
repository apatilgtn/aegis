begin;
create table public.request_coordination (
 action_request_id uuid primary key references public.action_requests(id),tenant_id uuid not null references public.tenants(id),
 owner_id uuid not null references auth.users(id),due_at timestamptz not null,updated_at timestamptz not null default now()
);
create table public.assurance_monitors (
 action_request_id uuid primary key references public.action_requests(id),tenant_id uuid not null references public.tenants(id),
 enabled boolean not null default true,interval_minutes integer not null default 15 check(interval_minutes between 5 and 1440),
 next_check_at timestamptz not null default now(),updated_at timestamptz not null default now()
);
create index assurance_monitors_due on public.assurance_monitors(next_check_at) where enabled;
create index plan_approvals_request_scope on public.plan_approvals using gin(request_ids);
create table public.verification_observations (
 id uuid primary key default gen_random_uuid(),action_request_id uuid not null references public.action_requests(id),tenant_id uuid not null references public.tenants(id),
 state text not null check(state in ('pending','verified','failed','unavailable','expired')),previous_state text,
 details jsonb not null,checked_at timestamptz not null
);
create index verification_observation_request_time on public.verification_observations(action_request_id,checked_at desc);
create table public.operator_effort (
 id uuid primary key default gen_random_uuid(),action_request_id uuid not null references public.action_requests(id),tenant_id uuid not null references public.tenants(id),
 actor_id uuid not null references auth.users(id),minutes integer not null check(minutes between 1 and 480),note text not null check(char_length(note) between 10 and 500),occurred_at timestamptz not null default now()
);
do $$ declare tab text;begin
 foreach tab in array array['request_coordination','assurance_monitors','verification_observations','operator_effort'] loop
  execute format('alter table public.%I enable row level security',tab);
  execute format('revoke all on public.%I from public,anon,authenticated',tab);
  execute format('grant select on public.%I to authenticated',tab);
  execute format('grant select,insert,update on public.%I to service_role',tab);
  execute format('create policy tenant_read on public.%I for select to authenticated using(exists(select 1 from public.tenant_memberships m where m.tenant_id=%I.tenant_id and m.user_id=(select auth.uid()) and m.status=''active''))',tab,tab);
  execute format('create trigger check_assurance_tenant before insert or update on public.%I for each row execute function aegis_private.check_assurance_tenant()',tab);
 end loop;
 foreach tab in array array['verification_observations','operator_effort'] loop
  execute format('create trigger immutable_evidence before update or delete on public.%I for each row execute function aegis_private.reject_receipt_mutation()',tab);
 end loop;
end $$;
create function public.coordinate_request(p_request uuid,p_due timestamptz) returns void language plpgsql security definer set search_path='' as $$
declare r public.action_requests;
begin
 select * into r from public.action_requests where id=p_request for update;
 if not found or not exists(select 1 from public.tenant_memberships where tenant_id=r.tenant_id and user_id=(select auth.uid()) and status='active' and role='platform_admin') then raise exception 'Platform administrator required';end if;
 if p_due is null or p_due<now() or p_due>now()+interval '30 days' then raise exception 'Deadline must be within the next 30 days';end if;
 insert into public.request_coordination(action_request_id,tenant_id,owner_id,due_at) values(r.id,r.tenant_id,(select auth.uid()),p_due)
 on conflict(action_request_id) do update set owner_id=excluded.owner_id,due_at=excluded.due_at,updated_at=now();
 insert into public.audit_events(tenant_id,actor_id,action,object_type,object_id,metadata) values(r.tenant_id,(select auth.uid()),'review.coordinator_assigned','action_request',r.id::text,jsonb_build_object('due_at',p_due));
end $$;
create function public.set_assurance_monitor(p_request uuid,p_enabled boolean,p_interval integer default 15) returns void language plpgsql security definer set search_path='' as $$
declare r public.action_requests;
begin
 select * into r from public.action_requests where id=p_request for update;
 if not found or not exists(select 1 from public.tenant_memberships where tenant_id=r.tenant_id and user_id=(select auth.uid()) and status='active' and role='platform_admin') then raise exception 'Platform administrator required';end if;
 if p_enabled is null or p_interval is null or p_interval not between 5 and 1440 then raise exception 'Interval must be 5 to 1440 minutes';end if;
 if r.status<>'completed' or r.capability not in ('access_grant','label_fix') then raise exception 'Only delivered cloud changes can be monitored';end if;
 if p_enabled and (not exists(select 1 from public.proposal_receipts where action_request_id=r.id and manifest is not null) or not exists(select 1 from public.change_verifications where action_request_id=r.id and state='verified')) then raise exception 'A manifest and verified baseline are required before monitoring';end if;
 if p_enabled and r.capability='access_grant' and coalesce((r.policy_decision->>'proposed_expires_at')::timestamptz,'-infinity')<=now() then raise exception 'Grant window has ended';end if;
 insert into public.assurance_monitors(action_request_id,tenant_id,enabled,interval_minutes) values(r.id,r.tenant_id,p_enabled,p_interval)
 on conflict(action_request_id) do update set enabled=excluded.enabled,interval_minutes=excluded.interval_minutes,next_check_at=now(),updated_at=now();
 insert into public.audit_events(tenant_id,actor_id,action,object_type,object_id,metadata) values(r.tenant_id,(select auth.uid()),'assurance.monitor_changed','action_request',r.id::text,jsonb_build_object('enabled',p_enabled,'interval_minutes',p_interval));
end $$;
create function public.schedule_assurance_checks() returns integer language plpgsql security definer set search_path='' as $$
declare m public.assurance_monitors;run uuid;n integer:=0;
begin
 if coalesce(auth.role(),'')<>'service_role' then raise exception 'Worker required';end if;
 for m in select * from public.assurance_monitors where enabled and next_check_at<=now() order by next_check_at limit 20 for update skip locked loop
  select id into run from public.workflow_runs where action_request_id=m.action_request_id;
  if run is not null and not exists(select 1 from public.workflow_jobs where workflow_run_id=run and job_type='verify_change' and status in ('queued','leased')) then
   insert into public.workflow_jobs(tenant_id,workflow_run_id,job_type,idempotency_key,payload) values(m.tenant_id,run,'verify_change','assurance:'||gen_random_uuid()::text,jsonb_build_object('action_request_id',m.action_request_id,'scheduled',true));n:=n+1;
  end if;
  update public.assurance_monitors set next_check_at=now()+make_interval(mins=>m.interval_minutes),updated_at=now() where action_request_id=m.action_request_id;
 end loop;return n;
end $$;
create function aegis_private.record_verification_observation() returns trigger language plpgsql security definer set search_path='' as $$
declare prior text;run uuid;event_name text;
begin
 if not exists(select 1 from public.action_requests where id=new.action_request_id and tenant_id=new.tenant_id) then raise exception 'Verification tenant mismatch';end if;
 if tg_op='UPDATE' then prior:=old.state;end if;
 insert into public.verification_observations(action_request_id,tenant_id,state,previous_state,details,checked_at) values(new.action_request_id,new.tenant_id,new.state,prior,new.details,new.checked_at);
 if new.state='expired' then update public.assurance_monitors set enabled=false,updated_at=now() where action_request_id=new.action_request_id;end if;
 event_name:=case when new.state='failed' then 'assurance.mismatch' when new.state='unavailable' then 'assurance.unavailable' when new.state='verified' and prior in ('failed','unavailable') then 'assurance.recovered' else 'verification.'||new.state end;
 insert into public.audit_events(tenant_id,action,object_type,object_id,metadata) values(new.tenant_id,event_name,'action_request',new.action_request_id::text,jsonb_build_object('previous_state',prior,'state',new.state,'checked_at',new.checked_at));
 select id into run from public.workflow_runs where action_request_id=new.action_request_id;
 if run is not null then insert into public.workflow_events(tenant_id,workflow_run_id,sequence_number,event_type,payload) values(new.tenant_id,run,aegis_private.next_workflow_sequence(run),event_name,jsonb_build_object('state',new.state,'previous_state',prior));end if;return new;
end $$;
create trigger record_verification_observation after insert or update on public.change_verifications for each row execute function aegis_private.record_verification_observation();
-- Keep historical first observations; do not invent a previous state or earlier checks.
insert into public.verification_observations(action_request_id,tenant_id,state,details,checked_at) select action_request_id,tenant_id,state,details,checked_at from public.change_verifications;
create function public.record_operator_effort(p_request uuid,p_minutes integer,p_note text) returns void language plpgsql security definer set search_path='' as $$
declare r public.action_requests;
begin
 select * into r from public.action_requests where id=p_request;
 if not found or not exists(select 1 from public.tenant_memberships where tenant_id=r.tenant_id and user_id=(select auth.uid()) and status='active' and (user_id=r.requester_id or role='platform_admin')) then raise exception 'Requester or administrator required';end if;
 insert into public.operator_effort(action_request_id,tenant_id,actor_id,minutes,note) values(r.id,r.tenant_id,(select auth.uid()),p_minutes,trim(p_note));
end $$;
create function public.suspend_workspace_member(p_tenant uuid,p_user uuid) returns void language plpgsql security definer set search_path='' as $$
begin
 perform 1 from public.tenants where id=p_tenant for update;
 if not exists(select 1 from public.tenant_memberships where tenant_id=p_tenant and user_id=(select auth.uid()) and status='active' and role='platform_admin') then raise exception 'Platform administrator required';end if;
 if p_user=(select auth.uid()) then raise exception 'Self-suspension is not allowed';end if;
 update public.tenant_memberships set status='suspended' where tenant_id=p_tenant and user_id=p_user and status='active';
 if not found then raise exception 'Active workspace member not found';end if;
 insert into public.audit_events(tenant_id,actor_id,action,object_type,object_id,metadata) values(p_tenant,(select auth.uid()),'identity.suspended','workspace_member',p_user::text,jsonb_build_object('scope','Workspace access and future governed execution only; existing GCP permissions require separate revocation'));
end $$;
revoke all on function public.coordinate_request(uuid,timestamptz),public.set_assurance_monitor(uuid,boolean,integer),public.record_operator_effort(uuid,integer,text),public.suspend_workspace_member(uuid,uuid) from public,anon;
grant execute on function public.coordinate_request(uuid,timestamptz),public.set_assurance_monitor(uuid,boolean,integer),public.record_operator_effort(uuid,integer,text),public.suspend_workspace_member(uuid,uuid) to authenticated;
revoke all on function public.schedule_assurance_checks() from public,anon,authenticated;
grant execute on function public.schedule_assurance_checks() to service_role;
create or replace function public.fail_workflow_job(
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
 if j.job_type not in ('verify_change','analyze_access') then
 update public.workflow_runs set
   status=case when terminal then 'failed' else 'pending' end,
   last_error_code=left(coalesce(nullif(p_error_code,''),'worker_error'),100),
   last_error_summary=left(coalesce(p_error,'Unknown worker error'),500),
   next_attempt_at=retry_at,completed_at=case when terminal then now() else null end,updated_at=now()
 where id=w.id;
 if terminal then update public.action_requests set status='failed',decided_at=now(),updated_at=now() where id=r.id; end if;
 end if;
 seq:=aegis_private.next_workflow_sequence(w.id);
 insert into public.workflow_events(tenant_id,workflow_run_id,sequence_number,event_type,actor_id,payload)
 values(r.tenant_id,w.id,seq,event_name,null,jsonb_build_object('job_type',j.job_type,'attempt',j.attempts,'max_attempts',j.max_attempts,'retry_at',retry_at,'error_code',p_error_code));
 insert into public.audit_events(tenant_id,actor_id,action,object_type,object_id,correlation_id,metadata)
 values(r.tenant_id,null,event_name,'action_request',r.id::text,w.id,jsonb_build_object('job_type',j.job_type,'attempt',j.attempts,'terminal',terminal,'error_code',p_error_code));
end $$;
revoke all on function public.fail_workflow_job(uuid,text,text,text,boolean) from public,anon,authenticated;
grant execute on function public.fail_workflow_job(uuid,text,text,text,boolean) to service_role;

create function public.reviewer_inbox(p_tenant uuid,p_view text default 'attention',p_offset integer default 0) returns table(
 request_id uuid,capability text,status text,justification text,created_at timestamptz,owner_id uuid,owner_name text,due_at timestamptz,approval_deadline timestamptz,
 verification_state text,checked_at timestamptz,gate_verdict text,gate_reason text,change_number text,change_url text,pr_url text,monitor_enabled boolean,next_check_at timestamptz,total_count bigint
) language plpgsql security definer set search_path='' as $$ begin
 if not exists(select 1 from public.tenant_memberships member where member.tenant_id=p_tenant and member.user_id=(select auth.uid()) and member.status='active' and member.role in ('platform_admin','auditor')) then raise exception 'Reviewer access required';end if;
 if p_view not in ('attention','mine','all') or p_offset is null or p_offset<0 or p_offset>100000 then raise exception 'Invalid inbox view';end if;
 return query with facts as (
 select r.id,r.capability,r.status,r.justification,r.created_at,c.owner_id,u.email::text owner_name,c.due_at,least(receipt.expires_at,plan.expires_at) approval_deadline,v.state verification_state,v.checked_at,e.verdict gate_verdict,e.reason gate_reason,
 w.external_references->'servicenow'->>'change_number' change_number,w.external_references->'servicenow'->>'change_url' change_url,w.external_references->'github'->>'pull_request_url' pr_url,m.enabled monitor_enabled,m.next_check_at
 from public.action_requests r left join public.request_coordination c on c.action_request_id=r.id left join auth.users u on u.id=c.owner_id
 left join public.proposal_receipts receipt on receipt.action_request_id=r.id left join public.change_verifications v on v.action_request_id=r.id
 left join public.workflow_runs w on w.action_request_id=r.id left join public.assurance_monitors m on m.action_request_id=r.id
 left join lateral(select x.verdict,x.reason from public.execution_check_events x where x.action_request_id=r.id order by x.checked_at desc limit 1)e on true
 left join lateral(select x.expires_at from public.plan_approvals x where x.tenant_id=p_tenant and x.request_ids @> array[r.id] order by x.created_at desc limit 1)plan on true
 where r.tenant_id=p_tenant
 ),filtered as(select * from facts f where
 (p_view<>'mine' or f.owner_id=(select auth.uid())) and
 (p_view<>'attention' or f.status not in ('completed','rejected','cancelled','expired','revoked') or (f.status='completed' and f.capability<>'incident_triage' and (coalesce(f.verification_state,'pending') not in ('verified','expired') or (f.monitor_enabled and (f.next_check_at<now()-interval '5 minutes' or coalesce(f.checked_at,'-infinity')<now()-make_interval(mins=>(select am.interval_minutes+5 from public.assurance_monitors am where am.action_request_id=f.id))))))))
 select f.*,count(*) over() from filtered f order by (f.verification_state='failed') desc nulls last,least(f.due_at,case when f.status<>'completed' then f.approval_deadline end) asc nulls last,f.created_at asc limit 50 offset p_offset;
end $$;
create function public.workspace_value_metrics(p_tenant uuid) returns jsonb language plpgsql security definer set search_path='' as $$
declare result jsonb;begin
 if not exists(select 1 from public.tenant_memberships member where member.tenant_id=p_tenant and member.user_id=(select auth.uid()) and member.status='active' and member.role in ('platform_admin','auditor')) then raise exception 'Reviewer access required';end if;
 with cohort as(select * from public.action_requests where tenant_id=p_tenant and created_at>=now()-interval '30 days' and capability in ('access_grant','label_fix')),
 durations as(select r.id,extract(epoch from (a.approved_at-r.created_at)) approval_seconds,extract(epoch from(v.verified_at-d.delivered_at)) verification_seconds from cohort r
 left join public.workflow_runs w on w.action_request_id=r.id
 left join lateral(select min(e.occurred_at) approved_at from public.workflow_events e where e.workflow_run_id=w.id and e.event_type='approval.approved')a on true
 left join lateral(select min(e.occurred_at) delivered_at from public.workflow_events e where e.workflow_run_id=w.id and e.event_type='delivery.completed')d on true
 left join lateral(select min(o.checked_at) verified_at from public.verification_observations o where o.action_request_id=r.id and o.state='verified' and o.checked_at>=d.delivered_at)v on true),
 attempts as(select * from public.execution_check_events where tenant_id=p_tenant and checked_at>=now()-interval '30 days')
 select jsonb_build_object('window_days',30,'requests',(select count(*) from cohort),'approval_samples',count(approval_seconds) filter(where approval_seconds>=0),'median_approval_seconds',percentile_cont(0.5) within group(order by approval_seconds) filter(where approval_seconds>=0),'verification_samples',count(verification_seconds) filter(where verification_seconds>=0),'median_verification_seconds',percentile_cont(0.5) within group(order by verification_seconds) filter(where verification_seconds>=0),
 'denied_checkpoints',(select count(*) from attempts where verdict='denied'),'stale_requests_blocked',(select count(distinct action_request_id) from attempts where verdict='denied' and exists(select 1 from jsonb_array_elements(checks)c where c->>'state' in ('changed','expired'))),
 'operator_minutes',(select coalesce(sum(minutes),0) from public.operator_effort where tenant_id=p_tenant and occurred_at>=now()-interval '30 days'),'effort_entries',(select count(*) from public.operator_effort where tenant_id=p_tenant and occurred_at>=now()-interval '30 days'),
 'estimated_minutes_saved',null,'savings_basis','Not measured: no validated manual-process baseline') into result from durations;
 return result;
end $$;
revoke all on function public.reviewer_inbox(uuid,text,integer),public.workspace_value_metrics(uuid) from public,anon;
grant execute on function public.reviewer_inbox(uuid,text,integer),public.workspace_value_metrics(uuid) to authenticated;


create function public.workspace_members(p_tenant uuid) returns table(user_id uuid,email text,role text,status text) language plpgsql security definer set search_path='' as $$ begin
 if not exists(select 1 from public.tenant_memberships m where m.tenant_id=p_tenant and m.user_id=(select auth.uid()) and m.status='active' and m.role in ('platform_admin','auditor')) then raise exception 'Reviewer access required';end if;
 return query select m.user_id,u.email::text,m.role,m.status from public.tenant_memberships m join auth.users u on u.id=m.user_id where m.tenant_id=p_tenant order by u.email;
end $$;
revoke all on function public.workspace_members(uuid) from public,anon;
grant execute on function public.workspace_members(uuid) to authenticated;

commit;
