#!/bin/bash
set -euo pipefail

celery -A code_mortis.jobs:celery worker -Q events,sync --concurrency=1 --loglevel=INFO --hostname=sync@%h &
sync_pid=$!
celery -A code_mortis.jobs:celery worker -Q code-graph --concurrency=1 --loglevel=INFO --hostname=graph@%h &
graph_pid=$!

stop_workers() {
  kill -TERM "$sync_pid" "$graph_pid" 2>/dev/null || true
  wait "$sync_pid" "$graph_pid" || true
}
trap stop_workers EXIT
trap 'exit 0' TERM INT
wait -n "$sync_pid" "$graph_pid"
