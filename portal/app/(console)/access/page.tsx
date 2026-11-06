import Link from "next/link";
import { Plus } from "lucide-react";
import { PageHead, RequestList } from "@/components/ui";
import { personalAccess, requireContext } from "@/lib/data";
import { formatDate, timeLeft, tierLabel } from "@/lib/view";

export default async function AccessPage() {
  const context = await requireContext();
  const { active, pending, history: past } = await personalAccess(context);
  return (
    <>
      <PageHead title="My access" lede="Time-bound grants recorded for your identity. Each request has its own cloud-verification evidence.">
        <Link className="btn btn-primary" href="/new/access"><Plus size={14} /> Request access</Link>
      </PageHead>
      <section className="section">
        <div className="section-head"><h2>Active grant windows</h2></div>
        {active.length ? (
          <div className="rows">
            {active.map((r) => (
              <Link className="row" href={`/requests/${r.id}`} key={r.id}>
                <span className="dot good" />
                <span className="row-main"><strong>{tierLabel(r.resource.tier)}</strong><span>Ends {formatDate(r.expiresAt)}</span></span>
                <span className="badge good">{timeLeft(r.expiresAt!)}</span>
                <span className="row-meta">{r.resource.scope_type === "bucket" ? String(r.resource.scope_identifier) : "Project scope"}</span>
              </Link>
            ))}
          </div>
        ) : (
          <div className="rows"><div className="rows-empty"><strong>No active access</strong>Request access when you need it; it is removed automatically afterwards.</div></div>
        )}
      </section>
      <section className="section">
        <div className="section-head"><h2>On the way</h2></div>
        <RequestList requests={pending} userId={context.userId} empty={<><strong>Nothing pending</strong>Requests waiting for approval or merge show here.</>} />
      </section>
      <section className="section">
        <div className="section-head"><h2>History <span className="muted">Latest 100 terminal requests</span></h2></div>
        <RequestList requests={past} userId={context.userId} empty={<><strong>No past access</strong>Ended, rejected and cancelled requests show here.</>} />
      </section>
    </>
  );
}
