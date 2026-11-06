begin;
-- Canonicalize project/environment/scope even when a caller uses PostgREST directly.
create function aegis_private.canonical_request_scope() returns trigger language plpgsql security definer set search_path='' as $$
declare account public.tenant_cloud_accounts; route public.service_entitlements; accounts integer; recent integer;
begin
 perform pg_advisory_xact_lock(hashtextextended(new.tenant_id::text||new.requester_id::text,0));
 select count(*) into recent from public.action_requests where tenant_id=new.tenant_id and requester_id=new.requester_id and created_at>now()-interval '1 minute';
 if recent>=10 then raise exception 'Request rate limit reached; retry in a minute'; end if;
 select count(*) into accounts from public.tenant_cloud_accounts where tenant_id=new.tenant_id and cloud='gcp' and enabled;
 if accounts<>1 then raise exception 'One governed GCP project must be configured'; end if;
 select * into account from public.tenant_cloud_accounts where tenant_id=new.tenant_id and cloud='gcp' and enabled;
 if coalesce(new.resource->>'cloud','gcp')<>'gcp' then raise exception 'Only the governed GCP project is supported'; end if;
 if new.capability='access_grant' then
  select * into strict route from public.service_entitlements where tenant_id=new.tenant_id and service_id=new.service_id and cloud='gcp' and environment=account.environment and tier=new.resource->>'tier' and target_identifier=account.project_id and enabled;
  if not found then raise exception 'No reviewed route matches the governed project and environment'; end if;
  new.resource:=new.resource||jsonb_build_object('scope_type',route.scope_type,'scope_identifier',route.scope_identifier);
 end if;
 new.resource:=new.resource||jsonb_build_object('cloud','gcp','environment',account.environment,'project_id',account.project_id);
 return new;
end $$;
create trigger action_request_canonical_scope before insert on public.action_requests for each row execute function aegis_private.canonical_request_scope();
create index action_requests_actor_rate on public.action_requests(tenant_id,requester_id,created_at desc);
create index ai_reservation_budget on public.ai_budget_reservations(tenant_id,created_at);
create index usage_tenant_time on public.ai_usage_events(tenant_id,occurred_at desc);
create index source_snapshot_project on public.cloud_snapshots(tenant_id,project_id,kind,captured_at desc);
commit;
