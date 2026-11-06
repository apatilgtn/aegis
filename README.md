# Aegis — Agentic Cloud Governance Plane

Aegis handles everyday cloud operations on **Google Cloud** — temporary access, incident investigation, label compliance and cost visibility — **without ever being able to change cloud infrastructure itself.** It proposes changes as pull requests and requests approval in **ServiceNow**; a person approves the change and a person merges the PR before the protected pipeline applies anything.

Built for the IEP Techathon: Agentic AI Hackathon 2026 (Anand Patil, Beyond Bank Australia). The original pitch is in [`Aegis-Agentic-Cloud-Governance-Solution-Design.docx`](./Aegis-Agentic-Cloud-Governance-Solution-Design.docx). **Current scope is GCP only**; Azure is out of scope for now (mocked Azure tool servers and modules are kept in the repo for later).

## Propose, don't mutate

```text
Portal request ─▶ OPA policy ─▶ ServiceNow change + approval ─▶ GitHub PR (GitHub App, cannot merge)
                                  (approver ≠ requester)                 │
                                                                 person merges PR
                                                                         │
                                                                         ▼
                                             GitHub Actions (keyless WIF) ─▶ GCP change
                                                                         │
                                       worker follows merge + pipeline ◀─┘ ─▶ status, expiry, ServiceNow note
```

Two independent human gates — a ServiceNow approval and a Git merge — sit between a request and any change. Investigating logs changes nothing, so it needs no approval; raising the resulting ServiceNow incident is the requester's explicit choice.

## Architecture

| Layer | What it does | Built with |
|---|---|---|
| Portal | Sign-in, workspace, requests, ServiceNow status, investigations, label compliance, cost, audit ledger, Ask Aegis | Next.js 16 + Supabase Auth ([`portal/`](./portal/)) |
| State & workflow | Requests, durable jobs, append-only events and audit, row-level security | Supabase Postgres ([`supabase/migrations/`](./supabase/migrations/)) |
| Guardrail | Deterministic allow/deny per capability; the model never decides | OPA/Rego ([`policy/`](./policy/)) |
| Worker | Runs policy checks, ServiceNow approvals, log searches, incidents, PRs, delivery tracking, snapshots, audit export | Python on Cloud Run as `aegis-reader`, no key ([`worker/`](./worker/)) |
| MCP gateway | One IAM-protected, read-only entry point to GCP tools for MCP clients | FastMCP on Cloud Run ([`mcp_gateway/`](./mcp_gateway/)) |
| Change path | Reviewed Terraform / label files → human merge → pipeline apply | GitHub App + GitHub Actions + Workload Identity Federation |
| Approval & incidents | Change requests with approvals; incidents | ServiceNow Table API via OAuth client credentials |
| AI assist | Turns plain English into a draft form; answers questions from live workspace data | NVIDIA `nemotron-3.5-lightning-30b-a3b` ([`portal/lib/nvidia.ts`](./portal/lib/nvidia.ts)) |
| Audit / SIEM | Every audit event copied to Cloud Logging log `aegis-audit` | Worker → Cloud Logging |

## Capabilities

**Request access** — pick a service and one of its reviewed access levels (tier 1 `roles/logging.viewer`, tier 2 `roles/storage.objectUser`) and a duration. After ServiceNow approval Aegis opens a PR; once merged, GCP grants the role with an IAM condition that **expires automatically** — no clean-up job.

**Investigate an issue** — name a service and a time window. The worker searches Cloud Logging (WARNING and above) immediately and shows a plain-language summary plus the latest entries. The requester then raises a ServiceNow incident or closes the search.

**Fix missing labels** — the worker checks every bucket for `owner`, `cost-center` and `environment` every 15 minutes. A fix adds only the missing labels after ServiceNow approval and a merged PR; existing labels are never changed and buckets are never imported into Terraform.

**Cost by cost center** — month-to-date cost from the BigQuery billing export, grouped by the `cost-center` label, with untagged spend called out.

**Ask Aegis** — a sentence like *"cilium-agent keeps failing, check the last day"* becomes a prefilled form you check and submit yourself; questions are answered only from your live workspace data.

## Status

| Piece | Status |
|---|---|
| Portal, Supabase workflow, OPA guardrail | Live |
| ServiceNow approval and incidents (`dev402482`) | Live; verified with test records |
| GitHub App PRs, delivery tracking | Live; tracking verified on real PRs |
| Expiring GCP access | Terraform plan verified against the live project |
| Incident triage on Cloud Logging | Live data verified |
| Label fixes | Live label check; dry run verified on a real bucket |
| Cost reconciliation | Built; waits for Cloud Billing export to BigQuery |
| NVIDIA drafting | Model and prompt verified (5/5); answers path untested |
| MCP gateway, audit export | Live on Cloud Run |
| Azure | Out of scope (mocked servers kept) |

## Repository layout

```text
portal/              Next.js portal (UI, server actions, NVIDIA helper)
worker/              Workflow worker: worker.py, gcp.py (read-only GCP), servicenow.py; Cloud Run + local images
mcp_gateway/         Read-only MCP server for GCP tools (Cloud Run, IAM-only)
policy/              OPA policies + tests: access_request, incident_triage, label_fix
supabase/migrations/ Schema, RLS, workflow functions
modules/gcp/         iam_binding (with optional expiry condition)
modules/azure/       Kept for later (out of scope)
infra/access-grants/ Terraform root for generated access grants
infra/label-fixes/   Reviewed label-fix files applied by the pipeline
scripts/             gcp_bootstrap.sh, deploy_gcp.sh, apply_label_fixes.py, p0_e2e.py
mcp_servers/azure_*  Mocked Azure tool servers (kept for later)
contracts/           Shared Pydantic types used by the Azure mocks
```

## Running it

- **GCP setup (once):** `scripts/gcp_bootstrap.sh <project-id>` creates the state bucket, `aegis-pipeline`, `aegis-reader` and keyless GitHub sign-in.
- **Deploy worker + gateway:** `scripts/deploy_gcp.sh <project-id>` builds images in Cloud Build and deploys both Cloud Run services; secrets go to Secret Manager.
- **Portal locally:** `cd portal && npm ci && npm run dev` with `portal/.env.local` (Supabase URL + publishable key, optional `NVIDIA_API_KEY`).
- **Local Docker stack:** see [`LOCAL_DEVELOPMENT.md`](./LOCAL_DEVELOPMENT.md).

Secrets live in Infisical (dev environment); local copies only in gitignored `.env`, `portal/.env.local` and `.secrets/`.

## Testing

```bash
python -m pytest -q                 # worker + kept Azure mocks
cd portal && npm run check          # lint, typecheck, build
docker run --rm -v "$PWD/policy:/policies:ro" openpolicyagent/opa:1.8.0-static test /policies
```

External calls are stubbed in tests, so they never create tickets, incidents or PRs.

## Security notes

- **No write credential to GCP outside the pipeline.** The worker and gateway run as `aegis-reader` (read-only, plus writing the audit log). `aegis-pipeline` is usable only from GitHub Actions on `main` of this repo.
- **Separation of duties twice over.** The requester is never assigned as a ServiceNow approver, and an approval by the requester is ignored. Portal users cannot insert approvals at all.
- **Least privilege by construction.** Access tiers use predefined roles (basic roles are rejected — they cannot expire). Label fixes use a custom role limited to reading and updating bucket metadata.
- **The model drafts, people decide.** NVIDIA output only prefills forms; server validation, OPA and ServiceNow approval still apply.
