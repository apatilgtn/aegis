# Aegis — Agentic Cloud Governance Plane

Aegis is a natural-language agent that handles everyday cloud operations (starting with access requests) across Azure and GCP — **without ever being able to touch cloud infrastructure directly.** It can only *propose* changes as Terraform pull requests and *request* approvals through ServiceNow. A human has to approve the request **and** merge the PR before anything is applied. Nothing in this system has a write credential to any cloud.

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

There's no form. Every field is a turn in the conversation:

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
10. **"Merge now"** — appears once approved. Clicking it re-checks approval server-side (never trusts a stale UI value) and merges the PR
11. **GitHub Actions** picks up the merge and runs `terraform apply` for real. The same "Refresh pipeline status" button shows the live build result once merged

The chat only ever writes to ServiceNow and GitHub (proposals and approvals); it has no credential capable of writing to Azure or GCP directly.

## Honest status: real vs. mocked vs. not-yet-built

| Piece | Status |
|---|---|
| ServiceNow approval flow | **Real.** Live PDI instance. Creates a `sc_request` + an explicit `sysapproval_approver` task (a bare Table API insert doesn't trigger ServiceNow's workflow engine — this was a real bug found and fixed) |
| GitHub PR + merge | **Real.** Opens branches, commits the generated `.tf` file, opens/merges PRs via the GitHub API |
| GCP IAM read (current access, `roles/*` lookup) | **Real.** Uses a dedicated read-only service account (`aegis-reader`, `roles/iam.securityReviewer`) |
| GCP IAM write (`terraform apply`) | **Real.** GitHub Actions authenticates via Workload Identity Federation (no service account key) as `aegis-pipeline` (`roles/resourcemanager.projectIamAdmin`, scoped to one project). Verified end-to-end multiple times, including by the project owner testing with their own Google account |
| CI/CD pipeline | **Real**, on GitHub Actions (not Cloud Build — deliberately switched so the same workflow shape extends to Azure or any other cloud as another job) |
| Azure identity/group membership | **Mocked.** No Azure tenant credentials exist in this environment; `mcp_servers/azure_entra/directory_service.py` is an in-memory stand-in with the same function signatures a real Graph API client would have |
| Natural-language understanding | **Not an LLM call.** The original design envisions an LLM-driven orchestrator; what's built is a deterministic guided conversation (buttons + short free-text fields) that produces the same `AccessGrantRequest` contract. Swapping in a real LLM means replacing the conversation's input-gathering step, not redesigning anything downstream |
| Other capabilities (cost/FinOps, tagging, incident triage) | **Not built.** Only `access_grant` (and its read-only sibling `access_check`) exist |

## Repository layout

```text
contracts/            Pydantic models — the cloud-neutral capability boundary
  capability_contract.py   AccessGrantRequest/Result, PolicyDecision, ApprovalRef, PullRequestRef
  entitlement_catalog.py   Deterministic catalog resolver (fails closed, never guesses an entitlement)

agents/               Capability agents (business logic, no I/O of its own)
  guardrail_agent.py       Calls the Rego policy, returns a PolicyDecision
  access_grant_agent.py    handle_access_grant, advance_pipeline, merge_access_grant, status checks
  access_check_agent.py    "What access do I currently have" — read-only
  terraform_render.py      Renders the HCL diff for a resolved entitlement

mcp_servers/          One subfolder per external system, real client + FastMCP tool server
  azure_entra/             Mocked directory (no real tenant)
  gcp_resource_manager/    Real project/IAM lookups
  servicenow/              Real ServiceNow client
  github/                  Real GitHub client (branch/commit/PR/merge/status)

policy/               OPA/Rego guardrail policy + tests
data/                 entitlement_catalog.yaml — the source of truth for what's grantable
modules/              Reusable Terraform modules (Azure Entra group membership, GCP IAM binding)
infra/access-grants/  The actual Terraform root module CI/CD applies — generated .tf files land here
.github/workflows/    terraform-apply.yml — GitHub Actions, GCP via Workload Identity Federation
ui/                   chat_app.py — the Streamlit conversational front-end
```

## Running it locally

```bash
pip install streamlit pydantic pyyaml regopy requests python-dotenv \
  google-auth google-api-python-client google-cloud-resource-manager \
  google-cloud-storage google-cloud-iam mcp

cp .env.example .env   # fill in ServiceNow + GitHub + GCP project details
streamlit run ui/chat_app.py
```

Secrets live in `.env` (gitignored) and, for GCP, a service account key under `.secrets/` (also gitignored) — see `.env.example` for the required variables.

## Testing

```bash
python3 -m pytest -q
```

29 tests across contracts, the entitlement catalog, the guardrail policy (evaluated for real against the actual `.rego` file via `regopy`, not mocked), and both capability agents. External calls (ServiceNow, GitHub) are stubbed in the automated suite so running tests never creates real tickets or PRs — the real integrations are verified separately via live scripted runs, several of which resulted in genuine, confirmed GCP IAM grants.

## Security notes

- **Least privilege by construction**: `aegis-reader` can only read; `aegis-pipeline` can only touch IAM bindings on one project and only ever runs inside GitHub Actions, never invoked directly by the chat.
- **No standing cloud credentials in CI**: the pipeline authenticates via Workload Identity Federation, not a stored key.
- **PR creation is gated on approval, not parallel to it**: a declined ServiceNow request never produces a GitHub PR (an earlier version of this app got this wrong; fixed and verified).
- **Secrets**: `.env` and `.secrets/` are gitignored. A temporary Owner-scoped bootstrap service account (`aegis-bootstrap`) was used to provision the two scoped service accounts and has not yet been revoked — that's the one cleanup item still outstanding.
