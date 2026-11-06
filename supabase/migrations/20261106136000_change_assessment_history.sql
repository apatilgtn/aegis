begin;
create table public.change_assessments (
 id uuid primary key default gen_random_uuid(),tenant_id uuid not null references public.tenants(id),
 created_by uuid not null references auth.users(id),action_request_id uuid references public.action_requests(id),
 input_sha256 text not null check(input_sha256 ~ '^[a-f0-9]{64}$'),report jsonb not null check(jsonb_typeof(report)='object' and pg_column_size(report)<=250000),
 source text not null default 'client_advisory' check(source='client_advisory'),created_at timestamptz not null default now()
);
alter table public.change_assessments enable row level security;
revoke all on public.change_assessments from public,anon,authenticated;
grant select,insert on public.change_assessments to authenticated;
grant all on public.change_assessments to service_role;
create policy tenant_read on public.change_assessments for select to authenticated using(exists(select 1 from public.tenant_memberships m where m.tenant_id=change_assessments.tenant_id and m.user_id=(select auth.uid()) and m.status='active'));
create policy own_assessment_insert on public.change_assessments for insert to authenticated with check(created_by=(select auth.uid()) and exists(select 1 from public.tenant_memberships m where m.tenant_id=change_assessments.tenant_id and m.user_id=(select auth.uid()) and m.status='active') and (action_request_id is null or exists(select 1 from public.action_requests r where r.id=change_assessments.action_request_id and r.tenant_id=change_assessments.tenant_id)));
create trigger immutable_assessment before update or delete on public.change_assessments for each row execute function aegis_private.reject_receipt_mutation();
create index assessments_tenant_time on public.change_assessments(tenant_id,created_at desc);
commit;
