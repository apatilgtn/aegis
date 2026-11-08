begin;
alter table public.governance_settings add column fallback_model text check(fallback_model is null or fallback_model ~ '^[a-z0-9-]+/[a-z0-9._-]+$');
alter table public.governance_settings add column fallback_threshold_pct integer not null default 80 check(fallback_threshold_pct between 1 and 100);
alter table public.ai_usage_events add column feature text not null default 'assistant';
create function public.ai_budget_status(p_tenant uuid) returns jsonb language plpgsql security definer set search_path='' as $$
declare s public.governance_settings; used bigint;
begin
 if not exists(select 1 from public.tenant_memberships where tenant_id=p_tenant and user_id=(select auth.uid()) and status='active') then raise exception 'Active membership required'; end if;
 select * into s from public.governance_settings where tenant_id=p_tenant;
 select coalesce(sum(reserved_tokens),0) into used from public.ai_budget_reservations where tenant_id=p_tenant and created_at>=date_trunc('day',now() at time zone 'UTC') at time zone 'UTC';
 return jsonb_build_object('budget',s.daily_ai_token_budget,'used',used,'fallback_model',s.fallback_model,'fallback_threshold_pct',s.fallback_threshold_pct,'resets_at',(date_trunc('day',now() at time zone 'UTC')+interval '1 day') at time zone 'UTC');
end $$;
revoke all on function public.ai_budget_status(uuid) from public,anon;
grant execute on function public.ai_budget_status(uuid) to authenticated;
create function public.set_ai_routing(p_tenant uuid,p_model text,p_threshold integer) returns void language plpgsql security definer set search_path='' as $$
begin
 if not exists(select 1 from public.tenant_memberships where tenant_id=p_tenant and user_id=(select auth.uid()) and status='active' and role='platform_admin') then raise exception 'Platform administrator required'; end if;
 if p_threshold not between 1 and 100 or (nullif(p_model,'') is not null and p_model !~ '^[a-z0-9-]+/[a-z0-9._-]+$') then raise exception 'Invalid model identifier or threshold'; end if;
 update public.governance_settings set fallback_model=nullif(p_model,''),fallback_threshold_pct=p_threshold,revision=revision+1,updated_at=now() where tenant_id=p_tenant;
 insert into public.audit_events(tenant_id,actor_id,action,object_type,object_id,metadata) values(p_tenant,(select auth.uid()),'ai.routing_changed','governance_settings',p_tenant::text,jsonb_build_object('fallback_model',nullif(p_model,''),'threshold_pct',p_threshold));
end $$;
revoke all on function public.set_ai_routing(uuid,text,integer) from public,anon;
grant execute on function public.set_ai_routing(uuid,text,integer) to authenticated;
drop function public.record_ai_usage(uuid,text,bigint,bigint,integer,boolean);
create function public.record_ai_usage(p_reservation uuid,p_model text,p_input bigint,p_output bigint,p_ms integer,p_success boolean,p_feature text default 'assistant') returns void language plpgsql security definer set search_path='' as $$
declare r public.ai_budget_reservations;
begin
 if coalesce(auth.role(),'') <> 'service_role' then raise exception 'Service role required'; end if;
 select * into r from public.ai_budget_reservations where id=p_reservation for update;
 if not found or r.settled_at is not null then raise exception 'Reservation unavailable'; end if;
 if p_input<0 or p_output<0 or p_ms<0 then raise exception 'Invalid usage'; end if;
 update public.ai_budget_reservations set settled_at=now(),reserved_tokens=case when p_input is not null and p_output is not null then greatest(p_input+p_output,0) else reserved_tokens end where id=r.id;
 insert into public.ai_usage_events(tenant_id,actor_id,model,input_tokens,output_tokens,duration_ms,outcome,feature)
 values(r.tenant_id,r.actor_id,left(p_model,200),p_input,p_output,p_ms,case when p_success then 'success' else 'failure' end,left(p_feature,80));
end $$;
revoke all on function public.record_ai_usage(uuid,text,bigint,bigint,integer,boolean,text) from public,anon,authenticated;
grant execute on function public.record_ai_usage(uuid,text,bigint,bigint,integer,boolean,text) to service_role;

commit;
