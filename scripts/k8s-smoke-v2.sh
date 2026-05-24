#!/usr/bin/env bash
set -euo pipefail

NAMESPACE="${NAMESPACE:-cloud-agent-poc-v2}"
WEB_URL="${WEB_URL:-http://127.0.0.1:18082}"
USER_ID="${USER_ID:-SmokeUser}"
RUN_ID="${RUN_ID:-$(printf 'run_%032x' "$(date +%s)")}"

log() {
  printf '[smoke] %s\n' "$*"
}

fail() {
  printf '[smoke] ERROR: %s\n' "$*" >&2
  exit 1
}

assert_equals() {
  local actual="$1"
  local expected="$2"
  local label="$3"
  if [ "$actual" != "$expected" ]; then
    fail "$label expected '$expected' but got '$actual'"
  fi
}

wait_deployment() {
  local deployment="$1"
  log "waiting for deployment/$deployment"
  kubectl -n "$NAMESPACE" rollout status "deploy/$deployment" --timeout=120s >/dev/null
}

log "checking namespace $NAMESPACE"
kubectl get namespace "$NAMESPACE" >/dev/null

for deployment in \
  cloud-agent-brain \
  cloud-agent-web \
  cloud-agent-session \
  cloud-agent-sandbox \
  cloud-agent-github-broker \
  postgres
do
  wait_deployment "$deployment"
done

brain_replicas="$(kubectl -n "$NAMESPACE" get deploy cloud-agent-brain -o jsonpath='{.spec.replicas}')"
assert_equals "$brain_replicas" "3" "cloud-agent-brain replicas"

for pvc in postgres-data sandbox-workspaces
do
  phase="$(kubectl -n "$NAMESPACE" get pvc "$pvc" -o jsonpath='{.status.phase}')"
  assert_equals "$phase" "Bound" "PVC $pvc phase"
done

log "checking web health at $WEB_URL"
curl -fsS "$WEB_URL/healthz" >/dev/null
curl -fsS "$WEB_URL/" | grep -q "Cloud Agent PoC"

log "creating user-scoped session through Web API"
session_json="$(
  curl -fsS \
    -X POST \
    -H "X-User-Id: $USER_ID" \
    "$WEB_URL/api/sessions"
)"
session_id="$(printf '%s' "$session_json" | python3 -c 'import json,sys; print(json.load(sys.stdin)["session_id"])')"
if [ -z "$session_id" ]; then
  fail "session API did not return a session_id"
fi
log "created session $session_id for $USER_ID"

log "checking sandbox manager UID and workspace permissions"
kubectl -n "$NAMESPACE" exec deploy/cloud-agent-sandbox -- sh -c \
  'test "$(id -u)" = "10001" && test -w /sandboxes/users'

log "checking Sandbox Manager workspace API with $RUN_ID"
kubectl -n "$NAMESPACE" exec deploy/cloud-agent-sandbox -- python -c "
import httpx
run_id = '$RUN_ID'
user_id = '$USER_ID'
expected_path = f'/sandboxes/users/{user_id}/{run_id}'
created = httpx.post(
    f'http://127.0.0.1:8000/internal/workspaces/{run_id}',
    json={'user_id': user_id},
    timeout=10,
)
print(created.status_code, created.text)
created.raise_for_status()
path = created.json()['path']
if path != expected_path:
    raise SystemExit(f'expected {expected_path}, got {path}')
deleted = httpx.delete(
    f'http://127.0.0.1:8000/internal/workspaces/{run_id}',
    params={'workspace_path': path},
    timeout=10,
)
print(deleted.status_code, deleted.text)
deleted.raise_for_status()
"

log "v2 smoke test passed"
