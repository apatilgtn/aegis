// Turns a request's recorded events into time spans per stage, for the
// overview timeline. Pure: safe on the server and in the browser.
import type { EventView, RequestView } from "@/lib/view";

/** Fixed order = fixed colour slot (validated categorical palette). Never reorder. */
export const STAGES = [
  { key: "policy", label: "Policy check" },
  { key: "approval", label: "Awaiting approval" },
  { key: "merge", label: "Awaiting merge" },
  { key: "working", label: "Aegis working" },
  { key: "live", label: "Access live" },
] as const;

export type StageKey = (typeof STAGES)[number]["key"];
export type Segment = { stage: StageKey; start: number; end: number; detail: string; open: boolean };

const at = (events: EventView[], ...types: string[]) => {
  const found = events.find((event) => types.includes(event.type));
  return found ? new Date(found.at).getTime() : null;
};

export function segmentsOf(request: RequestView, events: EventView[], now = Date.now()): Segment[] {
  const out: Segment[] = [];
  const terminal = ["completed", "failed", "rejected", "cancelled", "expired"].includes(request.status);
  const stoppedAt = terminal ? Math.min(now, new Date(request.updatedAt).getTime()) : now;
  const push = (stage: StageKey, start: number | null, end: number | null, detail: string) => {
    if (start === null) return;
    const finish = end ?? stoppedAt;
    if (finish > start) out.push({ stage, start, end: finish, detail, open: end === null && !terminal });
  };
  const created = new Date(request.createdAt).getTime();
  const policyAt = at(events, "policy.allowed", "policy.denied");

  // Requests from before events carried every step: show what is known.
  if (!events.length) {
    push(request.status === "awaiting_policy" ? "policy" : "working", created, ["completed", "rejected", "cancelled", "failed"].includes(request.status) ? new Date(request.updatedAt).getTime() : null, "Recorded before detailed tracking");
    return out;
  }

  push("policy", created, policyAt, request.policy.allowed === false ? "Denied by policy" : "Checked against OPA policy");
  if (policyAt === null || request.policy.allowed === false) return out;

  if (request.capability === "incident_triage") {
    const found = at(events, "triage.findings_ready");
    push("working", policyAt, found, "Searching Cloud Logging");
    if (found === null) return out;
    const decided = at(events, "triage.incident_requested", "triage.closed_without_incident");
    push("approval", found, decided, "Waiting for the requester to decide");
    if (decided === null) return out;
    const raised = at(events, "servicenow.incident_created");
    if (at(events, "triage.incident_requested") !== null) push("working", decided, raised, "Raising the ServiceNow incident");
    return out;
  }

  const decided = at(events, "approval.approved", "approval.rejected");
  push("approval", policyAt, decided, request.servicenow.changeNumber ? `ServiceNow ${request.servicenow.changeNumber}` : "Opening a ServiceNow change");
  if (decided === null || at(events, "approval.rejected") !== null) return out;

  const prOpened = at(events, "github.proposal_created");
  push("working", decided, prOpened, "Opening the pull request");
  if (prOpened === null) return out;

  const merged = at(events, "delivery.merged", "delivery.applying", "delivery.completed");
  const closed = at(events, "delivery.cancelled");
  push("merge", prOpened, merged ?? closed, request.github.prNumber ? `Pull request #${request.github.prNumber}` : "Pull request");
  if (merged === null) return out;

  const applied = at(events, "delivery.completed", "delivery.failed");
  push("working", merged, applied, "Pipeline applying in GCP");
  if (applied === null || at(events, "delivery.completed") === null) return out;

  if (request.capability === "access_grant" && request.expiresAt) {
    const ends = new Date(request.expiresAt).getTime();
    if (ends > applied) out.push({ stage: "live", start: applied, end: ends, detail: `GCP removes it at the end`, open: ends > now });
  }
  return out;
}

/** Minutes from policy allowed to ServiceNow approval, or null. */
export function approvalMinutes(events: EventView[]) {
  const allowed = at(events, "policy.allowed");
  const approved = at(events, "approval.approved");
  return allowed !== null && approved !== null && approved > allowed ? (approved - allowed) / 60000 : null;
}

export function median(values: number[]) {
  if (!values.length) return null;
  const sorted = [...values].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
}

export function duration(minutes: number) {
  if (minutes < 1) return "under a minute";
  if (minutes < 60) return `${Math.round(minutes)} min`;
  const hours = minutes / 60;
  if (hours < 48) return `${hours < 10 ? hours.toFixed(1) : Math.round(hours)} h`;
  return `${Math.round(hours / 24)} days`;
}
