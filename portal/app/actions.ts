"use server";

import { revalidatePath } from "next/cache";
import { createClient } from "@/lib/supabase/server";

export type ActionState = {
  ok: boolean;
  message: string;
  createdId?: string;
};

export type AegisAnswerState = {
  ok: boolean;
  message: string;
  answer: string;
  evidence: string[];
};

const initialError = (message: string): ActionState => ({ ok: false, message });

async function authenticatedContext() {
  const supabase = await createClient();
  const { data: claimsData, error: claimsError } = await supabase.auth.getClaims();
  const userId = claimsData?.claims?.sub;

  if (claimsError || !userId) {
    return { error: "Your session has expired. Please sign in again." } as const;
  }

  const { data: membership, error: membershipError } = await supabase
    .from("tenant_memberships")
    .select("tenant_id,role,status")
    .eq("user_id", userId)
    .eq("status", "active")
    .limit(1)
    .maybeSingle();

  if (membershipError || !membership) {
    return { error: "You do not have active platform access." } as const;
  }

  return { supabase, userId, membership } as const;
}

export async function createAccessRequest(
  _previousState: ActionState,
  formData: FormData,
): Promise<ActionState> {
  const context = await authenticatedContext();
  if ("error" in context) return initialError(context.error ?? "Authentication failed.");

  const serviceId = String(formData.get("serviceId") ?? "").trim();
  const cloud = String(formData.get("cloud") ?? "").trim();
  const environment = String(formData.get("environment") ?? "").trim();
  const tier = String(formData.get("tier") ?? "").trim();
  const justification = String(formData.get("justification") ?? "").trim();
  const ttlHours = Number(formData.get("ttlHours"));

  if (!serviceId || !["azure", "gcp"].includes(cloud)) {
    return initialError("Choose a service and cloud provider.");
  }
  if (!["prod", "nonprod"].includes(environment) || !["tier1", "tier2", "tier3"].includes(tier)) {
    return initialError("Choose a valid environment and access tier.");
  }
  if (justification.length < 10 || justification.length > 2000) {
    return initialError("Justification must be between 10 and 2,000 characters.");
  }
  if (!Number.isInteger(ttlHours) || ttlHours < 1 || ttlHours > 168) {
    return initialError("Access duration must be between 1 and 168 hours.");
  }

  const { data: service, error: serviceError } = await context.supabase
    .from("services")
    .select("id,slug")
    .eq("id", serviceId)
    .eq("tenant_id", context.membership.tenant_id)
    .maybeSingle();

  if (serviceError || !service) return initialError("That service is not available in your workspace.");

  const { data: request, error } = await context.supabase
    .from("action_requests")
    .insert({
      tenant_id: context.membership.tenant_id,
      requester_id: context.userId,
      beneficiary_id: context.userId,
      service_id: service.id,
      capability: "access_grant",
      justification,
      status: "awaiting_policy",
      risk_level: "unknown",
      resource: {
        cloud,
        environment,
        tier,
        ttl_hours: ttlHours,
        service_slug: service.slug,
      },
      idempotency_key: crypto.randomUUID(),
      submitted_at: new Date().toISOString(),
    })
    .select("id")
    .single();

  if (error) return initialError(error.message);

  revalidatePath("/");
  return { ok: true, message: "Request submitted for policy evaluation.", createdId: request.id };
}

export async function decideAccessRequest(
  _previousState: ActionState,
  formData: FormData,
): Promise<ActionState> {
  const context = await authenticatedContext();
  if ("error" in context) return initialError(context.error ?? "Authentication failed.");
  if (!["approver", "platform_admin"].includes(context.membership.role)) {
    return initialError("Your role cannot approve requests.");
  }

  const requestId = String(formData.get("requestId") ?? "").trim();
  const decision = String(formData.get("decision") ?? "").trim();
  const reason = String(formData.get("reason") ?? "").trim();

  if (!requestId || !["approved", "rejected"].includes(decision)) {
    return initialError("Choose a valid request and decision.");
  }
  if (decision === "rejected" && reason.length < 5) {
    return initialError("Add a short reason when rejecting a request.");
  }

  const { data: request, error: requestError } = await context.supabase
    .from("action_requests")
    .select("id,requester_id,beneficiary_id,status,tenant_id")
    .eq("id", requestId)
    .eq("tenant_id", context.membership.tenant_id)
    .maybeSingle();

  if (requestError || !request) return initialError("Request not found.");
  if (request.requester_id === context.userId || request.beneficiary_id === context.userId) {
    return initialError("Separation of duties prevents you from approving your own request.");
  }

  const { error } = await context.supabase.from("approvals").insert({
    tenant_id: context.membership.tenant_id,
    action_request_id: request.id,
    approver_id: context.userId,
    decision,
    reason: reason || null,
  });

  if (error) return initialError(error.message);

  revalidatePath("/");
  return { ok: true, message: decision === "approved" ? "Request approved." : "Request rejected." };
}

export async function createService(
  _previousState: ActionState,
  formData: FormData,
): Promise<ActionState> {
  const context = await authenticatedContext();
  if ("error" in context) return initialError(context.error ?? "Authentication failed.");
  if (context.membership.role !== "platform_admin") return initialError("Only platform administrators can add services.");

  const name = String(formData.get("name") ?? "").trim();
  const description = String(formData.get("description") ?? "").trim();
  const lifecycle = String(formData.get("lifecycle") ?? "production").trim();
  const tier = String(formData.get("tier") ?? "").trim();
  const repositoryUrl = String(formData.get("repositoryUrl") ?? "").trim();
  const slug = name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 64);

  if (name.length < 2 || !slug) return initialError("Service name must contain at least two letters or numbers.");
  if (!["experimental", "production", "deprecated"].includes(lifecycle)) return initialError("Choose a valid lifecycle.");
  if (repositoryUrl && !/^https:\/\/(github\.com|gitlab\.com)\//i.test(repositoryUrl)) {
    return initialError("Repository URL must be an HTTPS GitHub or GitLab URL.");
  }

  const { data, error } = await context.supabase
    .from("services")
    .insert({
      tenant_id: context.membership.tenant_id,
      name,
      slug,
      description: description || null,
      lifecycle,
      tier: tier || null,
      repository_url: repositoryUrl || null,
      created_by: context.userId,
    })
    .select("id")
    .single();

  if (error) return initialError(error.code === "23505" ? "A service with that name already exists." : error.message);

  revalidatePath("/");
  return { ok: true, message: "Service added to the live catalog.", createdId: data.id };
}

export async function updateDisplayName(
  _previousState: ActionState,
  formData: FormData,
): Promise<ActionState> {
  const context = await authenticatedContext();
  if ("error" in context) return initialError(context.error ?? "Authentication failed.");

  const displayName = String(formData.get("displayName") ?? "").trim();
  if (displayName.length < 2 || displayName.length > 80) {
    return initialError("Display name must be between 2 and 80 characters.");
  }

  const { error } = await context.supabase.auth.updateUser({
    data: { display_name: displayName },
  });
  if (error) return initialError(error.message);

  revalidatePath("/");
  return { ok: true, message: "Profile updated." };
}

const readableStatus = (value: string) => value.replaceAll("_", " ");

export async function askAegis(
  _previousState: AegisAnswerState,
  formData: FormData,
): Promise<AegisAnswerState> {
  const context = await authenticatedContext();
  if ("error" in context) {
    return { ok: false, message: context.error ?? "Authentication failed.", answer: "", evidence: [] };
  }

  const question = String(formData.get("question") ?? "").trim();
  if (question.length < 3 || question.length > 500) {
    return { ok: false, message: "Ask a question between 3 and 500 characters.", answer: "", evidence: [] };
  }

  const tenantId = context.membership.tenant_id;
  const [servicesResult, requestsResult, eventsResult] = await Promise.all([
    context.supabase.from("services").select("id,name,lifecycle,tier").eq("tenant_id", tenantId).order("name").limit(50),
    context.supabase.from("action_requests").select("id,status,risk_level,resource,created_at,services(name)").eq("tenant_id", tenantId).order("created_at", { ascending: false }).limit(50),
    context.supabase.from("workflow_events").select("event_type,occurred_at").eq("tenant_id", tenantId).order("occurred_at", { ascending: false }).limit(50),
  ]);

  const queryError = servicesResult.error || requestsResult.error || eventsResult.error;
  if (queryError) {
    return { ok: false, message: "Aegis could not read the live workspace safely. Try again.", answer: "", evidence: [] };
  }

  const services = servicesResult.data ?? [];
  const requests = requestsResult.data ?? [];
  const events = eventsResult.data ?? [];
  const normalized = question.toLowerCase();
  const awaitingApproval = requests.filter((request) => request.status === "awaiting_approval");
  const active = requests.filter((request) => ["awaiting_policy", "awaiting_approval", "proposal_pending", "pr_open", "executing"].includes(request.status));
  const failed = requests.filter((request) => ["failed", "rejected", "cancelled", "expired"].includes(request.status));
  const latest = requests[0];
  let answer: string;
  let evidence: string[];

  if (/approv|waiting|pending/.test(normalized)) {
    answer = awaitingApproval.length
      ? `${awaitingApproval.length} request${awaitingApproval.length === 1 ? " is" : "s are"} waiting for an independent approval. ${active.length} governed workflow${active.length === 1 ? " is" : "s are"} active overall.`
      : `The independent approval queue is clear. ${active.length} governed workflow${active.length === 1 ? " remains" : "s remain"} active in other stages.`;
    evidence = ["action_requests.status", "tenant membership scope"];
  } else if (/service|catalog|owner/.test(normalized)) {
    const names = services.slice(0, 5).map((service) => service.name).join(", ");
    answer = services.length
      ? `The live catalog contains ${services.length} service${services.length === 1 ? "" : "s"}. ${names}${services.length > 5 ? ", and more" : ""}.`
      : "The live service catalog is empty. A platform administrator must add a service before governed actions can be submitted.";
    evidence = ["services", "tenant membership scope"];
  } else if (/fail|error|reject|risk|problem/.test(normalized)) {
    const workflowFailures = events.filter((event) => /fail|dead_letter/.test(event.event_type));
    answer = `${failed.length} request${failed.length === 1 ? " has" : "s have"} a terminal unsuccessful status, and ${workflowFailures.length} recent workflow failure event${workflowFailures.length === 1 ? " is" : "s are"} visible in the current evidence window.`;
    evidence = ["action_requests.status", "workflow_events.event_type"];
  } else if (/latest|recent|last/.test(normalized)) {
    const serviceRelation = latest?.services;
    const service = Array.isArray(serviceRelation) ? serviceRelation[0] : serviceRelation;
    answer = latest
      ? `The latest request is for ${service?.name ?? "an uncatalogued service"}; its current status is ${readableStatus(latest.status)} and its recorded risk is ${readableStatus(latest.risk_level)}.`
      : "There are no access requests in this workspace yet.";
    evidence = ["action_requests.created_at", "action_requests.status", "services.name"];
  } else {
    answer = `This workspace has ${services.length} catalogued service${services.length === 1 ? "" : "s"}, ${active.length} active governed workflow${active.length === 1 ? "" : "s"}, ${awaitingApproval.length} request${awaitingApproval.length === 1 ? "" : "s"} awaiting approval, and ${failed.length} unsuccessful request${failed.length === 1 ? "" : "s"} in the current history.`;
    evidence = ["services", "action_requests", "workflow_events"];
  }

  return { ok: true, message: "Live workspace analysis", answer, evidence };
}
