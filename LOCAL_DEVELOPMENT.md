# Aegis local runtime

The normal runtime is the Cloud Run worker (`scripts/deploy_gcp.sh`). The local Docker stack runs the portal, OPA and a worker on your machine; Supabase stays the hosted identity, Postgres and Realtime control plane.

## Secrets (Infisical, dev environment)

- `SUPABASE_URL`, `SUPABASE_PUBLISHABLE_KEY`, `SUPABASE_SECRET_KEY`
- `GITHUB_REPO`, `GITHUB_APP_ID`, `GITHUB_APP_INSTALLATION_ID`, `GITHUB_APP_PRIVATE_KEY`
- `SN_INSTANCE_URL`, `SN_OAUTH_CLIENT_ID`, `SN_OAUTH_CLIENT_SECRET`, `SN_APPROVER_GROUP`
- `GCP_PROJECT_ID`, `NVIDIA_API_KEY`, `NVIDIA_MODEL`

The Supabase secret key is for the worker only; never prefix it with `NEXT_PUBLIC_`. The GitHub App needs contents and pull-request write and actions read — never merge or administration rights.

## Machine identity bootstrap

1. Use the `aegis` machine identity's **Universal Auth Client ID** (not the identity ID) and a client secret.
2. Save the client secret to `.secrets/infisical-client-secret` (excluded from Git and the Docker build context).
3. `INFISICAL_CLIENT_ID` defaults to the Universal Auth Client ID in `docker-compose.yml`; `INFISICAL_PROJECT_ID` is the project ID. Neither is secret.

The entrypoint exchanges the client secret for a short-lived token and injects secrets directly into the worker process; nothing is written to disk.

## GCP access for the local worker

On Cloud Run the worker signs in as `aegis-reader` automatically. A local worker has no GCP identity, so log search, label checks and audit export fail locally unless you provide Application Default Credentials (for example `gcloud auth application-default login --impersonate-service-account=aegis-reader@<project>.iam.gserviceaccount.com`). Run only one worker at a time against the same Supabase project.

## Start / verify / stop

```bash
docker compose up --build                       # portal :3000, OPA :8181, worker
docker compose run --rm opa test /policies      # policy tests
docker compose down
```

## Workflow lifecycle

A request creates an `evaluate_policy` job. Allowed access and label-fix requests move to `awaiting_approval` and a `servicenow_approval` job opens a change and polls its approvals; approval queues `create_github_proposal`, then `track_github_delivery` follows the merge and pipeline. Allowed investigations queue `search_logs`; the requester then raises (`create_servicenow_incident`) or closes the search. Only the Supabase service role can claim, heartbeat, complete or fail jobs.
