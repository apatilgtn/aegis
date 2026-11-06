# Aegis Portal

Authenticated product portal for the Aegis Engineering Control Plane.

## Stack

- Next.js 16 App Router
- React 19
- TypeScript
- Tailwind CSS 4
- Lucide icons
- Supabase Auth, Postgres and Row Level Security

## Run

1. Copy `.env.example` to `.env.local` and provide the Supabase project URL and publishable key.
2. Use Node.js 22.13 or newer.
3. Install and run:

   ```bash
   npm ci
   npm run dev
   ```

Open `http://localhost:3000` for local development. The hosted workspace is
[the production Aegis portal](https://aegis-portal-280773488181.australia-southeast1.run.app).
Existing workspace users sign in with their Supabase account. Add this origin
and `/auth/callback` to the Supabase Auth redirect allowlist before onboarding
new users through verification-email links; keep email verification enabled.

Authentication and tenant membership are real. A verified user receives no workspace role automatically; an administrator must insert an active `tenant_memberships` record. Authenticated routes query the active tenant through RLS. Development previews use explicit sample data and are unavailable in production. Overview metrics cover the latest 100 requests; the request queue has server-side filters and pagination.

Run `npm run check` before shipping. It performs linting, strict TypeScript validation and a production build.

### Overview design preview

With the development server running, open `/auth/design-preview` to review the
editorial overview with sample requests. Its range, type and Everyone/Mine
filters work without a signed-in workspace; `?empty=1` shows the empty state.
This route reads no live data and returns 404 in production.

### Pre-execution change review

Open `/review` after signing in, or `/auth/design-preview?mode=review` in development
for the worked example. Upload or paste a Terraform plan exported with
`terraform show -json tfplan`. The browser analyzes plans up to 1 MB / 500 resource
changes. It flags selected destructive, public-access, privileged IAM, database
network and uncertainty conditions. Select resources in the map to trace declared
references between changed resources. Export a JSON report with the input SHA-256.
Optional NVIDIA explanations receive extracted findings only; raw plan attribute
values are not sent to the assistant. Sensitive Terraform attributes are excluded
from detailed checks. Analysis does not execute infrastructure or approve a request. Signed-in members
can save a sanitized advisory assessment, optionally linked to a request. Its
client-supplied findings never authorize execution.

Run `npm run test:review` for the deterministic evaluator tests.

This is advisory static plan analysis, not a live blast-radius simulator. It does
not infer unchanged resources, application/runtime dependencies, effective IAM
inheritance, cost estimates or cross-cloud relationships. The map shows at most
12 changed resources; the resource list includes all changes. Indexed module
references are matched to their declared resource, so instance-level relationships
may be conservative. Other providers receive generic action/uncertainty checks,
not GCP configuration checks. OPA and existing approval gates remain authoritative.

### Governance workspaces

The portal uses one black sidebar, white work surfaces and green/lime accents.
The access request is a three-step route/duration/review wizard with a scoped
summary. `/auth/design-preview?mode=access` is a non-submitting development preview.
Ask Aegis is a 360px × 510px floating conversation with a pinned composer.

| Workspace | Implemented behavior |
| --- | --- |
| Requests | Server-side capability/status/search views, pagination and per-request journeys |
| My access | Active grants, pending requests and recent history |
| Change review | Local Terraform checks, declared dependency graph, saved advisory history and optional AI explanations |
| Inventory | GCP resource metadata, ownership gaps and differences from the prior observed snapshot |
| Investigations | Cloud Logging evidence, related Admin Activity events and explicit incident submission |
| Label compliance | Searchable coverage, missing-label filters, preserved existing values and governed remediation |
| Cost & AI usage | Billing export reads when configured; provider token/latency/outcome telemetry, tenant budgets and configured fallback routing |
| Audit | Filtered events, cited AI narratives and Ed25519-signed evidence exports |
| Service catalog | Service ownership/runbooks, entitlement details and reviewed project/bucket scopes |
| Operations | Durable job status, failed read-step retry and cloud verification rechecks |
| Governance controls | Audited capability policies, production permission, pause controls, approval lifetime, AI limits and source health |
| Integrations | Per-user bearer API and MCP proposal/status tools with idempotency |

### Server configuration and rollout

Apply the migrations in `supabase/migrations/2026110613*.sql` before deploying the
new worker and portal. They default mutation capabilities to disabled. Configure
`SUPABASE_SECRET_KEY` only on the portal server for trusted AI usage settlement;
all interactive authorization and reads still use the signed-in user's client.
An unsettled AI reservation stays charged until UTC midnight. Unknown provider
usage is not converted into a zero-token call. Currency prices and GPU telemetry
are not configured by this release.

For signed exports, set `AEGIS_AUDIT_SIGNING_KEY_BASE64` to the base64-encoded PEM
of an Ed25519 private key in the deployment secret store. Publish the matching
public key separately. `docs/audit-public-key.pem` is the trusted key for this
workspace's locally configured signer; a key bundled inside an export alone does
not establish trust. Export signatures establish file integrity, not regulatory
compliance or immutable external retention.

The new worker hashes the deployed Rego bundle and binds reviewed proposals to
immutable artifact/policy receipts with expiry. It rechecks current controls,
identity separation and live ServiceNow approvals before proposal delivery and
at the execution gate. Cloud read-back is separate from pipeline success.
Historical proposals without receipts cannot use the new gate; submit a fresh
request. Approval time counts toward an access grant's absolute expiration.

Before enabling governed writes in production:

1. Review and merge the updated workflow and both gate scripts. Protect the main
   branch and the GitHub `production` environment with human approval.
2. Configure `AEGIS_GATE_URL` in repository variables and the worker's
   `AEGIS_GATE_AUDIENCE` / `AEGIS_PIPELINE_CALLER` environment variables. The
   caller is the existing pipeline service-account email.
3. Make the gate reachable from the runner while preserving Cloud Run IAM.
   The default worker's internal ingress is intentionally retained during this
   rollout; use an approved internal runner or deliberately change ingress and
   grant only the pipeline `roles/run.invoker`. Never enable unauthenticated access.
4. Check the approver group in ServiceNow. Approvers must be active members and
   differ from every requester. Exact-plan approval must finish within the
   seven-minute pipeline wait, or rerun to obtain a fresh plan and approval.
5. Set permitted capabilities, scope and duration, then enable governed writes.
   No deployment script should auto-merge a proposal or approve on a human's behalf.

The workflow applies the same saved Terraform binary after exact-plan approval.
Label plans bind bucket ownership, source hashes and metageneration; concurrent
metadata changes fail rather than overwrite labels. Execution pause blocks new
starts at the checkpoint, not an apply already in progress.

For MCP proposals set an externally reachable HTTPS `AEGIS_PORTAL_URL` on the
gateway. The client must send its own `X-Aegis-User-Token` and
`X-Aegis-Workspace`; Cloud Run IAM remains a separate gateway boundary. Localhost
portal addresses are not usable by the deployed gateway. Read tools operate
independently when this integration is not configured.

### Coverage boundaries

This release implements GCP governance. Azure execution, passive shadow-agent
sensors, automatic DLP/sandboxing, effective IAM inheritance graphs, GPU spend,
and cross-provider inference routing need additional connectors and deployment
infrastructure. Inventory changes compare observed metadata, not approved IaC.
Plan analysis is advisory static analysis, not a runtime simulation or an
outage/cost prediction. Missing connections are shown as unavailable.

### Verification

Run `npm run lint`, `npm run typecheck`, `node --test tests/*.test.mjs` and
`npx next build --webpack`. Run `python3 -m pytest -q` from the repository root.
`scripts/validate_governance_db.py` checks pending migrations and authorization
inside a transaction that is always rolled back; supply `SUPABASE_DB_URL` through
the environment. It requires an existing administrator and request as fixtures.

Reference format: [Terraform JSON plan](https://developer.hashicorp.com/terraform/internals/json-format).

### Hosted rollout status

The portal runs as the dedicated `aegis-portal` service account. Its only GCP
permissions are access to the three named server secrets. Browser-public values
are supplied during the image build; server secrets never enter the build archive
or image. `portal/.gcloudignore` restricts uploads to portal source. The runtime
image contains the Next standalone server, static files and public assets.

`portal/cloudbuild.yaml` accepts `_IMAGE`, `_SUPABASE_URL`,
`_SUPABASE_PUBLISHABLE_KEY` and `_SITE_URL` substitutions. Submit from the
repository root with `--ignore-file=portal/.gcloudignore`. Deployment uses port
8080, a small autoscaling limit, and Secret Manager references for
`SUPABASE_SECRET_KEY`, `NVIDIA_API_KEY` and
`AEGIS_AUDIT_SIGNING_KEY_BASE64`. Never pass these server secrets as build args.

The MCP gateway points to the hosted portal. Authenticated MCP tool discovery,
live bucket compliance reads and rejection of missing user credentials have
passed smoke checks. A generic live AI connectivity check also recorded actual
provider tokens through a budget reservation and trusted usage settlement.

The worker execution gate uses authenticated external ingress, approved for
GitHub-hosted CI. Cloud Run IAM grants invocation to the pipeline service account;
the application additionally checks its signed identity and exact audience.
Unauthenticated invocation remains disabled. Governed writes remain disabled
until the release is reviewed and merged and the gated workflow is verified.
The GitHub App has contents/PR write and Actions read permissions; it cannot
change workflow files, variables, branch protections or environment reviewers.
Repository administration uses a separate GitHub user login. Main requires
passing CI and a PR approval, and production deployments require manual review.

Billing is linked, but no billing export table is configured. A read-only Compute
Engine inventory found one stopped non-GPU VM; that is not GPU utilization
telemetry. Azure subscriptions and external GPU/agent telemetry endpoints must
be explicitly configured before those sources can be shown as connected.

Every apply job first runs `scripts/pipeline_plan_gate.py --smoke` with its
federated pipeline identity. This submits an empty, invalid plan and requires
the authenticated governance validation response, proving connectivity without
creating a ServiceNow change or authorizing any mutation.
