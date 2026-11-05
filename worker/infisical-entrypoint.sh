#!/bin/sh
set -eu

: "${INFISICAL_CLIENT_ID:?INFISICAL_CLIENT_ID is required}"
: "${INFISICAL_PROJECT_ID:?INFISICAL_PROJECT_ID is required}"

infisical_api_url="${INFISICAL_API_URL:-https://app.infisical.com}"
infisical_environment="${INFISICAL_ENVIRONMENT:-dev}"
infisical_secret_path="${INFISICAL_SECRET_PATH:-/}"
client_secret_file="${INFISICAL_CLIENT_SECRET_FILE:-/run/secrets/infisical_client_secret}"

if [ ! -r "$client_secret_file" ]; then
  echo "Infisical client-secret file is missing or unreadable" >&2
  exit 1
fi

client_secret="$(tr -d '\r\n' < "$client_secret_file")"
if [ -z "$client_secret" ]; then
  echo "Infisical client-secret file is empty" >&2
  exit 1
fi

infisical_token="$(
  infisical login \
    --method=universal-auth \
    --client-id="$INFISICAL_CLIENT_ID" \
    --client-secret="$client_secret" \
    --domain="$infisical_api_url" \
    --silent \
    --plain
)"
unset client_secret INFISICAL_CLIENT_ID INFISICAL_CLIENT_SECRET INFISICAL_CLIENT_SECRET_FILE

if [ -z "$infisical_token" ]; then
  echo "Infisical Universal Auth did not return an access token" >&2
  exit 1
fi

export INFISICAL_TOKEN="$infisical_token"
unset infisical_token

exec infisical run \
  --domain="$infisical_api_url" \
  --env="$infisical_environment" \
  --projectId="$INFISICAL_PROJECT_ID" \
  --path="$infisical_secret_path" \
  --silent \
  --telemetry=false \
  -- sh -c 'unset INFISICAL_TOKEN; exec "$@"' sh "$@"
