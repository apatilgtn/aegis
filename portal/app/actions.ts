"use server";

import { revalidatePath } from "next/cache";
import { createClient } from "@/lib/supabase/server";
import { chat, draftRequest, nvidiaConfigured, type RequestDraft } from "@/lib/nvidia";

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
  draft?: RequestDraft;
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

  const { data: route, error: routeError } = await context.supabase
    .from("service_entitlements")
    .select("max_ttl_hours")
    .eq("tenant_id", context.membership.tenant_id)
    .eq("service_id", service.id)
    .eq("cloud", cloud)
    .eq("environment", environment)
    .eq("tier", tier)
    .eq("enabled", true)
    .maybeSingle();

  if (routeError || !route) {
    return initialError("That access level is not offered for this service. Pick one of the listed options.");
  }
  if (ttlHours > route.max_ttl_hours) {
    return initialError(`This access level can be requested for at most ${route.max_ttl_hours} hours.`);
  }

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

const SAFE_HINT = /^[A-Za-z0-9 ._:/@-]{2,120}$/;
const BUCKET_NAME = /^[a-z0-9][a-z0-9._-]{1,220}[a-z0-9]$/;
const COST_CENTER = /^[a-z0-9][a-z0-9_-]{1,62}$/;
const SEARCH_WINDOWS = [1, 4, 24, 72, 168];

async function governingService(context: Exclude<Awaited<ReturnType<typeof authenticatedContext>>, { error: string }>) {
  const { data } = await context.supabase
    .from("services")
    .select("id,slug")
    .eq("tenant_id", context.membership.tenant_id)
    .order("created_at")
    .limit(1)
    .maybeSingle();
  return data;
}

async function insertRequest(
  context: Exclude<Awaited<ReturnType<typeof authenticatedContext>>, { error: string }>,
  capability: "incident_triage" | "label_fix",
  justification: string,
  resource: Record<string, unknown>,
): Promise<ActionState> {
  const service = await governingService(context);
  const { data: request, error } = await context.supabase
    .from("action_requests")
    .insert({
      tenant_id: context.membership.tenant_id,
      requester_id: context.userId,
      beneficiary_id: context.userId,
      service_id: service?.id ?? null,
      capability,
      justification,
      status: "awaiting_policy",
      risk_level: "unknown",
      resource,
      idempotency_key: crypto.randomUUID(),
      submitted_at: new Date().toISOString(),
    })
    .select("id")
    .single();
  if (error) return initialError(error.message);
  revalidatePath("/");
  return { ok: true, message: "Submitted. Aegis is checking it against policy now.", createdId: request.id };
}

export async function createIncidentTriage(_previousState: ActionState, formData: FormData): Promise<ActionState> {
  const context = await authenticatedContext();
  if ("error" in context) return initialError(context.error ?? "Authentication failed.");
  const hint = String(formData.get("resourceHint") ?? "").trim();
  const hours = Number(formData.get("timeWindowHours"));
  const justification = String(formData.get("justification") ?? "").trim();
  if (!SAFE_HINT.test(hint)) {
    return initialError("Name the service or resource using letters, numbers, spaces, '.', '_', '-', ':', '/' or '@' (2–120 characters).");
  }
  if (!SEARCH_WINDOWS.includes(hours)) return initialError("Choose one of the listed time windows.");
  if (justification.length < 10 || justification.length > 2000) {
    return initialError("Describe the problem in 10 to 2,000 characters.");
  }
  return insertRequest(context, "incident_triage", justification, {
    cloud: "gcp",
    resource_hint: hint,
    time_window_hours: hours,
  });
}

export async function createLabelFix(_previousState: ActionState, formData: FormData): Promise<ActionState> {
  const context = await authenticatedContext();
  if ("error" in context) return initialError(context.error ?? "Authentication failed.");
  const bucket = String(formData.get("bucket") ?? "").trim();
  const costCenter = String(formData.get("costCenter") ?? "").trim().toLowerCase();
  const justification = String(formData.get("justification") ?? "").trim();
  if (!BUCKET_NAME.test(bucket)) return initialError("Choose a bucket from the list.");
  if (!COST_CENTER.test(costCenter)) {
    return initialError("Cost center must be 2–63 lowercase letters, numbers, '_' or '-' (for example pmt-01).");
  }
  if (justification.length < 10 || justification.length > 2000) {
    return initialError("Justification must be between 10 and 2,000 characters.");
  }
  const { data: account } = await context.supabase
    .from("tenant_cloud_accounts")
    .select("environment")
    .eq("tenant_id", context.membership.tenant_id)
    .eq("enabled", true)
    .limit(1)
    .maybeSingle();
  if (!account) return initialError("This workspace has no GCP project connected yet.");
  return insertRequest(context, "label_fix", justification, {
    cloud: "gcp",
    environment: account.environment,
    bucket,
    cost_center: costCenter,
  });
}

export async function decideIncident(_previousState: ActionState, formData: FormData): Promise<ActionState> {
  const context = await authenticatedContext();
  if ("error" in context) return initialError(context.error ?? "Authentication failed.");
  const requestId = String(formData.get("requestId") ?? "");
  const raise = String(formData.get("decision") ?? "") === "raise";
  if (!/^[0-9a-f-]{36}$/.test(requestId)) return initialError("That request could not be found.");
  const { error } = await context.supabase.rpc("decide_incident", { p_request_id: requestId, p_raise: raise });
  if (error) return initialError(error.message);
  revalidatePath("/");
  return {
    ok: true,
    message: raise ? "Raising the incident in ServiceNow now." : "Closed without raising an incident.",
  };
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

  if (nvidiaConfigured()) {
    try {
      const [{ data: labelSnapshot }, { data: costSnapshot }] = await Promise.all([
        context.supabase.from("cloud_snapshots").select("captured_at,data").eq("tenant_id", tenantId).eq("kind", "label_compliance")
          .order("captured_at", { ascending: false }).limit(1).maybeSingle(),
        context.supabase.from("cloud_snapshots").select("captured_at,data").eq("tenant_id", tenantId).eq("kind", "cost")
          .order("captured_at", { ascending: false }).limit(1).maybeSingle(),
      ]);
      const labelData = (labelSnapshot?.data ?? null) as { buckets?: { bucket: string; missing: string[] }[] } | null;
      const buckets = (labelData?.buckets ?? []).filter((item) => item.missing.length).map((item) => item.bucket);
      const draft = await draftRequest(question, buckets);
      if (draft.capability !== "unknown") {
        return {
          ok: true,
          message: "Draft ready — check it before submitting",
          answer: "This looks like a request. Open the prepared form, check every field, then submit it yourself.",
          evidence: ["NVIDIA model draft", "form validation and policy still apply"],
          draft,
        };
      }
      const workspace = {
        services: services.map((service) => ({ name: service.name, lifecycle: service.lifecycle, tier: service.tier })),
        requests: requests.slice(0, 30).map((request) => ({
          status: request.status, risk: request.risk_level, created_at: request.created_at,
          resource: request.resource,
        })),
        recent_events: events.slice(0, 30).map((event) => ({ type: event.event_type, at: event.occurred_at })),
        label_compliance: labelData,
        cost: costSnapshot?.data ?? null,
      };
      const answer = await chat([
        {
          role: "system",
          content:
            "You are Aegis, a cloud governance assistant. Answer only from the WORKSPACE JSON. If the answer is not in it, say so. " +
            "Use plain language, at most 120 words. Never claim you can approve, merge or change anything.",
        },
        { role: "user", content: `WORKSPACE:\n${JSON.stringify(workspace).slice(0, 12000)}\n\nQUESTION: ${question}` },
      ]);
      return { ok: true, message: "Answer from your live workspace", answer, evidence: ["services", "action_requests", "workflow_events", "cloud_snapshots"] };
    } catch {
      // Fall through to the rule-based answer if the model is unavailable.
    }
  }

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
