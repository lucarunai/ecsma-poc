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

log "checking Sandbox tool NetworkPolicy"
kubectl -n "$NAMESPACE" get networkpolicy sandbox-tool-default-deny >/dev/null
kubectl -n "$NAMESPACE" get networkpolicy sandbox-session-default-deny >/dev/null
network_policy_app="$(
  kubectl -n "$NAMESPACE" get networkpolicy sandbox-tool-default-deny \
    -o jsonpath='{.spec.podSelector.matchLabels.app}'
)"
if [ "$network_policy_app" != "cloud-agent-sandbox-tool" ]; then
  fail "sandbox-tool-default-deny selector expected app=cloud-agent-sandbox-tool, got '$network_policy_app'"
fi
network_policy_types="$(
  kubectl -n "$NAMESPACE" get networkpolicy sandbox-tool-default-deny \
    -o jsonpath='{.spec.policyTypes[*]}'
)"
if [[ " $network_policy_types " != *" Ingress "* ]]; then
  fail "sandbox-tool-default-deny must include Ingress policyType, got '$network_policy_types'"
fi
if [[ " $network_policy_types " != *" Egress "* ]]; then
  fail "sandbox-tool-default-deny must include Egress policyType, got '$network_policy_types'"
fi
log "sandbox-tool-default-deny policyTypes => $network_policy_types"
session_policy_app="$(
  kubectl -n "$NAMESPACE" get networkpolicy sandbox-session-default-deny \
    -o jsonpath='{.spec.podSelector.matchLabels.app}'
)"
if [ "$session_policy_app" != "cloud-agent-task-sandbox" ]; then
  fail "sandbox-session-default-deny selector expected app=cloud-agent-task-sandbox, got '$session_policy_app'"
fi
session_policy_ingress_from="$(
  kubectl -n "$NAMESPACE" get networkpolicy sandbox-session-default-deny \
    -o jsonpath='{.spec.ingress[0].from[0].podSelector.matchLabels.app}'
)"
if [ "$session_policy_ingress_from" != "cloud-agent-sandbox" ]; then
  fail "sandbox-session-default-deny must only allow ingress from cloud-agent-sandbox, got '$session_policy_ingress_from'"
fi
log "sandbox-session-default-deny allows manager ingress only"

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
from cloud_agent_poc.sandbox_manager import (
    KubernetesTaskSandboxSessionRunner,
    KubernetesToolPodRunner,
)
from cloud_agent_poc.sandbox_protocol import (
    SandboxSessionCreateRequest,
    ToolExecutionRequest,
)

settings = Settings.from_env()
runner = KubernetesToolPodRunner(settings)
request = ToolExecutionRequest(
    run_id='run_11111111111111111111111111111111',
    task_attempt_id='taskattempt_security_probe',
    tool_call_id='toolcall_security_probe',
    tool_name='git_status',
    args={},
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
assert container['resources']['requests']['ephemeral-storage'] == settings.sandbox_ephemeral_storage_request
assert container['resources']['limits']['memory'] == '512Mi'
assert container['resources']['limits']['ephemeral-storage'] == settings.sandbox_ephemeral_storage_limit
runtime_limits = runner._resource_limits()
assert runtime_limits['tool_output_bytes'] == settings.sandbox_tool_output_bytes_limit
assert runtime_limits['runtime_log_bytes'] == settings.sandbox_runtime_log_bytes_limit
assert runtime_limits['workspace_bytes'] == settings.sandbox_workspace_bytes_limit
assert runtime_limits['workspace_files'] == settings.sandbox_workspace_file_limit
assert spec['volumes'][1]['emptyDir'] == {}
assert container['volumeMounts'][0]['subPath'] == 'users/SecurityProbe/run_11111111111111111111111111111111'
print('one-shot sandbox pod contract ok')

session_runner = KubernetesTaskSandboxSessionRunner(settings)
session_manifest = session_runner._pod_manifest(
    'sandbox-task-security-probe',
    SandboxSessionCreateRequest(
        sandbox_session_id='sbxsess_11111111111111111111111111111111',
        run_id='run_11111111111111111111111111111111',
        task_id='task_security_probe',
        task_attempt_id='taskattempt_security_probe',
        workspace_path='/sandboxes/users/SecurityProbe/run_11111111111111111111111111111111',
    ),
)
session_spec = session_manifest['spec']
session_container = session_spec['containers'][0]
session_env_names = {item['name'] for item in session_container['env']}
assert session_manifest['metadata']['labels']['app'] == 'cloud-agent-task-sandbox'
assert session_spec['automountServiceAccountToken'] is False
assert session_spec['activeDeadlineSeconds'] == settings.sandbox_session_timeout_seconds
assert session_container['command'][:2] == ['uvicorn', 'cloud_agent_poc.sandbox_daemon:app']
assert 'GITHUB_TOKEN' not in session_env_names
assert 'ANTHROPIC_API_KEY' not in session_env_names
assert session_container['securityContext']['runAsNonRoot'] is True
assert session_container['securityContext']['allowPrivilegeEscalation'] is False
assert session_container['resources']['limits']['memory'] == '512Mi'
assert session_container['volumeMounts'][0]['subPath'] == 'users/SecurityProbe/run_11111111111111111111111111111111'
print('task sandbox session pod contract ok')
"

log "v2 security verification passed"
