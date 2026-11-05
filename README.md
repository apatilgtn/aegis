# Aegis — Agentic Cloud Governance Plane

Aegis is a natural-language agent that handles everyday cloud operations across Azure and GCP — **without ever being able to touch cloud infrastructure directly.** For access requests and tag/compliance remediation, it can only *propose* changes as Terraform pull requests and *request* approvals through ServiceNow; a human has to approve the request **and** merge the PR before anything is applied. For incident investigation, it can only read logs and raise a ServiceNow ticket. It has no write credential to any cloud, for any capability.

Built for the IEP Techathon: Agentic AI Hackathon 2026 (Anand Patil, Beyond Bank Australia). The original pitch is in [`Aegis-Agentic-Cloud-Governance-Solution-Design.docx`](./Aegis-Agentic-Cloud-Governance-Solution-Design.docx); this README documents what has actually been built and verified against real systems, which in a few places is more concrete than the original design (real ServiceNow, real GCP, real CI/CD), and in one place is simpler than it (the "orchestrator" is currently a deterministic guided conversation, not an LLM call — see [Honest status](#honest-status-real-vs-mocked-vs-not-yet-built) below).

## The core idea: propose, don't mutate

```text
Requester ──chat──▶ Aegis ──policy check──▶ ServiceNow approval ──human approves──▶ GitHub PR
                                                                                         │
                                                                              human merges PR
                                                                                         │
                                                                                         ▼
                                                                          GitHub Actions → terraform apply
                                                                                         │
                                                                                         ▼
                                                                             real cloud IAM change
```

Two independent human gates sit between "someone asked for access" and "access actually exists": ServiceNow approval, then a Git merge. Aegis automates everything *around* those gates (understanding the request, checking policy, raising the ticket, opening the PR, watching the pipeline) but never removes them.

## Architecture

| Layer | What it does | Built with |
|---|---|---|
| 1. Conversational front-end | Gathers the request turn-by-turn in chat; checks current access before asking for more | Streamlit ([`ui/chat_app.py`](./ui/chat_app.py)) |
| 2. Capability agents | Resolve the request against policy, orchestrate the pipeline | [`agents/`](./agents/) |
| 3. Guardrail (policy-as-code) | Deterministic allow/deny — the agent explains the decision, never makes it | OPA/Rego ([`policy/access_grant.rego`](./policy/access_grant.rego)) |
| 4. Cloud tool servers (MCP) | Read live identity/IAM state per cloud, behind a cloud-neutral contract | [`mcp_servers/`](./mcp_servers/) |
| 5. Governed change path | Terraform diff → Git PR → ServiceNow approval → CI/CD apply | GitHub + ServiceNow + GitHub Actions |

The cloud-neutrality claim is real, not aspirational: the same `handle_access_grant()` call produces a correct Azure Entra diff or a correct GCP IAM diff from the identical request, differing only in which MCP tool servers get called.

## How the chatbot works for a user

There's no form. Every field is a turn in the conversation. The first turn is always the same regardless of which capability the user ends up in:

0. **"What would you like to do: request cloud access, report an issue, or check tagging/compliance?"** — this single button choice branches into one of the three flows below.

### Access request flow

1. **"Which cloud?"** — Azure or GCP (button choice)
2. **"Which environment?"** — Non-Prod or Production (Production is explained as unavailable and redirected to Non-Prod)
3. **"What's your email?"** — free text. Aegis immediately checks **real current access**:
   - GCP: a live IAM policy lookup for that email
   - Azure: a directory/group-membership lookup (mocked — see below)
   - If they already hold something, Aegis says so and asks if they want *additional* access or if that's all
4. **Role choice** — only the entitlements actually available for that cloud/environment are shown, as buttons (never free-typed)
5. **Duration** — quick preset buttons or free text, capped at the catalog's max for that role
6. **Justification** — free text, minimum 10 characters
7. **Confirmation** — a summary rendered as colored stat chips (cloud / business unit / access level / duration) plus the typed reason, with a final yes/no
8. **Submit** — Aegis runs the policy check and, if it passes, raises a ServiceNow request. **No GitHub PR exists yet at this point.**
9. **"Refresh pipeline status"** — a button the user can click any time. It polls ServiceNow; if rejected, it says so and stops (nothing was ever proposed on GitHub); if approved, it opens the PR **at that moment**, not before
10. **Independent GitHub review** — the application exposes no merge function. A reviewer merges in GitHub after branch-protection requirements pass
11. **GitHub Actions** picks up that human merge and runs `terraform apply`. The same "Refresh pipeline status" button shows the live build result once merged

The chat only ever writes to ServiceNow and GitHub (proposals and approvals) for this flow; it has no credential capable of writing to Azure or GCP directly.

### Incident triage flow

1. **"Which cloud?"** — Azure or GCP (button choice)
2. **"What's your email?"** — free text, recorded as the requester
3. **"What service or resource?"** — free text, e.g. `payments-api` — scopes the log search
4. **Time window** — preset buttons (last 1h / 24h / 7 days) or free text
5. **Justification** — free text, minimum 10 characters, explaining why the investigation is needed
6. **Aegis searches logs immediately** — no approval needed, since reading logs changes nothing:
   - GCP: a real Cloud Logging query, filtered to WARNING severity and above
   - Azure: a mocked Azure Monitor lookup (no Azure tenant credentials in this environment)
7. **Findings + summary** — up to 20 matching log entries in an expandable list, plus a deterministically generated (no LLM) plain-language summary: severity breakdown and the most frequent or most recent message
8. **"Raise incident" / "No thanks"** — raising is immediate and needs no approval, since nothing changes in any cloud; declining just ends the conversation

This flow only ever writes to ServiceNow (the incident record itself); it never touches GitHub, since there is no infrastructure change to propose.

### Tag remediation flow

1. **"Which cloud?"** — Azure or GCP (button choice)
2. **"Which environment?"** — Non-Prod or Production (Production is explained as unavailable and redirected to Non-Prod)
3. **"What's your email?"** — free text, recorded as the requester
4. **Resource name** — free text: a GCS bucket name (GCP) or resource group name (Azure). Aegis immediately checks **real current tags**:
   - GCP: a live Cloud Storage label lookup — a bucket that doesn't exist yet is simply treated as missing every required tag, the same as an under-tagged one
   - Azure: a mocked resource-tag lookup (no Azure tenant credentials in this environment)
   - If every required tag (`owner`, `cost-center`, `environment`) is already present, Aegis says so and stops — nothing to propose
5. **Cost-center code** — free text, minimum 2 characters, only asked if a tag is actually missing
6. **Justification** — free text, minimum 10 characters
7. **Confirmation** — a summary rendered as colored stat chips (cloud / resource / missing tags / cost center) plus the typed reason, with a final yes/no
8. **Submit** — Aegis runs the policy check and, if it passes, raises a ServiceNow request. **No GitHub PR exists yet at this point.** Only the missing required tags get filled in with a value (owner = requester, cost-center = the typed code, environment = the selected environment); any existing tag is preserved as-is, including ones with the "wrong" value — correcting existing wrong values is out of scope for this capability
9. **"Refresh pipeline status"** — identical mechanics to the access-request flow; the application never merges the PR
10. **GCP writes fail closed for now** — Aegis reads bucket labels, but will not render a write until the bucket's existing Terraform state and resource address are registered. It never creates or imports a bucket implicitly

This flow writes to ServiceNow and GitHub exactly like the access-request flow; it has no credential capable of writing to Azure or GCP directly.

## Honest status: real vs. mocked vs. not-yet-built

| Piece | Status |
|---|---|
| ServiceNow approval flow | **Integration ready, Flow setup required.** Uses OAuth client credentials and administrator-owned Flow Designer REST endpoints. Aegis never calls the Table API or stores a ServiceNow username/password |
| GitHub PR + merge | **PR creation is real.** Opens branches, commits the generated `.tf` file, and opens PRs through a GitHub App. Aegis has no merge API; protected-branch reviewers own the merge |
| GCP IAM read (current access, `roles/*` lookup) | **Real.** Uses a dedicated read-only service account (`aegis-reader`, `roles/iam.securityReviewer`) |
| GCP IAM write (`terraform apply`) | **Real.** GitHub Actions authenticates via Workload Identity Federation (no service account key) as `aegis-pipeline` (`roles/resourcemanager.projectIamAdmin`, scoped to one project). Verified end-to-end multiple times, including by the project owner testing with their own Google account |
| CI/CD pipeline | **Real**, on GitHub Actions (not Cloud Build — deliberately switched so the same workflow shape extends to Azure or any other cloud as another job) |
| Azure identity/group membership | **Mocked.** No Azure tenant credentials exist in this environment; `mcp_servers/azure_entra/directory_service.py` is an in-memory stand-in with the same function signatures a real Graph API client would have |
| Natural-language understanding | **Not an LLM call.** The original design envisions an LLM-driven orchestrator; what's built is a deterministic guided conversation (buttons + short free-text fields) that produces the same `AccessGrantRequest`/`IncidentTriageRequest` contract. Swapping in a real LLM means replacing the conversation's input-gathering step, not redesigning anything downstream |
| Incident triage: GCP log search | **Real.** Live Cloud Logging query via the same read-only `aegis-reader` service account (`roles/logging.viewer`, already granted — no new permission needed) |
| Incident triage: Azure log search | **Mocked.** No Azure tenant credentials exist in this environment; `mcp_servers/azure_monitor/log_lookup.py` returns canned findings for a couple of known resource hints, same function signature a real Azure Monitor client would have |
| Incident triage: incident creation | **Integration ready, Flow setup required.** The OAuth-protected incident Flow creates the record after user confirmation |
| Tag remediation: GCP tag read + write | **Read is real; write is fail-closed.** The legacy bucket-creating resource was removed. Writes remain disabled until an IaC ownership registry routes the proposal to the state that already owns the bucket |
| Tag remediation: Azure tag read/write | **Mocked.** No Azure tenant credentials exist in this environment; `mcp_servers/azure_resource_tags/resource_tags.py` returns canned tags for one known resource group. A PR can still be opened/merged for an Azure remediation, but (like access_grant's Azure path) there is no Azure job in the CI workflow yet, so `terraform apply` would fail without real credentials — an accepted, pre-existing limitation, not new to this capability |
| Other capabilities (cost/FinOps) | **Not built.** `access_grant`, `access_check`, `incident_triage`, and `tag_remediation` exist |

## Repository layout

```text
contracts/            Pydantic models — the cloud-neutral capability boundary
  capability_contract.py   AccessGrantRequest/Result, IncidentTriageRequest/Result, TagComplianceRequest/Result, ...
  entitlement_catalog.py   Deterministic catalog resolver (fails closed, never guesses an entitlement)
  tag_policy.py            Loads the required-tags list — same "never guess" discipline

agents/               Capability agents (business logic, no I/O of its own)
  guardrail_agent.py       Calls the Rego policy, returns a PolicyDecision
  access_grant_agent.py    handle_access_grant, advance_pipeline, read-only status checks; never merges
  access_check_agent.py    "What access do I currently have" — read-only
  incident_triage_agent.py triage_incident, raise_incident — no policy gate, no PR pipeline
  tag_remediation_agent.py check_tag_compliance, handle_tag_remediation, advance_pipeline; never merges
  terraform_render.py      Renders the HCL diff for a resolved entitlement
  tag_render.py            Renders the HCL diff for a tag remediation

mcp_servers/          One subfolder per external system, real client + FastMCP tool server
  azure_entra/             Mocked directory (no real tenant)
  gcp_resource_manager/    Real project/IAM lookups
  gcp_logging/             Real Cloud Logging queries (incident triage)
  azure_monitor/           Mocked log lookups (no real tenant)
  gcp_storage/             Real GCS bucket label lookups (tag remediation)
  azure_resource_tags/     Mocked resource tag lookups (no real tenant)
  servicenow/              OAuth + Flow client (access requests, status, incidents; no Table API)
  github/                  GitHub App client (branch/commit/PR/read-only status; no merge) — shared by every capability

policy/               OPA/Rego guardrail policies + tests (access_grant, tag_remediation)
data/                 entitlement_catalog.yaml, tag_policy.yaml — the source of truth for what's grantable/required
modules/              Reusable Terraform modules (Entra group membership, GCP IAM binding, tagged bucket, Azure resource tags)
infra/access-grants/     Terraform root module for access grants — generated .tf files land here
infra/tag-remediations/  Terraform root module for tag remediations — generated .tf files land here
.github/workflows/    portal/platform CI plus protected Terraform apply after merge
ui/                   chat_app.py — the Streamlit conversational front-end
```

## Running it locally

```bash
python -m pip install --requirement requirements.lock

cp .env.example .env   # fill in ServiceNow + GitHub + GCP project details
streamlit run ui/chat_app.py
```

Secrets live in `.env` (gitignored) and, for GCP, a service account key under `.secrets/` (also gitignored) — see `.env.example` for the required variables.

## Testing

```bash
python3 -m pytest -q
```

71 tests cover contracts, policies, render escaping, OAuth/Flow boundaries, and capability agents. External calls are stubbed, so tests never create tickets, incidents, or PRs.

## Security notes

- **Least privilege by construction**: `aegis-reader` can only read (IAM policies, logs, and now GCS bucket metadata); `aegis-pipeline` can only touch IAM bindings and GCS buckets on one project, and only ever runs inside GitHub Actions, never invoked directly by the chat. Each new capability that needed a new permission got its own narrowly scoped role grant, not a broadening of an existing one.
- **No standing cloud credentials in CI**: the pipeline authenticates via Workload Identity Federation, not a stored key.
- **PR creation is gated on approval, not parallel to it**: a declined ServiceNow request never produces a GitHub PR (an earlier version of this app got this wrong; fixed and verified).
- **Secrets**: `.env` and `.secrets/` are gitignored. The temporary Owner-scoped bootstrap service account (`aegis-bootstrap`) has been removed from project IAM and its user-managed key has been deleted. Runtime reads use the scoped reader identity; governed delivery uses the GitHub workload-identity path.
