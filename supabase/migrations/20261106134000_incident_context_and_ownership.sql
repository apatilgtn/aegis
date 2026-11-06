begin;
create table public.triage_change_context (
 action_request_id uuid primary key references public.action_requests(id),tenant_id uuid not null references public.tenants(id),
 changes jsonb not null default '[]',state text not null check(state in ('available','unavailable')),
 coverage jsonb not null, captured_at timestamptz not null default now()
);
alter table public.triage_change_context enable row level security;
revoke all on public.triage_change_context from public,anon,authenticated;
grant select on public.triage_change_context to authenticated;
grant all on public.triage_change_context to service_role;
create policy tenant_read on public.triage_change_context for select to authenticated using(exists(select 1 from public.tenant_memberships m where m.tenant_id=triage_change_context.tenant_id and m.user_id=(select auth.uid()) and m.status='active'));
alter table public.services add column owner_label text check(char_length(owner_label)<=120);
alter table public.services add column runbook_url text check(runbook_url is null or runbook_url ~ '^https://');

create function public.set_service_ownership(p_service uuid,p_owner text,p_runbook text) returns void language plpgsql security definer set search_path='' as $$
declare tenant uuid;
begin
 select tenant_id into tenant from public.services where id=p_service for update;
 if tenant is null or not exists(select 1 from public.tenant_memberships where tenant_id=tenant and user_id=(select auth.uid()) and status='active' and role='platform_admin') then raise exception 'Platform administrator required'; end if;
 if char_length(p_owner)>120 or char_length(p_runbook)>2000 or (nullif(p_runbook,'') is not null and p_runbook !~ '^https://') then raise exception 'Invalid ownership metadata'; end if;
 update public.services set owner_label=nullif(p_owner,''),runbook_url=nullif(p_runbook,'') where id=p_service;
 insert into public.audit_events(tenant_id,actor_id,action,object_type,object_id,metadata) values(tenant,(select auth.uid()),'service.ownership_changed','service',p_service::text,jsonb_build_object('owner_label',p_owner,'runbook_url',p_runbook));
end $$;
revoke all on function public.set_service_ownership(uuid,text,text) from public,anon;
grant execute on function public.set_service_ownership(uuid,text,text) to authenticated;

commit;
