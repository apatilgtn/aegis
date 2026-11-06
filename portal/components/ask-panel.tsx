"use client";

import { useRouter } from "next/navigation";
import { useActionState, useEffect, useRef, useState } from "react";
import { ArrowRight, LoaderCircle, Sparkles, X } from "lucide-react";
import { motion } from "motion/react";
import { askAegis, type AegisAnswerState } from "@/app/actions";
import type { RequestDraft } from "@/lib/nvidia";

const initial: AegisAnswerState = { ok: false, message: "", answer: "", evidence: [] };

const SUGGESTIONS = [
  "cilium-agent keeps failing, check the last day",
  "I need read access to logs for 4 hours to debug payments",
  "What is waiting for approval?",
  "Which buckets are missing labels?",
];

export function draftHref(draft: RequestDraft) {
  const params = new URLSearchParams();
  if (draft.capability === "access_grant") {
    params.set("tier", draft.tier);
    params.set("hours", String(draft.ttlHours));
    if (draft.justification) params.set("reason", draft.justification);
    return `/new/access?${params}`;
  }
  if (draft.capability === "incident_triage") {
    if (draft.resourceHint) params.set("hint", draft.resourceHint);
    params.set("hours", String(draft.timeWindowHours));
    if (draft.justification) params.set("reason", draft.justification);
    return `/new/investigation?${params}`;
  }
  if (draft.capability === "label_fix") {
    if (draft.bucket) params.set("bucket", draft.bucket);
    if (draft.costCenter) params.set("costCenter", draft.costCenter);
    if (draft.justification) params.set("reason", draft.justification);
    return `/new/labels?${params}`;
  }
  return "/";
}

export function AskPanel({ close, aiEnabled }: { close: () => void; aiEnabled: boolean }) {
  const router = useRouter();
  const [question, setQuestion] = useState("");
  const [messages, setMessages] = useState<{ question: string; response: AegisAnswerState }[]>([]);
  const [submitted, setSubmitted] = useState("");
  const thread = useRef<HTMLDivElement>(null);
  const [, action, pending] = useActionState(async (previous: AegisAnswerState, formData: FormData) => {
    const asked = String(formData.get("question") ?? "").trim();
    setSubmitted(asked);
    setQuestion("");
    let response: AegisAnswerState;
    try {
      response = await askAegis(previous, formData);
    } catch {
      response = { ok: false, message: "The assistant could not connect. Please try again.", answer: "", evidence: [] };
    }
    setMessages((history) => [...history, { question: asked, response }]);
    return response;
  }, initial);
  useEffect(() => {
    thread.current?.scrollTo({ top: thread.current.scrollHeight, behavior: "instant" });
  }, [messages, pending]);

  return (
    <motion.div
      className="ask-dock"
      role="presentation"
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      transition={{ duration: 0.18 }}
    >
      <motion.aside
        className="panel"
        role="dialog"
        aria-labelledby="ask-title"
        initial={{ y: 16, opacity: 0 }}
        animate={{ y: 0, opacity: 1 }}
        exit={{ y: 16, opacity: 0 }}
        transition={{ duration: 0.24, ease: [0.22, 1, 0.36, 1] }}
      >
        <div className="panel-head">
          <strong id="ask-title">
            <Sparkles size={14} /> Ask Aegis
          </strong>
          <button className="btn btn-ghost" onClick={close} aria-label="Close">
            <X size={16} />
          </button>
        </div>
        <div className="chat-thread" ref={thread} role="log" aria-live="polite" aria-label="Conversation with Aegis">
          {!messages.length && !pending && <div className="chat-welcome">
            <p>Ask about your workspace or draft a request. You review and submit every action.</p>
            <div className="chips">{SUGGESTIONS.map((suggestion) => <button type="button" className="chip" key={suggestion} onClick={() => setQuestion(suggestion)}>{suggestion}</button>)}</div>
          </div>}
          {messages.map(({ question: asked, response }, index) => (
            <div className="chat-turn" key={index}>
              <div className="chat-question"><span className="chat-speaker">You</span><p>{asked}</p></div>
              <div className={`answer ${response.ok ? "" : "chat-error"}`}>
                <span className="chat-speaker"><Sparkles size={12} /> Aegis</span>
                <p>{response.ok ? response.answer : response.message}</p>
                {response.draft && response.draft.capability !== "unknown" && <button className="btn btn-primary" onClick={() => { router.push(draftHref(response.draft!)); close(); }}>Open prepared form <ArrowRight size={14} /></button>}
                {response.ok && <details className="chat-evidence"><summary>{response.message}</summary><small>Based on: {response.evidence.join(", ")}</small></details>}
              </div>
            </div>
          ))}
          {pending && <div className="chat-turn"><div className="chat-question"><span className="chat-speaker">You</span><p>{submitted}</p></div><div className="chat-thinking"><LoaderCircle className="spin" size={14} /> Checking your workspace…</div></div>}
        </div>
        <form action={action} className="chat-composer">
          <textarea name="question" aria-label="Ask Aegis a question" value={question} onChange={(event) => setQuestion(event.target.value)} minLength={3} maxLength={500} required autoFocus placeholder="Ask a question or describe what you need…" />
          <button className="btn btn-primary" disabled={pending || question.trim().length < 3}>{pending ? <LoaderCircle className="spin" size={14} /> : <Sparkles size={14} />}{pending ? "Thinking…" : "Ask"}</button>
          {!aiEnabled && <small className="chat-connection-note">AI is not configured. Answers use verified workspace facts.</small>}
        </form>
      </motion.aside>
    </motion.div>
  );
}
