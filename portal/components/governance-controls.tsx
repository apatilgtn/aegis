"use client";
import { useActionState } from "react";
import { LoaderCircle, Pause, Play, Save, ShieldCheck } from "lucide-react";
import { requestVerification, changeServiceOwnership, changeAiRouting, changeCapabilityPolicy, changeEntitlementScope, changeGovernanceControl, changeGovernanceLimit, retryReadStep, type ActionState } from "@/app/actions";
import type { CapabilityPolicy, GovernanceSettings } from "@/lib/data";
const initial: ActionState = { ok: false, message: "" };
function Message({ state }: { state: ActionState }) { return state.message ? <p className={`control-feedback ${state.ok ? "good" : "bad"}`} role="status">{state.message}</p> : null; }
export function ControlToggle({ settings, control, title, description, admin }: { settings: GovernanceSettings; control: "writes_enabled" | "proposals_paused" | "executions_paused"; title: string; description: string; admin: boolean }) {
 const [state, action, pending] = useActionState(changeGovernanceControl, initial);
 const value = settings[control];
 return <form action={action} className="control-card"><div className="control-icon">{control === "writes_enabled" ? <ShieldCheck size={20} /> : <Pause size={20} />}</div><div><h3>{title}</h3><p>{description}</p><span className={`badge ${control === "writes_enabled" ? value ? "good" : "warn" : value ? "warn" : "good"}`}>{control === "writes_enabled" ? value ? "Enabled" : "Disabled" : value ? "Paused" : "Running"}</span></div><input type="hidden" name="revision" value={settings.revision} /><input type="hidden" name="control" value={control} /><input type="hidden" name="value" value={String(!value)} /><button className="btn" disabled={!admin || pending}>{pending ? <LoaderCircle className="spin" size={13} /> : value ? <Pause size={13} /> : <Play size={13} />}{control === "writes_enabled" ? value ? "Disable writes" : "Enable governed writes" : value ? "Resume" : "Pause"}</button><Message state={state} /></form>;
}
export function PolicyEditor({ policy, admin }: { policy: CapabilityPolicy; admin: boolean }) {
 const [state, action, pending] = useActionState(changeCapabilityPolicy, initial);
 return <form action={action} className="policy-editor"><input type="hidden" name="capability" value={policy.capability} /><div><h3>{policy.capability.replaceAll("_", " ")}</h3><p>{policy.allowed_roles.join(" · ")}</p></div><label className="check-field"><input type="checkbox" name="production" defaultChecked={policy.production_allowed} disabled={!admin} /> Allow production</label><label className="field"><span>Maximum hours</span><input name="maxHours" type="number" min={1} max={24} defaultValue={policy.max_ttl_hours} disabled={!admin} /></label><button className="btn" disabled={!admin || pending}><Save size={13} /> Save policy</button><Message state={state} /></form>;
}
export function LimitEditor({ limit, value, title, max, admin }: { limit: string; value: number; title: string; max: number; admin: boolean }) {
 const [state, action, pending] = useActionState(changeGovernanceLimit, initial);
 return <form action={action} className="limit-editor"><input type="hidden" name="limit" value={limit} /><label className="field"><span>{title}</span><input type="number" name="value" defaultValue={value} min={limit === "approval_ttl_minutes" ? 5 : 0} max={max} disabled={!admin} /></label><button className="btn" disabled={!admin || pending}>Save limit</button><Message state={state} /></form>;
}
export function ScopeEditor({ routeId, scope, bucket, buckets, admin }: { routeId: string; scope: string; bucket: string | null; buckets: string[]; admin: boolean }) {
 const [state, action, pending] = useActionState(changeEntitlementScope, initial);
 return <form action={action} className="scope-editor"><input type="hidden" name="route" value={routeId} /><label className="field"><span>Entitlement scope</span><select name="scope" defaultValue={scope} disabled={!admin}><option value="project">Project</option><option value="bucket">Single storage bucket</option></select></label><label className="field"><span>Bucket, if scoped</span><select name="bucket" defaultValue={bucket ?? ""} disabled={!admin}><option value="">Choose a recently inventoried bucket</option>{buckets.map(b => <option key={b}>{b}</option>)}</select></label><button className="btn" disabled={!admin || pending}>Save reviewed scope</button><Message state={state} /></form>;
}
export function RetryReadButton({ job }: { job: string }) {
 const [state, action, pending] = useActionState(retryReadStep, initial);
 return <form action={action}><input type="hidden" name="job" value={job} /><button className="btn" disabled={pending}>{pending ? "Queuing…" : "Retry read-only step"}</button><Message state={state} /></form>;
}

export function AiRoutingEditor({model,threshold,admin}:{model:string|null;threshold:number;admin:boolean}){
 const[state,action,pending]=useActionState(changeAiRouting,initial);return <form action={action} className="workflow-panel"><h3>Budget-aware model routing</h3><p className="muted">Use an explicitly reviewed fallback model once the reserved daily token quota reaches the threshold. An empty identifier disables rerouting. Provider availability must be verified before configuring a model.</p><label className="field"><span>Approved fallback model</span><input name="model" defaultValue={model??""} placeholder="provider/model-identifier" disabled={!admin}/></label><label className="field"><span>Quota threshold, percent</span><input name="threshold" type="number" min={1} max={100} defaultValue={threshold} disabled={!admin}/></label><button className="btn" disabled={!admin||pending}>Save routing policy</button><Message state={state}/></form>;
}

export function OwnershipEditor({service,owner,runbook}:{service:string;owner:string|null;runbook:string|null}){
 const[state,action,pending]=useActionState(changeServiceOwnership,initial);return <form action={action} className="scope-editor"><input name="service" type="hidden" value={service}/><label className="field"><span>Owning team</span><input name="owner" defaultValue={owner??""} maxLength={120} placeholder="Payments platform"/></label><label className="field"><span>Runbook URL</span><input name="runbook" defaultValue={runbook??""} type="url" placeholder="https://…"/></label><button className="btn" disabled={pending}>Save ownership</button><Message state={state}/></form>;
}

export function VerifyChangeButton({requestId}:{requestId:string}) {
 const [state,action,pending]=useActionState(requestVerification,initial);
 return <form action={action}><input type="hidden" name="requestId" value={requestId}/><button className="btn" disabled={pending}>{pending ? <LoaderCircle className="spin" size={13}/> : <ShieldCheck size={13}/>}Recheck cloud state</button><Message state={state}/></form>;
}
