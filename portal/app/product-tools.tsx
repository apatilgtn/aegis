"use client";

import { useActionState, useEffect, useMemo, useState } from "react";
import { Activity, Boxes, CheckCircle2, Fingerprint, Search, ShieldCheck, Sparkles, UserRound, X, Zap } from "lucide-react";
import { askAegis, updateDisplayName, type ActionState, type AegisAnswerState } from "./actions";
import type { PortalData } from "@/lib/domain";

type WorkspaceView = "Overview" | "Services" | "Actions" | "Compliance" | "Roadmap";

const closeOnBackdrop = (close: () => void) => (event: React.MouseEvent<HTMLDivElement>) => {
  if (event.target === event.currentTarget) close();
};

export function SearchDialog({ data, close, navigate }: { data: PortalData; close: () => void; navigate: (view: WorkspaceView) => void }) {
  const [query, setQuery] = useState("");
  const normalized = query.trim().toLowerCase();
  const results = useMemo(() => {
    if (!normalized) return [];
    const services = data.services.filter((service) => [service.name, service.slug, service.description, service.lifecycle, service.tier].some((value) => value?.toLowerCase().includes(normalized))).map((service) => ({ id: `service-${service.id}`, title: service.name, detail: `${service.lifecycle} · ${service.tier ?? "unclassified"}`, view: "Services" as WorkspaceView, icon: Boxes }));
    const requests = data.requests.filter((request) => [request.id, request.status, request.capability, request.justification, request.service?.name, ...Object.values(request.resource).map(String)].some((value) => value?.toLowerCase().includes(normalized))).map((request) => ({ id: `request-${request.id}`, title: request.service?.name ?? "Access request", detail: `${request.id.slice(0, 8).toUpperCase()} · ${request.status.replaceAll("_", " ")}`, view: "Actions" as WorkspaceView, icon: Zap }));
    const audits = data.auditEvents.filter((event) => [event.action, event.objectType, event.objectId].some((value) => value?.toLowerCase().includes(normalized))).map((event) => ({ id: `audit-${event.id}`, title: event.action.replaceAll(".", " "), detail: `${event.objectType} · ${event.objectId?.slice(0, 12) ?? "system"}`, view: "Compliance" as WorkspaceView, icon: Fingerprint }));
    return [...services, ...requests, ...audits].slice(0, 12);
  }, [data, normalized]);

  useEffect(() => {
    const escape = (event: KeyboardEvent) => { if (event.key === "Escape") close(); };
    window.addEventListener("keydown", escape);
    return () => window.removeEventListener("keydown", escape);
  }, [close]);

  return <div className="dialog-backdrop" role="presentation" onMouseDown={closeOnBackdrop(close)}><div className="dialog search-dialog" role="dialog" aria-modal="true" aria-labelledby="search-title"><div className="dialog-head"><div><span className="kicker">WORKSPACE SEARCH</span><h2 id="search-title">Find anything you can access</h2></div><button onClick={close} aria-label="Close"><X size={20} /></button></div><div className="command-input"><Search size={18} /><input autoFocus value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search services, requests and evidence…" aria-label="Search workspace" /><kbd>ESC</kbd></div><div className="search-results">{!normalized && <div className="tool-empty"><Search size={22} /><strong>Search the live workspace</strong><span>Results respect your existing tenant and audit permissions.</span></div>}{normalized && !results.length && <div className="tool-empty"><Search size={22} /><strong>No matching records</strong><span>Try a service name, request status, identifier or audit action.</span></div>}{results.map((result) => <button key={result.id} onClick={() => { navigate(result.view); close(); }}><span><result.icon size={17} /></span><div><strong>{result.title}</strong><small>{result.detail}</small></div><em>{result.view}</em></button>)}</div></div></div>;
}

const initialProfileState: ActionState = { ok: false, message: "" };

export function SettingsDialog({ displayName, email, role, tenantName, realtime, close }: { displayName: string; email: string; role: string; tenantName: string; realtime: string; close: () => void }) {
  const [state, action, pending] = useActionState(updateDisplayName, initialProfileState);
  return <div className="dialog-backdrop" role="presentation" onMouseDown={closeOnBackdrop(close)}><div className="dialog settings-dialog" role="dialog" aria-modal="true" aria-labelledby="settings-title"><div className="dialog-head"><div><span className="kicker">ACCOUNT & WORKSPACE</span><h2 id="settings-title">Settings</h2></div><button onClick={close} aria-label="Close"><X size={20} /></button></div><div className="settings-identity"><span><UserRound size={20} /></span><div><strong>{email}</strong><small>Authenticated by Supabase</small></div><i className="status-dot" /></div><form action={action} className="workflow-form"><label><span>Display name</span><input name="displayName" defaultValue={displayName} required minLength={2} maxLength={80} /></label>{state.message && <div className={state.ok ? "form-success" : "form-error"}>{state.message}</div>}<button className="primary-button" disabled={pending}>{pending ? "Saving…" : "Save profile"}</button></form><div className="settings-grid"><div><span>Workspace</span><strong>{tenantName}</strong></div><div><span>Role</span><strong>{role.replaceAll("_", " ")}</strong></div><div><span>Realtime</span><strong className={realtime === "live" ? "good-text" : "warn-text"}>{realtime}</strong></div><div><span>Security</span><strong>RLS enforced</strong></div></div><div className="settings-note"><ShieldCheck size={16} /><span>Workspace roles and privileged integration settings are administrator-controlled and cannot be elevated from this screen.</span></div></div></div>;
}

const initialAegisState: AegisAnswerState = { ok: false, message: "", answer: "", evidence: [] };
const suggestedQuestions = ["What is waiting for approval?", "Summarize the service catalog", "Are there recent failures?", "What is the latest request?"];

export function AskAegisDialog({ close }: { close: () => void }) {
  const [state, action, pending] = useActionState(askAegis, initialAegisState);
  const [question, setQuestion] = useState("");
  return <div className="dialog-backdrop" role="presentation" onMouseDown={closeOnBackdrop(close)}><div className="dialog aegis-dialog" role="dialog" aria-modal="true" aria-labelledby="aegis-title"><div className="dialog-head"><div><span className="kicker">GROUNDED WORKSPACE ASSISTANT</span><h2 id="aegis-title">Ask Aegis</h2></div><button onClick={close} aria-label="Close"><X size={20} /></button></div><p>Aegis reads only the live records your authenticated role can access. It cannot approve, merge or mutate infrastructure.</p><div className="prompt-chips">{suggestedQuestions.map((prompt) => <button type="button" key={prompt} onClick={() => setQuestion(prompt)}>{prompt}</button>)}</div><form action={action} className="ask-form"><textarea name="question" value={question} onChange={(event) => setQuestion(event.target.value)} minLength={3} maxLength={500} required placeholder="Ask about services, requests, approvals or workflow failures…" /><button className="aegis-submit" disabled={pending || question.trim().length < 3}>{pending ? <Activity className="spin" size={16} /> : <Sparkles size={16} />}{pending ? "Analyzing live data…" : "Ask Aegis"}</button></form>{state.message && !state.ok && <div className="form-error">{state.message}</div>}{state.ok && <div className="aegis-answer"><div><CheckCircle2 size={18} /><span>{state.message}</span></div><p>{state.answer}</p><small>Evidence: {state.evidence.join(" · ")}</small></div>}<div className="settings-note"><ShieldCheck size={16} /><span>This version uses deterministic live-data analysis. A local Ollama model can be added later without sending workspace data to a hosted AI provider.</span></div></div></div>;
}
