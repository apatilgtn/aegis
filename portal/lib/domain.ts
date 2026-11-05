export type ServiceSummary = {
  id: string;
  name: string;
  slug: string;
  description: string | null;
  lifecycle: string;
  tier: string | null;
  repositoryUrl: string | null;
};

export type ActionRequestSummary = {
  id: string;
  requesterId: string;
  capability: string;
  justification: string;
  status: string;
  riskLevel: string;
  resource: Record<string, unknown>;
  policyDecision: Record<string, unknown> | null;
  service: { id: string; name: string; slug: string } | null;
  createdAt: string;
  updatedAt: string;
};

export type WorkflowEventSummary = {
  id: number;
  workflowRunId: string;
  eventType: string;
  actorId: string | null;
  occurredAt: string;
  payload: Record<string, unknown>;
};

export type AuditEventSummary = {
  id: number;
  action: string;
  actorId: string | null;
  objectType: string;
  objectId: string | null;
  occurredAt: string;
};

export type PortalData = {
  services: ServiceSummary[];
  requests: ActionRequestSummary[];
  workflowEvents: WorkflowEventSummary[];
  auditEvents: AuditEventSummary[];
  loadError: string | null;
};
