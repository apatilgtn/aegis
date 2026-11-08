# GCP change assurance

Aegis records three different facts: what was approved, what the execution gate
checked, and what GCP returned after the pipeline. A successful pipeline is not a
verified access grant.

## Reviewer workflow

Open a request to see its journey and four evidence panels:

1. **Approved facts**: requester and beneficiary, reviewed project/bucket and role,
   governance revision, grant expiry, approval deadline, and artifact/manifest/
   policy fingerprints. Hashes are under an expandable detail section.
2. **Execution checkpoint**: approved-versus-observed comparisons, original and
   exact-plan ServiceNow approvals, saved-plan and commit hashes, and checkpoint
   history. An authorized checkpoint is permission to proceed, not proof of apply.
3. **Native GCP context**: a user-requested, read-only Policy Analyzer query for the
   reviewed identity/role/resource. Unknown conditions, API errors, incomplete
   results and scope limits remain visible.
4. **Observed outcome**: requested label values or the precise conditional IAM
   binding read back from GCP using the approved identity/scope/expiry snapshot.
   Administrators can queue another read without
   replaying the apply. Other/inherited grants are outside this verification.

The signed request export includes receipt manifests, plan approvals, checkpoint
history and native assessments. Exports state their limits and truncation.
Checkpoint and native-assessment records are append-only. Plan approval context
cannot be edited after creation; its current ServiceNow state is rechecked live.

A changed identity, reviewed scope, source artifact, policy bundle/revision, grant
expiry, or expired approval blocks the execution checkpoint. Records without the
new manifest require a fresh proposal; an old approval is never backfilled.

## Native coverage

Policy Analyzer inspects current **allow-policy** context within the governed
project. Project resource selectors use the project ID in the full resource name,
for example `//cloudresourcemanager.googleapis.com/projects/my-project`. Query
results are advisory, bounded to 10 seconds and 50 retained binding summaries.
Omitted `fullyExplored` fields remain incomplete, never a clean result.

Ancestor policies outside the project, group expansion, IAM deny policies and
principal access boundaries are not assessed. Cloud Asset Inventory is eventually
consistent. No matching binding is not proof that an identity has no other access.

Google's historical Policy Simulator does not evaluate conditional IAM binding
changes. All current Aegis temporary grants use a condition, so their simulator
state is **unsupported**, not simulated or passed. No replay API is invoked and
no simulation-administrator role is needed for this capability. The UI links to
[Google's documented limitation](https://docs.cloud.google.com/policy-intelligence/docs/simulate-iam-policies).

## Permissions and deployment

Run the operator's read-only preflight:

```sh
python3 scripts/check_gcp_readiness.py --project YOUR_PROJECT_ID
```

The script reads Cloud Run configuration, service-level gate invocation policy,
and the worker's native-analysis permissions. It never creates roles, grants
permissions, opens approvals, changes billing, or enables writes. A failed
impersonation attempt is reported as unknown; the script never grants itself
impersonation rights. Its service IAM check does not assess inherited IAM.

Native analysis adds these project-bounded permissions to existing inventory and
IAM-metadata reads:

- `cloudasset.assets.analyzeIamPolicy`
- `cloudasset.assets.searchAllIamPolicies`
- `serviceusage.services.use`

`cloudasset.assets.searchAllResources` is already needed by inventory. Custom IAM
roles additionally require role-definition visibility. See Google's
[required permissions](https://docs.cloud.google.com/policy-intelligence/docs/analyze-iam-policies).
The deployment's `aegisIamAnalysis` custom role contains only the three additions;
it cannot change IAM policies or resources. Grant it to the existing worker
identity only on the governed project. No organization-wide permission is needed
for the supported project-scoped query.

Apply `20261108100000_change_assurance.sql` before updating the worker and portal.
The worker image must copy `native_analysis.py`. Database changes do not enable
writes or modify existing requests. The CLI/pipeline now attaches the project and
checkpoint kind to its requests. The authenticated `--smoke` call records only
connectivity health and deliberately returns a validation error without opening
an approval or authorizing a mutation. Use the existing protected pipeline to
verify the federated caller; configuration alone cannot establish that result.

`/setup` gives a short project/data/routes/CI/pilot checklist and a recovery table.
A pilot is complete only after an actual cloud-verification record exists.

## Recovery

| Evidence | Action |
| --- | --- |
| Missing manifest | Submit a fresh request after rollout. |
| Changed policy, identity or scope | Reassess and approve a new proposal. |
| Expired approval | Submit a fresh request; do not extend immutable receipts. |
| Partial/unavailable native query | Inspect stated coverage, APIs, permissions and quotas. |
| Pipeline success, verification unavailable | Queue a read-back check. Do not blindly rerun a mutation. |
| Verified mismatch | Inspect actual cloud state and prepare a separately reviewed recovery proposal. |
| Read job failed | Inspect Operations and retry an eligible read step after fixing its source. |

The release creates no automatic rollback, grants no end-user access and never
approves on a human's behalf. A grant condition's expiry does not establish that
other access paths are absent.

## Design preview

In development, `/auth/design-preview?mode=assurance` shows clearly marked sample
historical evidence. Add `&outcome=blocked` to inspect a rejected scope change.
Actions that collect or submit real evidence are unavailable in the sample.
The entire design-preview path returns 404 in production.
