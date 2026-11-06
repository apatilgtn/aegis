"use client";

import { useRouter } from "next/navigation";
import { useActionState, useEffect, useState } from "react";
import { ArrowLeft, ArrowRight, Check, Clock3, Fingerprint, KeyRound, LoaderCircle, ShieldCheck } from "lucide-react";
import { createAccessRequest, createIncidentTriage, createLabelFix, type ActionState } from "@/app/actions";

const initial: ActionState = { ok: false, message: "" };

function useGoToRequest(state: ActionState) {
  const router = useRouter();
  useEffect(() => {
    if (state.ok && state.createdId) router.push(`/requests/${state.createdId}`);
  }, [router, state]);
}

function Submit({ pending, label }: { pending: boolean; label: string }) {
  return (
    <div>
      <button className="btn btn-primary" disabled={pending} style={{ height: 36, padding: "0 16px" }}>
        {pending ? <LoaderCircle className="spin" size={14} /> : null}
        {pending ? "Submitting…" : label} {!pending && <ArrowRight size={14} />}
      </button>
    </div>
  );
}

const DURATIONS = [1, 4, 8, 24, 72, 168];
const durationLabel = (hours: number) => (hours === 168 ? "7 days" : hours === 72 ? "3 days" : hours === 1 ? "1 hour" : `${hours} hours`);

export type AccessOption = { serviceId: string; serviceName: string; cloud: string; environment: string; tier: string; role: string; maxHours: number; target?: string };

const TIER_COPY: Record<string, { name: string; what: string }> = {
  tier1: { name: "Tier 1 · read logs", what: "View logs in Cloud Logging. Nothing can be changed." },
  tier2: { name: "Tier 2 · read/write storage", what: "Read and write objects in Cloud Storage buckets. No bucket settings or deletion of buckets." },
  tier3: { name: "Tier 3 · privileged", what: "Elevated access for exceptional work." },
};

export function AccessForm({ options, prefill, identity = "Your signed-in account", preview = false }: { options: AccessOption[]; prefill: { tier?: string; hours?: number; reason?: string }; identity?: string; preview?: boolean }) {
  const [state, action, pending] = useActionState(createAccessRequest, initial);
  useGoToRequest(state);
  const routeKey = (o: AccessOption) => `${o.serviceId}:${o.cloud}:${o.environment}:${o.tier}:${o.target ?? ""}`;
  const first = options.find((o) => o.tier === prefill.tier) ?? options[0];
  const [selected, setSelected] = useState(first ? routeKey(first) : "");
  const level = options.find((o) => routeKey(o) === selected) ?? first;
  const [step, setStep] = useState(0);
  const [reason, setReason] = useState(prefill.reason ?? "");
  const [hours, setHours] = useState(prefill.hours ?? 4);
  const services = [...new Map(options.map((o) => [o.serviceId, o.serviceName])).entries()];
  const [serviceId, setServiceId] = useState(first?.serviceId ?? "");
  const durations = level ? DURATIONS.filter((h) => h <= level.maxHours) : [];
  const ttl = durations.includes(hours) ? hours : durations.filter((h) => h <= hours).pop() ?? durations[0];
  if (!level) return <div className="workflow-empty"><KeyRound size={28} /><h2>No reviewed access routes</h2><p>A platform administrator must configure a service entitlement before access can be requested.</p></div>;
  return <form action={action} className="access-workflow" onSubmit={(e) => { if (step !== 2 || preview) e.preventDefault(); }}>
    <input type="hidden" name="serviceId" value={level.serviceId} /><input type="hidden" name="cloud" value={level.cloud} /><input type="hidden" name="environment" value={level.environment} /><input type="hidden" name="tier" value={level.tier} /><input type="hidden" name="ttlHours" value={ttl} /><input type="hidden" name="justification" value={reason} />
    <div className="workflow-steps" aria-label="Request progress">{["Choose access", "Set duration", "Review request"].map((label, i) => <span key={label} className={i === step ? "current" : i < step ? "done" : ""} aria-current={i === step ? "step" : undefined}><i>{i < step ? <Check size={12} /> : i + 1}</i>{label}</span>)}</div>
    <div className="access-workflow-grid"><section className="workflow-panel">
      <div className="workflow-panel-title"><span>{step === 0 ? <KeyRound size={20} /> : step === 1 ? <Clock3 size={20} /> : <ShieldCheck size={20} />}</span><div><h2>{["What access do you need?", "Keep it scoped in time.", "Ready for independent review."][step]}</h2><p>{["Choose a reviewed permission set for the service you support.", "Explain the task and request only the time you need.", "Check the identity, scope and permissions before submitting."][step]}</p></div></div>
      {step === 0 && <>
        <label className="field"><span>Service</span><select value={serviceId} onChange={(e) => { setServiceId(e.target.value); const route = options.find((o) => o.serviceId === e.target.value); if (route) setSelected(routeKey(route)); }}>{services.map(([id, name]) => <option value={id} key={id}>{name}</option>)}</select></label>
        <div className="access-levels" role="radiogroup" aria-label="Permission set">{options.filter((o) => o.serviceId === serviceId).map((o) => <label className={`access-level ${selected === routeKey(o) ? "selected" : ""}`} key={routeKey(o)}><input type="radio" name="selectedRoute" checked={selected === routeKey(o)} onChange={() => setSelected(routeKey(o))} /><span className="access-level-icon">{o.tier === "tier1" ? <Fingerprint size={21} /> : <KeyRound size={21} />}</span><span className="access-level-copy"><strong>{TIER_COPY[o.tier]?.name ?? o.tier}</strong><span>{TIER_COPY[o.tier]?.what}</span><code>{o.role}</code><small>{o.cloud.toUpperCase()} · {o.environment === "prod" ? "Production" : "Non-production"} · Maximum {durationLabel(o.maxHours)}</small></span><span className="selection-indicator">{selected === routeKey(o) && <Check size={12} />}</span></label>)}</div>
      </>}
      {step === 1 && <>
        <fieldset className="duration-field"><legend>Access duration</legend><div className="duration-options">{durations.map((h) => <label key={h} className={ttl === h ? "on" : ""}><input type="radio" name="durationChoice" checked={ttl === h} onChange={() => setHours(h)} /><Clock3 size={14} /><span>{durationLabel(h)}</span></label>)}</div><small>The grant carries an absolute expiry. Approval and deployment time can reduce the usable access window.</small></fieldset>
        <label className="field"><span>Business justification <small>Required</small></span><textarea value={reason} onChange={(e) => setReason(e.target.value)} minLength={10} maxLength={2000} placeholder="Describe the incident or task, the resources involved, and why this permission is required." /><small>{reason.length}/2,000 characters · visible to the independent approver</small></label>
      </>}
      {step === 2 && <><dl className="review-request-details"><dt>Identity</dt><dd>{identity}</dd><dt>Service</dt><dd>{level.serviceName}</dd><dt>Environment</dt><dd><span className="environment-label">{level.environment === "prod" ? "Production" : "Non-production"}</span></dd><dt>Target</dt><dd className="mono">{level.target ?? "Reviewed entitlement scope"}</dd><dt>IAM role</dt><dd className="mono">{level.role}</dd><dt>Requested duration</dt><dd>{durationLabel(ttl)}</dd><dt>Justification</dt><dd>{reason}</dd></dl><div className="workflow-note"><ShieldCheck size={17} /><p>This submits a proposal. Policy must allow it, another person must approve in ServiceNow, and a human must merge the reviewed change before the pipeline applies it.</p></div></>}
      {state.message && !state.ok && <div className="alert bad" role="alert">{state.message}</div>}
      <div className="workflow-actions">{step > 0 && <button type="button" className="btn" onClick={() => setStep(step - 1)} disabled={pending}><ArrowLeft size={14} /> Back</button>}<span />{step < 2 ? <button type="button" className="btn btn-primary" disabled={step === 1 && (reason.trim().length < 10 || !ttl)} onClick={() => setStep(step + 1)}>Continue <ArrowRight size={14} /></button> : <button type="submit" className="btn btn-primary" disabled={pending || preview}>{pending ? <LoaderCircle size={14} className="spin" /> : <ShieldCheck size={14} />}{preview ? "Preview only · no request submitted" : pending ? "Submitting…" : "Submit for policy review"}</button>}</div>
    </section><aside className="access-summary"><span className="summary-eyebrow">REQUEST SUMMARY</span><h3>{level.serviceName}</h3><span className="environment-label">{level.environment === "prod" ? "Production" : "Non-production"}</span><dl><dt>Permission</dt><dd>{TIER_COPY[level.tier]?.name ?? level.tier}</dd><dt>Scope</dt><dd>{level.target ?? "Reviewed entitlement"}</dd><dt>Duration</dt><dd>{durationLabel(ttl)}</dd><dt>Identity</dt><dd>{identity}</dd></dl><div className="summary-gates"><span><Check size={13} /> Deterministic policy check</span><span><Check size={13} /> Independent approval</span><span><Check size={13} /> Human-reviewed pull request</span><span><Check size={13} /> Time-bound IAM condition</span></div></aside></div>
  </form>;
}

const WINDOWS = [1, 4, 24, 72, 168];

export function InvestigationForm({ prefill }: { prefill: { hint?: string; hours?: number; reason?: string } }) {
  const [state, action, pending] = useActionState(createIncidentTriage, initial);
  useGoToRequest(state);
  const chosen = WINDOWS.includes(prefill.hours ?? 0) ? prefill.hours : 24;
  return (
    <form action={action} className="form">
      <label className="field">
        <span>Service or resource</span>
        <input name="resourceHint" required minLength={2} maxLength={120} defaultValue={prefill.hint} placeholder="payments-api, cilium-agent…" />
        <small>Aegis matches it against log text, service, container and namespace names.</small>
      </label>
      <fieldset className="field" style={{ border: 0, padding: 0, margin: 0 }}>
        <span>Time window</span>
        <div className="pills">
          {WINDOWS.map((hours) => (
            <label className="pill-option" key={hours}>
              <input type="radio" name="timeWindowHours" value={hours} defaultChecked={hours === chosen} />
              <span>{hours === 1 ? "Last hour" : hours === 168 ? "Last 7 days" : hours === 72 ? "Last 3 days" : `Last ${hours} hours`}</span>
            </label>
          ))}
        </div>
      </fieldset>
      <label className="field">
        <span>What is going wrong?</span>
        <textarea name="justification" required minLength={10} maxLength={2000} defaultValue={prefill.reason}
          placeholder="Customers see failed payments since about 9am." />
        <small>This goes into the ServiceNow incident if you raise one.</small>
      </label>
      {state.message && !state.ok && <div className="alert bad">{state.message}</div>}
      <Submit pending={pending} label="Search the logs" />
    </form>
  );
}

export type BucketOption = { bucket: string; missing: string[]; labels?: Record<string,string> };

export function LabelsForm({ buckets, prefill, identity = "", environment = "prod", project = "Governed project" }: { buckets: BucketOption[]; prefill: { bucket?: string; costCenter?: string; reason?: string }; identity?: string; environment?: string; project?: string }) {
  const [state, action, pending] = useActionState(createLabelFix, initial);
  useGoToRequest(state);
  const [selected,setSelected]=useState(buckets.find(b=>b.bucket===prefill.bucket)?.bucket??buckets[0]?.bucket??"");
  const [costCenter,setCostCenter]=useState(prefill.costCenter??"");
  const bucket=buckets.find(b=>b.bucket===selected);
  const clean=(v:string)=>v.trim().toLowerCase().replace(/[^a-z0-9_-]+/g,"-").replace(/^-+|-+$/g,"").slice(0,63);
  const proposed: Record<string,string>={owner:clean(identity.replace("@","-at-")),"cost-center":clean(costCenter),environment};
  return <form action={action} className="access-workflow"><div className="access-workflow-grid"><section className="workflow-panel">
    <div className="workflow-panel-title"><span><ShieldCheck size={20}/></span><div><h2>Prepare an additive label fix</h2><p>Preview the values before the independent approval workflow begins.</p></div></div>
    <label className="field"><span>Bucket</span><select name="bucket" value={selected} onChange={e=>setSelected(e.target.value)}>{buckets.map(b=><option key={b.bucket} value={b.bucket}>{b.bucket}</option>)}</select></label>
    <label className="field"><span>Cost center</span><input name="costCenter" required minLength={2} maxLength={63} pattern="[a-z0-9][a-z0-9_\-]{1,62}" value={costCenter} onChange={e=>setCostCenter(e.target.value)} placeholder="pmt-01"/><small>Lowercase letters, numbers, hyphens or underscores. Existing values are preserved.</small></label>
    <div className="label-preview"><div className="section-head"><h3>Before & proposed</h3><span className="badge info">Snapshot preview</span></div><div className="table-scroll"><table className="table"><thead><tr><th>Label</th><th>Observed value</th><th>Proposed value</th></tr></thead><tbody>{["owner","cost-center","environment"].map(k=><tr key={k}><td className="mono">{k}</td><td>{bucket?.missing.includes(k)?<span className="badge warn">Missing</span>:bucket?.labels?.[k]||"(empty value)"}</td><td>{bucket?.missing.includes(k)?<code className="proposed-label">+ {proposed[k]||"Enter a value above"}</code>:<span className="muted">Preserved</span>}</td></tr>)}</tbody></table></div><small className="muted">The worker rechecks live ownership and labels before proposing. Concurrent metadata changes require a new plan.</small></div>
    <label className="field"><span>Business justification</span><textarea name="justification" required minLength={10} maxLength={2000} defaultValue={prefill.reason} placeholder="Explain the ownership or cost-attribution gap this change resolves."/></label>
    {state.message&&!state.ok&&<div className="alert bad" role="alert">{state.message}</div>}<Submit pending={pending} label="Submit remediation proposal"/>
  </section><aside className="access-summary"><span className="summary-eyebrow">CHANGE SCOPE</span><h3>Bucket labels</h3><span className="environment-label">{environment==="prod"?"Production":"Non-production"}</span><dl><dt>Project</dt><dd className="mono">{project}</dd><dt>Bucket</dt><dd className="mono">{selected}</dd><dt>Labels to add</dt><dd>{bucket?.missing.join(", ")??"Choose a bucket"}</dd></dl><div className="summary-gates"><span><Check size={13}/> Live bucket ownership check</span><span><Check size={13}/> Existing labels preserved</span><span><Check size={13}/> Independent ServiceNow approval</span><span><Check size={13}/> Exact-plan execution gate</span></div></aside></div></form>;
}
