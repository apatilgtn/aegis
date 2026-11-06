-- Point every reviewed GCP access route at the new GCP project, and move
-- off basic roles: GCP cannot attach an expiry condition to owner/editor/
-- viewer, and they are far broader than a time-bound grant needs.
--   tier1 (read): roles/logging.viewer  — read logs for debugging
--   tier2 (edit): roles/storage.objectUser — read/write objects, no bucket admin
-- Requests already proposed keep their history; only new proposals change.

update public.service_entitlements
set target_identifier = 'project-4f0a7dac-0503-4e87-abe',
    role_identifier = case tier
      when 'tier1' then 'roles/logging.viewer'
      when 'tier2' then 'roles/storage.objectUser'
      else role_identifier
    end,
    updated_at = now()
where cloud = 'gcp'
  and entitlement_type = 'gcp_iam_binding'
  and target_identifier = 'ual-demo-af168';
