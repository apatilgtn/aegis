"use client";

import {
  Activity,
  ArrowRight,
  ArrowUpRight,
  Boxes,
  CheckCircle2,
  Fingerprint,
  Gauge,
  KeyRound,
  LayoutDashboard,
  LockKeyhole,
  LogOut,
  Menu,
  Plus,
  RefreshCw,
  Search,
  Settings,
  ShieldCheck,
  Sparkles,
  X,
  Zap,
} from "lucide-react";
import Image from "next/image";
import { useRouter } from "next/navigation";
import { useActionState, useEffect, useMemo, useState, useTransition } from "react";
import { createAccessRequest, createIncidentTriage, createLabelFix, createService, decideIncident, type ActionState } from "./actions";
import {
  AzureOriginalIcon,
  GitHubOriginalIcon,
  GoogleCloudOriginalIcon,
  OpaOriginalIcon,
  ServiceNowOriginalIcon,
  SupabaseOriginalIcon,
  TerraformOriginalIcon,
} from "./brand-icons";
import { AskAegisDialog, SearchDialog, SettingsDialog } from "./product-tools";
import { createClient } from "@/lib/supabase/client";
import type { ActionRequestSummary, DeliverySummary, EntitlementOption, PortalData, ServiceSummary } from "@/lib/domain";
import type { RequestDraft } from "@/lib/nvidia";

type View = "Overview" | "Services" | "Actions" | "Compliance";
type NavItem = { label: View; icon: typeof LayoutDashboard };

const navItems: NavItem[] = [
  { label: "Overview", icon: LayoutDashboard },
  { label: "Services", icon: Boxes },
  { label: "Actions", icon: Zap },
  { label: "Compliance", icon: ShieldCheck },
];

const initialState: ActionState = { ok: false, message: "" };

function Mark() {
  return (
    <div className="mark" aria-label="Aegis">
      <Image src="/brand/aegis-mark.svg" width={40} height={40} alt="" priority />
    </div>
  );
}

function Pill({ children, tone = "neutral" }: { children: React.ReactNode; tone?: string }) {
  return <span className={`pill pill-${tone}`}>{children}</span>;
}

function ShellCard({ children, className = "" }: { children: React.ReactNode; className?: string }) {
  return <section className={`card ${className}`}>{children}</section>;
}

function toneForStatus(status: string) {
  if (["approved", "completed"].includes(status)) return "mint";
  if (["rejected", "failed", "cancelled", "expired"].includes(status)) return "rose";
  if (["awaiting_policy", "awaiting_approval", "merged", "executing", "applying"].includes(status)) return "amber";
  return "blue";
}

const IN_PROGRESS_STATUSES = ["awaiting_policy", "awaiting_approval", "approved", "proposal_pending", "pr_open", "merged", "executing", "applying"];
const SENT_TO_GITHUB_STATUSES = ["pr_open", "merged", "executing", "applying", "completed"];
const PAST_APPROVAL_STATUSES = ["approved", "proposal_pending", ...SENT_TO_GITHUB_STATUSES];

const TIER_LABELS: Record<string, string> = {
  tier1: "Tier 1 · Read",
  tier2: "Tier 2 · Edit",
  tier3: "Tier 3 · Privileged",
};
const CLOUD_LABELS: Record<string, string> = { gcp: "Google Cloud", azure: "Microsoft Azure" };
const ENVIRONMENT_LABELS: Record<string, string> = { nonprod: "Non-production", prod: "Production" };
const DURATION_HOURS = [1, 4, 8, 24, 72, 168];

function statusLabel(status: string) {
  return status.replaceAll("_", " ").replace(/^./, (value) => value.toUpperCase());
}

const STATUS_LABELS: Record<string, string> = {
  awaiting_policy: "Policy check",
  awaiting_approval: "Waiting for approval",
  proposal_pending: "Opening pull request",
  pr_open: "Pull request open",
  merged: "Merged, waiting for pipeline",
  applying: "Applying in GCP",
  completed: "Access live",
};

const INCIDENT_STATUS_LABELS: Record<string, string> = {
  awaiting_policy: "Policy check",
  executing: "Searching logs",
  awaiting_approval: "Findings ready",
  completed: "Incident raised",
  cancelled: "Closed, no incident",
};

function requestStatusLabel(status: string, capability = "access_grant") {
  if (capability === "incident_triage") return INCIDENT_STATUS_LABELS[status] ?? statusLabel(status);
  if (capability === "label_fix" && status === "completed") return "Labels applied";
  if (status === "awaiting_approval") return "Waiting in ServiceNow";
  return STATUS_LABELS[status] ?? statusLabel(status);
}

function describeRequest(request: ActionRequestSummary) {
  const resource = request.resource;
  if (request.capability === "incident_triage") return `Investigate ${String(resource.resource_hint ?? "an issue")}`;
  if (request.capability === "label_fix") return `Add missing labels to ${String(resource.bucket ?? "a bucket")}`;
  return `${request.service?.name ?? "Access"} · ${TIER_LABELS[String(resource.tier)] ?? String(resource.tier ?? "access")}`;
}

function requestDetail(request: ActionRequestSummary) {
  const resource = request.resource;
  if (request.capability === "incident_triage") return `Logs · last ${String(resource.time_window_hours ?? "?")} hours`;
  if (request.capability === "label_fix") return `Cost center ${String(resource.cost_center ?? "?")}`;
  return [resource.cloud, resource.environment, resource.tier].filter(Boolean).join(" · ");
}

function formatExpiry(value: string) {
  const date = new Date(value);
  const label = date.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
  return date.getTime() <= Date.now() ? `Expired ${label}` : `Ends ${label}`;
}

function shortId(id: string) {
  return `REQ-${id.slice(0, 8).toUpperCase()}`;
}

function timeAgo(value: string) {
  const minutes = Math.max(0, Math.floor((Date.now() - new Date(value).getTime()) / 60000));
  if (minutes < 1) return "Just now";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}

function CloudProviderIcon({ cloud, size = 20 }: { cloud?: unknown; size?: number }) {
  const normalized = String(cloud || "gcp").toLowerCase();
  if (normalized.includes("azure")) {
    return <AzureOriginalIcon width={size} height={size} />;
  }
  return <GoogleCloudOriginalIcon width={size} height={size} />;
}

function EmptyState({
  icon: Icon,
  title,
  copy,
  action,
}: {
  icon: typeof Boxes;
  title: string;
  copy: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="empty-state">
      <span>
        <Icon size={24} />
      </span>
      <strong>{title}</strong>
      <p>{copy}</p>
      {action}
    </div>
  );
}

function RequestList({ requests, currentUserId }: { requests: ActionRequestSummary[]; currentUserId: string }) {
  if (!requests.length)
    return (
      <EmptyState
        icon={Zap}
        title="No governed actions yet"
        copy="Your first access request will appear here with its policy, approval and workflow status."
      />
    );
  return (
    <div className="activity-list">
      {requests.map((request) => {
        const detail = requestDetail(request);
        return (
          <div className="activity-row" key={request.id}>
            <span className="activity-dot" title={String(request.resource.cloud || "Google Cloud")}>
              <CloudProviderIcon cloud={request.resource.cloud} size={20} />
            </span>
            <div className="activity-copy">
              <strong>{describeRequest(request)}</strong>
              <span>
                {shortId(request.id)} · {detail || request.capability} ·{" "}
                {request.requesterId === currentUserId ? "Requested by you" : "Team member"}
                {request.status === "completed" && request.delivery.expiresAt && ` · ${formatExpiry(request.delivery.expiresAt)}`}
                {request.status === "failed" && request.delivery.reason && ` · ${request.delivery.reason}`}
                {request.delivery.changeNumber && (
                  <>
                    {" · "}
                    <ServiceNowLink delivery={request.delivery} />
                  </>
                )}
                {request.delivery.incidentNumber && request.delivery.incidentUrl && (
                  <>
                    {" · "}
                    <a href={request.delivery.incidentUrl} target="_blank" rel="noreferrer" className="inline-link">
                      {request.delivery.incidentNumber}
                    </a>
                  </>
                )}
              </span>
            </div>
            {request.delivery.pullRequestUrl && (
              <a
                className="row-arrow"
                href={request.delivery.pullRequestUrl}
                target="_blank"
                rel="noreferrer"
                title="Open the pull request on GitHub"
                aria-label="Open the pull request on GitHub"
              >
                <GitHubOriginalIcon width={16} height={16} />
              </a>
            )}
            <Pill tone={toneForStatus(request.status)}>{requestStatusLabel(request.status, request.capability)}</Pill>
            <time>{timeAgo(request.updatedAt)}</time>
          </div>
        );
      })}
    </div>
  );
}

function ServiceList({
  services,
  entitlements,
}: {
  services: ServiceSummary[];
  entitlements: EntitlementOption[];
}) {
  if (!services.length)
    return (
      <EmptyState
        icon={Boxes}
        title="Your catalog is ready"
        copy="Add the first service to connect ownership, repositories and governed actions."
      />
    );
  return (
    <div className="service-list">
      {services.map((service) => {
        const routes = entitlements.filter((route) => route.serviceId === service.id);
        const clouds = [...new Set(routes.map((route) => route.cloud))];
        return (
        <div className="service-row" key={service.id}>
          <span
            className="service-icon"
            title={clouds.length ? clouds.map((cloud) => CLOUD_LABELS[cloud] ?? cloud).join(", ") : "No cloud access configured"}
          >
            {clouds.length ? <CloudProviderIcon cloud={clouds[0]} size={20} /> : <Boxes size={20} />}
          </span>
          <div className="service-main">
            <strong>{service.name}</strong>
            <span>{service.description || service.slug}</span>
          </div>
          <Pill>{service.lifecycle}</Pill>
          <div className="score">
            <b>{service.tier || "—"}</b>
            <span>TIER</span>
          </div>
          <span className={routes.length ? "status mint" : "status"}>
            <i />
            {routes.length ? `${routes.length} access level${routes.length === 1 ? "" : "s"}` : "No access levels"}
          </span>
          {service.repositoryUrl ? (
            <a
              className="row-arrow"
              href={service.repositoryUrl}
              target="_blank"
              rel="noreferrer"
              aria-label={`Open ${service.name} repository on GitHub`}
              title="Open GitHub repository"
            >
              <GitHubOriginalIcon width={16} height={16} />
              <ArrowUpRight size={15} />
            </a>
          ) : (
            <span className="row-arrow" title="Catalogued">
              <ArrowRight size={16} />
            </span>
          )}
        </div>
        );
      })}
    </div>
  );
}

function Overview({
  data,
  displayName,
  currentUserId,
  openAction,
  openService,
}: {
  data: PortalData;
  displayName: string;
  currentUserId: string;
  openAction: () => void;
  openService: () => void;
}) {
  const inProgress = data.requests.filter((request) => IN_PROGRESS_STATUSES.includes(request.status)).length;
  const needApproval = data.requests.filter(
    (request) => request.status === "awaiting_approval" && request.capability !== "incident_triage",
  ).length;
  const sentToGitHub = data.requests.filter((request) => SENT_TO_GITHUB_STATUSES.includes(request.status)).length;
  const sentShare = data.requests.length ? Math.round((sentToGitHub / data.requests.length) * 100) : 0;
  const latest = data.requests[0];

  return (
    <>
      <div className="hero-row">
        <div>
          <div className="eyebrow">
            <span className="pulse" /> Live engineering estate
          </div>
          <h1>Welcome back, {displayName}.</h1>
          <p className="lede">
            Requests, policy decisions and immutable evidence come directly from your protected workspace.
          </p>
        </div>
        <div className="hero-actions">
          <button className="secondary-button" onClick={openService}>
            <Plus size={16} /> Add service
          </button>
          <button className="primary-button" onClick={openAction}>
            <KeyRound size={16} /> Request access
          </button>
        </div>
      </div>

      <div className="metric-grid">
        <ShellCard className="metric-card featured">
          <div className="metric-top">
            <span className="icon-box">
              <SupabaseOriginalIcon width={20} height={20} />
            </span>
            <Pill tone="dark">Live Catalog</Pill>
          </div>
          <div className="metric-value">{data.services.length}</div>
          <div className="metric-label">Catalogued services</div>
          <div className="submetric">
            <span className="submetric-brand">
              <SupabaseOriginalIcon width={14} height={14} /> Supabase-backed
            </span>
            <button className="inline-link" onClick={openService}>
              + Add service
            </button>
          </div>
        </ShellCard>

        <ShellCard className="metric-card">
          <div className="metric-top">
            <span className="icon-box">
              <OpaOriginalIcon width={20} height={20} />
            </span>
            <Pill>{data.requests.length} total</Pill>
          </div>
          <div className="metric-value">{inProgress}</div>
          <div className="metric-label">Requests in progress</div>
          <div className="submetric">
            <span className="submetric-brand">
              <OpaOriginalIcon width={14} height={14} /> OPA Policy + Approval
            </span>
            <span className="good">Realtime</span>
          </div>
        </ShellCard>

        <ShellCard className="metric-card">
          <div className="metric-top">
            <span className="icon-box">
              <ServiceNowOriginalIcon width={20} height={20} />
            </span>
            <Pill tone="amber">ServiceNow</Pill>
          </div>
          <div className="metric-value">{needApproval}</div>
          <div className="metric-label">Waiting for approval</div>
          <div className="submetric">
            <span className="submetric-brand">
              <ServiceNowOriginalIcon width={14} height={14} /> Approver ≠ requester
            </span>
            <span className="good">SoD enforced</span>
          </div>
        </ShellCard>

        <ShellCard className="metric-card">
          <div className="metric-top">
            <span className="icon-box">
              <TerraformOriginalIcon width={20} height={20} />
            </span>
            <Pill tone="mint">Audited</Pill>
          </div>
          <div className="metric-value">{sentToGitHub}</div>
          <div className="metric-label">Sent to GitHub for review</div>
          <div className="progress">
            <span style={{ width: `${sentShare}%` }} />
          </div>
          <div className="submetric">
            <span className="submetric-brand">
              <GitHubOriginalIcon width={14} height={14} /> Terraform pull request
            </span>
            <span>{sentShare}% of requests</span>
          </div>
        </ShellCard>
      </div>

      <div className="content-grid">
        <ShellCard className="wide-card">
          <div className="card-heading">
            <div>
              <span className="kicker">LIVE CATALOG</span>
              <h2>Services at a glance</h2>
            </div>
            <button className="secondary-button compact-btn" onClick={openService}>
              <Plus size={15} /> Add service
            </button>
          </div>
          <ServiceList services={data.services.slice(0, 5)} entitlements={data.entitlements} />
        </ShellCard>

        <ShellCard className="action-card">
          <div className="card-heading compact">
            <div>
              <span className="kicker">LATEST ACTION</span>
              <h2>{latest ? describeRequest(latest) : "No request yet"}</h2>
            </div>
            {latest && <Pill tone={toneForStatus(latest.status)}>{requestStatusLabel(latest.status, latest.capability)}</Pill>}
          </div>
          {latest ? (
            <>
              <div className="action-person">
                <div className="cloud-badge">
                  <CloudProviderIcon cloud={latest.resource.cloud} size={22} />
                </div>
                <div>
                  <strong>{shortId(latest.id)}</strong>
                  <span>
                    {String(latest.resource.cloud || "GCP").toUpperCase()} ·{" "}
                    {String(latest.resource.environment || "nonprod")} ·{" "}
                    {String(latest.resource.tier || "tier1")}
                  </span>
                </div>
              </div>
              <div className="timeline">
                <div className="timeline-step done">
                  <span>
                    <SupabaseOriginalIcon width={14} height={14} />
                  </span>
                  <div>
                    <b>Request captured</b>
                    <small>Identity bound to Supabase session</small>
                  </div>
                </div>
                <div className={`timeline-step ${latest.status === "awaiting_policy" ? "current" : "done"}`}>
                  <span>
                    <OpaOriginalIcon width={14} height={14} />
                  </span>
                  <div>
                    <b>OPA policy evaluated</b>
                    <small>
                      {latest.status === "awaiting_policy" ? "Queued for local worker" : "Decision recorded"}
                    </small>
                  </div>
                </div>
                <div
                  className={`timeline-step ${
                    latest.status === "awaiting_approval"
                      ? "current"
                      : PAST_APPROVAL_STATUSES.includes(latest.status)
                        ? "done"
                        : ""
                  }`}
                >
                  <span>
                    <ServiceNowOriginalIcon width={14} height={14} />
                  </span>
                  <div>
                    <b>ServiceNow approval</b>
                    <small>
                      {latest.delivery.approverEmail ? (
                        <>
                          Approved by {latest.delivery.approverEmail} in <ServiceNowLink delivery={latest.delivery} />
                        </>
                      ) : latest.delivery.changeNumber ? (
                        <>
                          Waiting in <ServiceNowLink delivery={latest.delivery} />; the requester cannot approve
                        </>
                      ) : (
                        "Someone other than the requester approves in ServiceNow"
                      )}
                    </small>
                  </div>
                </div>
                <div
                  className={`timeline-step ${
                    SENT_TO_GITHUB_STATUSES.includes(latest.status)
                      ? "done"
                      : ["approved", "proposal_pending"].includes(latest.status)
                        ? "current"
                        : ""
                  }`}
                >
                  <span>
                    <GitHubOriginalIcon width={14} height={14} />
                  </span>
                  <div>
                    <b>Governed Terraform PR</b>
                    <small>
                      {latest.delivery.pullRequestUrl ? (
                        <a href={latest.delivery.pullRequestUrl} target="_blank" rel="noreferrer">
                          Open pull request
                        </a>
                      ) : (
                        "Reviewed and merged by a person in GitHub"
                      )}
                    </small>
                  </div>
                </div>
                <div
                  className={`timeline-step ${
                    latest.status === "completed"
                      ? "done"
                      : ["merged", "applying"].includes(latest.status)
                        ? "current"
                        : ""
                  }`}
                >
                  <span>
                    <TerraformOriginalIcon width={14} height={14} />
                  </span>
                  <div>
                    <b>Access live in GCP</b>
                    <small>
                      {latest.status === "completed" && latest.delivery.expiresAt
                        ? `${formatExpiry(latest.delivery.expiresAt)} · removed by GCP automatically`
                        : latest.status === "failed" && latest.delivery.reason
                          ? latest.delivery.reason
                          : "Applied by the pipeline after merge"}
                      {latest.delivery.workflowRunUrl && (
                        <>
                          {" · "}
                          <a href={latest.delivery.workflowRunUrl} target="_blank" rel="noreferrer">
                            Pipeline run
                          </a>
                        </>
                      )}
                    </small>
                  </div>
                </div>
              </div>
              <div className="evidence-strip">
                <LockKeyhole size={15} />
                <span>Every transition is written to the immutable audit ledger</span>
              </div>
            </>
          ) : (
            <EmptyState
              icon={KeyRound}
              title="Start with an access request"
              copy="Aegis will create a durable workflow and immutable evidence trail."
              action={
                <button className="secondary-button" onClick={openAction}>
                  Request access
                </button>
              }
            />
          )}
        </ShellCard>
      </div>

      <div className="bottom-grid">
        <ShellCard className="wide-card">
          <div className="card-heading">
            <div>
              <span className="kicker">LIVE ACTIVITY</span>
              <h2>Governed changes</h2>
            </div>
            <Pill tone="mint">Realtime</Pill>
          </div>
          <RequestList requests={data.requests.slice(0, 5)} currentUserId={currentUserId} />
        </ShellCard>

        <ShellCard className="signal-card">
          <div className="card-heading">
            <div>
              <span className="kicker">WORKFLOW SIGNAL</span>
              <h2>What is happening</h2>
            </div>
            <Gauge size={20} />
          </div>
          {data.workflowEvents.slice(0, 3).map((event) => (
            <div className="signal-item" key={event.id}>
              <span>
                <Activity size={18} />
              </span>
              <div>
                <strong>{statusLabel(event.eventType.replace(".", " "))}</strong>
                <p>{timeAgo(event.occurredAt)} · Durable workflow event</p>
              </div>
            </div>
          ))}
          {!data.workflowEvents.length && (
            <EmptyState
              icon={Activity}
              title="No workflow events"
              copy="Events appear as soon as a request enters the workflow."
            />
          )}
        </ShellCard>
      </div>
    </>
  );
}

function ServicesView({
  services,
  entitlements,
  canManage,
  openService,
}: {
  services: ServiceSummary[];
  entitlements: EntitlementOption[];
  canManage: boolean;
  openService: () => void;
}) {
  return (
    <>
      <div className="hero-row">
        <div>
          <div className="eyebrow">
            <span className="pulse" /> Live service catalog
          </div>
          <h1>Every service, understood.</h1>
          <p className="lede">Each service shows the access levels people can request for it.</p>
        </div>
        {canManage && (
          <button className="primary-button" onClick={openService}>
            <Plus size={17} /> Add service
          </button>
        )}
      </div>
      <ShellCard className="wide-card">
        <div className="card-heading">
          <div>
            <span className="kicker">CATALOG</span>
            <h2>{services.length} catalogued services</h2>
          </div>
          <Pill tone="mint">Live data</Pill>
        </div>
        <ServiceList services={services} entitlements={entitlements} />
      </ShellCard>
    </>
  );
}

function ServiceNowLink({ delivery }: { delivery: DeliverySummary }) {
  if (!delivery.changeNumber) return null;
  return delivery.changeUrl ? (
    <a href={delivery.changeUrl} target="_blank" rel="noreferrer" className="inline-link">
      {delivery.changeNumber}
    </a>
  ) : (
    <span>{delivery.changeNumber}</span>
  );
}

function IncidentFindings({ request, currentUserId }: { request: ActionRequestSummary; currentUserId: string }) {
  const [state, action, pending] = useActionState(decideIncident, initialState);
  const mine = request.requesterId === currentUserId;
  const { delivery } = request;
  return (
    <div className="approval-row">
      <div>
        <strong>Findings for {String(request.resource.resource_hint ?? "your search")}</strong>
        <span>
          {shortId(request.id)} · last {String(request.resource.time_window_hours ?? "?")} hours ·{" "}
          {delivery.findingCount ?? 0} log entr{delivery.findingCount === 1 ? "y" : "ies"}
        </span>
        <p>{delivery.triageSummary ?? "No summary recorded."}</p>
        {delivery.findings.length > 0 && (
          <details>
            <summary>Show the {delivery.findings.length} most recent entries</summary>
            <ul className="finding-list">
              {delivery.findings.map((finding, index) => (
                <li key={`${finding.timestamp}-${index}`}>
                  <b>{finding.severity}</b> {finding.source && <em>{finding.source}</em>}{" "}
                  <time>{finding.timestamp ? new Date(finding.timestamp).toLocaleString() : ""}</time>
                  <code>{finding.message}</code>
                </li>
              ))}
            </ul>
          </details>
        )}
      </div>
      {mine ? (
        <form action={action}>
          <input type="hidden" name="requestId" value={request.id} />
          <div>
            <button name="decision" value="close" className="secondary-button" disabled={pending}>
              No incident needed
            </button>
            <button name="decision" value="raise" className="primary-button" disabled={pending}>
              {pending ? <RefreshCw className="spin" size={15} /> : <ServiceNowOriginalIcon width={15} height={15} />} Raise
              ServiceNow incident
            </button>
          </div>
          {state.message && <small className={state.ok ? "form-success" : "form-error"}>{state.message}</small>}
        </form>
      ) : (
        <small>Only the person who ran this search can raise the incident.</small>
      )}
    </div>
  );
}

function ActionsView({
  requests,
  currentUserId,
  openDialog,
}: {
  requests: ActionRequestSummary[];
  currentUserId: string;
  openDialog: (dialog: "action" | "incident" | "labels") => void;
}) {
  const inServiceNow = requests.filter(
    (request) => request.status === "awaiting_approval" && request.capability !== "incident_triage",
  );
  const findingsReady = requests.filter(
    (request) => request.status === "awaiting_approval" && request.capability === "incident_triage",
  );
  return (
    <>
      <div className="hero-row">
        <div>
          <div className="eyebrow">
            <span className="pulse" /> Governed workflows
          </div>
          <h1>Change without losing control.</h1>
          <p className="lede">
            Every change is checked against policy, approved in ServiceNow by someone else, and applied only after a
            person merges the pull request.
          </p>
        </div>
        <div className="hero-actions">
          <button className="secondary-button" onClick={() => openDialog("incident")}>
            <Search size={16} /> Investigate an issue
          </button>
          <button className="secondary-button" onClick={() => openDialog("labels")}>
            <ShieldCheck size={16} /> Fix missing labels
          </button>
          <button className="primary-button" onClick={() => openDialog("action")}>
            <KeyRound size={16} /> Request access
          </button>
        </div>
      </div>
      {findingsReady.length > 0 && (
        <ShellCard className="wide-card queue-card">
          <div className="card-heading">
            <div>
              <span className="kicker">INVESTIGATIONS</span>
              <h2>
                {findingsReady.length} search{findingsReady.length === 1 ? "" : "es"} ready to review
              </h2>
            </div>
            <Pill tone="amber">Your decision</Pill>
          </div>
          <div className="approval-list">
            {findingsReady.map((request) => (
              <IncidentFindings request={request} currentUserId={currentUserId} key={request.id} />
            ))}
          </div>
        </ShellCard>
      )}
      <ShellCard className="wide-card queue-card">
        <div className="card-heading">
          <div>
            <span className="kicker">SERVICENOW APPROVALS</span>
            <h2>{inServiceNow.length} waiting for an approver</h2>
          </div>
          <Pill tone="amber">
            <ServiceNowOriginalIcon width={13} height={13} /> Approved in ServiceNow
          </Pill>
        </div>
        {inServiceNow.length ? (
          <div className="approval-list">
            {inServiceNow.map((request) => (
              <div className="approval-row" key={request.id}>
                <div>
                  <strong>{describeRequest(request)}</strong>
                  <span>
                    {shortId(request.id)} · {request.requesterId === currentUserId ? "Requested by you" : "Team member"} ·{" "}
                    {request.delivery.changeNumber ? (
                      <>
                        Change <ServiceNowLink delivery={request.delivery} />
                      </>
                    ) : (
                      "Opening the ServiceNow change…"
                    )}
                  </span>
                  <p>{request.justification}</p>
                </div>
                <small>
                  An approver in the Aegis Cloud Approvers group decides in ServiceNow. The requester is never an
                  approver.
                </small>
              </div>
            ))}
          </div>
        ) : (
          <EmptyState
            icon={CheckCircle2}
            title="Nothing waiting in ServiceNow"
            copy="Requests that pass the policy check appear here until an approver decides."
          />
        )}
      </ShellCard>
      <ShellCard className="wide-card">
        <div className="card-heading">
          <div>
            <span className="kicker">ALL REQUESTS</span>
            <h2>Workflow history</h2>
          </div>
          <Pill>{requests.length} total</Pill>
        </div>
        <RequestList requests={requests} currentUserId={currentUserId} />
      </ShellCard>
    </>
  );
}

function money(value: number | string | null | undefined, currency: string | null) {
  const amount = Number(value ?? 0);
  try {
    return new Intl.NumberFormat(undefined, { style: "currency", currency: currency ?? "USD" }).format(amount);
  } catch {
    return amount.toFixed(2);
  }
}

function ComplianceView({ data, openLabels }: { data: PortalData; openLabels: () => void }) {
  const labels = data.labelCompliance;
  const cost = data.cost;
  return (
    <>
      <div className="hero-row">
        <div>
          <div className="eyebrow">
            <span className="pulse" /> Continuous evidence
          </div>
          <h1>Audit without archaeology.</h1>
          <p className="lede">
            Live label coverage and cost for {data.cloudProject?.projectId ?? "your GCP project"}, plus every recorded
            step. Audit events are also copied to Cloud Logging (log name aegis-audit) for your SIEM.
          </p>
        </div>
      </div>

      <ShellCard className="wide-card">
        <div className="card-heading">
          <div>
            <span className="kicker">REQUIRED LABELS</span>
            <h2>
              {labels ? `${labels.compliant} of ${labels.total} buckets fully labelled` : "Label coverage not checked yet"}
            </h2>
          </div>
          {labels && <Pill tone={labels.compliant === labels.total ? "mint" : "amber"}>Checked {timeAgo(labels.capturedAt)}</Pill>}
        </div>
        {labels?.buckets.length ? (
          <div className="audit-list">
            {labels.buckets.map((bucket) => (
              <div className="audit-row" key={bucket.bucket}>
                <span>
                  <GoogleCloudOriginalIcon width={18} height={18} />
                </span>
                <div>
                  <strong>{bucket.bucket}</strong>
                  <small>
                    {bucket.missing.length
                      ? `Missing: ${bucket.missing.join(", ")}`
                      : `All required labels present (${labels.required.join(", ")})`}
                  </small>
                </div>
                {bucket.missing.length ? (
                  <button className="secondary-button compact-btn" onClick={openLabels}>
                    Fix
                  </button>
                ) : (
                  <Pill tone="mint">Compliant</Pill>
                )}
              </div>
            ))}
          </div>
        ) : (
          <EmptyState
            icon={ShieldCheck}
            title="No label check yet"
            copy="The worker checks every bucket in the project for owner, cost-center and environment labels every 15 minutes."
          />
        )}
      </ShellCard>

      <ShellCard className="wide-card">
        <div className="card-heading">
          <div>
            <span className="kicker">COST BY COST CENTER</span>
            <h2>{cost ? `${money(cost.total, cost.currency)} this month` : "Cost data not connected yet"}</h2>
          </div>
          {cost && (
            <Pill tone={cost.untagged > 0 ? "amber" : "mint"}>{money(cost.untagged, cost.currency)} without a cost center</Pill>
          )}
        </div>
        {cost?.rows.length ? (
          <div className="audit-list">
            {cost.rows.slice(0, 15).map((row, index) => (
              <div className="audit-row" key={`${row.cost_center}-${row.service}-${index}`}>
                <span>
                  <Gauge size={18} />
                </span>
                <div>
                  <strong>{row.cost_center ?? "No cost center"}</strong>
                  <small>{row.service}</small>
                </div>
                <b>{money(row.net_cost, row.currency ?? cost.currency)}</b>
              </div>
            ))}
          </div>
        ) : (
          <EmptyState
            icon={Gauge}
            title={data.cloudProject?.billingConnected ? "No billing rows yet this month" : "Billing export is not connected"}
            copy={
              data.cloudProject?.billingConnected
                ? "Google adds billing rows to BigQuery a few times a day."
                : "Turn on Cloud Billing export to BigQuery, then an administrator records the table name for this workspace."
            }
          />
        )}
      </ShellCard>

      <ShellCard className="wide-card">
        <div className="card-heading">
          <div>
            <span className="kicker">AUDIT LEDGER</span>
            <h2>{data.auditEvents.length} recent events</h2>
          </div>
          <Pill tone="mint">Append only</Pill>
        </div>
        {data.auditEvents.length ? (
          <div className="audit-list">
            {data.auditEvents.map((event) => (
              <div className="audit-row" key={event.id}>
                <span>
                  <Fingerprint size={18} />
                </span>
                <div>
                  <strong>{statusLabel(event.action.replaceAll(".", " "))}</strong>
                  <small>
                    {event.objectType} · {event.objectId?.slice(0, 12) || "system"}
                  </small>
                </div>
                <time>{timeAgo(event.occurredAt)}</time>
              </div>
            ))}
          </div>
        ) : (
          <EmptyState
            icon={ShieldCheck}
            title="No visible audit events"
            copy="Platform administrators and auditors see immutable evidence here after the first transition."
          />
        )}
      </ShellCard>
    </>
  );
}

function routeKey(route: EntitlementOption) {
  return `${route.cloud}|${route.environment}|${route.tier}`;
}

function ActionDialog({
  services,
  entitlements,
  draft,
  close,
}: {
  services: ServiceSummary[];
  entitlements: EntitlementOption[];
  draft?: Extract<RequestDraft, { capability: "access_grant" }>;
  close: () => void;
}) {
  const [state, action, pending] = useActionState(createAccessRequest, initialState);
  const requestable = services.filter((service) => entitlements.some((route) => route.serviceId === service.id));
  const [serviceId, setServiceId] = useState(draft && requestable.length === 1 ? requestable[0].id : "");
  const draftRoute = draft
    ? entitlements.find((route) => route.serviceId === serviceId && route.tier === draft.tier)
    : undefined;
  const [selectedKey, setSelectedKey] = useState(draftRoute ? routeKey(draftRoute) : "");
  const routes = entitlements.filter((route) => route.serviceId === serviceId);
  const route = routes.find((candidate) => routeKey(candidate) === selectedKey) ?? routes[0];
  const durations = route ? DURATION_HOURS.filter((hours) => hours <= route.maxTtlHours) : [];
  const preferred = draft?.ttlHours ?? 8;
  const defaultDuration = durations.includes(preferred)
    ? preferred
    : durations.filter((hours) => hours <= preferred).pop() ?? durations[durations.length - 1];

  return (
    <div className="dialog-backdrop" role="presentation" onMouseDown={close}>
      <div
        className="dialog form-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="action-title"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <div className="dialog-head">
          <div>
            <span className="kicker">NEW GOVERNED ACTION</span>
            <h2 id="action-title">Request access</h2>
          </div>
          <button onClick={close} aria-label="Close">
            <X size={20} />
          </button>
        </div>
        <p>
          Access is granted to the account you are signed in with. After a policy check and approval in ServiceNow by someone else,
          Aegis opens a pull request for review. When the duration ends, GCP removes the access automatically.
        </p>
        {state.ok ? (
          <div className="success-panel">
            <CheckCircle2 size={28} />
            <strong>Request submitted</strong>
            <p>{state.message}</p>
            <button className="secondary-button" onClick={close}>
              View live workflow
            </button>
          </div>
        ) : (
          <form action={action} className="workflow-form">
            <label>
              <span>Service</span>
              <select
                name="serviceId"
                required
                value={serviceId}
                onChange={(event) => {
                  setServiceId(event.target.value);
                  setSelectedKey("");
                }}
              >
                <option value="" disabled>
                  Select a service
                </option>
                {requestable.map((service) => (
                  <option value={service.id} key={service.id}>
                    {service.name}
                  </option>
                ))}
              </select>
            </label>
            <div className="form-grid">
              <label>
                <span>Access level</span>
                <select
                  required
                  disabled={!routes.length}
                  value={route ? routeKey(route) : ""}
                  onChange={(event) => setSelectedKey(event.target.value)}
                >
                  {!routes.length && <option value="">Choose a service first</option>}
                  {routes.map((candidate) => (
                    <option value={routeKey(candidate)} key={routeKey(candidate)}>
                      {CLOUD_LABELS[candidate.cloud] ?? candidate.cloud} ·{" "}
                      {ENVIRONMENT_LABELS[candidate.environment] ?? candidate.environment} ·{" "}
                      {TIER_LABELS[candidate.tier] ?? candidate.tier}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                <span>Duration</span>
                <select name="ttlHours" key={route ? routeKey(route) : "none"} defaultValue={defaultDuration} disabled={!route}>
                  {durations.map((hours) => (
                    <option value={hours} key={hours}>
                      {hours === 1 ? "1 hour" : hours === 168 ? "7 days" : `${hours} hours`}
                    </option>
                  ))}
                </select>
              </label>
            </div>
            {route && (
              <>
                <input type="hidden" name="cloud" value={route.cloud} />
                <input type="hidden" name="environment" value={route.environment} />
                <input type="hidden" name="tier" value={route.tier} />
              </>
            )}
            <label>
              <span>Business justification</span>
              <textarea
                name="justification"
                required
                minLength={10}
                maxLength={2000}
                placeholder="Explain why this access is needed and what work it supports."
                defaultValue={draft?.justification}
              />
            </label>
            {!requestable.length && (
              <div className="form-error">
                No service has access levels set up yet. An administrator needs to add them first.
              </div>
            )}
            {state.message && <div className="form-error">{state.message}</div>}
            <button className="primary-button full" disabled={pending || !route}>
              {pending ? <RefreshCw className="spin" size={16} /> : <KeyRound size={16} />}
              {pending ? "Submitting…" : "Submit request"}
            </button>
          </form>
        )}
      </div>
    </div>
  );
}

function DialogShell({
  kicker,
  title,
  id,
  close,
  children,
}: {
  kicker: string;
  title: string;
  id: string;
  close: () => void;
  children: React.ReactNode;
}) {
  return (
    <div className="dialog-backdrop" role="presentation" onMouseDown={close}>
      <div
        className="dialog form-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby={id}
        onMouseDown={(event) => event.stopPropagation()}
      >
        <div className="dialog-head">
          <div>
            <span className="kicker">{kicker}</span>
            <h2 id={id}>{title}</h2>
          </div>
          <button onClick={close} aria-label="Close">
            <X size={20} />
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}

function Submitted({ message, close }: { message: string; close: () => void }) {
  return (
    <div className="success-panel">
      <CheckCircle2 size={28} />
      <strong>Submitted</strong>
      <p>{message}</p>
      <button className="secondary-button" onClick={close}>
        View live workflow
      </button>
    </div>
  );
}

function IncidentDialog({
  projectId,
  draft,
  close,
}: {
  projectId: string | null;
  draft?: Extract<RequestDraft, { capability: "incident_triage" }>;
  close: () => void;
}) {
  const [state, action, pending] = useActionState(createIncidentTriage, initialState);
  return (
    <DialogShell kicker="INCIDENT TRIAGE" title="Investigate an issue" id="incident-title" close={close}>
      <p>
        Aegis searches the last few hours of warnings and errors in {projectId ?? "your GCP project"} right away. Reading
        logs changes nothing, so no approval is needed. You then decide whether to raise a ServiceNow incident.
      </p>
      {state.ok ? (
        <Submitted message={state.message} close={close} />
      ) : (
        <form action={action} className="workflow-form">
          <label>
            <span>Service or resource</span>
            <input
              name="resourceHint"
              required
              minLength={2}
              maxLength={120}
              placeholder="payments-api or cilium-agent"
              defaultValue={draft?.resourceHint}
            />
          </label>
          <label>
            <span>Time window</span>
            <select name="timeWindowHours" defaultValue={String(draft?.timeWindowHours ?? 24)}>
              <option value="1">Last hour</option>
              <option value="4">Last 4 hours</option>
              <option value="24">Last 24 hours</option>
              <option value="72">Last 3 days</option>
              <option value="168">Last 7 days</option>
            </select>
          </label>
          <label>
            <span>What is going wrong?</span>
            <textarea
              name="justification"
              required
              minLength={10}
              maxLength={2000}
              placeholder="Customers see failed payments since about 9am."
              defaultValue={draft?.justification}
            />
          </label>
          {state.message && <div className="form-error">{state.message}</div>}
          <button className="primary-button full" disabled={pending}>
            {pending ? <RefreshCw className="spin" size={16} /> : <Search size={16} />}
            {pending ? "Submitting…" : "Search the logs"}
          </button>
        </form>
      )}
    </DialogShell>
  );
}

function LabelFixDialog({
  data,
  draft,
  close,
}: {
  data: PortalData;
  draft?: Extract<RequestDraft, { capability: "label_fix" }>;
  close: () => void;
}) {
  const [state, action, pending] = useActionState(createLabelFix, initialState);
  const candidates = (data.labelCompliance?.buckets ?? []).filter((bucket) => bucket.missing.length > 0);
  const [bucket, setBucket] = useState(
    candidates.some((candidate) => candidate.bucket === draft?.bucket) ? draft!.bucket : candidates[0]?.bucket ?? "",
  );
  const selected = candidates.find((candidate) => candidate.bucket === bucket);
  return (
    <DialogShell kicker="LABEL COMPLIANCE" title="Fix missing labels" id="labels-title" close={close}>
      <p>
        Aegis adds only the required labels a bucket is missing (owner, cost-center, environment). Existing labels are
        never changed. Approval happens in ServiceNow, then a person merges the pull request.
      </p>
      {state.ok ? (
        <Submitted message={state.message} close={close} />
      ) : !candidates.length ? (
        <EmptyState
          icon={CheckCircle2}
          title={data.labelCompliance ? "Every bucket is fully labelled" : "Label check has not run yet"}
          copy={
            data.labelCompliance
              ? "There is nothing to fix right now."
              : "The worker checks bucket labels every 15 minutes; try again shortly."
          }
        />
      ) : (
        <form action={action} className="workflow-form">
          <label>
            <span>Bucket</span>
            <select name="bucket" value={bucket} onChange={(event) => setBucket(event.target.value)} required>
              {candidates.map((candidate) => (
                <option value={candidate.bucket} key={candidate.bucket}>
                  {candidate.bucket}
                </option>
              ))}
            </select>
          </label>
          {selected && (
            <small>
              Will add: {selected.missing.join(", ")}. The owner will be your email address and environment will be
              non-production.
            </small>
          )}
          <label>
            <span>Cost center</span>
            <input
              name="costCenter"
              required
              minLength={2}
              maxLength={63}
              pattern="[a-z0-9][a-z0-9_-]{1,62}"
              placeholder="pmt-01"
              defaultValue={draft?.costCenter}
            />
          </label>
          <label>
            <span>Business justification</span>
            <textarea
              name="justification"
              required
              minLength={10}
              maxLength={2000}
              placeholder="Cost reports need an owner and cost center on this bucket."
              defaultValue={draft?.justification}
            />
          </label>
          {state.message && <div className="form-error">{state.message}</div>}
          <button className="primary-button full" disabled={pending}>
            {pending ? <RefreshCw className="spin" size={16} /> : <ShieldCheck size={16} />}
            {pending ? "Submitting…" : "Submit for approval"}
          </button>
        </form>
      )}
    </DialogShell>
  );
}

function ServiceDialog({ close }: { close: () => void }) {
  const [state, action, pending] = useActionState(createService, initialState);
  return (
    <div className="dialog-backdrop" role="presentation" onMouseDown={close}>
      <div
        className="dialog form-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="service-title"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <div className="dialog-head">
          <div>
            <span className="kicker">LIVE CATALOG</span>
            <h2 id="service-title">Add a service</h2>
          </div>
          <button onClick={close} aria-label="Close">
            <X size={20} />
          </button>
        </div>
        <p>Create the catalog record that governed requests will reference.</p>
        {state.ok ? (
          <div className="success-panel">
            <CheckCircle2 size={28} />
            <strong>Service added</strong>
            <p>{state.message}</p>
            <button className="secondary-button" onClick={close}>
              Done
            </button>
          </div>
        ) : (
          <form action={action} className="workflow-form">
            <label>
              <span>Name</span>
              <input name="name" required minLength={2} placeholder="Payments API" />
            </label>
            <label>
              <span>Description</span>
              <textarea
                name="description"
                maxLength={500}
                placeholder="What this service does and who it serves."
              />
            </label>
            <div className="form-grid">
              <label>
                <span>Lifecycle</span>
                <select name="lifecycle" defaultValue="production">
                  <option value="production">Production</option>
                  <option value="experimental">Experimental</option>
                  <option value="deprecated">Deprecated</option>
                </select>
              </label>
              <label>
                <span>Tier</span>
                <select name="tier" defaultValue="tier2">
                  <option value="tier1">Tier 1</option>
                  <option value="tier2">Tier 2</option>
                  <option value="tier3">Tier 3</option>
                </select>
              </label>
            </div>
            <label>
              <span>Repository URL</span>
              <input name="repositoryUrl" type="url" placeholder="https://github.com/your-org/repository" />
            </label>
            {state.message && <div className="form-error">{state.message}</div>}
            <button className="primary-button full" disabled={pending}>
              {pending ? <RefreshCw className="spin" size={16} /> : <Plus size={16} />}
              {pending ? "Adding…" : "Add to catalog"}
            </button>
          </form>
        )}
      </div>
    </div>
  );
}

type DashboardProps = {
  displayName: string;
  email: string;
  role: string;
  tenantName: string;
  tenantId: string;
  userId: string;
  data: PortalData;
};

function initialsFor(name: string) {
  return (
    name
      .split(/\s+/)
      .filter(Boolean)
      .slice(0, 2)
      .map((part) => part[0]?.toUpperCase())
      .join("") || "AU"
  );
}

export default function Dashboard({
  displayName,
  email,
  role,
  tenantName,
  tenantId,
  userId,
  data,
}: DashboardProps) {
  const [view, setView] = useState<View>("Overview");
  const [dialog, setDialog] = useState<"action" | "incident" | "labels" | "service" | "search" | "settings" | "aegis" | null>(null);
  const [mobileNav, setMobileNav] = useState(false);
  const [draft, setDraft] = useState<RequestDraft | null>(null);
  const closeDialog = () => {
    setDialog(null);
    setDraft(null);
  };
  const openDraft = (next: RequestDraft) => {
    setDraft(next);
    setDialog(next.capability === "incident_triage" ? "incident" : next.capability === "label_fix" ? "labels" : "action");
  };
  const [realtime, setRealtime] = useState<"connecting" | "live" | "offline">("connecting");
  const [refreshing, startRefresh] = useTransition();
  const router = useRouter();
  const title = useMemo(() => (view === "Overview" ? "Engineering Control Plane" : view), [view]);
  const canManageServices = role === "platform_admin";
  const queueCount = data.requests.filter((request) => request.status === "awaiting_approval").length;

  useEffect(() => {
    const supabase = createClient();
    const refresh = () => startRefresh(() => router.refresh());
    const channel = supabase
      .channel(`aegis-live-${tenantId}`)
      .on(
        "postgres_changes",
        { event: "*", schema: "public", table: "services", filter: `tenant_id=eq.${tenantId}` },
        refresh
      )
      .on(
        "postgres_changes",
        { event: "*", schema: "public", table: "action_requests", filter: `tenant_id=eq.${tenantId}` },
        refresh
      )
      .on(
        "postgres_changes",
        { event: "*", schema: "public", table: "approvals", filter: `tenant_id=eq.${tenantId}` },
        refresh
      )
      .on(
        "postgres_changes",
        { event: "*", schema: "public", table: "workflow_events", filter: `tenant_id=eq.${tenantId}` },
        refresh
      )
      .on(
        "postgres_changes",
        { event: "*", schema: "public", table: "audit_events", filter: `tenant_id=eq.${tenantId}` },
        refresh
      )
      .subscribe((status) =>
        setRealtime(
          status === "SUBSCRIBED"
            ? "live"
            : status === "CHANNEL_ERROR" || status === "TIMED_OUT"
              ? "offline"
              : "connecting"
        )
      );
    return () => {
      void supabase.removeChannel(channel);
    };
  }, [router, tenantId]);

  useEffect(() => {
    const openSearch = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setDialog("search");
      }
    };
    window.addEventListener("keydown", openSearch);
    return () => window.removeEventListener("keydown", openSearch);
  }, []);

  return (
    <div className="app-shell">
      <aside className={mobileNav ? "sidebar open" : "sidebar"}>
        <div className="brand">
          <Mark />
          <div>
            <strong>AEGIS</strong>
            <span>CONTROL PLANE</span>
          </div>
          <button className="mobile-close" onClick={() => setMobileNav(false)}>
            <X size={18} />
          </button>
        </div>

        <nav aria-label="Primary navigation">
          <span className="nav-label">WORKSPACE</span>
          {navItems.map((item) => (
            <button
              key={item.label}
              className={view === item.label ? "active" : ""}
              onClick={() => {
                setView(item.label);
                setMobileNav(false);
              }}
            >
              <item.icon size={19} />
              <span>{item.label}</span>
              {item.label === "Actions" && queueCount > 0 && <em>{queueCount}</em>}
            </button>
          ))}
        </nav>

        <div className="sidebar-bottom">
          <div className="trust-card">
            <div>
              <LockKeyhole size={15} /> Trust boundary
            </div>
            <strong>Propose, don&apos;t mutate.</strong>
            <p>Every change requires OPA policy, independent approval and a protected GitHub PR path.</p>
            <span>
              <SupabaseOriginalIcon width={14} height={14} /> Supabase identity active
            </span>
          </div>

          <div className="profile-summary" title={email}>
            <span className="avatar">{initialsFor(displayName)}</span>
            <div>
              <strong>{displayName}</strong>
              <small>
                {role.replaceAll("_", " ")} · {tenantName}
              </small>
            </div>
          </div>

          <form action="/auth/signout" method="post" className="signout-form">
            <button className="signout-button" type="submit">
              <LogOut size={16} />
              <span>Sign out</span>
            </button>
          </form>
        </div>
      </aside>

      <main>
        <header className="topbar">
          <div className="topbar-title">
            <button className="menu-button" onClick={() => setMobileNav(true)}>
              <Menu size={20} />
            </button>
            <span>{title}</span>
            <Pill tone={realtime === "live" ? "mint" : realtime === "offline" ? "rose" : "amber"}>
              <i className="tiny-dot" /> {refreshing ? "Updating" : realtime}
            </Pill>
            <div className="topbar-stack-icons" title="Connected integrations: Google Cloud, Supabase, GitHub, Terraform">
              <GoogleCloudOriginalIcon width={16} height={16} />
              <SupabaseOriginalIcon width={15} height={15} />
              <GitHubOriginalIcon width={15} height={15} />
              <TerraformOriginalIcon width={15} height={15} />
            </div>
          </div>

          <div className="topbar-actions">
            <button className="search-button" onClick={() => setDialog("search")}>
              <Search size={16} />
              <span>Search services, requests, evidence…</span>
              <kbd>⌘K</kbd>
            </button>
            <button className="icon-button" aria-label="Settings" onClick={() => setDialog("settings")}>
              <Settings size={18} />
            </button>
            <button className="aegis-button" onClick={() => setDialog("aegis")}>
              <Sparkles size={16} /> Ask Aegis
            </button>
          </div>
        </header>

        <div className="page-content">
          {data.loadError && (
            <div className="load-error">
              <ShieldCheck size={18} />
              {data.loadError}
              <button onClick={() => router.refresh()}>Retry</button>
            </div>
          )}
          {view === "Overview" && (
            <Overview
              data={data}
              displayName={displayName}
              currentUserId={userId}
              openAction={() => setDialog("action")}
              openService={() => setDialog("service")}
            />
          )}
          {view === "Services" && (
            <ServicesView
              services={data.services}
              entitlements={data.entitlements}
              canManage={canManageServices}
              openService={() => setDialog("service")}
            />
          )}
          {view === "Actions" && (
            <ActionsView requests={data.requests} currentUserId={userId} openDialog={setDialog} />
          )}
          {view === "Compliance" && <ComplianceView data={data} openLabels={() => setDialog("labels")} />}
        </div>
      </main>

      {dialog === "action" && (
        <ActionDialog
          services={data.services}
          entitlements={data.entitlements}
          draft={draft?.capability === "access_grant" ? draft : undefined}
          close={closeDialog}
        />
      )}
      {dialog === "incident" && (
        <IncidentDialog
          projectId={data.cloudProject?.projectId ?? null}
          draft={draft?.capability === "incident_triage" ? draft : undefined}
          close={closeDialog}
        />
      )}
      {dialog === "labels" && (
        <LabelFixDialog data={data} draft={draft?.capability === "label_fix" ? draft : undefined} close={closeDialog} />
      )}
      {dialog === "service" && <ServiceDialog close={() => setDialog(null)} />}
      {dialog === "search" && (
        <SearchDialog data={data} close={() => setDialog(null)} navigate={setView} />
      )}
      {dialog === "settings" && (
        <SettingsDialog
          displayName={displayName}
          email={email}
          role={role}
          tenantName={tenantName}
          realtime={realtime}
          close={() => setDialog(null)}
        />
      )}
      {dialog === "aegis" && <AskAegisDialog close={closeDialog} onDraft={openDraft} />}
    </div>
  );
}
