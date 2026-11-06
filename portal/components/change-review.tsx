"use client";

import { useId, useRef, useState } from "react";
import { ArrowDownToLine, ArrowRight, Braces, Check, ChevronRight, FileJson, GitBranch, Info, Layers3, LoaderCircle, Play, ShieldAlert, Sparkles, Upload } from "lucide-react";
import { dependentsOf, parsePlan, SAMPLE_PLAN, type ChangeReview } from "@/lib/change-review";
import { explainChangeReview, saveChangeAssessment } from "@/app/actions";

function DependencyMap({ report, selected, onSelect }: { report: ChangeReview; selected: string; onSelect: (address: string) => void }) {
  const marker = useId().replaceAll(":", "");
  const shown = report.nodes.slice(0, 12);
  const depths = new Map<string, number>();
  const depth = (address: string, trail = new Set<string>()): number => {
    if (depths.has(address)) return depths.get(address)!;
    if (trail.has(address)) return 0;
    const node = shown.find((n) => n.address === address);
    const next = new Set(trail).add(address);
    const result = Math.min(3, Math.max(0, ...(node?.dependencies.filter((d) => shown.some((n) => n.address === d)).map((d) => depth(d, next) + 1) ?? [])));
    depths.set(address, result);
    return result;
  };
  shown.forEach((node) => depth(node.address));
  const groups = Array.from({ length: 4 }, (_, i) => shown.filter((n) => depths.get(n.address) === i));
  const positions = new Map(groups.flatMap((group, column) => group.map((node, row) => [node.address, { x: 24 + column * 244, y: 42 + row * 100 }] as const)));
  const height = Math.max(210, Math.max(...groups.map((g) => g.length)) * 100 + 50);
  const width = Math.max(510, (Math.max(0, ...depths.values()) + 1) * 244);
  const impacted = new Set(dependentsOf(report, selected));
  return <div className="impact-map-wrap">
    <svg className="impact-map" viewBox={`0 0 ${width} ${height}`} style={{ minWidth: Math.min(width, 740) }} role="group" aria-label="Declared dependencies among changed resources">
      <defs><marker id={marker} markerWidth="6" markerHeight="6" refX="5" refY="3" orient="auto"><path d="M0,0 L6,3 L0,6" fill="#8da287" /></marker></defs>
      {shown.flatMap((node) => node.dependencies.map((dependency) => {
        const a = positions.get(dependency), b = positions.get(node.address);
        return a && b ? <path key={`${dependency}-${node.address}`} d={`M ${a.x + 210} ${a.y + 31} C ${a.x + 230} ${a.y + 31}, ${b.x - 24} ${b.y + 31}, ${b.x - 5} ${b.y + 31}`} fill="none" stroke={impacted.has(node.address) && (dependency === selected || impacted.has(dependency)) ? "#d38d54" : "#a2b29a"} strokeWidth="1.5" markerEnd={`url(#${marker})`} /> : null;
      }))}
      {shown.map((node) => {
        const p = positions.get(node.address)!;
        const risk = report.findings.some((f) => f.resource === node.address && ["critical", "high"].includes(f.severity));
        return <g key={node.address} transform={`translate(${p.x} ${p.y})`} className={`impact-node ${selected === node.address ? "selected" : ""} ${impacted.has(node.address) ? "dependent" : ""}`} role="button" tabIndex={0} aria-label={`${node.address}, ${node.actions.join(" then ")}`} aria-pressed={selected === node.address} onClick={() => onSelect(node.address)} onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onSelect(node.address); } }}>
          <title>{node.address}</title><rect width="210" height="64" rx="10" /><circle cx="16" cy="20" r="4" fill={risk ? "#d16d51" : "#5b8750"} /><text x="28" y="24" className="impact-node-name">{node.address.split(".").slice(-1)[0].slice(0, 24)}</text><text x="14" y="46" className="impact-node-type">{node.type.replace("google_", "").slice(0, 30)}</text>
        </g>;
      })}
      {!shown.length && <text x="24" y="60" className="impact-node-type">No managed resource changes in this plan.</text>}
    </svg>
    <p>Arrows follow declared references from dependency to dependent. {report.nodes.length > 12 ? "The map shows the first 12 changes; the resource list includes all changes." : "Select a resource to trace its downstream references."}</p>
  </div>;
}

export function ChangeReviewWorkspace({ defaultExample = false, aiEnabled = false, requestId, history = [] }: { defaultExample?: boolean; aiEnabled?: boolean; requestId?: string; history?: {id:string;input_sha256:string;created_at:string;report:ChangeReview}[] }) {
  const [input, setInput] = useState(defaultExample ? SAMPLE_PLAN : "");
  const [report, setReport] = useState<ChangeReview | null>(() => defaultExample ? parsePlan(SAMPLE_PLAN) : null);
  const [example, setExample] = useState(defaultExample);
  const [selected, setSelected] = useState(defaultExample ? "google_sql_database_instance.orders" : "");
  const [tab, setTab] = useState<"findings" | "dependencies">("dependencies");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [explanation, setExplanation] = useState("");
  const [explaining, setExplaining] = useState(false);
  const [fingerprint, setFingerprint] = useState("");
  const [checkedAt, setCheckedAt] = useState("");
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saveMessage, setSaveMessage] = useState("");
  const file = useRef<HTMLInputElement>(null);
  const generation = useRef(0);
  const analyze = async (value = input, isExample = false) => {
    generation.current++;
    setBusy(true); setError(""); setExplanation(""); setSaveMessage("");
    try {
      const result = parsePlan(value);
      const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value));
      setFingerprint(Array.from(new Uint8Array(digest)).map((v) => v.toString(16).padStart(2, "0")).join(""));
      setCheckedAt(new Date().toISOString());
      setReport(result); setSelected(result.nodes[0]?.address ?? ""); setExample(isExample); setDirty(false);
    } catch (e) { setReport(null); setError(e instanceof Error ? e.message : "The plan could not be analyzed."); }
    finally { setBusy(false); }
  };
  const loadFile = async (uploaded: File | undefined) => {
    if (!uploaded) return;
    if (uploaded.size > 1_000_000) { setError("Use a JSON plan smaller than 1 MB."); return; }
    try { const value = await uploaded.text(); setInput(value); await analyze(value); }
    catch { setError("The file could not be read. Choose it again or paste the JSON."); }
  };
  const explain = async () => {
    if (!report) return;
    const current = generation.current;
    setExplaining(true); setExplanation("");
    try {
      const result = await explainChangeReview(JSON.stringify(report));
      if (current === generation.current) setExplanation(result.message);
    } catch { if (current === generation.current) setExplanation("The AI explanation is unavailable. The deterministic findings remain available."); }
    finally { setExplaining(false); }
  };
  const exportReport = () => {
    if (!report || dirty) return;
    const blob = new Blob([JSON.stringify({ ...report, sample: example, assessedAt: checkedAt || null, planSha256: fingerprint || null, scope: "Advisory static analysis of supplied Terraform plan; not an execution approval or live topology assessment." }, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob), link = document.createElement("a"); link.href = url; link.download = "aegis-change-review.json"; link.click(); URL.revokeObjectURL(url);
  };
  const urgent = report?.findings.filter((f) => ["critical", "high"].includes(f.severity)).length ?? 0;
  const downstream = report ? dependentsOf(report, selected) : [];
  const selectedNode = report?.nodes.find((n) => n.address === selected);
  return <div className="review-workspace">
    <header className="review-heading"><div><div className="review-eyebrow"><span /> PRE-EXECUTION REVIEW</div><h1>Understand the change.<br /><span>Before it changes anything.</span></h1><p>Review a Terraform plan for destructive changes, access expansion and declared downstream dependencies.</p></div><div className="review-mode"><ShieldAlert size={16} /><span>Read-only assessment<small>Cloud execution is never triggered here</small></span></div></header>
    {requestId && <div className="workspace-context"><span className="badge">Linked request</span><code>{requestId}</code><span>Advisory review is separate from exact execution-plan approval.</span></div>}
    {history.length > 0 && <details className="assessment-history"><summary>Saved advisory assessments ({history.length})</summary>{history.map(h => <button key={h.id} className="assessment-history-row" onClick={() => { generation.current++; setReport(h.report); setSelected(h.report.nodes[0]?.address ?? ""); setFingerprint(h.input_sha256); setCheckedAt(h.created_at); setInput(""); setExample(false); setDirty(false); setExplanation(""); }}><span>{new Date(h.created_at).toLocaleString("en-AU")}</span><code>{h.input_sha256.slice(0,16)}…</code><span>{h.report.nodes.length} changes · {h.report.findings.length} findings</span></button>)}</details>}
    <div className="review-stepbar"><span className="current"><i>1</i> Import a plan</span><ChevronRight size={14} /><span className={report ? "current" : ""}><i>2</i> Inspect the impact</span><ChevronRight size={14} /><span><i>3</i> Export for human review</span></div>
    <div className="review-grid">
      <section className="plan-editor">
        <div className="review-panel-head"><div><Braces size={17} /><h2>Proposed change</h2></div><span className="review-tag">Terraform JSON</span></div>
        <div className="plan-import"><input ref={file} type="file" accept=".json,application/json" hidden onChange={(event) => { void loadFile(event.target.files?.[0]); event.target.value = ""; }} /><button className="plan-upload" disabled={busy} onClick={() => file.current?.click()}><span><Upload size={20} /></span><strong>Choose a plan file</strong><small>JSON · up to 1 MB · processed in your browser</small></button><div className="plan-command"><FileJson size={14} /><code>terraform show -json tfplan</code></div></div>
        <label className="plan-source"><span>Or paste plan JSON</span><textarea value={input} onChange={(event) => { setInput(event.target.value); setDirty(true); generation.current++; setExplanation(""); }} placeholder={'{\n  "format_version": "1.2",\n  "resource_changes": [...]\n}'} spellCheck={false} maxLength={1_000_000} aria-label="Terraform plan JSON" /></label>
        <div className="plan-editor-actions"><button className="btn btn-primary" disabled={!input.trim() || busy} onClick={() => void analyze(input, example && input === SAMPLE_PLAN)}>{busy ? <LoaderCircle className="spin" size={14} /> : <Play size={14} />} Analyze plan</button><button className="btn btn-ghost" disabled={busy} onClick={() => { setInput(SAMPLE_PLAN); void analyze(SAMPLE_PLAN, true); }}>Load example</button></div>
        {error && <div className="alert bad" role="alert">{error}</div>}
        <div className="plan-privacy"><Check size={14} /><span>Raw plan values stay in your browser. AI explanations receive only the extracted resource metadata and findings.</span></div>
      </section>
      <section className="review-results" aria-label="Change assessment">
        {!report ? <div className="review-empty"><span><Layers3 size={32} strokeWidth={1.3} /></span><h2>A clearer decision starts here.</h2><p>Import a plan to see what changes, where risk concentrates, and which changed resources reference one another.</p><button className="btn" onClick={() => { setInput(SAMPLE_PLAN); void analyze(SAMPLE_PLAN, true); }}>Explore a worked example <ArrowRight size={14} /></button><div className="review-empty-legend"><span><i className="severity-dot critical" /> Destructive changes</span><span><i className="severity-dot medium" /> Privilege expansion</span><span><i className="severity-dot info" /> Dependencies</span></div></div> : <>
          <div className={`review-verdict ${urgent ? "hold" : "inspect"}`}><span className="verdict-symbol">{urgent ? <ShieldAlert size={24} /> : <Info size={24} />}</span><div><span className="review-eyebrow">{example ? "EXAMPLE ASSESSMENT" : "PLAN ASSESSMENT"}</span><h2>{urgent ? "Pause. Review the risk first." : "Review the remaining uncertainty."}</h2><p>{urgent ? `${urgent} high-priority finding${urgent === 1 ? " needs" : "s need"} independent review.` : "No selected high-priority rule triggered. This does not establish that the plan is safe."}</p></div><button className="icon-btn" disabled={dirty} onClick={exportReport} aria-label="Export assessment JSON" title="Export assessment JSON"><ArrowDownToLine size={18} /></button></div>
          {dirty && <div className="review-stale"><Info size={14} /> The input has changed. Analyze it again to refresh this assessment.</div>}
          <div className="impact-metrics"><div><span>Changed resources</span><strong>{report.nodes.length}</strong><small>Included in this plan</small></div><div><span>Priority findings</span><strong>{urgent.toString().padStart(2, "0")}</strong><small>Critical and high severity</small></div><div><span>Dependency links</span><strong>{report.nodes.reduce((sum, n) => sum + n.dependencies.length, 0).toString().padStart(2, "0")}</strong><small>Declared between changes</small></div></div>
          <div className="review-tabs"><button className={tab === "dependencies" ? "on" : ""} onClick={() => setTab("dependencies")}><GitBranch size={14} /> Impact map</button><button className={tab === "findings" ? "on" : ""} onClick={() => setTab("findings")}><ShieldAlert size={14} /> Findings <span>{report.findings.length}</span></button></div>
          {tab === "dependencies" ? <div className="impact-section"><DependencyMap report={report} selected={selected} onSelect={setSelected} /><div className="impact-selection"><span className="review-eyebrow">SELECTED RESOURCE</span><strong>{selectedNode?.address ?? "No resource selected"}</strong><p>{downstream.length ? `${downstream.length} changed resource${downstream.length === 1 ? " references" : "s reference"} this resource, directly or transitively.` : "No downstream changed-resource references were found in the supplied configuration."}</p>{downstream.length > 0 && <div className="downstream-tags">{downstream.map((address) => <button key={address} onClick={() => setSelected(address)}>{address.split(".").pop()} <ChevronRight size={12} /></button>)}</div>}</div></div> : <div className="review-findings">{report.findings.length ? report.findings.map((f) => <article className="review-finding" key={f.id}><div><span className={`severity-label ${f.severity}`}>{f.severity}</span><span className="faint mono">{f.id}</span></div><h3>{f.title}</h3><button className="finding-resource" onClick={() => { setSelected(f.resource); setTab("dependencies"); }}>{f.resource}</button><p>{f.evidence}</p><div className="finding-remedy"><ArrowRight size={13} /><span>{f.recommendation}</span></div></article>) : <div className="rows-empty">No selected rule triggered. Review the plan and its unknowns independently.</div>}</div>}
          <details className="changed-resource-list"><summary>All changed resources ({report.nodes.length})</summary>{report.nodes.map((node) => <button key={node.address} onClick={() => { setSelected(node.address); setTab("dependencies"); }}><code>{node.address}</code><span>{node.actions.join(" → ")}</span></button>)}</details>
          <div className="review-ai"><div><span><Sparkles size={17} /></span><div><h3>Make the findings easier to review</h3><p>{aiEnabled ? "Ask AI to explain the extracted findings. It cannot change the assessment." : "The rule-based review works without AI. Configure NVIDIA to enable explanations."}</p></div></div><button className="btn" onClick={() => void explain()} disabled={!aiEnabled || explaining || dirty}>{explaining ? <LoaderCircle className="spin" size={14} /> : <Sparkles size={14} />} Explain findings</button>{explanation && <p className="review-ai-answer" aria-live="polite">{explanation}</p>}</div>
          <div className="assessment-save"><button className="btn" disabled={!fingerprint || dirty || example || saving} onClick={async () => { setSaving(true); try { const result = await saveChangeAssessment(JSON.stringify(report), fingerprint, requestId); setSaveMessage(result.message); } catch { setSaveMessage("The assessment could not be saved. Try again."); } finally { setSaving(false); } }}>{saving ? "Saving…" : "Save advisory assessment"}</button>{saveMessage && <span role="status">{saveMessage}</span>}</div>
          <div className="review-scope"><Info size={15} /><p>Advisory analysis of the supplied plan, not a live topology or execution simulation. Unchanged resources, runtime dependencies, effective IAM inheritance, cost impact and cross-cloud effects are not inferred. OPA, ServiceNow and human merge gates remain authoritative.</p></div>
          {fingerprint && <details className="review-fingerprint"><summary>Assessment provenance</summary><p>Terraform {report.terraformVersion} · {checkedAt}</p><code>Plan SHA-256: {fingerprint}</code><p>A checksum identifies the input file. This is not a signature or immutable audit record.</p></details>}
        </>}
      </section>
    </div>
    <div className="review-footer"><span><Check size={13} /> Deterministic rules</span><span><Check size={13} /> No cloud changes</span><span><Check size={13} /> Exportable evidence</span><span>GCP-focused · Terraform JSON format 1.x</span></div>
  </div>;
}
