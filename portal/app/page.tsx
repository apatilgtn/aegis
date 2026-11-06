import { redirect } from "next/navigation";
import { AccessPending } from "./access-pending";
import Dashboard from "./dashboard";
import { createClient } from "@/lib/supabase/server";
import type { CostSnapshot, DeliverySummary, LabelComplianceSnapshot, LogFinding, PortalData } from "@/lib/domain";

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
  const latestSnapshot = (kind: string) =>
    supabase
      .from("cloud_snapshots")
      .select("project_id,captured_at,data")
      .eq("tenant_id", typedMembership.tenant_id)
      .eq("kind", kind)
      .order("captured_at", { ascending: false })
      .limit(1)
      .maybeSingle();
  const [servicesResult, entitlementsResult, requestsResult, workflowEventsResult, auditEventsResult, accountResult, labelResult, costResult] = await Promise.all([
    supabase
      .from("services")
      .select("id,name,slug,description,lifecycle,tier,repository_url")
      .eq("tenant_id", typedMembership.tenant_id)
      .order("name"),
    supabase
      .from("service_entitlements")
      .select("service_id,cloud,environment,tier,max_ttl_hours")
      .eq("tenant_id", typedMembership.tenant_id)
      .eq("enabled", true)
      .order("tier"),
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
    supabase
      .from("tenant_cloud_accounts")
      .select("project_id,billing_table")
      .eq("tenant_id", typedMembership.tenant_id)
      .eq("enabled", true)
      .limit(1)
      .maybeSingle(),
    latestSnapshot("label_compliance"),
    latestSnapshot("cost"),
  ]);

  const queryError = [servicesResult.error, entitlementsResult.error, requestsResult.error, accountResult.error, labelResult.error, costResult.error, workflowEventsResult.error, auditEventsResult.error]
    .find(Boolean);

  const workflowEvents = workflowEventsResult.data ?? [];
  // Events are newest first, so the first value found for a request is the latest.
  const deliveryFor = (requestId: string): DeliverySummary => {
    const delivery: DeliverySummary = {
      pullRequestUrl: null, workflowRunUrl: null, expiresAt: null, reason: null, changeNumber: null, changeUrl: null,
      approverEmail: null, incidentNumber: null, incidentUrl: null, triageSummary: null, findings: [], findingCount: null,
    };
    for (const event of workflowEvents) {
      const payload = (event.payload ?? {}) as Record<string, unknown>;
      if (payload.action_request_id !== requestId) continue;
      const text = (key: string) => (typeof payload[key] === "string" ? (payload[key] as string) : null);
      delivery.pullRequestUrl ??= text("pull_request_url");
      delivery.workflowRunUrl ??= text("workflow_run_url");
      delivery.expiresAt ??= text("expires_at");
      delivery.reason ??= text("reason");
      delivery.changeNumber ??= text("change_number");
      delivery.changeUrl ??= text("change_url");
      delivery.approverEmail ??= text("approver_email");
      delivery.incidentNumber ??= text("incident_number");
      delivery.incidentUrl ??= text("incident_url");
      if (event.event_type === "triage.findings_ready" && delivery.triageSummary === null) {
        delivery.triageSummary = text("summary");
        delivery.findings = Array.isArray(payload.findings) ? (payload.findings as LogFinding[]).slice(0, 20) : [];
        delivery.findingCount = typeof payload.finding_count === "number" ? payload.finding_count : delivery.findings.length;
      }
    }
    return delivery;
  };

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
    entitlements: (entitlementsResult.data ?? []).map((route) => ({
      serviceId: route.service_id,
      cloud: route.cloud,
      environment: route.environment,
      tier: route.tier,
      maxTtlHours: route.max_ttl_hours,
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
      delivery: deliveryFor(request.id),
      createdAt: request.created_at,
      updatedAt: request.updated_at,
    })),
    workflowEvents: workflowEvents.map((event) => ({
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
    cloudProject: accountResult.data
      ? { projectId: accountResult.data.project_id, billingConnected: Boolean(accountResult.data.billing_table) }
      : null,
    labelCompliance: labelResult.data
      ? ({
          ...(labelResult.data.data as Omit<LabelComplianceSnapshot, "capturedAt" | "projectId">),
          capturedAt: labelResult.data.captured_at,
          projectId: labelResult.data.project_id,
        } as LabelComplianceSnapshot)
      : null,
    cost: costResult.data
      ? ({
          ...(costResult.data.data as Omit<CostSnapshot, "capturedAt" | "projectId">),
          capturedAt: costResult.data.captured_at,
          projectId: costResult.data.project_id,
        } as CostSnapshot)
      : null,
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
