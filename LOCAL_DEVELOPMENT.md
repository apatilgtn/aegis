# Aegis local runtime

The local stack runs the portal, OPA and a durable workflow worker in Docker. Supabase remains the free hosted identity, Postgres and Realtime control plane.

## Secrets

Create an Infisical project and add these development secrets:

- `SUPABASE_URL`
- `SUPABASE_PUBLISHABLE_KEY`
- `SUPABASE_SECRET_KEY`
- `GITHUB_REPO`
- `GITHUB_APP_ID`
- `GITHUB_APP_INSTALLATION_ID`
- `GITHUB_APP_PRIVATE_KEY`

Use a Supabase secret key only in the worker. It must never be prefixed with `NEXT_PUBLIC_` or exposed to the browser.
Give the GitHub App repository contents and pull-request write access only. Do not grant merge administration; protected branch reviewers perform the merge in GitHub.

## Machine identity bootstrap

The worker image contains a pinned Infisical CLI and fetches its application secrets directly at startup. Only the Universal Auth bootstrap credential remains on the local host.

1. Create a project-scoped machine identity with the `Viewer` role and Universal Auth.
2. Put its client secret in `.secrets/infisical-client-secret`. This directory is excluded from Git and the Docker build context.
3. Set `INFISICAL_CLIENT_ID` and `INFISICAL_PROJECT_ID` in your shell or a local Compose environment file. These identifiers are not secrets.

Docker mounts the bootstrap credential read-only at `/run/secrets`. The entrypoint exchanges it for a short-lived token, clears the Universal Auth credential from the environment, and uses `infisical run` to inject application secrets directly into the worker process. No application-secret export is written to disk.

## Start

```powershell
docker compose up --build
```

OPA is available at `http://localhost:8181`; the portal is at `http://localhost:3000`. The worker polls atomically leased jobs, heartbeats active leases and applies exponential retries. It does not hold cloud credentials and does not merge pull requests.

## Workflow lifecycle

Submitting an access request creates an idempotent `evaluate_policy` job. The worker evaluates it through OPA and moves an allowed request to `awaiting_approval`. An independent approver can approve or reject it; self-approval is blocked in the database. Approval moves the request to `proposal_pending` and queues an idempotent `create_github_proposal` job. That job remains unclaimed until the GitHub App proposal handler is enabled.

Only the Supabase server role can claim, heartbeat, complete or fail jobs. Browser clients observe requests, approvals and workflow events through tenant-scoped RLS and Realtime.

## Verify policy

```powershell
docker compose run --rm opa test /policies
```

## Stop

```powershell
docker compose down
```
