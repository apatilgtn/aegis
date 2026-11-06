-- ServiceNow becomes the approval of record, and two new capabilities
-- arrive: incident triage (read logs, optionally raise an incident) and
-- label fixes (add missing required labels to a GCP bucket via a PR).

-- 1. Capabilities ------------------------------------------------------------------------------
alter table public.action_requests drop constraint if exists action_requests_capability_check;
alter table public.action_requests add constraint action_requests_capability_check
  check (capability in ('access_grant','incident_triage','label_fix'));

-- 2. One approval path: ServiceNow. Nobody can approve from the portal or API.
revoke insert, update, delete on table public.approvals from authenticated, anon;

-- 3. Which GCP project (and billing export) each workspace governs ------------------------------
create table public.tenant_cloud_accounts (
  tenant_id uuid not null references public.tenants(id) on delete cascade,
  cloud text not null check (cloud = 'gcp'),
  project_id text not null check (project_id ~ '^[a-z][a-z0-9-]{4,28}[a-z0-9]$'),
  environment text not null default 'nonprod' check (environment in ('nonprod','prod')),
  billing_table text check (billing_table is null or billing_table ~ '^[a-z][a-z0-9-]{4,28}[a-z0-9]\.[A-Za-z0-9_]+\.gcp_billing_export(_resource)?_v1_[0-9A-F_]+$'),
  enabled boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (tenant_id, cloud, project_id)
);
alter table public.tenant_cloud_accounts enable row level security;
alter table public.tenant_cloud_accounts force row level security;
revoke all on table public.tenant_cloud_accounts from public, anon, authenticated;
grant select on table public.tenant_cloud_accounts to authenticated;
grant select, insert, update, delete on table public.tenant_cloud_accounts to service_role;
create policy tenant_cloud_accounts_select_for_members on public.tenant_cloud_accounts for select to authenticated using (
  exists(select 1 from public.tenant_memberships m
    where m.tenant_id = tenant_cloud_accounts.tenant_id and m.user_id = (select auth.uid()) and m.status = 'active'));

insert into public.tenant_cloud_accounts(tenant_id, cloud, project_id)
select t.id, 'gcp', 'project-4f0a7dac-0503-4e87-abe' from public.tenants t where t.slug = 'aegis'
on conflict do nothing;

-- 4. Read-only snapshots the worker refreshes (label compliance, cost) --------------------------
create table public.cloud_snapshots (
  id bigint generated always as identity primary key,
  tenant_id uuid not null references public.tenants(id) on delete cascade,
  kind text not null check (kind in ('label_compliance','cost')),
  project_id text not null,
  captured_at timestamptz not null default now(),
  data jsonb not null check (jsonb_typeof(data) = 'object')
);
create index cloud_snapshots_latest_idx on public.cloud_snapshots(tenant_id, kind, captured_at desc);
alter table public.cloud_snapshots enable row level security;
alter table public.cloud_snapshots force row level security;
revoke all on table public.cloud_snapshots from public, anon, authenticated;
grant select on table public.cloud_snapshots to authenticated;
grant select, insert, delete on table public.cloud_snapshots to service_role;
create policy cloud_snapshots_select_for_members on public.cloud_snapshots for select to authenticated using (
  exists(select 1 from public.tenant_memberships m
    where m.tenant_id = cloud_snapshots.tenant_id and m.user_id = (select auth.uid()) and m.status = 'active'));

-- 5. Where the audit export to Cloud Logging has got to --------------------------------------------
create table public.audit_export_state (
  tenant_id uuid primary key references public.tenants(id) on delete cascade,
  last_event_id bigint not null default 0,
  updated_at timestamptz not null default now()
);
alter table public.audit_export_state enable row level security;
alter table public.audit_export_state force row level security;
revoke all on table public.audit_export_state from public, anon, authenticated;
grant select, insert, update on table public.audit_export_state to service_role;

-- Shared helper: record a workflow event and its audit twin.
create or replace function aegis_private.record_event(
  p_tenant uuid, p_run uuid, p_request uuid, p_event text, p_actor uuid, p_payload jsonb
) returns void language plpgsql security definer set search_path = '' as $$
declare seq bigint; payload jsonb;
begin
  payload := coalesce(p_payload, '{}'::jsonb) || jsonb_build_object('action_request_id', p_request);
  seq := aegis_private.next_workflow_sequence(p_run);
  insert into public.workflow_events(tenant_id, workflow_run_id, sequence_number, event_type, actor_id, payload)
  values (p_tenant, p_run, seq, p_event, p_actor, payload);
  insert into public.audit_events(tenant_id, actor_id, action, object_type, object_id, correlation_id, metadata)
  values (p_tenant, p_actor, p_event, 'action_request', p_request::text, p_run, payload);
end $$;
revoke all on function aegis_private.record_event(uuid, uuid, uuid, text, uuid, jsonb) from public, anon, authenticated;

-- Shared helper: lock a leased job of the given type plus its run and request.
create or replace function aegis_private.lock_job(p_job_id uuid, p_worker_id text, p_job_type text)
returns table(job public.workflow_jobs, run public.workflow_runs, request public.action_requests)
language plpgsql security definer set search_path = '' as $$
declare j public.workflow_jobs; w public.workflow_runs; r public.action_requests;
begin
  if coalesce(auth.role(), '') <> 'service_role' then raise exception 'service_role required'; end if;
  select * into j from public.workflow_jobs where id = p_job_id for update;
  if not found or j.status <> 'leased' or j.locked_by <> p_worker_id or j.job_type <> p_job_type then
    raise exception '% job lease is not owned by this worker', p_job_type;
  end if;
  select * into w from public.workflow_runs where id = j.workflow_run_id for update;
  select * into r from public.action_requests where id = w.action_request_id for update;
  return query select j, w, r;
end $$;
revoke all on function aegis_private.lock_job(uuid, text, text) from public, anon, authenticated;

create or replace function aegis_private.requeue_job(p_job_id uuid, p_seconds integer) returns void
language sql security definer set search_path = '' as $$
  update public.workflow_jobs set status = 'queued', attempts = 0, lease_until = null, locked_by = null, last_error = null,
    available_at = now() + make_interval(secs => greatest(30, least(coalesce(p_seconds, 60), 3600))), updated_at = now()
  where id = p_job_id;
$$;
revoke all on function aegis_private.requeue_job(uuid, integer) from public, anon, authenticated;

create or replace function aegis_private.finish_job(p_job_id uuid) returns void
language sql security definer set search_path = '' as $$
  update public.workflow_jobs set status = 'succeeded', lease_until = null, locked_by = null, updated_at = now() where id = p_job_id;
$$;
revoke all on function aegis_private.finish_job(uuid) from public, anon, authenticated;

create or replace function aegis_private.enqueue(p_tenant uuid, p_run uuid, p_request uuid, p_job_type text) returns void
language sql security definer set search_path = '' as $$
  insert into public.workflow_jobs(tenant_id, workflow_run_id, job_type, idempotency_key, payload)
  values (p_tenant, p_run, p_job_type, p_job_type || ':' || p_request::text, jsonb_build_object('action_request_id', p_request))
  on conflict (tenant_id, idempotency_key) do nothing;
$$;
revoke all on function aegis_private.enqueue(uuid, uuid, uuid, text) from public, anon, authenticated;

-- 6. Policy result routes each capability to its next step -------------------------------------
create or replace function public.complete_policy_evaluation(p_job_id uuid, p_worker_id text, p_allowed boolean, p_decision jsonb)
returns void language plpgsql security definer set search_path = '' as $$
declare l record; next_status text; next_run text; next_job text;
begin
  select * into l from aegis_private.lock_job(p_job_id, p_worker_id, 'evaluate_policy');
  if (l.request).status <> 'awaiting_policy' then raise exception 'Action request is not awaiting policy'; end if;
  if p_allowed then
    if (l.request).capability = 'incident_triage' then
      next_status := 'executing'; next_run := 'running'; next_job := 'search_logs';
    else
      next_status := 'awaiting_approval'; next_run := 'waiting_approval'; next_job := 'servicenow_approval';
    end if;
  else
    next_status := 'rejected'; next_run := 'failed';
  end if;
  update public.action_requests set status = next_status,
    risk_level = coalesce(nullif(p_decision->>'risk_level', ''), 'unknown'), policy_decision = p_decision,
    decided_at = case when p_allowed then null else now() end, updated_at = now()
  where id = (l.request).id;
  update public.workflow_runs set status = next_run, completed_at = case when p_allowed then null else now() end, updated_at = now()
  where id = (l.run).id;
  perform aegis_private.finish_job(p_job_id);
  if next_job is not null then
    perform aegis_private.enqueue((l.request).tenant_id, (l.run).id, (l.request).id, next_job);
  end if;
  perform aegis_private.record_event((l.request).tenant_id, (l.run).id, (l.request).id,
    case when p_allowed then 'policy.allowed' else 'policy.denied' end, null, p_decision);
end $$;
revoke all on function public.complete_policy_evaluation(uuid, text, boolean, jsonb) from public, anon, authenticated;
grant execute on function public.complete_policy_evaluation(uuid, text, boolean, jsonb) to service_role;

-- 7. ServiceNow approval -----------------------------------------------------------------------
create function public.record_servicenow_approval(
  p_job_id uuid, p_worker_id text, p_state text, p_details jsonb, p_recheck_seconds integer default 30
) returns void language plpgsql security definer set search_path = '' as $$
declare l record; first_report boolean;
begin
  if p_state not in ('requested', 'approved', 'rejected') then raise exception 'Unknown approval state %', p_state; end if;
  if jsonb_typeof(coalesce(p_details, '{}'::jsonb)) <> 'object' or coalesce(p_details->>'change_number', '') !~ '^CHG[0-9]{5,}$' then
    raise exception 'Approval details must include the ServiceNow change number';
  end if;
  select * into l from aegis_private.lock_job(p_job_id, p_worker_id, 'servicenow_approval');
  if (l.request).status <> 'awaiting_approval' then raise exception 'Action request is not awaiting approval'; end if;
  first_report := (l.run).external_references->'servicenow' is null;
  update public.workflow_runs set external_references = external_references || jsonb_build_object('servicenow', p_details),
    updated_at = now() where id = (l.run).id;
  if first_report then
    perform aegis_private.record_event((l.request).tenant_id, (l.run).id, (l.request).id, 'servicenow.change_opened', null, p_details);
  end if;

  if p_state = 'requested' then
    perform aegis_private.requeue_job(p_job_id, p_recheck_seconds);
    return;
  end if;

  perform aegis_private.finish_job(p_job_id);
  if p_state = 'approved' then
    update public.action_requests set status = 'proposal_pending', decided_at = now(), updated_at = now() where id = (l.request).id;
    update public.workflow_runs set status = 'waiting_delivery', updated_at = now() where id = (l.run).id;
    perform aegis_private.enqueue((l.request).tenant_id, (l.run).id, (l.request).id, 'create_github_proposal');
  else
    update public.action_requests set status = 'rejected', decided_at = now(), updated_at = now() where id = (l.request).id;
    update public.workflow_runs set status = 'failed', completed_at = now(), last_error_code = 'rejected_in_servicenow',
      last_error_summary = 'Rejected in ServiceNow', updated_at = now() where id = (l.run).id;
  end if;
  perform aegis_private.record_event((l.request).tenant_id, (l.run).id, (l.request).id, 'approval.' || p_state, null, p_details);
end $$;
revoke all on function public.record_servicenow_approval(uuid, text, text, jsonb, integer) from public, anon, authenticated;
grant execute on function public.record_servicenow_approval(uuid, text, text, jsonb, integer) to service_role;

-- 8. Incident triage -----------------------------------------------------------------------------
create function public.record_log_search(p_job_id uuid, p_worker_id text, p_summary text, p_findings jsonb)
returns void language plpgsql security definer set search_path = '' as $$
declare l record; findings jsonb;
begin
  if jsonb_typeof(coalesce(p_findings, '[]'::jsonb)) <> 'array' then raise exception 'Findings must be an array'; end if;
  select * into l from aegis_private.lock_job(p_job_id, p_worker_id, 'search_logs');
  if (l.request).capability <> 'incident_triage' or (l.request).status <> 'executing' then
    raise exception 'Action request is not an incident search in progress';
  end if;
  select coalesce(jsonb_agg(f), '[]'::jsonb) into findings
  from (select f from jsonb_array_elements(coalesce(p_findings, '[]'::jsonb)) f limit 20) s;
  perform aegis_private.finish_job(p_job_id);
  update public.action_requests set status = 'awaiting_approval', updated_at = now() where id = (l.request).id;
  update public.workflow_runs set status = 'waiting_approval',
    external_references = external_references || jsonb_build_object('triage', jsonb_build_object(
      'summary', left(coalesce(p_summary, ''), 2000), 'finding_count', jsonb_array_length(coalesce(p_findings, '[]'::jsonb)))),
    updated_at = now() where id = (l.run).id;
  perform aegis_private.record_event((l.request).tenant_id, (l.run).id, (l.request).id, 'triage.findings_ready', null,
    jsonb_build_object('summary', left(coalesce(p_summary, ''), 2000), 'findings', findings,
                       'finding_count', jsonb_array_length(coalesce(p_findings, '[]'::jsonb))));
end $$;
revoke all on function public.record_log_search(uuid, text, text, jsonb) from public, anon, authenticated;
grant execute on function public.record_log_search(uuid, text, text, jsonb) to service_role;

-- The requester decides whether the findings deserve a ServiceNow incident.
create function public.decide_incident(p_request_id uuid, p_raise boolean)
returns void language plpgsql security definer set search_path = '' as $$
declare r public.action_requests; w public.workflow_runs;
begin
  select * into r from public.action_requests where id = p_request_id for update;
  if not found or r.requester_id <> (select auth.uid()) then raise exception 'Only the requester can decide this'; end if;
  if r.capability <> 'incident_triage' or r.status <> 'awaiting_approval' then raise exception 'These findings are not waiting for a decision'; end if;
  if not exists(select 1 from public.tenant_memberships m where m.tenant_id = r.tenant_id and m.user_id = r.requester_id and m.status = 'active') then
    raise exception 'You do not have active workspace access';
  end if;
  select * into w from public.workflow_runs where action_request_id = r.id for update;
  if p_raise then
    update public.action_requests set status = 'executing', updated_at = now() where id = r.id;
    update public.workflow_runs set status = 'running', updated_at = now() where id = w.id;
    perform aegis_private.enqueue(r.tenant_id, w.id, r.id, 'create_servicenow_incident');
  else
    update public.action_requests set status = 'cancelled', decided_at = now(), updated_at = now() where id = r.id;
    update public.workflow_runs set status = 'cancelled', completed_at = now(), updated_at = now() where id = w.id;
  end if;
  perform aegis_private.record_event(r.tenant_id, w.id, r.id, case when p_raise then 'triage.incident_requested' else 'triage.closed_without_incident' end,
    r.requester_id, '{}'::jsonb);
end $$;
revoke all on function public.decide_incident(uuid, boolean) from public, anon;
grant execute on function public.decide_incident(uuid, boolean) to authenticated;

create function public.record_servicenow_incident(p_job_id uuid, p_worker_id text, p_details jsonb)
returns void language plpgsql security definer set search_path = '' as $$
declare l record;
begin
  if coalesce(p_details->>'incident_number', '') !~ '^INC[0-9]{5,}$' then raise exception 'Incident details must include the incident number'; end if;
  select * into l from aegis_private.lock_job(p_job_id, p_worker_id, 'create_servicenow_incident');
  if (l.request).capability <> 'incident_triage' or (l.request).status <> 'executing' then raise exception 'No incident is being raised'; end if;
  perform aegis_private.finish_job(p_job_id);
  update public.action_requests set status = 'completed', decided_at = now(), updated_at = now() where id = (l.request).id;
  update public.workflow_runs set status = 'succeeded', completed_at = now(),
    external_references = external_references || jsonb_build_object('servicenow', p_details), updated_at = now()
  where id = (l.run).id;
  perform aegis_private.record_event((l.request).tenant_id, (l.run).id, (l.request).id, 'servicenow.incident_created', null, p_details);
end $$;
revoke all on function public.record_servicenow_incident(uuid, text, jsonb) from public, anon, authenticated;
grant execute on function public.record_servicenow_incident(uuid, text, jsonb) to service_role;

-- 9. Point the access levels at the governed project (keeps step 1 of 20261106090000 idempotent).
update public.service_entitlements set target_identifier = 'project-4f0a7dac-0503-4e87-abe', updated_at = now()
where cloud = 'gcp' and target_identifier = 'ual-demo-af168';
