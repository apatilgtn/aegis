-- Production capability controls, immutable proposal receipts and verified outcomes.
begin;
create table public.governance_settings (
 tenant_id uuid primary key references public.tenants(id) on delete cascade,
 writes_enabled boolean not null default false,
 proposals_paused boolean not null default false,
 executions_paused boolean not null default false,
 approval_ttl_minutes integer not null default 60 check(approval_ttl_minutes between 5 and 1440),
 daily_ai_token_budget bigint not null default 100000 check(daily_ai_token_budget between 0 and 100000000),
 revision bigint not null default 1,
 updated_at timestamptz not null default now()
);
create table public.capability_policies (
 tenant_id uuid not null references public.tenants(id) on delete cascade,
 capability text not null check(capability in ('access_grant','label_fix','incident_triage')),
 enabled boolean not null default true,
 production_allowed boolean not null default false,
 max_ttl_hours integer not null default 4 check(max_ttl_hours between 1 and 72),
 allowed_roles text[] not null default array['roles/logging.viewer','roles/storage.objectUser'],
 primary key(tenant_id,capability)
);
create table public.proposal_receipts (
 action_request_id uuid primary key references public.action_requests(id), tenant_id uuid not null references public.tenants(id),
 project_id text not null, artifact_path text not null, artifact_sha256 text not null check(artifact_sha256 ~ '^[a-f0-9]{64}$'),
 policy_sha256 text not null check(policy_sha256 ~ '^[a-f0-9]{64}$'),
 expires_at timestamptz not null, created_at timestamptz not null default now()
);
create table public.change_verifications (
 action_request_id uuid primary key references public.action_requests(id), tenant_id uuid not null references public.tenants(id),
 state text not null check(state in ('pending','verified','failed','unavailable','expired')),
 details jsonb not null default '{}', checked_at timestamptz not null default now()
);
create table public.cloud_source_health (
 tenant_id uuid not null references public.tenants(id), project_id text not null, source text not null,
 state text not null check(state in ('healthy','unavailable','error')),
 summary text not null, checked_at timestamptz not null default now(), primary key(tenant_id,project_id,source)
);
create table public.ai_usage_events (
 id bigint generated always as identity primary key, tenant_id uuid not null references public.tenants(id),
 actor_id uuid not null references auth.users(id), provider text not null default 'nvidia', model text not null,
 input_tokens bigint, output_tokens bigint, duration_ms integer not null check(duration_ms>=0),
 outcome text not null check(outcome in ('success','failure')), occurred_at timestamptz not null default now(),
 check(input_tokens is null or input_tokens>=0), check(output_tokens is null or output_tokens>=0)
);
create table public.ai_budget_reservations (
 id uuid primary key default gen_random_uuid(), tenant_id uuid not null references public.tenants(id), actor_id uuid not null references auth.users(id),
 reserved_tokens bigint not null check(reserved_tokens>=0), created_at timestamptz not null default now(), settled_at timestamptz
);
create table public.plan_approvals (
 id uuid primary key default gen_random_uuid(), tenant_id uuid not null references public.tenants(id),
 plan_sha256 text not null check(plan_sha256 ~ '^[a-f0-9]{64}$'), request_ids uuid[] not null,
 commit_sha text not null check(commit_sha ~ '^[a-f0-9]{40}$'), change_sys_id text, change_number text,
 state text not null default 'requested' check(state in ('requested','approved','rejected')),
 approver_email text, expires_at timestamptz not null, created_at timestamptz not null default now(),
 unique(tenant_id,plan_sha256,commit_sha)
);
-- Members can read their tenant's operational state; writes go through controlled RPCs/workers.
do $$ declare tab text; begin
 foreach tab in array array['governance_settings','capability_policies','proposal_receipts','change_verifications','cloud_source_health','ai_usage_events','plan_approvals'] loop
  execute format('alter table public.%I enable row level security',tab);
  execute format('alter table public.%I force row level security',tab);
  execute format('revoke all on public.%I from public,anon,authenticated',tab);
  execute format('grant select on public.%I to authenticated',tab);
  execute format('grant all on public.%I to service_role',tab);
  execute format('create policy tenant_read on public.%I for select to authenticated using (exists(select 1 from public.tenant_memberships m where m.tenant_id=%I.tenant_id and m.user_id=(select auth.uid()) and m.status=''active''))',tab,tab);
 end loop;
end $$;
alter table public.ai_budget_reservations enable row level security;
revoke all on public.ai_budget_reservations from public,anon,authenticated;
grant all on public.ai_budget_reservations to service_role;
-- Receipts are append-only. A changed proposal requires a new request and approval.
create function aegis_private.reject_receipt_mutation() returns trigger language plpgsql as $$ begin raise exception 'Proposal receipts are immutable'; end $$;
create trigger immutable_receipt before update or delete on public.proposal_receipts for each row execute function aegis_private.reject_receipt_mutation();
insert into public.governance_settings(tenant_id) select id from public.tenants;
insert into public.capability_policies(tenant_id,capability,production_allowed)
select t.id,c,c='incident_triage' from public.tenants t cross join unnest(array['access_grant','label_fix','incident_triage']) c;
-- Controls use optimistic concurrency and always record the authenticated administrator.
create function public.set_governance_control(p_tenant uuid,p_revision bigint,p_control text,p_value boolean) returns void
language plpgsql security definer set search_path='' as $$
begin
 if not exists(select 1 from public.tenant_memberships where tenant_id=p_tenant and user_id=(select auth.uid()) and role='platform_admin' and status='active') then raise exception 'Platform administrator required'; end if;
 if p_control not in ('writes_enabled','proposals_paused','executions_paused') then raise exception 'Unknown control'; end if;
 update public.governance_settings set
  writes_enabled=case when p_control='writes_enabled' then p_value else writes_enabled end,
  proposals_paused=case when p_control='proposals_paused' then p_value else proposals_paused end,
  executions_paused=case when p_control='executions_paused' then p_value else executions_paused end,
  revision=revision+1,updated_at=now() where tenant_id=p_tenant and revision=p_revision;
 if not found then raise exception 'Settings changed; refresh before trying again'; end if;
 insert into public.audit_events(tenant_id,actor_id,action,object_type,object_id,metadata)
 values(p_tenant,(select auth.uid()),'governance.control_changed','governance_settings',p_tenant::text,jsonb_build_object('control',p_control,'value',p_value));
end $$;
revoke all on function public.set_governance_control(uuid,bigint,text,boolean) from public,anon;
grant execute on function public.set_governance_control(uuid,bigint,text,boolean) to authenticated;
create function public.set_capability_policy(p_tenant uuid,p_capability text,p_production boolean,p_max_ttl integer) returns void
language plpgsql security definer set search_path='' as $$
begin
 if not exists(select 1 from public.tenant_memberships where tenant_id=p_tenant and user_id=(select auth.uid()) and role='platform_admin' and status='active') then raise exception 'Platform administrator required'; end if;
 if p_max_ttl not between 1 and 24 then raise exception 'Production duration must be 1 to 24 hours'; end if;
 perform 1 from public.governance_settings where tenant_id=p_tenant for update;
 update public.capability_policies set production_allowed=p_production,max_ttl_hours=p_max_ttl where tenant_id=p_tenant and capability=p_capability;
 if not found then raise exception 'Unknown capability'; end if;
 update public.governance_settings set revision=revision+1,updated_at=now() where tenant_id=p_tenant;
 insert into public.audit_events(tenant_id,actor_id,action,object_type,object_id,metadata)
 values(p_tenant,(select auth.uid()),'governance.capability_changed','capability_policy',p_capability,jsonb_build_object('production_allowed',p_production,'max_ttl_hours',p_max_ttl));
end $$;
revoke all on function public.set_capability_policy(uuid,text,boolean,integer) from public,anon;
grant execute on function public.set_capability_policy(uuid,text,boolean,integer) to authenticated;
-- Reserve a conservative maximum token count before any provider call. A crash
-- leaves the reservation charged for the day, rather than silently overspending.
create function public.reserve_ai_budget(p_tenant uuid,p_tokens bigint) returns uuid language plpgsql security definer set search_path='' as $$
declare budget bigint; used bigint; reservation uuid;
begin
 if not exists(select 1 from public.tenant_memberships where tenant_id=p_tenant and user_id=(select auth.uid()) and status='active') then raise exception 'Active membership required'; end if;
 if p_tokens < 1 or p_tokens > 1000000 then raise exception 'Invalid reservation'; end if;
 select daily_ai_token_budget into budget from public.governance_settings where tenant_id=p_tenant for update;
 if not found then raise exception 'AI budget not configured'; end if;
 if (select count(*) from public.ai_budget_reservations where tenant_id=p_tenant and actor_id=(select auth.uid()) and created_at>now()-interval '1 minute')>=30 then raise exception 'AI request rate limit reached'; end if;
 select coalesce(sum(reserved_tokens),0) into used from public.ai_budget_reservations where tenant_id=p_tenant and created_at >= date_trunc('day',now() at time zone 'UTC') at time zone 'UTC';
 if used+p_tokens>budget then raise exception 'Daily AI token budget reached'; end if;
 insert into public.ai_budget_reservations(tenant_id,actor_id,reserved_tokens) values(p_tenant,(select auth.uid()),p_tokens) returning id into reservation;
 return reservation;
end $$;
revoke all on function public.reserve_ai_budget(uuid,bigint) from public,anon;
grant execute on function public.reserve_ai_budget(uuid,bigint) to authenticated;
create function public.record_ai_usage(p_reservation uuid,p_model text,p_input bigint,p_output bigint,p_ms integer,p_success boolean) returns void language plpgsql security definer set search_path='' as $$
declare r public.ai_budget_reservations;
begin
 if coalesce(auth.role(),'') <> 'service_role' then raise exception 'Service role required'; end if;
 select * into r from public.ai_budget_reservations where id=p_reservation for update;
 if not found or r.settled_at is not null then raise exception 'Reservation unavailable'; end if;
 if p_input<0 or p_output<0 or p_ms<0 then raise exception 'Invalid usage'; end if;
 update public.ai_budget_reservations set settled_at=now(),reserved_tokens=case when p_input is not null and p_output is not null then greatest(p_input+p_output,0) else reserved_tokens end where id=r.id;
 insert into public.ai_usage_events(tenant_id,actor_id,model,input_tokens,output_tokens,duration_ms,outcome)
 values(r.tenant_id,r.actor_id,left(p_model,200),p_input,p_output,p_ms,case when p_success then 'success' else 'failure' end);
end $$;
revoke all on function public.record_ai_usage(uuid,text,bigint,bigint,integer,boolean) from public,anon,authenticated;
grant execute on function public.record_ai_usage(uuid,text,bigint,bigint,integer,boolean) to service_role;
-- The source-health record distinguishes disconnected telemetry from an empty inventory.
alter table public.cloud_snapshots drop constraint cloud_snapshots_kind_check;
alter table public.cloud_snapshots add constraint cloud_snapshots_kind_check check(kind in ('label_compliance','cost','inventory','observed_drift'));
commit;
