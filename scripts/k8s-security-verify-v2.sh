#!/usr/bin/env bash
set -euo pipefail

NAMESPACE="${NAMESPACE:-cloud-agent-poc-v2}"
SANDBOX_SA="system:serviceaccount:${NAMESPACE}:cloud-agent-sandbox-manager"

log() {
  printf '[security] %s\n' "$*"
}

fail() {
  printf '[security] ERROR: %s\n' "$*" >&2
  exit 1
}

expect_can_i() {
  local expected="$1"
  shift
  local actual
  actual="$(kubectl -n "$NAMESPACE" auth can-i "$@" --as "$SANDBOX_SA" || true)"
  if [ "$actual" != "$expected" ]; then
    fail "expected auth can-i $* to be '$expected' for $SANDBOX_SA, got '$actual'"
  fi
  log "auth can-i $* => $actual"
}

expect_no_token() {
  local deployment="$1"
  log "checking deployment/$deployment has no mounted service account token"
  kubectl -n "$NAMESPACE" exec "deploy/$deployment" -- sh -c \
    'test ! -e /var/run/secrets/kubernetes.io/serviceaccount/token'
}

log "checking namespace $NAMESPACE"
kubectl get namespace "$NAMESPACE" >/dev/null

log "checking Sandbox Manager RBAC"
expect_can_i yes create pods
expect_can_i yes get pods
expect_can_i yes delete pods
expect_can_i yes get pods/log
expect_can_i no get secrets
expect_can_i no list secrets
expect_can_i no create pods --subresource=exec
expect_can_i no list configmaps

for deployment in \
  cloud-agent-brain \
  cloud-agent-web \
  cloud-agent-session \
  cloud-agent-github-broker
do
  expect_no_token "$deployment"
done

log "checking generated one-shot Sandbox tool pod contract"
kubectl -n "$NAMESPACE" exec deploy/cloud-agent-sandbox -- python -c "
from cloud_agent_poc.config import Settings
from cloud_agent_poc.sandbox_manager import KubernetesToolPodRunner
from cloud_agent_poc.sandbox_protocol import ToolExecutionRequest

settings = Settings.from_env()
runner = KubernetesToolPodRunner(settings)
request = ToolExecutionRequest(
    run_id='run_11111111111111111111111111111111',
    task_id='task_security_probe',
    task_attempt_id='taskattempt_security_probe',
    tool_call_id='toolcall_security_probe',
    tool_name='git_status',
    arguments={},
    workspace_path='/sandboxes/users/SecurityProbe/run_11111111111111111111111111111111',
)
manifest = runner._pod_manifest('sandbox-tool-security-probe', request)
spec = manifest['spec']
container = spec['containers'][0]
env_names = {item['name'] for item in container['env']}
assert spec['automountServiceAccountToken'] is False
assert spec['restartPolicy'] == 'Never'
assert spec['activeDeadlineSeconds'] == settings.sandbox_tool_timeout_seconds
assert 'GITHUB_TOKEN' not in env_names
assert 'ANTHROPIC_API_KEY' not in env_names
assert container['securityContext']['runAsNonRoot'] is True
assert container['securityContext']['runAsUser'] == 10001
assert container['securityContext']['allowPrivilegeEscalation'] is False
assert container['securityContext']['capabilities']['drop'] == ['ALL']
assert container['securityContext']['seccompProfile']['type'] == 'RuntimeDefault'
assert container['resources']['requests']['cpu'] == '100m'
assert container['resources']['limits']['memory'] == '512Mi'
assert spec['volumes'][1]['emptyDir'] == {}
assert container['volumeMounts'][0]['subPath'] == 'users/SecurityProbe/run_11111111111111111111111111111111'
print('one-shot sandbox pod contract ok')
"

log "v2 security verification passed"
