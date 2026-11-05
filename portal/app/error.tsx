"use client";

import { AlertTriangle, RefreshCw } from "lucide-react";
import { useEffect } from "react";

export default function ErrorPage({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  useEffect(() => { console.error("Aegis route failed", error); }, [error]);
  return <main className="auth-shell"><section className="auth-card"><div className="empty-state"><span><AlertTriangle size={24} /></span><strong>We could not load your workspace</strong><p>The error was contained. Your request data has not been changed.</p><button className="primary-button" onClick={reset}><RefreshCw size={15} /> Try again</button></div></section></main>;
}
