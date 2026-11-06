export type ServiceSummary = {
  id: string;
  name: string;
  slug: string;
  description: string | null;
  lifecycle: string;
  tier: string | null;
  repositoryUrl: string | null;
};

export type LogFinding = { timestamp: string; severity: string; source: string; message: string };

export type DeliverySummary = {
  pullRequestUrl: string | null;
  workflowRunUrl: string | null;
  expiresAt: string | null;
  reason: string | null;
  changeNumber: string | null;
  changeUrl: string | null;
  approverEmail: string | null;
  incidentNumber: string | null;
  incidentUrl: string | null;
  triageSummary: string | null;
  findings: LogFinding[];
  findingCount: number | null;
};

export type BucketCompliance = {
  bucket: string;
  location: string;
  labels: Record<string, string>;
  missing: string[];
};

export type LabelComplianceSnapshot = {
  capturedAt: string;
  projectId: string;
  required: string[];
  buckets: BucketCompliance[];
  compliant: number;
  total: number;
};

export type CostRow = { cost_center: string | null; service: string; net_cost: string | null; currency: string | null };

export type CostSnapshot = {
  capturedAt: string;
  projectId: string;
  month: string;
  currency: string | null;
  total: number;
  untagged: number;
  rows: CostRow[];
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
  delivery: DeliverySummary;
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

export type EntitlementOption = {
  serviceId: string;
  cloud: string;
  environment: string;
  tier: string;
  maxTtlHours: number;
};

export type PortalData = {
  services: ServiceSummary[];
  entitlements: EntitlementOption[];
  requests: ActionRequestSummary[];
  workflowEvents: WorkflowEventSummary[];
  auditEvents: AuditEventSummary[];
  cloudProject: { projectId: string; billingConnected: boolean } | null;
  labelCompliance: LabelComplianceSnapshot | null;
  cost: CostSnapshot | null;
  loadError: string | null;
};
