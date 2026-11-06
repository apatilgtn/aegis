"use client";

import { useActionState, useState } from "react";
import { ArrowRight, KeyRound, LoaderCircle } from "lucide-react";
import Image from "next/image";
import { authenticate, type AuthState } from "./actions";

const initialState: AuthState = {};

export function LoginForm() {
  const [mode, setMode] = useState<"signin" | "signup">("signin");
  const [state, action, pending] = useActionState(authenticate, initialState);

  return (
    <div className="login-card">
      <div className="login-brand"><span className="auth-mark"><Image src="/brand/aegis-mark.svg" width={31} height={31} alt="Aegis" priority /></span><div><strong>AEGIS</strong><small>ENGINEERING CONTROL PLANE</small></div></div>
      <div className="login-copy"><span className="kicker">TRUSTED IDENTITY</span><h1>{mode === "signin" ? "Welcome back." : "Create your identity."}</h1><p>Sign in with your verified account. Workspace access and roles are assigned separately by an administrator.</p></div>
      <div className="auth-tabs" role="tablist" aria-label="Authentication mode">
        <button type="button" className={mode === "signin" ? "active" : ""} onClick={() => setMode("signin")}>Sign in</button>
        <button type="button" className={mode === "signup" ? "active" : ""} onClick={() => setMode("signup")}>Create account</button>
      </div>
      <form action={action} className="auth-form">
        <input type="hidden" name="intent" value={mode} />
        {mode === "signup" && <label><span>Name</span><input name="displayName" autoComplete="name" minLength={2} required placeholder="Your name" /></label>}
        <label><span>Work email</span><input name="email" type="email" autoComplete="email" required placeholder="you@company.com" /></label>
        <label><span>Password</span><input name="password" type="password" autoComplete={mode === "signin" ? "current-password" : "new-password"} minLength={mode === "signup" ? 12 : 1} required placeholder={mode === "signup" ? "12+ characters" : "Your password"} /></label>
        {state.error && <div className="auth-alert error" role="alert">{state.error}</div>}
        {state.message && <div className="auth-alert success" role="status">{state.message}</div>}
        <button className="primary-button full auth-submit" disabled={pending} type="submit">
          {pending ? <><LoaderCircle className="spin" size={16} /> Working…</> : <>{mode === "signin" ? "Sign in securely" : "Create account"}<ArrowRight size={16} /></>}
        </button>
      </form>
      <div className="login-foot"><KeyRound size={14} /> Your role comes from your Aegis workspace membership, not from the email you type.</div>
    </div>
  );
}
