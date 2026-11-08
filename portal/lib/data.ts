// Server-only data loaders. Every query runs as the signed-in user, so
// Supabase row-level security decides what each person can see.
import { cache } from "react";
import { redirect } from "next/navigation";
import { createClient } from "@/lib/supabase/server";
import type { EventView, LogFinding, RequestView } from "@/lib/view";

export type Context = {
  supabase: Awaited<ReturnType<typeof createClient>>;
  userId: string;
  email: string;
  displayName: string;
  role: string;
  tenantId: string;
  tenantName: string;
};

/** Signed-in user and workspace, or null when they have no active membership yet. */
export const getContext = cache(async (): Promise<Context | null | { pending: { displayName: string; email: string } }> => {
  const supabase = await createClient();
  const { data, error } = await supabase.auth.getClaims();
  const claims = data?.claims;
  if (error || !claims?.sub) redirect("/login");
  const userId = claims.sub;
  const [{ data: profile }, { data: membership }] = await Promise.all([
    supabase.from("profiles").select("display_name").eq("user_id", userId).maybeSingle(),
    supabase
      .from("tenant_memberships")
      .select("role,status,tenant_id,tenants(name)")
      .eq("user_id", userId)
      .eq("status", "active")
      .limit(1)
      .maybeSingle(),
  ]);
  const email = typeof claims.email === "string" ? claims.email : "";
  const metaName = typeof claims.user_metadata?.display_name === "string" ? claims.user_metadata.display_name.trim() : "";
  const displayName = metaName || profile?.display_name?.trim() || email.split("@")[0] || "Aegis user";
  const tenant = membership?.tenants as { name: string } | { name: string }[] | null | undefined;
  const tenantName = Array.isArray(tenant) ? tenant[0]?.name : tenant?.name;
  if (!membership || !tenantName) return { pending: { displayName, email } };
  return { supabase, userId, email, displayName, role: membership.role, tenantId: membership.tenant_id, tenantName };
});

export async function requireContext(): Promise<Context> {
  const context = await getContext();
  if (!context || "pending" in context) redirect("/");
  return context;
}

const text = (value: unknown) => (typeof value === "string" && value ? value : null);
const number = (value: unknown) => (typeof value === "number" ? value : null);

type RequestRow = {
  id: string;
  requester_id: string;
  capability: string;
  justification: string;
  status: string;
  risk_level: string;
  resource: Record<string, unknown>;
  policy_decision: Record<string, unknown> | null;
  created_at: string;
  updated_at: string;
  decided_at: string | null;
  expires_at: string | null;
  services: { name: string } | { name: string }[] | null;
};

type RunRow = { action_request_id: string; external_references: Record<string, Record<string, unknown>> | null; last_error_summary: string | null };

function toView(row: RequestRow, run: RunRow | undefined, verification?: {state:string;checked_at:string}): RequestView {
  const refs = run?.external_references ?? {};
  const sn = refs.servicenow ?? {};
  const gh = refs.github ?? {};
  const delivery = refs.delivery ?? {};
  const triage = refs.triage ?? {};
  const decision = row.policy_decision ?? {};
  const service = Array.isArray(row.services) ? row.services[0] : row.services;
  return {
    id: row.id,
    shortId: `REQ-${row.id.slice(0, 8).toUpperCase()}`,
    capability: row.capability as RequestView["capability"],
    status: row.status,
    requesterId: row.requester_id,
    justification: row.justification,
    resource: row.resource ?? {},
    createdAt: row.created_at,
    updatedAt: row.updated_at,
    decidedAt: row.decided_at,
    expiresAt: row.expires_at ?? text(delivery.expires_at),
    serviceName: service?.name ?? null,
    policy: {
      allowed: typeof decision.allowed === "boolean" ? decision.allowed : null,
      reasons: Array.isArray(decision.reasons) ? decision.reasons.map(String) : [],
      policyIds: Array.isArray(decision.policy_ids) ? decision.policy_ids.map(String) : [],
      risk: row.risk_level,
    },
    servicenow: {
      changeNumber: text(sn.change_number),
      changeUrl: text(sn.change_url),
      approverEmail: text(sn.approver_email),
      incidentNumber: text(sn.incident_number),
      incidentUrl: text(sn.incident_url),
    },
    github: { prNumber: number(gh.pull_request_number), prUrl: text(gh.pull_request_url) },
    delivery: { runUrl: text(delivery.workflow_run_url), reason: text(delivery.reason), mergedAt: text(delivery.merged_at) },
    triage: { summary: text(triage.summary), findingCount: number(triage.finding_count) },
    lastError: run?.last_error_summary ?? null,
    verification: verification ? {state:verification.state,checkedAt:verification.checked_at} : undefined,
  };
}

const REQUEST_COLUMNS =
  "id,requester_id,capability,justification,status,risk_level,resource,policy_decision,created_at,updated_at,decided_at,expires_at,services(name)";

async function withRuns(context: Context, rows: RequestRow[]) {
  if (!rows.length) return [];
  const [{data:runs,error:runsError},{data:verified,error:verificationError}] = await Promise.all([
    context.supabase.from("workflow_runs").select("action_request_id,external_references,last_error_summary").in("action_request_id", rows.map(r=>r.id)),
    context.supabase.from("change_verifications").select("action_request_id,state,checked_at").in("action_request_id",rows.map(r=>r.id)),
  ]);
  if(runsError||verificationError) throw new Error("Workflow or verification evidence could not be read.");
  const byRequest=new Map((runs as RunRow[]|null??[]).map(run=>[run.action_request_id,run]));
  const byVerification=new Map((verified??[]).map(v=>[v.action_request_id,v]));
  return rows.map(row=>toView(row,byRequest.get(row.id),byVerification.get(row.id)));
}

export const listRequests = cache(async (context: Context, limit = 100): Promise<RequestView[]> => {
  const { data, error } = await context.supabase
    .from("action_requests")
    .select(REQUEST_COLUMNS)
    .eq("tenant_id", context.tenantId)
    .order("created_at", { ascending: false })
    .limit(limit);
  if (error) throw new Error("Requests could not be loaded. Please retry.");
  return withRuns(context, (data as RequestRow[] | null) ?? []);
});

export async function getRequest(context: Context, id: string) {
  if (!/^[0-9a-f-]{36}$/.test(id)) return null;
  const { data, error } = await context.supabase.from("action_requests").select(REQUEST_COLUMNS).eq("id", id).eq("tenant_id", context.tenantId).maybeSingle();
  if (error) throw new Error("The request could not be loaded.");
  if (!data) return null;
  const [request] = await withRuns(context, [data as RequestRow]);
  const { data: run } = await context.supabase.from("workflow_runs").select("id").eq("action_request_id", id).maybeSingle();
  let events: EventView[] = [];
  let findings: LogFinding[] = [];
  if (run) {
    const { data: rows } = await context.supabase
      .from("workflow_events")
      .select("id,event_type,occurred_at,payload")
      .eq("workflow_run_id", run.id)
      .order("sequence_number");
    events = (rows ?? []).map((row) => ({ id: row.id, type: row.event_type, at: row.occurred_at, payload: (row.payload ?? {}) as Record<string, unknown> }));
    const ready = [...events].reverse().find((event) => event.type === "triage.findings_ready");
    if (ready && Array.isArray(ready.payload.findings)) findings = ready.payload.findings as LogFinding[];
  }
  const [{data:monitor,error:monitorError}, { data: verification }, { data: receipt }, { data: planApprovals }, { data: changeContext }, {data:executionChecks,error:checksError}, {data:nativeAssessments,error:nativeError}] = await Promise.all([
    context.supabase.from("assurance_monitors").select("enabled,interval_minutes,next_check_at").eq("action_request_id",id).maybeSingle(),
    context.supabase.from("change_verifications").select("state,details,checked_at").eq("action_request_id", id).maybeSingle(),
    context.supabase.from("proposal_receipts").select("artifact_path,artifact_sha256,policy_sha256,expires_at,created_at,manifest,manifest_sha256").eq("action_request_id", id).maybeSingle(),
    context.supabase.from("plan_approvals").select("plan_sha256,commit_sha,change_number,state,approver_email,expires_at,manifest_sha256s").contains("request_ids", [id]).order("created_at", { ascending: false }),
    context.supabase.from("triage_change_context").select("changes,state,coverage,captured_at").eq("action_request_id",id).maybeSingle(),
    context.supabase.from("execution_check_events").select("*").eq("action_request_id",id).order("checked_at",{ascending:false}).limit(30),
    context.supabase.from("native_access_assessments").select("*").eq("action_request_id",id).order("checked_at",{ascending:false}).limit(5),
  ]);
  if(checksError||nativeError||monitorError) throw new Error("Assurance evidence could not be read.");
  if(executionChecks?.[0])request.execution={verdict:executionChecks[0].verdict,checkpoint:executionChecks[0].checkpoint,at:executionChecks[0].checked_at};
  if(planApprovals?.[0])request.executionApproval={state:planApprovals[0].state,changeNumber:planApprovals[0].change_number};
  return { monitor, request, events, findings, verification, receipt, changeContext, planApprovals: planApprovals ?? [], executionChecks:executionChecks??[], nativeAssessments:nativeAssessments??[] };
}

export type BucketCompliance = { bucket: string; location: string; labels: Record<string, string>; missing: string[] };
export type LabelSnapshot = { capturedAt: string; projectId: string; required: string[]; buckets: BucketCompliance[]; compliant: number; total: number };
export type CostRow = { cost_center: string | null; service: string; net_cost: string | null; currency: string | null };
export type CostSnapshot = { capturedAt: string; projectId: string; month: string; currency: string | null; total: number; untagged: number; rows: CostRow[] };

export async function latestSnapshot<T>(context: Context, kind: "label_compliance" | "cost" | "inventory" | "observed_drift"): Promise<T | null> {
  const { data, error } = await context.supabase
    .from("cloud_snapshots")
    .select("project_id,captured_at,data")
    .eq("tenant_id", context.tenantId)
    .eq("kind", kind)
    .order("captured_at", { ascending: false })
    .limit(1)
    .maybeSingle();
  if (error) throw new Error("Cloud evidence could not be read. Please retry.");
  if (!data) return null;
  return { ...(data.data as object), capturedAt: data.captured_at, projectId: data.project_id } as T;
}

export async function cloudAccount(context: Context) {
  const { data } = await context.supabase
    .from("tenant_cloud_accounts")
    .select("project_id,environment,billing_table")
    .eq("tenant_id", context.tenantId)
    .eq("enabled", true)
    .limit(1)
    .maybeSingle();
  return data ? { projectId: data.project_id as string, environment: data.environment as string, billingConnected: Boolean(data.billing_table) } : null;
}

export type ServiceRow = { id: string; name: string; slug: string; description: string | null; lifecycle: string; tier: string | null; repository_url: string | null; owner_label: string | null; runbook_url: string | null };
export type EntitlementRow = { id: string; service_id: string; cloud: string; environment: string; tier: string; max_ttl_hours: number; role_identifier: string; target_identifier: string; scope_type: string; scope_identifier: string | null };

export async function servicesWithAccess(context: Context) {
  const [{ data: services, error: serviceError }, { data: entitlements, error: entitlementError }] = await Promise.all([
    context.supabase.from("services").select("id,name,slug,description,lifecycle,tier,repository_url,owner_label,runbook_url").eq("tenant_id", context.tenantId).order("name"),
    context.supabase
      .from("service_entitlements")
      .select("id,service_id,cloud,environment,tier,max_ttl_hours,role_identifier,target_identifier,scope_type,scope_identifier")
      .eq("tenant_id", context.tenantId)
      .eq("enabled", true)
      .order("tier"),
  ]);
  if (serviceError || entitlementError) throw new Error("The service catalog could not be read. Please retry.");
  return { services: (services as ServiceRow[] | null) ?? [], entitlements: (entitlements as EntitlementRow[] | null) ?? [] };
}

export async function auditEvents(context: Context, limit = 100) {
  const { data, error } = await context.supabase
    .from("audit_events")
    .select("id,action,actor_id,object_type,object_id,occurred_at")
    .eq("tenant_id", context.tenantId)
    .order("occurred_at", { ascending: false })
    .limit(limit);
  return { events: data ?? [], allowed: !error };
}

/** Requests plus every recorded event for each, for timelines and timing metrics. */
export const listRequestsWithEvents = cache(async (context: Context) => {
  const requests = await listRequests(context);
  const eventsByRequest = new Map<string, EventView[]>();
  if (!requests.length) return { requests, eventsByRequest };
  const { data: runs } = await context.supabase
    .from("workflow_runs")
    .select("id,action_request_id")
    .in("action_request_id", requests.map((request) => request.id));
  const requestByRun = new Map((runs ?? []).map((run) => [run.id as string, run.action_request_id as string]));
  if (requestByRun.size) {
    const { data: rows } = await context.supabase
      .from("workflow_events")
      .select("id,workflow_run_id,event_type,occurred_at,payload")
      .in("workflow_run_id", [...requestByRun.keys()])
      .order("occurred_at")
      .limit(2000);
    for (const row of rows ?? []) {
      const requestId = requestByRun.get(row.workflow_run_id as string);
      if (!requestId) continue;
      const list = eventsByRequest.get(requestId) ?? [];
      list.push({ id: row.id, type: row.event_type, at: row.occurred_at, payload: (row.payload ?? {}) as Record<string, unknown> });
      eventsByRequest.set(requestId, list);
    }
  }
  return { requests, eventsByRequest };
});

export type GovernanceSettings = { tenant_id: string; writes_enabled: boolean; proposals_paused: boolean; executions_paused: boolean; approval_ttl_minutes: number; daily_ai_token_budget: number; fallback_model: string | null; fallback_threshold_pct: number; revision: number; updated_at: string };
export type CapabilityPolicy = { capability: string; enabled: boolean; production_allowed: boolean; max_ttl_hours: number; allowed_roles: string[] };
export async function governanceState(context: Context) {
  const [{ data: settings, error }, { data: policies, error: policyError }] = await Promise.all([
    context.supabase.from("governance_settings").select("*").eq("tenant_id", context.tenantId).maybeSingle(),
    context.supabase.from("capability_policies").select("*").eq("tenant_id", context.tenantId).order("capability"),
  ]);
  return { settings: settings as GovernanceSettings | null, policies: (policies ?? []) as CapabilityPolicy[], available: !error && !policyError && Boolean(settings) };
}
export async function sourceHealth(context: Context) {
  const { data, error } = await context.supabase.from("cloud_source_health").select("project_id,source,state,summary,checked_at").eq("tenant_id", context.tenantId).order("source");
  if (error) throw new Error("Cloud connection health could not be loaded.");
  return data ?? [];
}
export async function operationalJobs(context: Context) {
  const { data, error } = await context.supabase.from("workflow_jobs").select("id,job_type,status,attempts,max_attempts,last_error,updated_at,workflow_run_id,available_at,lease_until").eq("tenant_id", context.tenantId).order("updated_at", { ascending: false }).limit(100);
  if (error) throw new Error("Operational jobs are visible to platform administrators and auditors.");
  return data ?? [];
}
export async function aiUsage(context: Context) {
  const { data, error } = await context.supabase.from("ai_usage_events").select("id,actor_id,feature,model,input_tokens,output_tokens,duration_ms,outcome,occurred_at").eq("tenant_id", context.tenantId).gte("occurred_at", new Date(Date.now() - 7 * 86_400_000).toISOString()).order("occurred_at", { ascending: false }).limit(1000);
  if (error) throw new Error("AI usage telemetry could not be loaded.");
  return data ?? [];
}
export type InventorySnapshot = { capturedAt: string; projectId: string; assets: { name: string; type: string; display_name: string; location: string; labels: Record<string,string>; state: string }[]; total: number };
export type DriftSnapshot = { capturedAt: string; projectId: string; baseline_captured_at: string; changes: { name: string; change: string; before: unknown; after: unknown }[]; scope: string };

export async function requestPage(context: Context, filters: { page: number; view: string; capability?: string; search?: string; mine?: boolean }) {
 const statuses:Record<string,string[]>={servicenow:["awaiting_approval"],done:["completed"],problems:["failed","rejected"],open:["awaiting_policy","awaiting_approval","proposal_pending","pr_open","merged","applying","executing"]};
 let query=context.supabase.from("action_requests").select(REQUEST_COLUMNS,{count:"exact"}).eq("tenant_id",context.tenantId);
 if(statuses[filters.view])query=query.in("status",statuses[filters.view]);
 if(filters.view==="servicenow")query=query.neq("capability","incident_triage");
 if(filters.view==="needs_me")query=query.eq("capability","incident_triage").eq("status","awaiting_approval").eq("requester_id",context.userId);
 if(filters.capability)query=query.eq("capability",filters.capability);
 if(filters.mine)query=query.eq("requester_id",context.userId);
 if(filters.search)query=query.ilike("justification",`%${filters.search.slice(0,100)}%`);
 const{data,error,count}=await query.order("created_at",{ascending:false}).range((filters.page-1)*25,filters.page*25-1);
 if(error)throw new Error("Requests could not be loaded. Retry without losing your filters.");
 return {requests:await withRuns(context,(data as RequestRow[]|null)??[]),count:count??0};
}
export async function personalAccess(context:Context){
 const columns=REQUEST_COLUMNS;
 const [{data:active,error:activeError},{data:pending,error:pendingError},{data:history,error:historyError}]=await Promise.all([
  context.supabase.from("action_requests").select(columns).eq("tenant_id",context.tenantId).eq("requester_id",context.userId).eq("capability","access_grant").eq("status","completed").gt("expires_at",new Date().toISOString()).order("expires_at"),
  context.supabase.from("action_requests").select(columns).eq("tenant_id",context.tenantId).eq("requester_id",context.userId).eq("capability","access_grant").in("status",["awaiting_policy","awaiting_approval","proposal_pending","pr_open","merged","applying"]).order("created_at",{ascending:false}),
  context.supabase.from("action_requests").select(columns).eq("tenant_id",context.tenantId).eq("requester_id",context.userId).eq("capability","access_grant").in("status",["completed","rejected","failed","cancelled","expired"]).order("created_at",{ascending:false}).limit(100),
 ]);
 if(activeError||pendingError||historyError)throw new Error("Personal access could not be loaded.");
 const a=await withRuns(context,(active as RequestRow[]|null)??[]),p=await withRuns(context,(pending as RequestRow[]|null)??[]),h=await withRuns(context,(history as RequestRow[]|null)??[]);
 const verified=a.filter(r=>r.verification?.state==="verified"),unverified=a.filter(r=>r.verification?.state!=="verified");
 return{active:verified,pending:[...unverified,...p],history:h.filter(r=>!a.some(v=>v.id===r.id))};
}
