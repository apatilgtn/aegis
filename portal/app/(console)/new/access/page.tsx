import { AccessForm, type AccessOption } from "@/components/forms";
import { PageHead } from "@/components/ui";
import { governanceState, requireContext, servicesWithAccess } from "@/lib/data";

export default async function NewAccessPage({ searchParams }: { searchParams: Promise<{ tier?: string; hours?: string; reason?: string }> }) {
  const context = await requireContext();
  const params = await searchParams;
  const [{ services, entitlements }, governance] = await Promise.all([servicesWithAccess(context), governanceState(context)]);
  const policy = governance.policies.find(p => p.capability === "access_grant");
  const names = new Map(services.map((service) => [service.id, service.name]));
  const options: AccessOption[] = entitlements
    .filter((e) => names.has(e.service_id))
    .map((e) => ({ serviceId: e.service_id, serviceName: names.get(e.service_id)!, cloud: e.cloud, environment: e.environment, tier: e.tier, role: e.role_identifier, maxHours: Math.min(e.max_ttl_hours, policy?.max_ttl_hours ?? e.max_ttl_hours), target: e.scope_type === "bucket" ? e.scope_identifier ?? e.target_identifier : e.target_identifier }));
  return (
    <>
      <PageHead title="Request access" lede="Time-bound access to the governed GCP project. It is granted to the account you are signed in with." />
      {(!governance.settings?.writes_enabled || governance.settings.proposals_paused || !policy?.production_allowed) && <div className="workflow-note" style={{marginBottom:20}}><p>Governed production requests require enabled write and capability controls. An administrator can review their availability in Governance controls. Submissions still pass the server-side policy check.</p></div>}
      <AccessForm options={options} identity={context.email} prefill={{ tier: params.tier, hours: Number(params.hours) || undefined, reason: params.reason }} />
    </>
  );
}
