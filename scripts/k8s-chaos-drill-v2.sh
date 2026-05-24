#!/usr/bin/env bash
set -euo pipefail

NAMESPACE="${NAMESPACE:-cloud-agent-poc-v2}"
DATABASE="${DATABASE:-ecsma_poc}"
WEB_URL="${WEB_URL:-http://127.0.0.1:18082}"
RUN_ID="${RUN_ID:-}"
OWNER_USER="${OWNER_USER:-Luca}"
OTHER_USER="${OTHER_USER:-Josephine}"
DRILL="${1:-}"

log() {
  printf '[chaos] %s\n' "$*"
}

fail() {
  printf '[chaos] ERROR: %s\n' "$*" >&2
  exit 1
}

usage() {
  cat <<'USAGE'
Usage:
  RUN_ID=run_xxx ./scripts/k8s-chaos-drill-v2.sh brain-crash
  RUN_ID=run_xxx ./scripts/k8s-chaos-drill-v2.sh sandbox-pod-crash
  RUN_ID=run_xxx OWNER_USER=Luca OTHER_USER=Josephine ./scripts/k8s-chaos-drill-v2.sh ownership

Notes:
  - brain-crash expects a running run that has been claimed by a Brain worker.
  - sandbox-pod-crash expects an active one-shot sandbox tool pod for the run.
  - ownership can run against any existing run owned by OWNER_USER.
USAGE
}

require_run_id() {
  if ! [[ "$RUN_ID" =~ ^run_[a-f0-9]{32}$ ]]; then
    usage
    fail "RUN_ID must be set to a valid run id"
  fi
}

sql_scalar() {
  local query="$1"
  kubectl -n "$NAMESPACE" exec deploy/postgres -- \
    psql -U postgres -d "$DATABASE" -Atq -c "$query" | tr -d '[:space:]'
}

wait_until_sql_gt() {
  local query="$1"
  local threshold="$2"
  local label="$3"
  local timeout_seconds="${4:-120}"
  local deadline=$((SECONDS + timeout_seconds))
  local value
  while [ "$SECONDS" -lt "$deadline" ]; do
    value="$(sql_scalar "$query")"
    if [ "${value:-0}" -gt "$threshold" ]; then
      log "$label observed ($value > $threshold)"
      return 0
    fi
    sleep 2
  done
  fail "timed out waiting for $label"
}

wait_until_sql_nonempty_changed() {
  local query="$1"
  local previous="$2"
  local label="$3"
  local timeout_seconds="${4:-120}"
  local deadline=$((SECONDS + timeout_seconds))
  local value
  while [ "$SECONDS" -lt "$deadline" ]; do
    value="$(sql_scalar "$query")"
    if [ -n "$value" ] && [ "$value" != "$previous" ]; then
      log "$label observed ($value)"
      return 0
    fi
    sleep 2
  done
  fail "timed out waiting for $label"
}

http_status() {
  local method="$1"
  local user="$2"
  local url="$3"
  curl -sS -o /dev/null -w '%{http_code}' \
    -X "$method" \
    -H "X-User-Id: $user" \
    "$url"
}

brain_crash_drill() {
  require_run_id
  local status claimed_by brain_pod previous_expired_count new_claimed_by
  status="$(sql_scalar "select status from runs where id = '$RUN_ID';")"
  if [ "$status" != "running" ]; then
    fail "brain-crash drill requires run status 'running', got '$status'"
  fi

  claimed_by="$(sql_scalar "select coalesce(claimed_by, '') from runs where id = '$RUN_ID';")"
  if [ -z "$claimed_by" ]; then
    fail "run is running but claimed_by is empty"
  fi

  brain_pod="${claimed_by%-*}"
  previous_expired_count="$(sql_scalar "select count(*) from session_events where run_id = '$RUN_ID' and event_type = 'run.lease.expired';")"

  log "deleting claimed Brain pod $brain_pod for $RUN_ID"
  kubectl -n "$NAMESPACE" delete pod "$brain_pod" --wait=false >/dev/null

  wait_until_sql_gt \
    "select count(*) from session_events where run_id = '$RUN_ID' and event_type = 'run.lease.expired';" \
    "$previous_expired_count" \
    "run.lease.expired event"

  wait_until_sql_nonempty_changed \
    "select coalesce(claimed_by, '') from runs where id = '$RUN_ID';" \
    "$claimed_by" \
    "new Brain claim"

  new_claimed_by="$(sql_scalar "select coalesce(claimed_by, '') from runs where id = '$RUN_ID';")"
  log "Brain crash drill passed; old claim=$claimed_by new claim=$new_claimed_by"
}

sandbox_pod_crash_drill() {
  require_run_id
  local pod previous_failed_count
  pod="$(
    kubectl -n "$NAMESPACE" get pods \
      -l "app=cloud-agent-sandbox-tool,cloud-agent-run-id=$RUN_ID" \
      -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true
  )"
  if [ -z "$pod" ]; then
    fail "no active one-shot sandbox tool pod found for $RUN_ID"
  fi

  previous_failed_count="$(sql_scalar "select count(*) from tool_calls where run_id = '$RUN_ID' and status in ('failed', 'orphaned');")"
  log "deleting sandbox tool pod $pod for $RUN_ID"
  kubectl -n "$NAMESPACE" delete pod "$pod" --wait=false >/dev/null

  wait_until_sql_gt \
    "select count(*) from tool_calls where run_id = '$RUN_ID' and status in ('failed', 'orphaned');" \
    "$previous_failed_count" \
    "failed or orphaned tool call"

  log "Sandbox pod crash drill passed"
}

ownership_drill() {
  require_run_id
  local owner_get other_get other_replay other_wake

  owner_get="$(http_status GET "$OWNER_USER" "$WEB_URL/api/runs/$RUN_ID")"
  if [ "$owner_get" != "200" ]; then
    fail "owner $OWNER_USER should read $RUN_ID with HTTP 200, got $owner_get"
  fi

  other_get="$(http_status GET "$OTHER_USER" "$WEB_URL/api/runs/$RUN_ID")"
  other_replay="$(http_status GET "$OTHER_USER" "$WEB_URL/api/runs/$RUN_ID/replay")"
  other_wake="$(http_status POST "$OTHER_USER" "$WEB_URL/api/runs/$RUN_ID/wake")"

  if [ "$other_get" != "404" ]; then
    fail "other user read expected HTTP 404, got $other_get"
  fi
  if [ "$other_replay" != "404" ]; then
    fail "other user replay expected HTTP 404, got $other_replay"
  fi
  if [ "$other_wake" != "404" ]; then
    fail "other user wake expected HTTP 404, got $other_wake"
  fi

  log "Ownership drill passed for owner=$OWNER_USER other=$OTHER_USER run=$RUN_ID"
}

case "$DRILL" in
  brain-crash)
    brain_crash_drill
    ;;
  sandbox-pod-crash)
    sandbox_pod_crash_drill
    ;;
  ownership)
    ownership_drill
    ;;
  *)
    usage
    exit 2
    ;;
esac
