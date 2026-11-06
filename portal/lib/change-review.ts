/** Deterministic, local analysis of Terraform JSON plans. No cloud operations. */
export type Severity = "critical" | "high" | "medium" | "info";
export type Finding = { id: string; severity: Severity; resource: string; title: string; evidence: string; recommendation: string };
export type ChangeNode = { address: string; type: string; actions: string[]; dependencies: string[]; unknowns: boolean };
export type ChangeReview = { version: "1"; terraformVersion: string; nodes: ChangeNode[]; findings: Finding[]; incomplete: boolean; unsupported: number };
const object = (v: unknown): Record<string, unknown> => v !== null && typeof v === "object" && !Array.isArray(v) ? v as Record<string, unknown> : {};
const text = (v: unknown, fallback = "") => typeof v === "string" ? v.slice(0, 300) : fallback;
const list = (v: unknown): unknown[] => Array.isArray(v) ? v : [];
const anyTrue = (v: unknown, depth = 0): boolean => depth < 16 && (v === true || (typeof v === "object" && v !== null && Object.values(v).some((item) => anyTrue(item, depth + 1))));

export function parsePlan(input: string): ChangeReview {
  if (new TextEncoder().encode(input).length > 1_000_000) throw new Error("Use a JSON plan smaller than 1 MB.");
  let value: unknown;
  try { value = JSON.parse(input); } catch { throw new Error("This is not valid JSON. Export a saved plan with terraform show -json tfplan."); }
  const plan = object(value);
  if (!/^1\./.test(text(plan.format_version))) throw new Error("Use a Terraform JSON plan with format_version 1.x.");
  if (!Array.isArray(plan.resource_changes)) throw new Error("This file has no resource_changes array. Upload plan JSON, not state or a Terraform configuration.");
  if (plan.resource_changes.length > 500) throw new Error("This review supports up to 500 resource changes per plan.");
  const nodes: ChangeNode[] = [];
  const findings: Finding[] = [];
  const add = (resource: string, severity: Severity, title: string, evidence: string, recommendation: string) => findings.push({ id: `AR-${String(findings.length + 1).padStart(3, "0")}`, resource, severity, title, evidence, recommendation });
  if (plan.errored === true) add("Plan", "critical", "Terraform could not finish planning", "errored = true", "Resolve the Terraform error and generate a fresh plan before review.");
  const incomplete = plan.complete === false || list(plan.deferred_changes).length > 0;
  if (incomplete) add("Plan", "high", "The plan is incomplete", "Deferred changes exist or complete = false.", "Generate a complete plan; the omitted changes are outside this assessment.");
  const configured = new Map<string, string[]>();
  const references = (v: unknown, depth = 0): string[] => {
    if (depth > 14 || v === null || typeof v !== "object") return [];
    const record = object(v);
    return [...list(record.references).filter((r): r is string => typeof r === "string"), ...Object.entries(v).filter(([key]) => key !== "references").flatMap(([, child]) => references(child, depth + 1))];
  };
  const walk = (module: unknown, prefix = "", depth = 0) => {
    if (depth > 10) return;
    const m = object(module);
    for (const value of list(m.resources)) {
      const r = object(value);
      const address = text(r.address);
      if (address) configured.set(prefix + address, [...list(r.depends_on).filter((v): v is string => typeof v === "string"), ...references(r.expressions)].map((ref) => prefix + ref));
    }
    for (const [name, call] of Object.entries(object(m.module_calls))) walk(object(call).module, `${prefix}module.${name}.`, depth + 1);
  };
  walk(object(plan.configuration).root_module);
  let unsupported = 0;
  for (const value of plan.resource_changes) {
    const r = object(value), change = object(r.change);
    const address = text(r.address), type = text(r.type);
    const actions = list(change.actions).map((v) => text(v));
    if (!address || !type || !actions.length || actions.some((a) => !["create", "read", "update", "delete", "no-op", "forget"].includes(a))) throw new Error("A resource change has missing metadata or unsupported actions. Regenerate the plan with a supported Terraform version.");
    if (r.mode === "data" || actions.every((a) => a === "no-op" || a === "read")) continue;
    const unknowns = anyTrue(change.after_unknown);
    nodes.push({ address, type, actions, dependencies: [], unknowns });
    const deleting = actions.includes("delete"), replacing = deleting && actions.includes("create");
    if (deleting || actions.includes("forget")) add(address, /google_(sql_database_instance|storage_bucket)$/.test(type) ? "critical" : "high", replacing ? "Resource replacement" : actions.includes("forget") ? "Resource removed from Terraform management" : "Resource deletion", `Planned actions: ${actions.join(" → ")}.`, replacing ? "Verify backup, recovery and dependent-service behaviour before approving replacement." : "Confirm ownership, dependencies and recovery before accepting this change.");
    if (!type.startsWith("google_")) {
      unsupported++;
      add(address, "medium", "Provider-specific checks unavailable", `No provider-specific rules are defined for ${type}.`, "Use the provider's own policy and plan checks; this review cannot assess its configuration.");
    }
    if (unknowns) add(address, "medium", "Some values are unknown until apply", "after_unknown contains unresolved values.", "Review the resolved values before execution; this analysis cannot assess them.");
    if (anyTrue(change.after_sensitive)) add(address, "medium", "Sensitive values excluded from detailed checks", "Terraform marks planned attributes as sensitive.", "Review these attributes through an approved secure process; they are never included in the report or AI explanation.");
    if (deleting && !replacing) continue;
    const after = object(change.after), sensitive = object(change.after_sensitive);
    const safe = (key: string) => (change.after_sensitive === true || anyTrue(sensitive[key])) ? undefined : after[key];
    if (/^google_.*_iam_(member|binding|policy)$/.test(type)) {
      let bindings: Record<string, unknown>[] = [];
      if (type.endsWith("_iam_policy") && typeof safe("policy_data") === "string") {
        try { bindings = list(object(JSON.parse(String(safe("policy_data")))).bindings).map(object); }
        catch { add(address, "high", "Embedded IAM policy could not be inspected", "policy_data is not valid JSON.", "Inspect the complete policy before approval; embedded grants were not assessed."); }
      }
      const members = [safe("member"), ...list(safe("members")), ...bindings.flatMap(b => list(b.members))];
      if (members.some((v) => v === "allUsers" || v === "allAuthenticatedUsers")) add(address, "critical", "Access granted to a public principal", "member/members includes allUsers or allAuthenticatedUsers.", "Replace public principals with a specific approved identity and the minimum required role.");
      const roles = [text(safe("role")), ...bindings.map(b => text(b.role))];
      const privileged = ["roles/owner", "roles/editor", "roles/iam.serviceAccountTokenCreator", "roles/resourcemanager.projectIamAdmin"];
      const role = roles.find(r => privileged.includes(r)) ?? roles.find(r => r === "roles/viewer") ?? roles.find(r => /admin$/i.test(r)) ?? roles[0];
      if (["roles/owner", "roles/editor", "roles/iam.serviceAccountTokenCreator", "roles/resourcemanager.projectIamAdmin"].includes(role)) add(address, "critical", "Privileged IAM role", `Planned role: ${role}.`, "Use a reviewed least-privilege entitlement and require independent approval.");
      else if (role === "roles/viewer") add(address, "high", "Broad basic IAM role", "Planned role: roles/viewer.", "Replace the basic role with a narrower predefined role for the required service.");
      else if (/admin$/i.test(role)) add(address, "high", "Administrative IAM role", `Planned role: ${role}.`, "Explain why administrative privileges are needed and consider a narrower role.");
      if (type.includes("project_iam")) add(address, "medium", "Permission scope reaches the project", "This is a project-level IAM resource.", "Review every workload and data resource that inherits the permission; service-level dependency edges do not describe IAM inheritance.");
      if (type.endsWith("_iam_policy")) add(address, "high", "Authoritative IAM policy replacement", "An iam_policy resource manages the complete policy at its scope.", "Review the policy diff for removal of existing grants and unintended access expansion.");
      if (type.endsWith("_iam_binding")) add(address, "medium", "Authoritative role membership", "An iam_binding resource manages all members of its role.", "Confirm existing required members are retained and no other Terraform stack manages this role.");
    }
    if (type === "google_sql_database_instance") {
      if (safe("deletion_protection") === false) add(address, "high", "Database deletion protection disabled", "deletion_protection = false.", "Retain deletion protection unless a separately reviewed removal is intended.");
      if (change.after_sensitive !== true && !anyTrue(sensitive.settings)) {
        const settings = object(list(safe("settings"))[0]);
        const ip = object(list(settings.ip_configuration)[0]);
        if (list(ip.authorized_networks).some((network) => ["0.0.0.0/0", "::/0"].includes(text(object(network).value)))) add(address, "critical", "Database network open to the internet", "An authorized_networks range permits all addresses.", "Restrict the range to approved networks or use private connectivity.");
      }
    }
    if (type === "google_storage_bucket" && safe("public_access_prevention") === "inherited") add(address, "info", "Bucket public-access prevention is inherited", "public_access_prevention = inherited; the ancestor policy is not present in this plan.", "Check the effective organization policy before granting bucket access.");
  }
  const base = (address: string) => address.replace(/\[(?:"[^"\\]*(?:\\.[^"\\]*)*"|\d+)\]/g, "");
  for (const node of nodes) {
    const refs = configured.get(base(node.address)) ?? [];
    node.dependencies = nodes.filter((other) => other !== node && refs.some((ref) => ref === base(other.address) || ref.startsWith(base(other.address) + ".") || base(other.address).startsWith(ref + "."))).map((other) => other.address);
  }
  const rank: Record<Severity, number> = { critical: 0, high: 1, medium: 2, info: 3 };
  findings.sort((a, b) => rank[a.severity] - rank[b.severity]);
  return { version: "1", terraformVersion: text(plan.terraform_version, "Unknown"), nodes, findings, incomplete, unsupported };
}

/** Graph direction: if B references A, changing A can affect B. */
export function dependentsOf(report: ChangeReview, address: string): string[] {
  const seen = new Set<string>([address]);
  const pending = [address];
  while (pending.length) {
    const current = pending.pop()!;
    for (const node of report.nodes) if (node.dependencies.includes(current) && !seen.has(node.address)) { seen.add(node.address); pending.push(node.address); }
  }
  seen.delete(address);
  return [...seen];
}

export const SAMPLE_PLAN = JSON.stringify({
  format_version: "1.2", terraform_version: "1.9.8", complete: true,
  resource_changes: [
    { address: "google_sql_database_instance.orders", type: "google_sql_database_instance", mode: "managed", change: { actions: ["delete", "create"], before: { deletion_protection: true }, after: { deletion_protection: false }, after_unknown: {} } },
    { address: "google_project_iam_member.agent", type: "google_project_iam_member", mode: "managed", change: { actions: ["create"], after: { role: "roles/editor", member: "allAuthenticatedUsers" }, after_unknown: {} } },
    { address: "google_cloud_run_v2_service.checkout", type: "google_cloud_run_v2_service", mode: "managed", change: { actions: ["update"], after: {}, after_unknown: {} } },
    { address: "google_cloud_run_v2_service.fulfilment", type: "google_cloud_run_v2_service", mode: "managed", change: { actions: ["update"], after: {}, after_unknown: { uri: true } } },
  ],
  configuration: { root_module: { resources: [
    { address: "google_cloud_run_v2_service.checkout", expressions: { template: { references: ["google_sql_database_instance.orders.connection_name"] } } },
    { address: "google_cloud_run_v2_service.fulfilment", expressions: { template: { references: ["google_cloud_run_v2_service.checkout.uri"] } } },
  ] } },
}, null, 2);
