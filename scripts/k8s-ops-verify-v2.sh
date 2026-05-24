#!/usr/bin/env bash
set -euo pipefail

NAMESPACE="${NAMESPACE:-cloud-agent-poc-v2}"
WEB_URL="${WEB_URL:-http://127.0.0.1:18082}"
USER_ID="${USER_ID:-Luca}"
WINDOW_HOURS="${WINDOW_HOURS:-24}"

log() {
  printf '[ops] %s\n' "$*"
}

log "checking namespace $NAMESPACE"
kubectl get namespace "$NAMESPACE" >/dev/null

log "waiting for web and session deployments"
kubectl -n "$NAMESPACE" rollout status deploy/cloud-agent-web --timeout=120s >/dev/null
kubectl -n "$NAMESPACE" rollout status deploy/cloud-agent-session --timeout=120s >/dev/null

log "checking independent ops dashboard at $WEB_URL/ops"
ops_html="$(curl -fsS "$WEB_URL/ops")"
printf '%s' "$ops_html" | grep -q "Cloud Agent Ops"
printf '%s' "$ops_html" | grep -q "/api/ops/metrics"
printf '%s' "$ops_html" | grep -q "/api/ops/alerts"
printf '%s' "$ops_html" | grep -q "support-bundle"

log "checking ops metrics contract"
metrics_json="$(
  curl -fsS \
    -H "X-User-Id: $USER_ID" \
    "$WEB_URL/api/ops/metrics?window_hours=$WINDOW_HOURS"
)"
printf '%s' "$metrics_json" | python3 -c "
import json, sys
data = json.load(sys.stdin)
assert data['schema_version'] == 'ops_metric_snapshot.v1'
assert data['user_id']
assert 'run_counts' in data
assert 'replay_summary' in data
assert 'recent_runs' in data
print('metrics contract ok')
"

log "checking ops alerts contract"
alerts_json="$(
  curl -fsS \
    -H "X-User-Id: $USER_ID" \
    "$WEB_URL/api/ops/alerts?window_hours=$WINDOW_HOURS"
)"
printf '%s' "$alerts_json" | python3 -c "
import json, sys
data = json.load(sys.stdin)
assert data['schema_version'] == 'ops_alerts.v1'
assert data['status'] in {'healthy', 'attention_required'}
assert isinstance(data['alerts'], list)
print('alerts contract ok')
"

latest_run="$(
  kubectl -n "$NAMESPACE" exec deploy/postgres -- psql \
    -U postgres \
    -d ecsma_poc \
    -t \
    -A \
    -c "SELECT user_id || ' ' || id FROM runs ORDER BY created_at DESC LIMIT 1;"
)"

if [ -n "$latest_run" ]; then
  latest_user="${latest_run%% *}"
  latest_run_id="${latest_run#* }"
  log "checking run ops contracts for $latest_user / $latest_run_id"
  summary_json="$(
    curl -fsS \
      -H "X-User-Id: $latest_user" \
      "$WEB_URL/api/runs/$latest_run_id/ops-summary"
  )"
  printf '%s' "$summary_json" | python3 -c "
import json, sys
data = json.load(sys.stdin)
assert data['schema_version'] == 'ops_run_summary.v1'
assert data['run_id'] == '$latest_run_id'
assert 'failure_summary' in data
print('run summary contract ok')
"
  bundle_json="$(
    curl -fsS \
      -H "X-User-Id: $latest_user" \
      "$WEB_URL/api/runs/$latest_run_id/support-bundle?visibility=customer"
  )"
  printf '%s' "$bundle_json" | python3 -c "
import json, sys
data = json.load(sys.stdin)
assert data['schema_version'] == 'support_bundle.v1'
assert data['visibility'] == 'customer'
assert 'triage' in data
assert 'redaction' in data
print('support bundle contract ok')
"
else
  log "no run found; skipped run-level ops contract checks"
fi

log "v2 ops verification passed"
