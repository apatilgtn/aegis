# P0 completion evidence

Verified on 27 September 2026 (Australia/Sydney).

## Release gates

- Supabase Auth supplies requester identity; action creation binds `requester_id` and `beneficiary_id` to the authenticated user through RLS.
- Database separation-of-duties enforcement rejects requester self-approval.
- A two-user verification completed requester submission, OPA evaluation, independent approval, audit recording, and governed GitHub proposal creation. Evidence PR: <https://github.com/apatilgtn/aegis/pull/25>. The verifier never merges.
- The legacy agents expose no application-side merge operation.
- Terraform renderers use encoded values, and the tag-remediation module reads an existing bucket instead of creating or destroying one.
- The legacy Streamlit UI contains no `unsafe_allow_html` path.
- ServiceNow OAuth client credentials and the scoped Aegis integration API were verified from the local container boundary. Username/password and direct Table API calls are not part of the runtime integration.
- Infisical injects worker and integration secrets at process start; application secrets are not exported to disk.
- The temporary GCP bootstrap account has no project IAM binding and no user-managed key.
- Dependency locks and GitHub Actions workflows cover Python, Next.js, OPA, Docker Compose, and Terraform.

## Verification results

| Gate | Result |
| --- | --- |
| Python dependency integrity | Passed |
| Python tests | 81 passed |
| OPA tests | 13 passed |
| Portal lint, typecheck, production build | Passed |
| Terraform formatting | Passed |
| Access-grants Terraform validation | Passed |
| Tag-remediations Terraform validation | Passed |
| Two-user live workflow | Passed |
| Self-approval attempt | Rejected (HTTP 400) |
| Audit evidence | `created`, `policy.allowed`, `approved`, `github.proposal_created` |
| Final workflow state | `pr_open` |

## Repeatable live check

Run `scripts/p0_e2e.py` through the worker container. It creates two synthetic confirmed users, grants temporary tenant memberships, proves self-approval is rejected, obtains an independent decision, waits for a real GitHub pull request, verifies the audit events, removes both memberships, and globally signs out both sessions. It does not merge the pull request.

P1 begins with durable production operations: reconciliation and webhooks, access expiry/revocation, richer RBAC/ABAC, tenant isolation hardening, telemetry/redaction, and enterprise-grade delivery controls.
