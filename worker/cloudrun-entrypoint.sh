#!/bin/sh
# Start OPA on localhost only, wait until it answers, then run the worker.
set -eu
opa run --server --addr=127.0.0.1:8181 --log-level=error /policies &
i=0
until python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8181/health',timeout=1)" 2>/dev/null; do
  i=$((i+1)); [ "$i" -gt 50 ] && echo "OPA did not start" >&2 && exit 1; sleep 0.2
done
exec python /app/worker.py
