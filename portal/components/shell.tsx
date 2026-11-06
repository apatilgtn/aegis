"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState, useTransition } from "react";
import { AnimatePresence, motion } from "motion/react";
import { KeyRound, LogOut, Plus, Search, Tags } from "lucide-react";
import { createClient } from "@/lib/supabase/client";
import { AskPanel } from "./ask-panel";
import { WorkspaceFrame } from "./workspace-frame";

type Props = {
  children: React.ReactNode;
  displayName: string;
  email: string;
  role: string;
  tenantId: string;
  tenantName: string;
  counts: { needsMe: number; open: number };
  aiEnabled: boolean;
};

function initials(name: string) {
  return name.split(/\s+/).filter(Boolean).slice(0, 2).map((part) => part[0]?.toUpperCase()).join("") || "A";
}

export function Shell({ children, displayName, email, role, tenantId, tenantName, counts, aiEnabled }: Props) {
  const pathname = usePathname();
  const router = useRouter();
  const [asking, setAsking] = useState(false);
  // A menu belongs to the page it was opened on, so navigating closes it.
  const [openMenu, setOpenMenu] = useState<{ kind: "new" | "user"; path: string } | null>(null);
  const menu = openMenu?.path === pathname ? openMenu.kind : null;
  const setMenu = (kind: "new" | "user" | null) => setOpenMenu(kind ? { kind, path: pathname } : null);
  const [live, setLive] = useState<"connecting" | "live" | "offline">("connecting");
  const [, startRefresh] = useTransition();

  useEffect(() => {
    const supabase = createClient();
    const refresh = () => startRefresh(() => router.refresh());
    const channel = supabase.channel(`aegis-${tenantId}`);
    for (const table of ["action_requests", "workflow_events", "workflow_runs", "cloud_snapshots"]) {
      channel.on("postgres_changes", { event: "*", schema: "public", table, filter: `tenant_id=eq.${tenantId}` }, refresh);
    }
    channel.subscribe((status) => setLive(status === "SUBSCRIBED" ? "live" : status === "CHANNEL_ERROR" || status === "TIMED_OUT" ? "offline" : "connecting"));
    return () => void supabase.removeChannel(channel);
  }, [router, tenantId]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setAsking(true);
      }
      if (event.key === "Escape") {
        setAsking(false);
        setOpenMenu(null);
      }
    };
    const onClick = (event: MouseEvent) => {
      if (event.target instanceof Element && !event.target.closest(".menu-anchor")) setOpenMenu(null);
    };
    window.addEventListener("keydown", onKey);
    window.addEventListener("mousedown", onClick);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("mousedown", onClick);
    };
  }, []);

  return (
    <>
      <WorkspaceFrame tenantName={tenantName} openCount={counts.open} connection={live} onAsk={() => setAsking(true)}
        actions={
          <div className="menu-anchor">
            <button className="btn btn-primary btn-full" onClick={() => setMenu(menu === "new" ? null : "new")} aria-expanded={menu === "new"}><Plus size={16} /> New request</button>
            <AnimatePresence>{menu === "new" && <motion.div className="menu" initial={{ opacity: 0, y: -4 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -4 }} transition={{ duration: 0.14 }}>
              <Link href="/new/access"><KeyRound size={15} /><span><strong>Request access</strong><small>Time-bound GCP access</small></span></Link>
              <Link href="/new/investigation"><Search size={15} /><span><strong>Investigate an issue</strong><small>Search logs, then decide</small></span></Link>
              <Link href="/new/labels"><Tags size={15} /><span><strong>Fix missing labels</strong><small>Add required bucket labels</small></span></Link>
            </motion.div>}</AnimatePresence>
          </div>
        }
        account={
          <div className="menu-anchor sidebar-account">
            <button className="account-button" onClick={() => setMenu(menu === "user" ? null : "user")} aria-label="Account" aria-expanded={menu === "user"}>
              <span className="avatar">{initials(displayName)}{counts.needsMe > 0 && <i className="avatar-badge" />}</span><span><strong>{displayName}</strong><small>{role.replaceAll("_", " ")}</small></span>
            </button>
            <AnimatePresence>{menu === "user" && <motion.div className="menu account-menu" initial={{ opacity: 0, y: 4 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: 4 }} transition={{ duration: 0.14 }}>
              <div className="menu-who"><strong>{displayName}</strong><small>{email}</small><small>{tenantName}</small></div>
              <form action="/auth/signout" method="post"><button type="submit"><LogOut size={15} /><span><strong>Sign out</strong></span></button></form>
            </motion.div>}</AnimatePresence>
          </div>
        }
      >{children}</WorkspaceFrame>
      <AnimatePresence>{asking && <AskPanel close={() => setAsking(false)} aiEnabled={aiEnabled} />}</AnimatePresence>
    </>
  );
}
