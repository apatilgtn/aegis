-- Reviewed service-to-IaC routes and atomic GitHub proposal completion.

create table public.service_entitlements (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references public.tenants(id) on delete cascade,
  service_id uuid not null references public.services(id) on delete cascade,
  cloud text not null check (cloud in ('gcp','azure')),
  environment text not null check (environment in ('prod','nonprod')),
  tier text not null check (tier in ('tier1','tier2','tier3')),
  entitlement_type text not null check (entitlement_type in ('gcp_iam_binding','entra_group')),
  target_identifier text not null check (char_length(target_identifier) between 3 and 255),
  role_identifier text not null check (char_length(role_identifier) between 3 and 255),
  terraform_module_source text not null check (terraform_module_source ~ '^modules/(gcp|azure)/[a-z0-9_/-]+$'),
  max_ttl_hours integer not null check (max_ttl_hours between 1 and 168),
  enabled boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique(service_id,cloud,environment,tier),
  unique(tenant_id,id)
);

create index service_entitlements_tenant_service_idx on public.service_entitlements(tenant_id,service_id);
alter table public.service_entitlements enable row level security;
alter table public.service_entitlements force row level security;
revoke all on table public.service_entitlements from public,anon,authenticated;
grant select on table public.service_entitlements to authenticated;
grant select,insert,update,delete on table public.service_entitlements to service_role;

create policy service_entitlements_select_for_members on public.service_entitlements
for select to authenticated using (
  exists(select 1 from public.tenant_memberships m
    where m.tenant_id=service_entitlements.tenant_id
      and m.user_id=(select auth.uid()) and m.status='active')
);

insert into public.service_entitlements(
  tenant_id,service_id,cloud,environment,tier,entitlement_type,target_identifier,
  role_identifier,terraform_module_source,max_ttl_hours
)
select s.tenant_id,s.id,'gcp','nonprod','tier1','gcp_iam_binding','ual-demo-af168',
       'roles/viewer','modules/gcp/iam_binding',72
from public.services s join public.tenants t on t.id=s.tenant_id
where t.slug='aegis' and s.slug='aegis-control-plane'
on conflict(service_id,cloud,environment,tier) do update set
  entitlement_type=excluded.entitlement_type,target_identifier=excluded.target_identifier,
  role_identifier=excluded.role_identifier,terraform_module_source=excluded.terraform_module_source,
  max_ttl_hours=excluded.max_ttl_hours,enabled=true,updated_at=now();

insert into public.service_entitlements(
  tenant_id,service_id,cloud,environment,tier,entitlement_type,target_identifier,
  role_identifier,terraform_module_source,max_ttl_hours
)
select s.tenant_id,s.id,'gcp','nonprod','tier2','gcp_iam_binding','ual-demo-af168',
       'roles/editor','modules/gcp/iam_binding',24
from public.services s join public.tenants t on t.id=s.tenant_id
where t.slug='aegis' and s.slug='aegis-control-plane'
on conflict(service_id,cloud,environment,tier) do update set
  entitlement_type=excluded.entitlement_type,target_identifier=excluded.target_identifier,
  role_identifier=excluded.role_identifier,terraform_module_source=excluded.terraform_module_source,
  max_ttl_hours=excluded.max_ttl_hours,enabled=true,updated_at=now();

create function public.complete_github_proposal(
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
  if p_branch!~'^aegis/access-request-[0-9a-f-]{36}$'
     or p_commit_sha!~'^[0-9a-f]{40}$'
     or p_file_path!~'^infra/access-grants/[0-9a-f-]{36}\.tf$' then
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
  seq:=aegis_private.next_workflow_sequence(w.id);
  insert into public.workflow_events(tenant_id,workflow_run_id,sequence_number,event_type,payload)
  values(r.tenant_id,w.id,seq,'github.proposal_created',refs->'github');
  insert into public.audit_events(tenant_id,action,object_type,object_id,correlation_id,metadata)
  values(r.tenant_id,'github.proposal_created','action_request',r.id::text,w.id,refs->'github');
end $$;

revoke all on function public.complete_github_proposal(uuid,text,bigint,text,text,text,text) from public,anon,authenticated;
grant execute on function public.complete_github_proposal(uuid,text,bigint,text,text,text,text) to service_role;
