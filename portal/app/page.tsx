import { redirect } from "next/navigation";
import { AccessPending } from "./access-pending";
import Dashboard from "./dashboard";
import { createClient } from "@/lib/supabase/server";
import type { PortalData } from "@/lib/domain";

export const dynamic = "force-dynamic";

type Membership = {
  role: string;
  status: string;
  tenant_id: string;
  tenants: { name: string; slug: string } | null;
};

export default async function Home() {
  const supabase = await createClient();
  const { data: claimsData, error: claimsError } = await supabase.auth.getClaims();
  const claims = claimsData?.claims;

  if (claimsError || !claims?.sub) {
    redirect("/login");
  }

  const userId = claims.sub;
  const [{ data: profile }, { data: membership }] = await Promise.all([
    supabase.from("profiles").select("display_name").eq("user_id", userId).maybeSingle(),
    supabase
      .from("tenant_memberships")
      .select("role,status,tenant_id,tenants(name,slug)")
      .eq("user_id", userId)
      .eq("status", "active")
      .limit(1)
      .maybeSingle(),
  ]);

  const typedMembership = membership as Membership | null;
  const email = typeof claims.email === "string" ? claims.email : "Signed-in user";
  const metadataDisplayName = typeof claims.user_metadata?.display_name === "string" ? claims.user_metadata.display_name.trim() : "";
  const displayName = metadataDisplayName || profile?.display_name?.trim() || email.split("@")[0] || "Aegis user";

  if (!typedMembership?.tenants) {
    return <AccessPending displayName={displayName} email={email} />;
  }

  const canReadAudit = ["platform_admin", "auditor"].includes(typedMembership.role);
  const [servicesResult, requestsResult, workflowEventsResult, auditEventsResult] = await Promise.all([
    supabase
      .from("services")
      .select("id,name,slug,description,lifecycle,tier,repository_url")
      .eq("tenant_id", typedMembership.tenant_id)
      .order("name"),
    supabase
      .from("action_requests")
      .select("id,requester_id,capability,justification,status,risk_level,resource,policy_decision,created_at,updated_at,services(id,name,slug)")
      .eq("tenant_id", typedMembership.tenant_id)
      .order("created_at", { ascending: false })
      .limit(50),
    supabase
      .from("workflow_events")
      .select("id,workflow_run_id,event_type,actor_id,occurred_at,payload")
      .eq("tenant_id", typedMembership.tenant_id)
      .order("occurred_at", { ascending: false })
      .limit(50),
    canReadAudit
      ? supabase
          .from("audit_events")
          .select("id,action,actor_id,object_type,object_id,occurred_at")
          .eq("tenant_id", typedMembership.tenant_id)
          .order("occurred_at", { ascending: false })
          .limit(25)
      : Promise.resolve({ data: [], error: null }),
  ]);

  const queryError = [servicesResult.error, requestsResult.error, workflowEventsResult.error, auditEventsResult.error]
    .find(Boolean);

  const portalData: PortalData = {
    services: (servicesResult.data ?? []).map((service) => ({
      id: service.id,
      name: service.name,
      slug: service.slug,
      description: service.description,
      lifecycle: service.lifecycle,
      tier: service.tier,
      repositoryUrl: service.repository_url,
    })),
    requests: (requestsResult.data ?? []).map((request) => ({
      id: request.id,
      requesterId: request.requester_id,
      capability: request.capability,
      justification: request.justification,
      status: request.status,
      riskLevel: request.risk_level,
      resource: request.resource as Record<string, unknown>,
      policyDecision: request.policy_decision as Record<string, unknown> | null,
      service: Array.isArray(request.services) ? request.services[0] ?? null : request.services,
      createdAt: request.created_at,
      updatedAt: request.updated_at,
    })),
    workflowEvents: (workflowEventsResult.data ?? []).map((event) => ({
      id: event.id,
      workflowRunId: event.workflow_run_id,
      eventType: event.event_type,
      actorId: event.actor_id,
      occurredAt: event.occurred_at,
      payload: event.payload as Record<string, unknown>,
    })),
    auditEvents: (auditEventsResult.data ?? []).map((event) => ({
      id: event.id,
      action: event.action,
      actorId: event.actor_id,
      objectType: event.object_type,
      objectId: event.object_id,
      occurredAt: event.occurred_at,
    })),
    loadError: queryError ? "Some live data could not be loaded. Try refreshing the page." : null,
  };

  return (
    <Dashboard
      displayName={displayName}
      email={email}
      role={typedMembership.role}
      tenantName={typedMembership.tenants.name}
      tenantId={typedMembership.tenant_id}
      userId={userId}
      data={portalData}
    />
  );
}
