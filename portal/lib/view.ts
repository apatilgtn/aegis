// Shared, pure view helpers for requests (safe in server and client components).

export type Capability = "access_grant" | "incident_triage" | "label_fix";
export type Tone = "neutral" | "info" | "warn" | "good" | "bad";
export type StepState = "done" | "current" | "todo" | "failed" | "skipped";

export type LogFinding = { timestamp: string; severity: string; source: string; message: string };

export type RequestView = {
  id: string;
  shortId: string;
  capability: Capability;
  status: string;
  requesterId: string;
  justification: string;
  resource: Record<string, unknown>;
  createdAt: string;
  updatedAt: string;
  decidedAt: string | null;
  expiresAt: string | null;
  serviceName: string | null;
  policy: { allowed: boolean | null; reasons: string[]; policyIds: string[]; risk: string };
  servicenow: { changeNumber: string | null; changeUrl: string | null; approverEmail: string | null; incidentNumber: string | null; incidentUrl: string | null };
  github: { prNumber: number | null; prUrl: string | null };
  delivery: { runUrl: string | null; reason: string | null; mergedAt: string | null };
  triage: { summary: string | null; findingCount: number | null };
  lastError: string | null;
  verification?: {state:string;checkedAt:string};
  execution?: {verdict:string;checkpoint:string;at:string};
  executionApproval?: {state:string;changeNumber:string|null};
};

export type EventView = { id: number; type: string; at: string; payload: Record<string, unknown> };

const TIERS: Record<string, string> = { tier1: "Tier 1 · read logs", tier2: "Tier 2 · read/write storage", tier3: "Tier 3 · privileged" };

export function tierLabel(tier: unknown) {
  return TIERS[String(tier)] ?? String(tier ?? "access");
}

export function title(request: RequestView) {
  const r = request.resource;
  if (request.capability === "incident_triage") return `Investigate ${String(r.resource_hint ?? "an issue")}`;
  if (request.capability === "label_fix") return `Add missing labels to ${String(r.bucket ?? "a bucket")}`;
  return `${tierLabel(r.tier)} access`;
}

export function subtitle(request: RequestView) {
  const r = request.resource;
  if (request.capability === "incident_triage") return `Warnings and errors · last ${hours(r.time_window_hours)}`;
  if (request.capability === "label_fix") return `Cost center ${String(r.cost_center ?? "?")}`;
  return `${request.serviceName ? `${request.serviceName} · ` : ""}GCP ${r.environment === "prod" ? "production" : r.environment === "nonprod" ? "non-production" : "environment unspecified"} · ${hours(r.ttl_hours)}`;
}

export function capabilityLabel(capability: Capability) {
  return { access_grant: "Access", incident_triage: "Investigation", label_fix: "Label fix" }[capability];
}

function hours(value: unknown) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "?";
  if (n === 168) return "7 days";
  if (n % 24 === 0 && n >= 24) return `${n / 24} day${n === 24 ? "" : "s"}`;
  return `${n} hour${n === 1 ? "" : "s"}`;
}

/** One plain-language status per request, chosen for the person reading it. */
export function statusOf(request: RequestView): { label: string; tone: Tone } {
  const s = request.status;
  if (request.capability === "incident_triage") {
    return (
      {
        awaiting_policy: { label: "Checking policy", tone: "info" },
        executing: { label: request.triage.summary ? "Raising incident" : "Searching logs", tone: "info" },
        awaiting_approval: { label: "Findings ready", tone: "warn" },
        completed: { label: "Incident raised", tone: "good" },
        cancelled: { label: "Closed, no incident", tone: "neutral" },
        rejected: { label: "Not allowed", tone: "bad" },
        failed: { label: "Failed", tone: "bad" },
      } as Record<string, { label: string; tone: Tone }>
    )[s] ?? { label: s, tone: "neutral" };
  }
  if (s === "completed" && request.capability === "access_grant" && request.expiresAt && new Date(request.expiresAt) <= new Date()) {
    return { label: "Grant window ended", tone: "neutral" };
  }
  if(s === "completed") {
    const state=request.verification?.state;
    return state==="verified" ? {label:request.capability==="label_fix"?"Labels verified":"Access verified",tone:"good"} : state==="failed" ? {label:"Verification mismatch",tone:"bad"} : {label:state==="unavailable"?"Read-back unavailable":"Pipeline finished · unverified",tone:"warn"};
  }
  return (
    {
      awaiting_policy: { label: "Checking policy", tone: "info" },
      awaiting_approval: { label: "Waiting in ServiceNow", tone: "warn" },
      proposal_pending: { label: "Opening pull request", tone: "info" },
      pr_open: { label: "Waiting for merge", tone: "warn" },
      merged: { label: "Merged, pipeline starting", tone: "info" },
      applying: { label: "Applying in GCP", tone: "info" },
      completed: { label: request.capability === "label_fix" ? "Labels applied" : "Access live", tone: "good" },
      rejected: { label: request.policy.allowed === false ? "Not allowed by policy" : "Rejected in ServiceNow", tone: "bad" },
      cancelled: { label: "Cancelled", tone: "neutral" },
      failed: { label: "Failed", tone: "bad" },
    } as Record<string, { label: string; tone: Tone }>
  )[s] ?? { label: s, tone: "neutral" };
}

export function needsMe(request: RequestView, userId: string) {
  return request.capability === "incident_triage" && request.status === "awaiting_approval" && request.requesterId === userId;
}

export function isOpen(request: RequestView) {
  return !["completed", "rejected", "cancelled", "failed", "expired", "revoked"].includes(request.status);
}

export type Step = { key: string; label: string; state: StepState; detail?: string; href?: string | null };

const ORDER = ["awaiting_policy", "awaiting_approval", "proposal_pending", "pr_open", "merged", "applying", "completed"];

/** The governed journey of a request, step by step. */
export function stepsOf(request: RequestView): Step[] {
  const s = request.status;
  const terminalBad = ["rejected", "failed", "cancelled"].includes(s);
  const policyDenied = request.policy.allowed === false;

  if (request.capability === "incident_triage") {
    const searched = Boolean(request.triage.summary);
    const raised = Boolean(request.servicenow.incidentNumber);
    return [
      { key: "submitted", label: "Submitted", state: "done", detail: formatDate(request.createdAt) },
      policyStep(request),
      {
        key: "search", label: "Search Cloud Logging",
        state: policyDenied ? "skipped" : searched ? "done" : s === "executing" ? "current" : s === "failed" ? "failed" : "todo",
        detail: searched ? `${request.triage.findingCount ?? 0} matching entries` : undefined,
      },
      {
        key: "decide", label: "Your decision",
        state: policyDenied ? "skipped" : s === "awaiting_approval" ? "current" : raised || (s === "executing" && searched) ? "done" : s === "cancelled" ? "done" : "todo",
        detail: s === "cancelled" ? "Closed without an incident" : raised || (s === "executing" && searched) ? "Raise an incident" : undefined,
      },
      {
        key: "incident", label: "ServiceNow incident",
        state: s === "cancelled" || policyDenied ? "skipped" : raised ? "done" : s === "executing" && searched ? "current" : s === "failed" ? "failed" : "todo",
        detail: request.servicenow.incidentNumber ?? undefined, href: request.servicenow.incidentUrl,
      },
    ];
  }

  const at = ORDER.indexOf(s);
  const reached = (status: string) => at >= ORDER.indexOf(status) && at !== -1;
  const rejectedInSnow = s === "rejected" && !policyDenied;
  const prClosed = s === "cancelled";
  const steps: Step[] = [
    { key: "submitted", label: "Submitted", state: "done", detail: formatDate(request.createdAt) },
    policyStep(request),
    {
      key: "approval", label: "ServiceNow approval",
      state: policyDenied ? "skipped" : rejectedInSnow ? "failed" : reached("proposal_pending") || prClosed || Boolean(request.servicenow.approverEmail) ? "done" : s === "awaiting_approval" ? "current" : "todo",
      detail: request.servicenow.approverEmail
        ? `Approved by ${request.servicenow.approverEmail}`
        : rejectedInSnow ? "Rejected" : request.servicenow.changeNumber ?? (s === "awaiting_approval" ? "Opening change" : undefined),
      href: request.servicenow.changeUrl,
    },
    {
      key: "pr", label: "Pull request",
      state: policyDenied || rejectedInSnow ? "skipped" : prClosed ? "failed" : reached("merged") || (s === "failed" && Boolean(request.delivery.mergedAt)) ? "done" : reached("proposal_pending") ? "current" : "todo",
      detail: prClosed ? "Closed without merging" : request.github.prNumber ? `#${request.github.prNumber}${reached("merged") || request.delivery.mergedAt ? " merged" : s === "failed" ? " opened before failure" : " waiting for review"}` : undefined,
      href: request.github.prUrl,
    },
    {
      key: "apply", label: "Protected pipeline",
      state: policyDenied || rejectedInSnow || prClosed ? "skipped" : s === "completed" ? "done" : s === "failed" && request.github.prNumber ? "failed" : reached("merged") ? "current" : "todo",
      detail: s === "failed" ? request.delivery.reason ?? request.lastError ?? undefined : request.delivery.runUrl ? "Pipeline run" : undefined,
      href: request.delivery.runUrl,
    },
  ];
  steps.splice(4,0,{
    key:"exact_plan",label:"Exact-plan approval",state:request.executionApproval?.state==="approved"?"done":request.executionApproval?.state==="rejected"?"failed":reached("merged")?"current":"todo",detail:request.executionApproval?.changeNumber??"Separate approval of the saved execution plan",
  },{
    key:"execution_gate",label:"Execution checkpoint",state:request.execution?.verdict==="denied"?"failed":request.execution?.verdict==="authorized"&&request.execution.checkpoint==="pre_apply"?"done":reached("merged")?"current":"todo",detail:request.execution?`${request.execution.checkpoint.replaceAll("_"," ")} · ${request.execution.verdict} · ${formatDate(request.execution.at)}`:"Identity, scope, policy and expiry must still match",
  });
  steps.push({key:"verification",label:"GCP read-back",state:request.verification?.state==="verified"?"done":request.verification?.state==="failed"?"failed":s==="completed"?"current":"todo",detail:request.verification?`${request.verification.state} · ${formatDate(request.verification.checkedAt)}`:"Cloud verification is separate from pipeline success"});
  if (request.capability === "access_grant") {
    const expired = request.expiresAt ? new Date(request.expiresAt) <= new Date() : false;
    steps.push({
      key: "expiry", label: "Conditional grant window",
      state: terminalBad || policyDenied ? "skipped" : expired ? "done" : s === "completed" ? "current" : "todo",
      detail: request.expiresAt ? `${expired ? "Ended" : "Ends"} ${formatDate(request.expiresAt)}` : undefined,
    });
  }
  return steps;
}

function policyStep(request: RequestView): Step {
  const { allowed, reasons, policyIds } = request.policy;
  return {
    key: "policy", label: "Policy check",
    state: allowed === true ? "done" : allowed === false ? "failed" : request.status === "awaiting_policy" ? "current" : "todo",
    detail: allowed === false ? reasons.join("; ") || "Denied" : allowed ? `Allowed${policyIds.length ? ` · ${policyIds.join(", ")}` : ""}` : undefined,
  };
}

export function formatDate(value: string | null | undefined) {
  if (!value) return "";
  return new Date(value).toLocaleString("en-AU", { dateStyle: "medium", timeStyle: "short", timeZone: "UTC" }) + " UTC";
}

export function timeAgo(value: string) {
  const minutes = Math.max(0, Math.floor((Date.now() - new Date(value).getTime()) / 60000));
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes}m ago`;
  const h = Math.floor(minutes / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

export function timeLeft(value: string) {
  const ms = new Date(value).getTime() - Date.now();
  if (ms <= 0) return "ended";
  const minutes = Math.round(ms / 60000);
  if (minutes < 60) return `${minutes} min left`;
  const h = Math.floor(minutes / 60);
  return h < 24 ? `${h}h ${minutes % 60}m left` : `${Math.floor(h / 24)}d ${h % 24}h left`;
}

export function eventLabel(type: string) {
  return (
    {
      "request.submitted": "Request submitted",
      "policy.allowed": "Policy allowed the request",
      "policy.denied": "Policy denied the request",
      "servicenow.change_opened": "ServiceNow change opened",
      "approval.approved": "Approved in ServiceNow",
      "approval.rejected": "Rejected in ServiceNow",
      "github.proposal_created": "Pull request opened",
      "delivery.merged": "Pull request merged",
      "delivery.applying": "Pipeline applying",
      "delivery.completed": "Applied in GCP",
      "delivery.failed": "Delivery failed",
      "delivery.cancelled": "Pull request closed",
      "triage.findings_ready": "Log search finished",
      "triage.incident_requested": "Incident requested",
      "triage.closed_without_incident": "Closed without an incident",
      "servicenow.incident_created": "ServiceNow incident created",
      "workflow.retry_scheduled": "Step will be retried",
      "workflow.dead_lettered": "Step gave up after retries",
    } as Record<string, string>
  )[type] ?? type.replaceAll(".", " ").replaceAll("_", " ");
}
