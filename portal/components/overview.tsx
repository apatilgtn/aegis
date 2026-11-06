import Link from "next/link";
import { CountUp, JourneyChart, type JourneyRow } from "@/components/journey-chart";
import type { LabelSnapshot } from "@/lib/data";
import { approvalMinutes, duration, median, segmentsOf } from "@/lib/journey";
import { capabilityLabel, isOpen, needsMe, statusOf, subtitle, timeLeft, title, type RequestView, type EventView } from "@/lib/view";

const RANGES = [
  { key: "1d", label: "D", ms: 86_400_000 },
  { key: "7d", label: "W", ms: 7 * 86_400_000 },
  { key: "30d", label: "M", ms: 30 * 86_400_000 },
  { key: "90d", label: "Q", ms: 90 * 86_400_000 },
  { key: "all", label: "All", ms: 0 },
];
const TYPES = [
  { key: "", label: "All activity" },
  { key: "access_grant", label: "Access" },
  { key: "incident_triage", label: "Investigations" },
  { key: "label_fix", label: "Label fixes" },
];

type Search = { range?: string; type?: string; who?: string };

function href(current: Search, next: Search, basePath: string) {
  const merged = { ...current, ...next };
  const query = new URLSearchParams();
  if (merged.range && merged.range !== "30d") query.set("range", merged.range);
  if (merged.type) query.set("type", merged.type);
  if (merged.who === "mine") query.set("who", "mine");
  const text = query.toString();
  return text ? `${basePath}?${text}` : basePath;
}

function delta(now: number, before: number) {
  if (!before) return null;
  const change = Math.round(((now - before) / before) * 100);
  return `${change > 0 ? "+" : ""}${change}%`;
}

export function Overview({ requests, eventsByRequest, labels, userId, params, now, basePath = "/" }: {
  requests: RequestView[];
  eventsByRequest: Map<string, EventView[]>;
  labels: LabelSnapshot | null;
  userId: string;
  params: Search;
  now: number;
  basePath?: string;
}) {
  const linkTo = (next: Search) => href(params, next, basePath);
  const range = RANGES.find((r) => r.key === params.range) ?? RANGES[2];
  const type = TYPES.find((t) => t.key === params.type) ?? TYPES[0];
  const mineOnly = params.who === "mine";
  const oldest = requests.length ? Math.min(...requests.map((r) => new Date(r.createdAt).getTime())) : now - 86_400_000;
  const from = range.ms ? now - range.ms : Math.min(oldest, now - 60_000);
  const scoped = requests.filter((r) => (!type.key || r.capability === type.key) && (!mineOnly || r.requesterId === userId));
  const segmentsFor = (r: RequestView) => segmentsOf(r, eventsByRequest.get(r.id) ?? [], now);
  const inWindow = scoped.filter((r) => isOpen(r) || segmentsFor(r).some((s) => s.end >= from) || new Date(r.createdAt).getTime() >= from);
  const rows: JourneyRow[] = inWindow.slice(0, 12).map((r) => ({ id: r.id, title: title(r), subtitle: `${capabilityLabel(r.capability)} · ${statusOf(r).label}`, segments: segmentsFor(r) }));
  const latestEnd = Math.max(now, ...rows.flatMap((row) => row.segments.map((s) => s.end)));
  const domainEnd = Math.min(latestEnd, now + (now - from) * 0.25);

  // Headline numbers: this period against the period before it.
  const periodMs = range.ms || now - oldest || 86_400_000;
  const submitted = (lo: number, hi: number) => scoped.filter((r) => { const t = new Date(r.createdAt).getTime(); return t >= lo && t < hi; });
  const thisPeriod = submitted(now - periodMs, now + 1);
  const lastPeriod = submitted(now - 2 * periodMs, now - periodMs);
  const approvalTimes = (list: RequestView[]) => list.map((r) => approvalMinutes(eventsByRequest.get(r.id) ?? [])).filter((m): m is number => m !== null);
  const medianNow = median(approvalTimes(thisPeriod));
  const medianBefore = median(approvalTimes(lastPeriod));
  const waiting = scoped.filter((r) => r.status === "awaiting_approval" && r.capability !== "incident_triage").length;
  const active = scoped.filter((r) => r.capability === "access_grant" && r.status === "completed" && r.expiresAt && new Date(r.expiresAt).getTime() > now)
    .sort((a, b) => new Date(a.expiresAt!).getTime() - new Date(b.expiresAt!).getTime());
  const forMe = requests.filter((r) => needsMe(r, userId));
  const coverage = labels && labels.total ? Math.round((labels.compliant / labels.total) * 100) : null;

  return (
    <div className="overview">
      <div className="overview-intro"><span className="eyebrow">WORKSPACE OVERVIEW</span><h1>Governance overview</h1><p>Access, investigations and governance. Every request, every step, in one place.</p><small className="muted">Activity and timing metrics cover the latest 100 requests. Use the request queue to search older records.</small></div>
      <div className="overview-review-link"><div><h2>Know the impact before you approve.</h2><p>Inspect a Terraform plan for destructive changes, access expansion and declared dependencies.</p></div><Link className="btn btn-primary" href={basePath === "/" ? "/review" : "/auth/design-preview?mode=review"}>Review a change →</Link></div>
      <div className="overview-filters">
        <div className="activity-filters" aria-label="Activity type">
          {TYPES.map((t) => <Link key={t.key || "all"} href={linkTo({ type: t.key })} className={t.key === type.key ? "on" : ""} aria-current={t.key === type.key ? "true" : undefined}>{t.label}</Link>)}
        </div>
        <div className="segmented" aria-label="Requested by">
          <Link href={linkTo({ who: "" })} className={!mineOnly ? "on" : ""}>Everyone</Link>
          <Link href={linkTo({ who: "mine" })} className={mineOnly ? "on" : ""}>Mine</Link>
        </div>
      </div>
      <div className="board">
      <aside className="kpis" aria-label="Headline numbers">
        <div className="kpi">
          <span>Requests this period</span>
          <strong><CountUp value={thisPeriod.length} /></strong>
          {delta(thisPeriod.length, lastPeriod.length) && <small>{delta(thisPeriod.length, lastPeriod.length)} vs previous</small>}
        </div>
        <div className="kpi">
          <span>Waiting in ServiceNow</span>
          <strong><CountUp value={waiting} /></strong>
          <small>Approver is never the requester</small>
        </div>
        <div className="kpi">
          <span>Median time to approve</span>
          <strong>{medianNow === null ? "Not yet" : duration(medianNow)}</strong>
          <small>{medianNow !== null && medianBefore !== null ? `was ${duration(medianBefore)}` : "From policy allowed to ServiceNow approval"}</small>
        </div>
        <div className="kpi">
          <span>Active access</span>
          <strong><CountUp value={active.length} /></strong>
          <small>{active[0]?.expiresAt ? `Next ends: ${timeLeft(active[0].expiresAt)}` : "Every grant ends on its own"}</small>
        </div>
        <div className="kpi">
          <span>Bucket label coverage</span>
          <strong>{coverage === null ? "Not yet" : <CountUp value={coverage} suffix="%" />}</strong>
          <small>{labels ? `${labels.compliant} of ${labels.total} buckets fully labelled` : "Not checked yet"}</small>
        </div>
      </aside>
      <section className="board-main">
        {forMe.length > 0 && (
          <Link className="callout" href={`/requests/${forMe[0].id}`}>
            <span className="dot warn" />
            <span><strong>{forMe.length === 1 ? "One investigation is" : `${forMe.length} investigations are`} waiting for your decision.</strong> {title(forMe[0])}</span>
            <span className="link">Review</span>
          </Link>
        )}
        <div className="board-head">
          <h2>Governed activity</h2>
          <div className="ranges" aria-label="Time range">
            {RANGES.map((r) => (
              <Link key={r.key} href={linkTo({ range: r.key })} className={r.key === range.key ? "on" : ""} aria-current={r.key === range.key ? "true" : undefined} title={r.key === "all" ? "All time" : `Last ${r.key}`}>{r.label}</Link>
            ))}
          </div>
        </div>
        <p className="board-lede">Each bar is one request moving through policy, ServiceNow approval, merge and GCP. Hover a bar for its timing; select a request to see its full journey.</p>
        <JourneyChart rows={rows} domain={[from, domainEnd]} now={now} />
      </section>



      <section className="board-table">
        <div className="board-head">
          <h2>Request data</h2>
          <Link className="btn" href="/requests">All requests</Link>
        </div>
        <div className="overview-table-scroll" tabIndex={0} role="region" aria-label="Request data">
        <table className="table editorial">
          <thead>
            <tr><th>Request</th><th>Type</th><th>Stage now</th><th>Approved by</th><th>Pull request</th><th>Submitted</th></tr>
          </thead>
          <tbody>
            {inWindow.slice(0, 12).map((r) => (
              <tr key={r.id}>
                <td><Link className="cell-link" href={`/requests/${r.id}`}><strong>{title(r)}</strong><span>{subtitle(r)}</span></Link></td>
                <td>{capabilityLabel(r.capability)}</td>
                <td><span className="status"><span className={`dot ${statusOf(r).tone}`} /> {statusOf(r).label}</span></td>
                <td className="muted">{r.servicenow.approverEmail ?? (r.servicenow.changeNumber ? r.servicenow.changeNumber : "Not yet")}</td>
                <td>{r.github.prUrl ? <a className="link" href={r.github.prUrl} target="_blank" rel="noreferrer">#{r.github.prNumber}</a> : <span className="faint">None</span>}</td>
                <td className="muted">{new Date(r.createdAt).toLocaleDateString("en-AU", { day: "numeric", month: "short" })}</td>
              </tr>
            ))}
            {!inWindow.length && <tr><td colSpan={6} className="muted">No requests in this period.</td></tr>}
          </tbody>
        </table>
        </div>
      </section>
      </div>
    </div>
  );
}
