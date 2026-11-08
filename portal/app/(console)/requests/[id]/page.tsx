import { MonitorControl, EffortControl } from "@/components/review-controls";
import { RequestAssurance } from "@/components/request-assurance";
import Link from "next/link";
import { notFound } from "next/navigation";
import { ExternalLink, Info } from "lucide-react";
import { GitHubOriginalIcon, ServiceNowOriginalIcon } from "@/components/brand-icons";
import { IncidentDecision } from "@/components/incident-decision";
import { Status, Timeline } from "@/components/ui";
import { getRequest, requireContext } from "@/lib/data";
import { capabilityLabel, eventLabel, formatDate, stepsOf, subtitle, tierLabel, timeLeft, title, type RequestView } from "@/lib/view";

function whatNow(request: RequestView, mine: boolean): string {
  const s = request.status;
  if (request.policy.allowed === false) return "The policy check did not allow this request, so nothing was sent to ServiceNow. The reasons are below.";
  if (request.capability === "incident_triage") {
    if (s === "awaiting_policy") return "Checking the request against policy. This takes a few seconds.";
    if (s === "executing" && !request.triage.summary) return "Searching Cloud Logging for warnings and errors. Nothing is changed.";
    if (s === "awaiting_approval") return mine ? "The search has finished. Review the findings and decide whether this needs a ServiceNow incident." : "The search has finished and is waiting for the person who started it.";
    if (s === "executing") return "Creating the ServiceNow incident.";
    if (s === "completed") return `ServiceNow incident ${request.servicenow.incidentNumber ?? ""} was raised with these findings.`;
    if (s === "cancelled") return "Closed without raising an incident.";
    return "This investigation could not finish.";
  }
  if (s === "awaiting_policy") return "Checking the request against policy. This takes a few seconds.";
  if (s === "awaiting_approval") return request.servicenow.changeNumber
    ? `Waiting for an approver in ServiceNow change ${request.servicenow.changeNumber}. The requester can never approve their own request.`
    : "Opening a change in ServiceNow for approval.";
  if (s === "proposal_pending") return "Approved in ServiceNow. Aegis is opening the pull request.";
  if (s === "pr_open") return "The pull request is open. A person must review and merge it on GitHub before anything changes.";
  if (s === "merged" || s === "applying") return "The pipeline has started. Exact-plan approval and execution checks must pass before any cloud change.";
  if (s === "completed") {
    if(request.verification?.state!=="verified")return request.verification?.state==="failed"?"The pipeline finished, but cloud read-back did not match the requested outcome. Review the comparison before relying on this change.":"The pipeline finished. The requested cloud outcome has not been independently verified; review the read-back evidence below.";
    if (request.capability === "label_fix") return "The missing labels were added to the bucket. Existing labels were not changed.";
    return request.expiresAt && new Date(request.expiresAt) > new Date()
      ? `Access is live. GCP removes it automatically on ${formatDate(request.expiresAt)}.`
      : "This access has ended. GCP stopped honouring it at the end time.";
  }
  if (s === "rejected") return "An approver rejected this in ServiceNow, so no pull request was opened.";
  if (s === "cancelled") return "The pull request was closed without merging, so nothing was changed.";
  return request.delivery.reason ?? request.lastError ?? "This request stopped before finishing.";
}

export default async function RequestPage({ params }: { params: Promise<{ id: string }> }) {
  const context = await requireContext();
  const { id } = await params;
  const found = await getRequest(context, id);
  if (!found) notFound();
  const { monitor, request, events, findings, verification, receipt, planApprovals, changeContext, executionChecks, nativeAssessments } = found;
  const mine = request.requesterId === context.userId;
  const operatorInitiated=events.find(e=>e.type==="request.submitted")?.payload.submission_origin==="authorized_operator";

  return (
    <>
      <div className="page-head">
        <div>
          <div className="actions" style={{ marginBottom: 6 }}>
            <span className="badge">{capabilityLabel(request.capability)}</span>
            <span className="faint mono">{request.shortId}</span>
          </div>
          <h1>{title(request)}</h1>
          <p>{subtitle(request)}</p>
        </div>
        <div className="actions">
          <Link className="btn" href={`/review?requestId=${request.id}`}>Review a plan</Link>
          <Status request={request} />
          {request.servicenow.changeUrl && (
            <a className="btn" href={request.servicenow.changeUrl} target="_blank" rel="noreferrer">
              <ServiceNowOriginalIcon width={14} height={14} /> {request.servicenow.changeNumber}
            </a>
          )}
          {request.servicenow.incidentUrl && (
            <a className="btn" href={request.servicenow.incidentUrl} target="_blank" rel="noreferrer">
              <ServiceNowOriginalIcon width={14} height={14} /> {request.servicenow.incidentNumber}
            </a>
          )}
          {request.github.prUrl && (
            <a className="btn" href={request.github.prUrl} target="_blank" rel="noreferrer">
              <GitHubOriginalIcon width={14} height={14} /> PR #{request.github.prNumber}
            </a>
          )}
        </div>
      </div>

      {operatorInitiated&&<div className="workflow-note" style={{marginBottom:20}}><p><strong>Operator-initiated request.</strong> A trusted deployment operator submitted this for the registered request owner. Human approvals are recorded separately and remain required.</p></div>}
      <div className="grid-main">
        <div style={{ display: "grid", gap: 16 }}>
          <div className="note">
            <Info size={15} />
            <span>{whatNow(request, mine)}</span>
          </div>

          {request.policy.allowed === false && request.policy.reasons.length > 0 && (
            <div className="alert bad">
              <strong>Why the policy said no</strong>
              <ul style={{ margin: "6px 0 0", paddingLeft: 18 }}>
                {request.policy.reasons.map((reason) => <li key={reason}>{reason}</li>)}
              </ul>
            </div>
          )}

          {request.capability === "incident_triage" && request.triage.summary && (
            <div className="card card-pad" style={{ display: "grid", gap: 12 }}>
              <div>
                <h3>What the logs show</h3>
                <p>{request.triage.summary}</p>
              </div>
              {request.status === "awaiting_approval" && mine && <IncidentDecision requestId={request.id} />}
              {findings.length > 0 && (
                <details>
                  <summary className="link" style={{ cursor: "pointer" }}>Show the {findings.length} most recent entries</summary>
                  <ul className="findings" style={{ marginTop: 10 }}>
                    {findings.map((finding, index) => (
                      <li key={`${finding.timestamp}-${index}`}>
                        <div className="meta">
                          <span className={`badge ${finding.severity === "WARNING" ? "warn" : "bad"}`}>{finding.severity}</span>
                          {finding.source && <span>{finding.source}</span>}
                          <span>{formatDate(finding.timestamp)}</span>
                        </div>
                        <code>{finding.message}</code>
                      </li>
                    ))}
                  </ul>
                </details>
              )}
            </div>
          )}

          {request.capability === "incident_triage" && <section className="workflow-panel"><div className="section-head"><h2>Related change evidence</h2><span className={`badge ${changeContext?.state === "available" ? "info" : "warn"}`}>{changeContext?.state ?? "Not collected"}</span></div><p className="muted">Recent Admin Activity events matching the resource hint and investigation window. A temporal match does not prove causation.</p>{changeContext?.state === "available" && Array.isArray(changeContext.changes) && changeContext.changes.length ? changeContext.changes.map((change: {timestamp:string;method:string;resource:string},i:number) => <div className="change-context-event" key={i}><span className="muted">{formatDate(change.timestamp)}</span><strong>{change.method}</strong><code>{change.resource}</code></div>) : <p className="muted">{changeContext?.state === "available" ? "No matching change events were returned in this window." : "Change-event evidence is unavailable. The log findings remain available above."}</p>}{changeContext && <small className="muted">Search limits: up to 50 matching log rows and 20 Admin Activity events. Only the latest 20 log findings are retained in the request event.</small>}</section>}
          <div className="card card-pad">
            <h3 style={{ marginBottom: 14 }}>Journey</h3>
            <Timeline steps={stepsOf(request)} />
          </div>

          {request.capability!=="incident_triage"&&<section className="workflow-panel section"><h2>Ongoing assurance and operator effort</h2><p className="muted">Read-only monitoring checks the approved binding or labels. It cannot detect all access paths or changes between polls.</p>{context.role==="platform_admin"&&request.status==="completed"&&(monitor||verification?.state==="verified")&&<MonitorControl requestId={request.id} enabled={monitor?.enabled??false} interval={monitor?.interval_minutes??15}/>}<Link className="link" href="/assurance">View observation history →</Link>{(mine||context.role==="platform_admin")&&<details className="section"><summary>Record hands-on operator time</summary><EffortControl requestId={request.id}/></details>}</section>}
          {request.capability!=="incident_triage"&&<RequestAssurance request={request} receipt={receipt} checks={executionChecks} plans={planApprovals} verification={verification} native={nativeAssessments[0]??null} canAnalyze={mine||["platform_admin","auditor"].includes(context.role)} admin={context.role==="platform_admin"} auditor={["platform_admin","auditor"].includes(context.role)}/>}
          <div className="card card-pad">
            <h3>Reason given</h3>
            <p style={{ color: "var(--text-2)" }}>{request.justification}</p>
          </div>
        </div>

        <div style={{ display: "grid", gap: 16 }}>
          <div className="card card-pad">
            <dl className="kv">
              <dt>Requested by</dt>
              <dd>{mine ? "You" : "A teammate"}</dd>
              <dt>Submitted</dt>
              <dd>{formatDate(request.createdAt)}</dd>
              {request.capability === "access_grant" && (
                <>
                  <dt>Access</dt>
                  <dd>{tierLabel(request.resource.tier)} on GCP {request.resource.environment === "prod" ? "production" : "non-production"}</dd>
                  <dt>Duration</dt>
                  <dd>{String(request.resource.ttl_hours ?? "?")} hours</dd>
                  {request.expiresAt && (
                    <>
                      <dt>Ends</dt>
                      <dd>{formatDate(request.expiresAt)} <span className="faint">({timeLeft(request.expiresAt)})</span></dd>
                    </>
                  )}
                </>
              )}
              {request.capability === "label_fix" && (
                <>
                  <dt>Bucket</dt>
                  <dd className="mono">{String(request.resource.bucket ?? "")}</dd>
                  <dt>Cost center</dt>
                  <dd>{String(request.resource.cost_center ?? "")}</dd>
                </>
              )}
              {request.capability === "incident_triage" && (
                <>
                  <dt>Searched for</dt>
                  <dd>{String(request.resource.resource_hint ?? "")}</dd>
                  <dt>Window</dt>
                  <dd>Last {String(request.resource.time_window_hours ?? "?")} hours</dd>
                </>
              )}
              <dt>Risk</dt>
              <dd>{request.policy.risk}</dd>
            </dl>
          </div>

          <details className="tech">
            <summary>Technical details</summary>
            <div className="tech-body" style={{ display: "grid", gap: 14 }}>
              <dl className="kv">
                <dt>Request ID</dt>
                <dd className="mono">{request.id}</dd>
                {request.policy.policyIds.length > 0 && (<><dt>Policies</dt><dd className="mono">{request.policy.policyIds.join(", ")}</dd></>)}
                {request.servicenow.changeNumber && (<><dt>Change</dt><dd className="mono">{request.servicenow.changeNumber}</dd></>)}
                {request.servicenow.approverEmail && (<><dt>Approver</dt><dd>{request.servicenow.approverEmail}</dd></>)}
                {request.github.prNumber && (<><dt>Pull request</dt><dd className="mono">#{request.github.prNumber}</dd></>)}
                {request.delivery.runUrl && (
                  <>
                    <dt>Pipeline</dt>
                    <dd><a className="link" href={request.delivery.runUrl} target="_blank" rel="noreferrer">Run <ExternalLink size={11} /></a></dd>
                  </>
                )}
              </dl>
              <div>
                <div className="kicker" style={{ marginBottom: 6 }}>Recorded events</div>
                <ul className="event-list">
                  {events.map((event) => (
                    <li key={event.id}>
                      <span className="faint">{formatDate(event.at)}</span>
                      <span>{eventLabel(event.type)}</span>
                    </li>
                  ))}
                </ul>
              </div>
            </div>
          </details>
          <Link className="link" href="/requests">← All requests</Link>
        </div>
      </div>
    </>
  );
}
