# Sandbox Layer 升级实际实现记录

本文档记录 `sandbox-layer-upgrade-plan.zh.md` 之后，Sandbox Layer 分阶段升级的实际落地情况。计划文档用于方案讨论；本文档只记录已经实现、验证过的内容。

## 1. 当前范围

当前已经开始执行 Phase 1：补齐 K8s Pod Sandbox 基础边界。

已完成：

- Phase 1A：Sandbox one-shot tool Pod NetworkPolicy default deny。
- Phase 1B：Sandbox one-shot tool Pod resource / output / workspace 基础治理。
- Phase 1C：Tool execution envelope 增加 runtime/profile/resource/workspace evidence。

尚未完成：

- pids limit / inode quota：这类更适合通过 runtime/node policy 或更强 sandbox runtime 落地，当前暂不在 PoC 代码里硬做。
- Phase 2：RuntimeClass 支持 gVisor / Kata，当前明确暂不实现，只保留在后续路线图。
- Phase 3：Sandbox Session Pool / per-task sandbox。
- Phase 4：Workspace backend 抽象。
- Phase 5：Firecracker / microVM Runner。
- Phase 6：Wasm Runner。

## 2. Phase 1A：NetworkPolicy Default Deny

### 2.1 目标

Phase 1A 只收紧 Sandbox one-shot tool Pod 的网络边界，不改变长期服务之间的通信链路。

目标是让动态创建的 Sandbox tool Pod 默认不能访问：

- namespace 内部 service。
- cluster 内部 service。
- cloud metadata endpoint。
- 外部网络。
- 任何 inbound traffic。

这一步解决的是 network isolation 缺口。它不同于之前已经完成的 credential isolation：

| 已有 credential isolation | 本次 network isolation |
| --- | --- |
| Sandbox Pod 没有 `GITHUB_TOKEN` | Sandbox Pod 默认不能随便连网络 |
| Sandbox Pod 没有 `ANTHROPIC_API_KEY` | Sandbox Pod 默认不能访问内部 service |
| Sandbox Pod 没有 service account token | Sandbox Pod 默认不能访问 metadata / internet |

### 2.2 实现方式

新增 v2 NetworkPolicy：

```text
k8s-v2/network-policy.yaml
```

核心内容：

```yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: sandbox-tool-default-deny
  namespace: cloud-agent-poc-v2
spec:
  podSelector:
    matchLabels:
      app: cloud-agent-sandbox-tool
  policyTypes:
    - Ingress
    - Egress
```

该 policy 只选择动态 one-shot Sandbox tool Pod：

```text
app=cloud-agent-sandbox-tool
```

不选择长期服务：

```text
cloud-agent-web
cloud-agent-brain
cloud-agent-session
cloud-agent-sandbox
cloud-agent-github-broker
postgres
```

因此不会打断 Web、Brain、Session、Sandbox Manager、GitHub Broker、Postgres 的主链路。

### 2.3 为什么只选 Sandbox Tool Pod

当前真正执行不可信 repo/test/generated code 的是 one-shot Sandbox tool Pod。长期服务运行的是平台代码，不应该执行用户 repo 里的任意代码。

因此 Phase 1A 的收口对象是：

```text
sandbox-tool-*
```

而不是整个 namespace。

后续如果要做到 namespace default deny，需要额外补 allow policies，显式允许：

- Web -> Session。
- Brain -> Session / Sandbox Manager / GitHub Broker。
- Sandbox Manager -> Kubernetes API。
- GitHub Broker -> GitHub。
- Session -> Postgres。

### 2.4 测试与验证

新增 manifest contract test：

```text
tests/test_k8s_security_manifests.py
```

覆盖：

- `k8s-v2/network-policy.yaml` 存在。
- kind 是 `NetworkPolicy`。
- name 是 `sandbox-tool-default-deny`。
- namespace 是 `cloud-agent-poc-v2`。
- selector 是 `app=cloud-agent-sandbox-tool`。
- `policyTypes` 包含 `Ingress` 和 `Egress`。
- 不包含 `ipBlock` / `namespaceSelector` 这类默认放行规则。

更新 runtime verification：

```text
scripts/k8s-security-verify-v2.sh
```

新增检查：

- `sandbox-tool-default-deny` NetworkPolicy 已部署。
- selector 指向 `app=cloud-agent-sandbox-tool`。
- policyTypes 包含 `Ingress` 和 `Egress`。

更新 artifact contract test：

```text
tests/test_testing_strategy_artifacts.py
```

确保 security verification script 持续检查 NetworkPolicy。

### 2.5 已执行验证

本地 contract tests：

```bash
PYTHONPATH=src python3 -m unittest tests.test_k8s_security_manifests tests.test_testing_strategy_artifacts -v
```

通过。

全量 tests：

```bash
PYTHONPATH=src python3 -m unittest discover -v
```

结果：

```text
92 tests OK
```

部署到 v2：

```bash
kubectl apply -f k8s-v2/network-policy.yaml
```

结果：

```text
networkpolicy.networking.k8s.io/sandbox-tool-default-deny created
```

v2 security verification：

```bash
./scripts/k8s-security-verify-v2.sh
```

结果：

```text
[security] sandbox-tool-default-deny policyTypes => Ingress Egress
[security] v2 security verification passed
```

v2 smoke verification：

```bash
./scripts/k8s-smoke-v2.sh
```

结果：

```text
[smoke] v2 smoke test passed
```

### 2.6 当前限制

这一步新增的是 Kubernetes NetworkPolicy object。实际网络拦截是否生效取决于集群 CNI 是否支持并执行 NetworkPolicy。

当前脚本验证了：

- policy object 存在。
- selector 正确。
- policyTypes 正确。
- 现有 v2 主链路未被破坏。

后续 Phase 1A 可以继续补 runtime-level enforcement drill：

- 创建带 `app=cloud-agent-sandbox-tool` label 的探测 Pod。
- 尝试访问 `cloud-agent-session`、`cloud-agent-github-broker`、`postgres`、metadata IP。
- 在支持 NetworkPolicy 的 CNI 上断言访问失败。

该检查在 Docker Desktop / 本地集群里可能受 CNI 能力限制，因此当前先作为后续增强项保留。

## 3. Phase 1B：Resource / Output / Workspace Governance

### 3.1 目标

Phase 1B 的目标是补齐 Sandbox one-shot tool Pod 的基础资源治理。此前 Pod 已经有 CPU、memory 和 timeout，但缺少本地临时存储限制、tool output cap、workspace usage check 和 symlink/hardlink 防护。

这一步先补：

- `ephemeral-storage` requests / limits。
- tool stdout/stderr output bytes cap。
- Kubernetes pod log `limitBytes` cap。
- workspace bytes / file count check。
- symlink / hardlink workspace file 防护。
- v2 ConfigMap 中的可配置默认值。
- manifest/runtime verification 检查。

暂不在本步实现：

- pids limit。
- inode quota。

原因是这两类限制更适合由 container runtime、node policy、RuntimeClass 或更强隔离运行时承载，不适合在当前 Python tool layer 里做成“看起来像限制”的弱模拟。

### 3.2 实现方式

新增配置：

```text
SANDBOX_EPHEMERAL_STORAGE_REQUEST=128Mi
SANDBOX_EPHEMERAL_STORAGE_LIMIT=1Gi
SANDBOX_TOOL_OUTPUT_BYTES_LIMIT=4000
SANDBOX_RUNTIME_LOG_BYTES_LIMIT=65536
SANDBOX_WORKSPACE_BYTES_LIMIT=104857600
SANDBOX_WORKSPACE_FILE_LIMIT=10000
```

配置位置：

```text
src/cloud_agent_poc/config.py
k8s-v2/configmap.yaml
```

One-shot Pod manifest 中的 resources 从：

```yaml
requests:
  cpu: 100m
  memory: 128Mi
limits:
  cpu: 500m
  memory: 512Mi
```

扩展为：

```yaml
requests:
  cpu: 100m
  memory: 128Mi
  ephemeral-storage: 128Mi
limits:
  cpu: 500m
  memory: 512Mi
  ephemeral-storage: 1Gi
```

实现位置：

```text
src/cloud_agent_poc/sandbox_manager.py
src/cloud_agent_poc/sandbox_runtime.py
src/cloud_agent_poc/sandbox_tools.py
```

具体逻辑：

- `sandbox_manager.py` 创建 one-shot Pod 时继续设置 CPU、memory、ephemeral-storage 和 timeout；读取 Pod logs 时使用 Kubernetes `limitBytes`。
- `sandbox_tools.py` 对 command stdout/stderr 做按字节截断，并返回 original bytes / truncated / limit evidence。
- `sandbox_tools.py` 在 write/edit 或 clone/checkout 后检查 workspace bytes 和 file count。
- `sandbox_tools.py` 对 read/write/edit 拒绝 symlink 和 hardlink 文件，glob/grep 跳过 symlink/hardlink 文件。
- `sandbox_runtime.py` 和 `sandbox_manager.py` 把这些限制写入 runtime resource evidence。

### 3.3 设计说明

`ephemeral-storage` 主要约束 Pod root filesystem、container writable layer、`emptyDir` 等临时存储。当前 workspace 本身仍然挂载在 `sandbox-workspaces` PVC 上，所以这一步不能替代 per-run workspace quota。

因此当前资源治理分成两层：

| 资源 | 当前状态 |
| --- | --- |
| CPU / memory | 已限制。 |
| Pod ephemeral storage | 本步已限制。 |
| Tool timeout | 已限制。 |
| Workspace PVC usage | 已增加 bytes / file count 检查。 |
| stdout/stderr output size | 已增加 bytes cap 和 truncation evidence。 |
| Kubernetes pod logs | 已增加 `limitBytes` 读取限制。 |
| symlink / hardlink | 已增加受控文件工具防护。 |
| pids / inode | 后续 runtime/node policy。 |

### 3.4 测试与验证

更新测试：

```text
tests/test_sandbox_runtime.py
tests/test_k8s_security_manifests.py
tests/test_testing_strategy_artifacts.py
```

覆盖：

- One-shot Pod resources 包含 `ephemeral-storage` request/limit。
- v2 ConfigMap 声明 `SANDBOX_EPHEMERAL_STORAGE_REQUEST` 和 `SANDBOX_EPHEMERAL_STORAGE_LIMIT`。
- v2 ConfigMap 声明 output/log/workspace limit。
- v2 security verification script 检查生成的 one-shot Pod manifest。
- command output 超过 cap 时会截断并记录 evidence。
- workspace 超过 bytes limit 时拒绝写入。
- symlink workspace file 被拒绝。

## 4. Phase 1C：Runtime/Profile/Resource/Workspace Evidence Envelope

### 4.1 目标

Phase 1C 的目标是让每次 Sandbox tool execution 不只返回成功/失败，还返回更完整的 runtime evidence。

这一步让 `ToolExecutionEnvelope.runtime` 增加可选字段：

```text
runtime_profile
isolation
runtime_class
network_policy
egress_policy
resource_limits
workspace
output
```

这样后续可以在 UI、audit、replay、testing drill 中看到：

- 本次 tool 用的是什么 runtime profile。
- 隔离级别是 process / container / 未来 gVisor/Kata/microVM。
- 是否受 NetworkPolicy / egress policy 约束。
- 资源限制是多少。
- workspace 当前有多少文件、多少 bytes。
- stdout/stderr/runtime log 大小。

### 4.2 实现方式

协议扩展：

```text
src/cloud_agent_poc/sandbox_protocol.py
```

`SandboxRuntimeMetadata` 增加可选字段。字段都是 backward-compatible 的 optional/default 字段，不改变已有 envelope schema version。

Direct runtime：

```text
src/cloud_agent_poc/sandbox_runtime.py
```

执行后写入：

```text
type=direct
runtime_profile=direct
isolation=process
resource_limits.cpu=500m
resource_limits.memory=512Mi
resource_limits.ephemeral_storage=1Gi
resource_limits.timeout_seconds=180
workspace.path
workspace.file_count
workspace.bytes
output.stdout_bytes / stderr_bytes
```

Kubernetes one-shot runtime：

```text
src/cloud_agent_poc/sandbox_manager.py
```

Sandbox Manager 读取 Pod logs 并解析 runtime envelope 后，会补充/覆盖：

```text
type=sandbox_pod
runtime_profile=kubernetes_container
isolation=container
pod_name
pod_phase
exit_code
duration_ms
network_policy=sandbox-tool-default-deny
egress_policy=default-deny
resource_limits
output.runtime_log_bytes
```

如果 Pod lookup/log/runtime 失败，failure envelope 也会带上同样的 runtime/profile/resource/workspace evidence。

同时修正了 Pod name 派生逻辑：`execution_id` 可能来自外部输入或测试场景，不一定只包含 hex 字符。Sandbox Manager 现在会把 `execution_id` 规范化为 Kubernetes DNS-safe pod name，避免 `_` 等非法字符导致 Pod 创建失败。

新增配置：

```text
SANDBOX_NETWORK_POLICY_NAME=sandbox-tool-default-deny
SANDBOX_EGRESS_POLICY=default-deny
```

配置位置：

```text
src/cloud_agent_poc/config.py
k8s-v2/configmap.yaml
```

### 4.3 示例 Envelope 片段

Direct mode 示例：

```json
{
  "runtime": {
    "type": "direct",
    "runtime_profile": "direct",
    "isolation": "process",
    "resource_limits": {
      "cpu": "500m",
      "memory": "512Mi",
      "ephemeral_storage": "1Gi",
      "timeout_seconds": 180
    },
    "workspace": {
      "path": "/tmp/run_xxx",
      "file_count": 1,
      "bytes": 15
    }
  }
}
```

Kubernetes one-shot Pod 示例：

```json
{
  "runtime": {
    "type": "sandbox_pod",
    "runtime_profile": "kubernetes_container",
    "isolation": "container",
    "pod_name": "sandbox-tool-...",
    "pod_phase": "Succeeded",
    "exit_code": 0,
    "network_policy": "sandbox-tool-default-deny",
    "egress_policy": "default-deny",
    "resource_limits": {
      "cpu": "500m",
      "memory": "512Mi",
      "ephemeral_storage": "1Gi",
      "timeout_seconds": 180
    },
    "output": {
      "runtime_log_bytes": 1200,
      "stdout_bytes": 20,
      "stderr_bytes": 0
    }
  }
}
```

### 4.4 测试与验证

更新测试：

```text
tests/test_sandbox_runtime.py
```

覆盖：

- Direct mode envelope 带 `runtime_profile=direct`。
- Direct mode envelope 带 `isolation=process`。
- Direct mode envelope 带 resource limits。
- Direct mode envelope 带 workspace file count / bytes。
- Kubernetes failure envelope 带 `runtime_profile=kubernetes_container`。
- Kubernetes failure envelope 带 `isolation=container`。
- Kubernetes failure envelope 带 network / egress policy。
- Kubernetes failure envelope 带 resource limits 和 workspace evidence。

更新 runtime verification：

```text
scripts/k8s-security-verify-v2.sh
```

覆盖：

- 生成的 one-shot Pod manifest 带 `ephemeral-storage` request/limit。

## 5. 当前验证结果

Phase 1A / 1B / 1C 已执行本地测试：

```bash
python3 -m py_compile src/cloud_agent_poc/sandbox_manager.py src/cloud_agent_poc/sandbox_runtime.py src/cloud_agent_poc/sandbox_protocol.py src/cloud_agent_poc/config.py
PYTHONPATH=src python3 -m unittest tests.test_sandbox_runtime -v
PYTHONPATH=src python3 -m unittest tests.test_k8s_security_manifests tests.test_testing_strategy_artifacts -v
PYTHONPATH=src python3 -m unittest discover -v
```

结果：

```text
97 tests OK
```

已部署到 v2：

```bash
docker build -t cloud-agent-poc-v2:local .
kubectl apply -f k8s-v2/configmap.yaml -f k8s-v2/network-policy.yaml
kubectl -n cloud-agent-poc-v2 rollout restart deploy/cloud-agent-sandbox
kubectl -n cloud-agent-poc-v2 rollout status deploy/cloud-agent-sandbox --timeout=60s
```

结果：

```text
deployment "cloud-agent-sandbox" successfully rolled out
```

v2 runtime verification：

```bash
./scripts/k8s-security-verify-v2.sh
./scripts/k8s-smoke-v2.sh
```

结果：

```text
[security] v2 security verification passed
[smoke] v2 smoke test passed
```

额外执行了 one-shot runtime evidence probe，验证 Sandbox Manager 创建真实 one-shot Pod 执行 `write_workspace_file` 后，返回的 envelope 包含：

```json
{
  "type": "sandbox_pod",
  "runtime_profile": "kubernetes_container",
  "isolation": "container",
  "pod_name": "sandbox-tool-runtime-evidence",
  "pod_phase": "Succeeded",
  "exit_code": 0,
  "network_policy": "sandbox-tool-default-deny",
  "egress_policy": "default-deny",
  "resource_limits": {
    "cpu": "500m",
    "memory": "512Mi",
    "ephemeral_storage": "1Gi",
    "timeout_seconds": 180,
    "tool_output_bytes": 4000,
    "runtime_log_bytes": 65536,
    "workspace_bytes": 104857600,
    "workspace_files": 10000
  },
  "workspace": {
    "file_count": 1,
    "bytes": 15
  }
}
```

因此当前实际实现范围停在 Phase 1A / 1B / 1C。Phase 2 RuntimeClass / gVisor / Kata 后续再讨论，不进入本次改动。
