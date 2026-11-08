import Link from "next/link";
import { AssuranceExample } from "@/components/assurance-example";
import { notFound } from "next/navigation";
import { WorkspaceFrame } from "@/components/workspace-frame";
import { AccessForm } from "@/components/forms";
import { PageHead } from "@/components/ui";
import { ChangeReviewWorkspace } from "@/components/change-review";
import { Overview } from "@/components/overview";
import type { EventView, RequestView } from "@/lib/view";

// Static sample data only. This route is unavailable in production and never
// reads workspace data or creates requests, tickets, approvals or cloud changes.
export default async function DesignPreview({ searchParams }: {
  searchParams: Promise<{ range?: string; type?: string; who?: string; empty?: string; mode?: string; outcome?: string }>;
}) {
  if (process.env.NODE_ENV !== "development") notFound();
  const params = await searchParams;
  // eslint-disable-next-line react-hooks/purity -- sample clock for this server response
  const now = Date.now();
  const ago = (hours: number) => new Date(now - hours * 3_600_000).toISOString();
  const requests: RequestView[] = [];
  const eventsByRequest = new Map<string, EventView[]>();
  const samples = [
    { capability: "access_grant", status: "awaiting_approval", age: 3, name: "Payments API" },
    { capability: "incident_triage", status: "awaiting_approval", age: 5, name: "cilium-agent" },
    { capability: "label_fix", status: "pr_open", age: 8, name: "team-exports" },
    { capability: "access_grant", status: "completed", age: 12, name: "Customer platform" },
    { capability: "access_grant", status: "applying", age: 16, name: "Payments API" },
    { capability: "incident_triage", status: "completed", age: 20, name: "checkout-api" },
    { capability: "label_fix", status: "failed", age: 26, name: "analytics-staging" },
    { capability: "access_grant", status: "completed", age: 32, name: "Reporting" },
  ] as const;
  if (params.empty !== "1") samples.forEach((sample, index) => {
    const id = `00000000-0000-4000-8000-${String(index + 1).padStart(12, "0")}`;
    const events: EventView[] = [];
    const event = (type: string, elapsed: number) => events.push({ id: events.length + 1, type, at: ago(sample.age - elapsed), payload: {} });
    event("policy.allowed", 0.1);
    const investigation = sample.capability === "incident_triage";
    if (investigation) {
      event("triage.findings_ready", 0.25);
      if (sample.status === "completed") {
        event("triage.incident_requested", 1.5);
        event("servicenow.incident_created", 1.6);
      }
    } else if (sample.status !== "awaiting_approval") {
      event("approval.approved", 1.5);
      event("github.proposal_created", 1.7);
      if (sample.status !== "pr_open") event("delivery.merged", 3);
      if (sample.status === "completed") event("delivery.completed", 3.5);
      if (sample.status === "failed") event("delivery.failed", 3.5);
    }
    requests.push({
      id, shortId: `REQ-${index + 1}`, capability: sample.capability, status: sample.status,
      requesterId: index === 1 || index % 3 === 0 ? "preview-user" : "colleague", justification: "Sample request for reviewing the overview design.",
      resource: { tier: index % 2 ? "tier2" : "tier1", ttl_hours: 24, environment: "nonprod", resource_hint: sample.name, bucket: sample.name, cost_center: "pmt-01", time_window_hours: 24 },
      createdAt: ago(sample.age), updatedAt: events[events.length - 1].at, decidedAt: ago(sample.age - 1.5),
      expiresAt: sample.capability === "access_grant" && sample.status === "completed" ? ago(sample.age - 27.5) : null,
      serviceName: sample.name,
      policy: { allowed: true, reasons: [], policyIds: ["AEGIS-001"], risk: "low" },
      servicenow: { changeNumber: investigation ? null : `CHG000100${index}`, changeUrl: null, approverEmail: sample.status === "awaiting_approval" ? null : "reviewer@example.com", incidentNumber: investigation && sample.status === "completed" ? "INC0001001" : null, incidentUrl: null },
      github: { prNumber: investigation || sample.status === "awaiting_approval" ? null : 40 + index, prUrl: null },
      delivery: { runUrl: null, reason: sample.status === "failed" ? "Sample pipeline failure" : null, mergedAt: null },
      triage: { summary: investigation ? "Three warning entries found." : null, findingCount: investigation ? 3 : null },
      lastError: null,
      verification: sample.status === "completed" && sample.capability !== "incident_triage" ? {state:"verified",checkedAt:ago(sample.age - 3.6)} : undefined,
    });
    eventsByRequest.set(id, events);
  });
  return (
    <WorkspaceFrame tenantName="Aegis Cloud" openCount={5} overviewHref="/auth/design-preview" activeHref={params.mode === "review" ? "/review" : params.mode === "access" ? "/requests" : "/"} connection="sample" actions={<Link className="btn btn-primary btn-full" href="/new/access">+ New request</Link>} account={<div className="preview-account"><span className="avatar">AP</span><span><strong>Alex Parker</strong><small>Platform administrator</small></span></div>}>
        <p className="preview-note">Design preview · sample requests only. <Link className="link" href="/auth/design-preview?empty=1">View empty state</Link> · <Link className="link" href="/auth/design-preview">Overview</Link> · <Link className="link" href="/auth/design-preview?mode=review">Change review</Link> · <Link className="link" href="/auth/design-preview?mode=access">Access wizard</Link> · <Link className="link" href="/auth/design-preview?mode=assurance">Approval evidence</Link></p>
        {params.mode === "assurance" ? <><PageHead title="Change assurance" lede="Reviewable approval facts, execution comparisons and observed outcomes."/><AssuranceExample blocked={params.outcome==="blocked"}/></> : params.mode === "access" ? <><PageHead title="Request access" lede="A guided, scoped request with independent review. This is a non-submitting design preview."/><AccessForm preview identity="alex@example.com" prefill={{hours:1}} options={[{serviceId:"preview-service",serviceName:"Payments platform",cloud:"gcp",environment:"prod",tier:"tier1",role:"roles/logging.viewer",maxHours:4,target:"Production GCP project"},{serviceId:"preview-service",serviceName:"Payments platform",cloud:"gcp",environment:"prod",tier:"tier2",role:"roles/storage.objectUser",maxHours:1,target:"payments-exports bucket"}]}/></> : params.mode === "review" ? <ChangeReviewWorkspace defaultExample /> : <Overview requests={requests} eventsByRequest={eventsByRequest} labels={{ capturedAt: ago(0.25), projectId: "sample-project", required: ["owner", "cost-center", "environment"], buckets: [], compliant: 17, total: 20 }} userId="preview-user" params={{ range: "1d", ...params }} now={now} basePath="/auth/design-preview" />}
    </WorkspaceFrame>
  );
}
