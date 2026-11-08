# Production pilot: evidence before claims

Use one dedicated empty GCP bucket and a real, nonprivileged test user. Public
access prevention and uniform bucket-level access must remain enabled. Do not put
production data in the bucket. The beneficiary must sign in and have an active
Aegis membership. A coordinator is not an approver.

## Preconditions

- Main-branch CI passes and the protected GitHub workflow has reached the gate
  through Workload Identity Federation. A smoke test proves connectivity only.
- Create a dedicated pilot service/entitlement: `roles/storage.objectUser`, one
  reviewed bucket, one-hour maximum, production. Do not widen an existing route.
- An administrator reviews capability availability and write controls. Enabling
  writes is a separate audited configuration action, not a side effect of setup.
- Independent ServiceNow approver and GitHub reviewer are available. Never approve
  for the beneficiary or reuse another person's session.
- Record the request IDs, artifact/manifest/plan fingerprints, commit, approval
  times, grant expiry and verification time in the signed request export.

## Acceptance matrix

| Scenario | Procedure | Evidence required |
| --- | --- | --- |
| Success | Beneficiary submits a one-hour scoped request; independent original approval, PR review/merge, production environment approval and exact-plan approval complete. | Authorized pre-apply checkpoint followed by GCP `verified` read-back. CI alone is insufficient. |
| Rejection | Submit a separate request and have the independent approver reject it. | Rejected approval, no delivery apply; GCP binding unchanged. |
| Expiry | Submit a fresh request and let its approval window expire. | Expired checkpoint denial and no mutation; submit a new request rather than extending a receipt. |
| Mismatch | After a verified pilot, enable read-only monitoring. Use a separately reviewed maintenance change to remove only the pilot binding. | New failed read-back, retained prior verified observation, mismatch audit event and reviewer inbox item. Do not modify unrelated IAM or disable the gate to induce a test. |
| Recovery | Prepare and independently approve a fresh recovery proposal, then read back again. | Original mismatch retained, later verified observation and `assurance.recovered` event. Recovery is not an automatic rollback. |

The mismatch maintenance change and recovery proposal require their own review.
Do not mutate production state merely to satisfy a checklist. If those changes
have not been approved, record the scenario as pending, not passed.

## Automated evidence

`python3 -m pytest -q` covers gate authorization, rejection, expiry, altered facts,
offboarding and exact IAM read-back. `scripts/validate_governance_db.py` exercises
migration/RLS, scheduling, failed-read isolation, mismatch and recovery in one
transaction that is always rolled back. These tests are not production pilots.

After the live pilot, export evidence from the request and record actual operator
minutes. Do not infer time saved from number of requests or AI token usage.
Remove test objects and retire the pilot route through reviewed cleanup. Retain
the evidence according to the organization's policy.
