# Enterprise readiness evidence

This release adds controls and validation tools. It does not establish enterprise
certification, a completed disaster-recovery drill, or a customer SLA.

## Identity lifecycle

`/people` lists workspace membership for administrators/auditors. Administrators
can suspend another active member. Self-suspension is blocked. Membership is
checked on UI/API operations and again for requester and beneficiary at execution.
Suspension is audited and immediately blocks further workspace access as RLS and
API checks are evaluated. Previously issued data and in-flight operations cannot
be recalled. Suspend the upstream identity and separately revoke existing GCP
permissions/sessions. Automatic SCIM provisioning is not implemented.

## Continuous assurance

Monitoring is explicitly enabled on a delivered request with an immutable manifest
and verified baseline. The worker polls every configured interval (minimum five
minutes); at most 20 due monitors are queued per scheduling pass. Active jobs are
not duplicated. Reads run while cloud writes are disabled. Every new verification
is retained; mismatch, unavailable, and recovery are distinct audit/workflow events.
Expiry stops grant monitoring without claiming all other access was revoked.

Coverage is the exact requested IAM binding or labels, not inherited permissions,
other grants, network/data-plane activity or application health. Changes between
polls may be missed. Scheduler health and read completion are separate facts.

## Operational monitoring

Operator database tools require Python with `psycopg2` installed (the same dependency as the database validator).

Run `scripts/check_operational_health.py` with `SUPABASE_DB_URL` from a managed
secret. It uses read-only transactions and exits nonzero for overdue leases,
old queued jobs, recent terminal failures, overdue monitors, mismatches or stale
inventory. Run it from a trusted scheduler and wire nonzero exits to an existing
on-call channel; this release does not configure a notification destination.
Use external HTTPS uptime monitoring for portal availability. Confirm receipt of
a test alert; a dashboard or log entry alone does not prove alert delivery.

## Isolated restore drill

1. Pause writers through the maintenance process, including worker processing and
   interactive mutations. Capture a backup checkpoint and fingerprint manifest
   from the same quiescent data state. A later manifest cannot validate an earlier
   backup. Keep the manifest private.
2. Capture fingerprints using
   `python3 scripts/restore_drill.py capture --manifest /private/tmp/aegis-backup-manifest.json`.
   The source connection comes from `SUPABASE_DB_URL`; the file contains counts and
   hashes, not row contents or credentials.
3. Restore the backup into a **different isolated Supabase project**, with worker,
   webhooks, emails and cloud integrations disabled. Disable cloud writes before
   backup so the restored configuration also starts disabled.
4. Set `AEGIS_RESTORE_DB_URL` using the secret manager. Run
   `python3 scripts/restore_drill.py verify --manifest /private/tmp/aegis-backup-manifest.json`.
   The tool refuses a matching source identity and never restores or writes a DB.
5. Run rollback authorization checks against the isolated target. Separately test
   sign-in, Secret Manager references, evidence signing, portal startup and API
   denial. Do not replay production jobs or call real mutation endpoints.
6. Record backup time, restore start/end, recovered checkpoint, RPO/RTO, test
   results and incident owner. Delete isolated personal data under retention rules.

Fingerprints validate selected governance tables and RLS flags, not the whole
application. A matching hash report is one part of recovery evidence. A real
isolated restore target is required before claiming the drill passed.

## Security review scope

Reviewed boundaries: tenant RLS and service-only writes; coordinator versus
approver separation; identity suspension at execution; immutable observations;
read-job lease ownership and deduplication; read failure versus delivered outcome;
source identity versus restore target; and evidence export truncation.

Automated authorization/regression checks exercise those boundaries. Independent
penetration testing, external certification, HA/failover, alert delivery and
organization-wide identity revocation remain unverified. Approval evidence is
append-only within the database boundary, not independently WORM-protected.

## Value measurement

`/value` uses a rolling 30-day window, sample counts and medians. Approval latency
is submission to first recorded ServiceNow approval. Verification latency is
pipeline completion to first retained verified read-back afterward. Denied
attempts and distinct requests with stale facts are separate measures. Recorded
operator minutes are self-reported work, not savings. Time saved remains unknown
until a validated manual-process baseline exists.

## Automated local logical restore

With explicit approval to copy the governance data to the operator's machine,
run `python3 -m scripts.local_restore_drill --report /private/tmp/aegis-restore-report.json`.
Use an official PostgreSQL 17 image; a digest-pinned Google Docker Hub mirror is
also supported through `--image`. Docker and `psycopg2` are required.

The runner exports a consistent read-only snapshot and fingerprints the same
snapshot. It restores an explicit allowlist of 29 Aegis tables and their functions
into a disposable Docker database, verifies offline networking/no published ports,
compares every selected table, and checks nonmember RLS denial plus administrator
inbox/metrics access. Only referenced auth identity IDs are restored; profile and
authentication schemas are metadata-only. Unrelated applications, auth credentials,
sessions and vaults are excluded. Temporary backup files and containers are removed
before a successful report is emitted. The private report contains counts, hashes,
timings and checks; do not commit it to a public repository.

This is a logical governance-data restore drill. It does not establish managed
Supabase PITR recovery, login restoration, application/secret failover, or an RTO
service commitment. Those require a separate full-environment exercise.
