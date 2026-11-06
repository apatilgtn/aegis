-- Classify the explicitly selected Aegis project and its access routes as prod.
-- Existing request resources and approvals remain historical evidence.
-- Production OPA deny rules are unchanged; this does not authorize execution.
begin;

with changed as (
  update public.tenant_cloud_accounts a
  set environment = 'prod', updated_at = now()
  where a.cloud = 'gcp'
    and a.project_id = 'project-4f0a7dac-0503-4e87-abe'
    and a.environment <> 'prod'
    and exists (select 1 from public.tenants t where t.id = a.tenant_id and t.slug = 'aegis')
  returning a.tenant_id, a.project_id
)
insert into public.audit_events(tenant_id, action, object_type, object_id, metadata)
select tenant_id, 'cloud_account.reclassified', 'tenant_cloud_account', project_id,
  jsonb_build_object('previous_environment', 'nonprod', 'environment', 'prod',
    'reason', 'Explicit project classification requested by the workspace operator',
    'production_policy', 'unchanged')
from changed;

update public.service_entitlements e
set environment = 'prod', updated_at = now()
where e.cloud = 'gcp'
  and e.target_identifier = 'project-4f0a7dac-0503-4e87-abe'
  and e.environment <> 'prod'
  and exists (
    select 1 from public.tenant_cloud_accounts a
    join public.tenants t on t.id = a.tenant_id
    where a.tenant_id = e.tenant_id and a.cloud = 'gcp'
      and a.project_id = e.target_identifier and a.environment = 'prod' and t.slug = 'aegis'
  );

commit;
