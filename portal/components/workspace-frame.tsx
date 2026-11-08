"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState } from "react";
import { Activity, ArrowUpRight, Blocks, ChartNoAxesCombined, ClipboardList, Fingerprint, LayoutDashboard, Menu, ShieldCheck, SlidersHorizontal, Sparkles, Plug, Radio, Workflow, X } from "lucide-react";

const NAV = [
  { href: "/", label: "Overview", icon: LayoutDashboard, group: "Workspace" },
  { href: "/review", label: "Change review", icon: Workflow, group: "Workspace" },
  { href: "/requests", label: "Requests", icon: ClipboardList, group: "Workspace" },
  { href: "/access", label: "My access", icon: Fingerprint, group: "Workspace" },
  { href: "/inventory", label: "Resource inventory", icon: Blocks, group: "Governance" },
  { href: "/investigations", label: "Investigations", icon: Radio, group: "Workspace" },
  { href: "/labels", label: "Label compliance", icon: ShieldCheck, group: "Governance" },
  { href: "/cost", label: "Cost insights", icon: ChartNoAxesCombined, group: "Governance" },
  { href: "/audit", label: "Audit log", icon: Activity, group: "Governance" },
  { href: "/services", label: "Service catalog", icon: Blocks, group: "Platform" },
  { href: "/operations", label: "Operations", icon: Activity, group: "Platform" },
  { href: "/integrations", label: "Integrations", icon: Plug, group: "Platform" },
  { href: "/setup", label: "Readiness & recovery", icon: ShieldCheck, group: "Platform" },
  { href: "/administration", label: "Governance controls", icon: SlidersHorizontal, group: "Platform" },
];

/** One navigation surface, shared by the live workspace and sample preview. */
export function WorkspaceFrame({ children, tenantName, openCount = 0, overviewHref = "/", activeHref, actions, account, onAsk, connection = "live" }: {
  children: React.ReactNode;
  tenantName: string;
  openCount?: number;
  overviewHref?: string;
  activeHref?: string;
  actions?: React.ReactNode;
  account?: React.ReactNode;
  onAsk?: () => void;
  connection?: "live" | "connecting" | "offline" | "sample";
}) {
  const pathname = usePathname();
  const [expandedAt, setExpandedAt] = useState<string | null>(null);
  const expanded = expandedAt === pathname;
  const active = (href: string) => activeHref ? href === activeHref : href === "/" ? pathname === overviewHref : pathname === href || pathname.startsWith(`${href}/`) || (href === "/requests" && pathname.startsWith("/new/"));
  return (
    <div className="workspace-app">
      <a className="skip-link" href="#workspace-main">Skip to content</a>
      <aside className="workspace-sidebar" data-expanded={expanded}>
        <div className="sidebar-brand">
          <Link href={overviewHref} className="brand-lockup" aria-label="Aegis overview"><span className="brand-symbol"><ShieldCheck size={22} strokeWidth={1.8} /></span><span>AEGIS<small>Cloud governance</small></span></Link>
          <button className="icon-btn sidebar-toggle" aria-label={expanded ? "Close navigation" : "Open navigation"} aria-expanded={expanded} aria-controls="workspace-navigation" onClick={() => setExpandedAt(expanded ? null : pathname)}>{expanded ? <X size={20} /> : <Menu size={20} />}</button>
        </div>
        <div id="workspace-navigation" className="sidebar-content">
          <div className="workspace-switch"><span className="workspace-initial">{tenantName.slice(0, 1).toUpperCase()}</span><span><strong>{tenantName}</strong><small>Governed workspace</small></span></div>
          {actions && <div className="sidebar-actions">{actions}</div>}
          <nav className="sidebar-nav" aria-label="Main navigation">
            {["Workspace", "Governance", "Platform"].map((group) => <div className="sidebar-group" key={group}><span className="sidebar-group-label">{group}</span>{NAV.filter((item) => item.group === group).map(({ href, label, icon: Icon }) => <Link key={href} href={href === "/" ? overviewHref : href === "/review" && overviewHref !== "/" ? `${overviewHref}?mode=review` : href} className={active(href) ? "active" : ""} aria-current={active(href) ? "page" : undefined} onClick={() => setExpandedAt(null)}><Icon size={18} strokeWidth={1.7} /><span>{label}</span>{href === "/requests" && openCount > 0 && <em>{openCount}</em>}</Link>)}</div>)}
          </nav>
          <div className="sidebar-bottom">
            {onAsk && <button className="sidebar-assistant" onClick={onAsk}><span className="assistant-icon"><Sparkles size={18} /></span><span><strong>Ask Aegis</strong><small>A little help, grounded in data</small></span><ArrowUpRight size={15} /></button>}
            <div className="sidebar-connection"><span className={`dot ${connection === "live" ? "good" : connection === "offline" ? "bad" : "info"}`} />{connection === "sample" ? "Sample workspace" : connection === "live" ? "Live workspace updates" : connection === "offline" ? "Updates disconnected" : "Connecting to workspace"}</div>
            {account}
          </div>
        </div>
      </aside>
      <main id="workspace-main" className="workspace-main" tabIndex={-1}><div className="page">{children}</div></main>
    </div>
  );
}
