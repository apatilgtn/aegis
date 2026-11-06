begin;
create function aegis_private.audit_plan_approval() returns trigger language plpgsql security definer set search_path='' as $$
begin
 if tg_op='INSERT' or new.state is distinct from old.state then
  insert into public.audit_events(tenant_id,action,object_type,object_id,metadata)
  values(new.tenant_id,'execution_plan.'||new.state,'execution_plan',new.id::text,jsonb_build_object('plan_sha256',new.plan_sha256,'commit_sha',new.commit_sha,'request_ids',new.request_ids,'change_number',new.change_number,'approver_email',new.approver_email,'expires_at',new.expires_at));
 end if;
 return new;
end $$;
create trigger plan_approval_audit after insert or update on public.plan_approvals for each row execute function aegis_private.audit_plan_approval();
create function aegis_private.audit_change_verification() returns trigger language plpgsql security definer set search_path='' as $$
begin
 insert into public.audit_events(tenant_id,action,object_type,object_id,metadata) values(new.tenant_id,'verification.'||new.state,'action_request',new.action_request_id::text,jsonb_build_object('details',new.details,'checked_at',new.checked_at));
 return new;
end $$;
create trigger change_verification_audit after insert or update on public.change_verifications for each row execute function aegis_private.audit_change_verification();
create function aegis_private.audit_advisory_assessment() returns trigger language plpgsql security definer set search_path='' as $$
begin
 insert into public.audit_events(tenant_id,actor_id,action,object_type,object_id,metadata) values(new.tenant_id,new.created_by,'assessment.saved','change_assessment',new.id::text,jsonb_build_object('input_sha256',new.input_sha256,'request_id',new.action_request_id,'source',new.source));
 return new;
end $$;
create trigger change_assessment_audit after insert on public.change_assessments for each row execute function aegis_private.audit_advisory_assessment();
create function aegis_private.audit_ai_usage() returns trigger language plpgsql security definer set search_path='' as $$
begin
 insert into public.audit_events(tenant_id,actor_id,action,object_type,object_id,metadata) values(new.tenant_id,new.actor_id,'ai.'||new.outcome,'ai_usage',new.id::text,jsonb_build_object('provider',new.provider,'model',new.model,'feature',new.feature,'input_tokens',new.input_tokens,'output_tokens',new.output_tokens,'duration_ms',new.duration_ms));
 return new;
end $$;
create trigger ai_usage_audit after insert on public.ai_usage_events for each row execute function aegis_private.audit_ai_usage();
commit;
