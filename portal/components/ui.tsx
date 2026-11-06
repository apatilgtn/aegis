import Link from "next/link";
import { Check, Circle, Minus, X } from "lucide-react";
import { capabilityLabel, statusOf, subtitle, timeAgo, title, type RequestView, type Step } from "@/lib/view";

export function Status({ request, dot = true }: { request: RequestView; dot?: boolean }) {
  const { label, tone } = statusOf(request);
  return (
    <span className="status">
      {dot && <span className={`dot ${tone}`} />} {label}
    </span>
  );
}

export function RequestRow({ request, mine }: { request: RequestView; mine?: boolean }) {
  return (
    <Link className="row" href={`/requests/${request.id}`}>
      <span className={`dot ${statusOf(request).tone}`} />
      <span className="row-main">
        <strong>{title(request)}</strong>
        <span>
          {capabilityLabel(request.capability)} · {subtitle(request)}
          {mine === false ? " · teammate" : ""}
        </span>
      </span>
      <Status request={request} dot={false} />
      <span className="row-meta">{timeAgo(request.updatedAt)}</span>
    </Link>
  );
}

export function RequestList({ requests, userId, empty }: { requests: RequestView[]; userId: string; empty: React.ReactNode }) {
  if (!requests.length) return <div className="rows"><div className="rows-empty">{empty}</div></div>;
  return (
    <div className="rows">
      {requests.map((request) => (
        <RequestRow key={request.id} request={request} mine={request.requesterId === userId} />
      ))}
    </div>
  );
}

const NODE = {
  done: <Check size={12} strokeWidth={3} />,
  failed: <X size={12} strokeWidth={3} />,
  current: <Circle size={8} fill="currentColor" />,
  todo: null,
  skipped: <Minus size={12} />,
};

export function Timeline({ steps }: { steps: Step[] }) {
  return (
    <ol className="timeline">
      {steps.map((step) => (
        <li className={`step ${step.state}`} key={step.key}>
          <span className="node">{NODE[step.state]}</span>
          <div className="step-body">
            <strong>{step.label}</strong>
            {step.detail &&
              (step.href ? (
                <a className="link" href={step.href} target="_blank" rel="noreferrer">
                  {step.detail} ↗
                </a>
              ) : (
                <span>{step.detail}</span>
              ))}
          </div>
        </li>
      ))}
    </ol>
  );
}

export function PageHead({ title, lede, children }: { title: string; lede?: React.ReactNode; children?: React.ReactNode }) {
  return (
    <div className="page-head">
      <div>
        <h1>{title}</h1>
        {lede && <p>{lede}</p>}
      </div>
      {children && <div className="actions">{children}</div>}
    </div>
  );
}
