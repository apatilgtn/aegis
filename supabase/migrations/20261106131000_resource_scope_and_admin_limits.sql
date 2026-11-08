begin;
alter table public.service_entitlements add column scope_type text not null default 'project' check(scope_type in ('project','bucket'));
alter table public.service_entitlements add column scope_identifier text;
alter table public.service_entitlements add constraint entitlement_scope_check check(scope_type='project' or (scope_identifier is not null and scope_identifier ~ '^[a-z0-9][a-z0-9._-]{1,220}[a-z0-9]$'));
create function public.configure_entitlement_scope(p_route uuid,p_scope text,p_bucket text) returns void language plpgsql security definer set search_path='' as $$
declare route public.service_entitlements;
begin
 select * into route from public.service_entitlements where id=p_route for update;
 if not found or not exists(select 1 from public.tenant_memberships where tenant_id=route.tenant_id and user_id=(select auth.uid()) and role='platform_admin' and status='active') then raise exception 'Platform administrator required'; end if;
 if p_scope not in ('project','bucket') then raise exception 'Unsupported scope'; end if;
 if p_scope='bucket' and route.role_identifier not like 'roles/storage.%' then raise exception 'Bucket scope requires a reviewed storage role'; end if;
 if p_scope='bucket' and not exists(select 1 from public.cloud_snapshots s, jsonb_array_elements(s.data->'buckets') b where s.tenant_id=route.tenant_id and s.project_id=route.target_identifier and s.kind='label_compliance' and s.captured_at>now()-interval '30 minutes' and b->>'bucket'=p_bucket) then raise exception 'Bucket must be present in a recent project inventory'; end if;
 update public.service_entitlements set scope_type=p_scope,scope_identifier=case when p_scope='bucket' then p_bucket else null end,terraform_module_source=case when p_scope='bucket' then 'modules/gcp/bucket_iam_binding' else 'modules/gcp/iam_binding' end,updated_at=now() where id=p_route;
 update public.governance_settings set revision=revision+1,updated_at=now() where tenant_id=route.tenant_id;
 insert into public.audit_events(tenant_id,actor_id,action,object_type,object_id,metadata) values(route.tenant_id,(select auth.uid()),'entitlement.scope_changed','service_entitlement',p_route::text,jsonb_build_object('scope',p_scope,'bucket',p_bucket));
end $$;
revoke all on function public.configure_entitlement_scope(uuid,text,text) from public,anon;
grant execute on function public.configure_entitlement_scope(uuid,text,text) to authenticated;
create function public.set_governance_limit(p_tenant uuid,p_limit text,p_value bigint) returns void language plpgsql security definer set search_path='' as $$
begin
 if not exists(select 1 from public.tenant_memberships where tenant_id=p_tenant and user_id=(select auth.uid()) and role='platform_admin' and status='active') then raise exception 'Platform administrator required'; end if;
 if p_limit='daily_ai_token_budget' and p_value between 0 and 100000000 then
  update public.governance_settings set daily_ai_token_budget=p_value,revision=revision+1,updated_at=now() where tenant_id=p_tenant;
 elsif p_limit='approval_ttl_minutes' and p_value between 5 and 1440 then
  update public.governance_settings set approval_ttl_minutes=p_value,revision=revision+1,updated_at=now() where tenant_id=p_tenant;
 else raise exception 'Invalid limit or value'; end if;
 insert into public.audit_events(tenant_id,actor_id,action,object_type,object_id,metadata) values(p_tenant,(select auth.uid()),'governance.limit_changed','governance_settings',p_tenant::text,jsonb_build_object('limit',p_limit,'value',p_value));
end $$;
revoke all on function public.set_governance_limit(uuid,text,bigint) from public,anon;
grant execute on function public.set_governance_limit(uuid,text,bigint) to authenticated;
commit;
