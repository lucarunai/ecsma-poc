# Sandbox Layer 升级方案

本文档只讨论 Sandbox Layer。Session Layer、Brain Layer、Human Approval、Replay Audit 等能力作为上下文存在，但不在本文档展开。目标是基于当前 PoC 的 Sandbox 实现，参考商业 sandbox / serverless / agent runtime 的成熟方案，梳理当前已经做到哪里、还缺哪些设计维度，以及后续如何分阶段演进。

## 1. 当前 Sandbox Layer 设计分析

当前系统的 Sandbox Layer 是一个 K8s-native one-shot tool execution sandbox。

核心链路：

```text
Brain SDK tool call
  -> Sandbox Manager
    -> create one-shot Kubernetes Pod
      -> run cloud_agent_poc.sandbox_runtime
      -> mount user-scoped run workspace
      -> execute controlled workspace tool
      -> return ToolExecutionEnvelope
    -> delete one-shot Pod
```

核心组件：

| 组件 | 作用 |
| --- | --- |
| `sandbox_app.py` | Sandbox Manager HTTP API，创建/删除 workspace，接收 tool execution request。 |
| `sandbox_manager.py` | 根据 `SANDBOX_EXECUTION_MODE` 选择 direct 或 Kubernetes runner；K8s runner 创建 one-shot tool Pod。 |
| `sandbox_runtime.py` | one-shot Pod 内部入口，从 env 读取 encoded tool request，执行 tool，输出 envelope。 |
| `sandbox_tools.py` | 受控 workspace tools：文件读写、grep/glob、git status/diff、unittest、branch/commit 等。 |
| `sandbox_protocol.py` | `ToolExecutionRequest`、`ToolExecutionEnvelope`、runtime metadata 等跨边界契约。 |
| `github_broker_app.py` | 与 Sandbox 并列的 trusted executor，独立持有 GitHub secret。 |
| `k8s-v2/sandbox.yaml` | Sandbox Manager Deployment、workspace PVC、RBAC、workspace permission initContainer。 |

当前 Sandbox 已有能力：

1. **Controlled tool API**
   Agent 不直接拿 raw Bash/Read/Write/Edit，而是通过平台定义的 workspace tools 产生副作用。

2. **Secretless Sandbox Pod**
   One-shot Sandbox tool Pod 不注入 `GITHUB_TOKEN`，不注入 `ANTHROPIC_API_KEY`，也不挂载默认 Kubernetes service account token。

3. **Trusted GitHub Broker**
   GitHub clone/push/PR 等 credentialed 操作由 GitHub Broker 执行。Sandbox Pod 只处理无 secret 的 workspace/local tool。

4. **One-shot execution**
   每次 tool execution 创建一个 Pod，用完删除。失败时返回 runtime failure envelope。

5. **User-scoped workspace**
   Workspace 路径是：

   ```text
   /sandboxes/users/{user_id}/{run_id}
   ```

   One-shot Pod 通过 PVC `subPath` 只挂载当前 run workspace。

6. **基础 Pod hardening**
   One-shot Pod 已设置：

   ```text
   automountServiceAccountToken: false
   restartPolicy: Never
   activeDeadlineSeconds
   runAsNonRoot: true
   runAsUser: 10001
   allowPrivilegeEscalation: false
   capabilities.drop: ["ALL"]
   seccompProfile: RuntimeDefault
   resources requests/limits
   /tmp emptyDir
   ```

7. **Sandbox Manager RBAC 最小化**
   Sandbox Manager 只需要创建/读取/删除 Pod 和读取 Pod logs。

8. **Runtime envelope**
   每次执行返回 `ToolExecutionEnvelope`，包含 execution id、run id、tool call id、tool name、execution status、runtime metadata、failure message 或 tool result。

当前设计适合作为 PoC baseline：它证明了 agent side effect 不直接发生在 Brain Pod，Sandbox Pod 没有 secret，tool execution 可审计、可恢复、可 cattle 化。

## 2. 商业 Sandbox 方案可借鉴思想

### 2.1 Cloudflare Workers：Capability API 与 mediated access

Cloudflare Workers 使用 V8 isolate。对当前系统最有价值的不是 V8 isolate 本身，而是两个思想：

1. **不直接给用户代码原始系统能力。**
   用户代码通过平台 API 访问网络、KV、对象存储等能力。

2. **外部访问由 runtime/supervisor/proxy mediated。**
   Sandbox 内代码不应该自然拥有任意文件系统、任意网络和任意 secret。

映射到当前 Sandbox：

- 已借鉴：controlled tool API、GitHub Broker、secretless Sandbox Pod。
- 可继续借鉴：network egress proxy、capability-scoped file/network API、tool-level policy。

适用判断：

- 不建议把当前 Python/Git/unittest coding workflow 改成 V8 isolate。
- 应该借鉴它的 capability model，而不是照搬 runtime。

### 2.2 AWS Lambda / Firecracker：microVM 强隔离

Firecracker 代表 microVM 路线。它适合 serverless、多租户、强隔离场景。每个 sandbox 有 VM 边界，不只是 Linux namespace/cgroup。

可借鉴思想：

- 高风险不可信代码应该有 VM 级隔离。
- 用 snapshot / prewarm 降低冷启动。
- 将 runtime、rootfs、network、block device、logs 作为 VM 生命周期的一部分统一管理。

映射到当前 Sandbox：

- 当前 one-shot Pod 是 container-level isolation，仍共享 node kernel。
- Firecracker 可以作为长期 `FirecrackerRunner`，用于高风险 tool 或真正多租户任意代码执行。

适用判断：

- 不建议马上重写为 Firecracker。
- 应先抽象 Sandbox runtime adapter，让 Firecracker 成为可选执行后端。

### 2.3 Google gVisor / GKE Sandbox：用户态内核模型

gVisor 在应用和 host kernel 之间放一层 user-space kernel/Sentry，减少 workload 直接攻击 host kernel 的 syscall 面。GKE Sandbox 通过 Kubernetes runtime 集成 gVisor。

可借鉴思想：

- 继续使用 Kubernetes Pod API，但让 one-shot Sandbox Pod 使用更强 runtime。
- 用 `RuntimeClass` 做 runtime selection，而不是改掉整个调度系统。

映射到当前 Sandbox：

- 当前 `KubernetesToolPodRunner` 已经集中生成 Pod manifest。
- 可以增加 `runtimeClassName: gvisor`。
- 可以按 tool 风险选择普通 Pod 或 gVisor Pod。

适用判断：

- 这是当前架构最自然的中期升级。
- 兼容性和性能需要测试，尤其是 Git、Python unittest、文件 I/O、package install。

### 2.4 Kata Containers：Pod-level lightweight VM

Kata Containers 用 lightweight VM 承载 Kubernetes Pod。它保留 K8s workflow，但隔离边界更接近 VM。

可借鉴思想：

- 高风险 Pod 可以通过 `RuntimeClass` 切换到 Kata。
- 不必先自建 Firecracker orchestrator，也能获得比普通 container 更强的隔离。
- Pod 仍然是 Kubernetes 管理对象，现有 one-shot Pod 生命周期模型可以保留。

映射到当前 Sandbox：

- 在 `SandboxManager` 里支持 `SANDBOX_RUNTIME_CLASS=kata`。
- 对 `run_python_unittest`、未来 raw command、dependency install、build 等高风险 workload 使用 Kata。

适用判断：

- 比 gVisor 更强，但成本和节点要求更高。
- 适合作为 Level 2.5 / Level 3 之间的过渡。

### 2.5 Modal / E2B / Azure Dynamic Sessions：Agent Sandbox Session Pool

这类产品面向 AI agent / code interpreter / notebook / ephemeral dev environment，重点不是单个 syscall，而是“如何给 agent 快速分配一个可恢复、可销毁、可审计的执行环境”。

可借鉴思想：

- Sandbox 可以是 per-tool，也可以是 per-task / per-run session。
- 使用 prewarmed pool 减少冷启动。
- 通过 TTL、heartbeat、snapshot、artifact export 管理生命周期。
- Sandbox session 可以 crash 后重建，workspace / artifact 作为 durable boundary。

映射到当前 Sandbox：

- 当前是 per-tool one-shot Pod，隔离干净但冷启动多。
- 后续可以支持 per-task 或 per-run sandbox session，用于连续 build/test/edit。

适用判断：

- 很适合 agent coding workflow。
- 需要配合 workspace snapshot、session TTL、resource quota、cleanup。

### 2.6 Wasm / WASI：Capability filesystem for narrow tools

Wasm/WASI 适合小型 deterministic tool 或第三方 plugin。WASI 的 filesystem access 是 capability-based，默认只能访问显式授予的目录。

可借鉴思想：

- 对小型 verifier / formatter / static analyzer，可以用 Wasm 限制能力面。
- 每个 tool 明确声明需要的目录、网络、env、clock、random 等 capability。

映射到当前 Sandbox：

- 不适合完整 Python repo test。
- 适合作为未来 `WasmRunner`，执行小型 deterministic MCP tools。

## 3. 当前 Sandbox 的主要缺口

### 3.1 Runtime isolation 仍是普通 container

当前 one-shot Pod 仍共享 node host kernel。`runAsNonRoot`、drop capabilities、seccomp 可以降低风险，但不等于 VM 隔离。

风险入口：

```text
run_python_unittest
future raw command
build/lint/package install
malicious repo test code
native extension / subprocess / syscall surface
```

缺口：

- 没有 gVisor / Kata / Firecracker runtime profile。
- 不能按 tool 风险选择隔离级别。
- Envelope 中没有记录 runtime class / isolation level。

### 3.2 Network egress 还没完全收口

当前已做到：

- Sandbox Pod 没有 Kubernetes service account token。
- Sandbox Pod 没有 GitHub/Anthropic secret。

但这不等于网络隔离。没有 NetworkPolicy 时，Pod 可能仍能访问：

```text
namespace 内部 service
cluster 内部 service
cloud metadata IP
外网任意地址
```

缺口：

- 没有 default deny egress。
- 没有 egress allowlist。
- 没有 egress proxy。
- 没有 network destination audit。
- 没有明确阻断 metadata service。

### 3.3 Filesystem 仍是共享 PVC + subPath

当前模型：

```text
sandbox-workspaces PVC
  /users/Luca/run_a
  /users/Luca/run_b
  /users/Josephine/run_c
```

One-shot Pod 只挂载当前 run 的 subPath：

```text
mount PVC subPath users/Luca/run_a -> /workspace/run_a
```

这个比挂整个 PVC 安全，但底层仍是同一个 volume。

缺口：

- 一个 run 写爆 PVC 可能影响其他 run。
- 难做 per-run disk quota / inode quota。
- 权限初始化目前是对 `/sandboxes` 做整体修复，边界较粗。
- cleanup / archive / snapshot 都要靠路径管理。
- 若 subPath 或权限配置出错，同 UID `10001` 会增加影响面。

### 3.4 Resource governance 不完整

当前已有：

- CPU/memory requests/limits。
- `activeDeadlineSeconds`。

仍需考虑：

- pids limit。
- ephemeral storage limit。
- PVC workspace quota。
- stdout/stderr/log size cap。
- file descriptor limit。
- network bandwidth limit。
- fork bomb / zip bomb / massive small files。

### 3.5 Tool capability 和 risk profile 还不够显式

当前 tool 是按名字路由到 Sandbox 或 GitHub Broker，但还没有统一的 capability/risk metadata。

建议每个 tool 声明：

```text
filesystem: read-only / write-workspace / git-mutate
network: none / broker-only / egress-proxy
secret: none / broker
runtime: pod / gvisor / kata / firecracker
approval: none / required
timeout
cpu/memory/disk/log limit
```

这样 Sandbox Manager 可以根据 policy 自动选择 runtime profile。

### 3.6 Observability 和 evidence 还可以加强

当前已有 runtime envelope，但还可以补：

- files changed。
- bytes read/written。
- stdout/stderr bytes。
- network destinations。
- DNS queries。
- resource peak usage。
- workspace diff summary。
- secret scanning result。
- runtime profile / runtime class。

这会让 Sandbox 的每次执行不仅“有结果”，还“有证据”。

## 4. Sandbox Layer 分阶段升级方案

### 4.1 Phase 1：补齐 K8s Pod Sandbox 基础边界

目标：在不改变 runtime 模型的前提下，把当前 K8s one-shot Pod sandbox 做扎实。

建议实现：

1. **NetworkPolicy default deny**

   - Sandbox tool Pod 默认禁止所有 ingress/egress。
   - 禁止访问 Session/Web/Postgres/GitHub Broker 等内部服务。
   - 禁止访问 metadata IP。
   - 需要外网时必须走 egress proxy 或 broker。

2. **Resource governance 补强**

   - 增加 `ephemeral-storage` requests/limits。
   - 增加 log output cap。
   - 增加 pids limit 或通过 runtime/node policy 控制。
   - 增加 workspace size / inode 检查。

3. **Filesystem policy 补强**

   - 保留 shared PVC + subPath，但增加 per-run workspace usage check。
   - 禁止 symlink/hardlink 路径逃逸。
   - cleanup 前后记录 workspace metadata。
   - commit/push/PR 前做 secret scanning。

4. **Envelope 扩展**

   增加 runtime / policy / resource evidence：

   ```json
   {
     "runtime": {
       "type": "sandbox_pod",
       "isolation": "kubernetes_container",
       "runtime_class": null,
       "network_policy": "default-deny",
       "egress_policy": "none",
       "resource_limits": {
         "cpu": "500m",
         "memory": "512Mi",
         "ephemeral_storage": "1Gi"
       }
     },
     "evidence": {
       "stdout_bytes": 1200,
       "stderr_bytes": 30,
       "files_changed": [],
       "workspace_bytes": 123456
     }
   }
   ```

测试：

- Manifest contract tests：NetworkPolicy、resources、service account、runtime security。
- Runtime smoke tests：Sandbox Pod 不能访问内部 service、metadata IP。
- Path tests：symlink/hardlink/path traversal。
- Resource tests：timeout、large output cap、workspace size cap。

### 4.2 Phase 2：引入 RuntimeClass，支持 gVisor / Kata

目标：不改变 Sandbox Manager 调用方，升级 one-shot Pod 的隔离边界。

建议实现：

1. 增加配置：

   ```text
   SANDBOX_RUNTIME_CLASS=
   SANDBOX_DEFAULT_RUNTIME_PROFILE=kubernetes_container
   ```

2. 在 Pod manifest 中可选加入：

   ```yaml
   runtimeClassName: gvisor
   ```

   或：

   ```yaml
   runtimeClassName: kata
   ```

3. 增加 runtime profile policy：

   | Tool | 默认 profile |
   | --- | --- |
   | read/glob/grep/git_status/git_diff | `kubernetes_container` 或 `gvisor` |
   | write/edit/create_branch/commit | `gvisor` |
   | run_python_unittest | `gvisor` 或 `kata` |
   | future raw command/build/install | `kata` |

4. Envelope 记录：

   ```text
   runtime_class
   runtime_profile
   isolation_level
   ```

测试：

- Unit test：Pod manifest 包含 runtimeClassName。
- Runtime smoke：gVisor/Kata 环境下 `git_status`、`write_file`、`run_python_unittest` 正常。
- Compatibility test：常见 Python unittest / git operations 在 gVisor/Kata 下通过。

### 4.3 Phase 3：Sandbox Session Pool / per-task sandbox

目标：借鉴 Modal/E2B/Azure Dynamic Sessions，减少 per-tool 冷启动，并支持更长的 build/test 工作流。

新增概念：

```text
sandbox_sessions
  id
  run_id
  task_id
  user_id
  runtime_profile
  workspace_path
  pod_name / vm_id
  status
  heartbeat_at
  expires_at
```

模式：

| 模式 | 适用 |
| --- | --- |
| per-tool one-shot | 简单工具、风险低、清理要求高 |
| per-task sandbox | 一个 task 内连续多次文件/git/test |
| per-run sandbox | 大型 coding workflow、需要 warm environment |

关键要求：

- Sandbox session crash 后可以重新创建。
- Workspace 仍然是 durable boundary。
- Session 只缓存环境，不作为状态 source of truth。
- TTL 到期自动销毁。
- 每次 tool execution 仍写 envelope。

测试：

- Session pool lifecycle test。
- Sandbox pod crash 后重新创建并继续使用 workspace。
- TTL cleanup test。
- 同一 run 不同 task 是否复用按 policy 决定。

### 4.4 Phase 4：Workspace backend 抽象

目标：从“共享 PVC + subPath”演进为可替换 workspace backend。

接口：

```text
WorkspaceProvider
  create(run_id, user_id)
  mount_spec(run_id, user_id)
  snapshot(run_id)
  usage(run_id)
  cleanup(run_id)
```

实现路线：

| Backend | 说明 |
| --- | --- |
| `SharedPVCSubPathWorkspaceProvider` | 当前实现。 |
| `PerUserPVCWorkspaceProvider` | 每个 user 一个 PVC。 |
| `PerRunPVCWorkspaceProvider` | 每个 run 一个 PVC，更好做 quota/cleanup。 |
| `SnapshotWorkspaceProvider` | run 结束或 handoff 时 snapshot。 |
| `MicroVMBlockDeviceWorkspaceProvider` | Firecracker/Kata 级别磁盘。 |

测试：

- 每种 provider 的 path/mount contract。
- quota/usage/cleanup。
- path escape / symlink escape。
- workspace snapshot restore。

### 4.5 Phase 5：Firecracker / microVM Runner

目标：为真正高风险、多租户、任意代码执行提供 VM 级边界。

新增 runner：

```text
FirecrackerRunner
  allocate microVM
  attach rootfs snapshot
  attach run workspace disk
  configure isolated network
  run sandbox_runtime
  collect logs / envelope
  destroy microVM
```

适用：

- raw command。
- untrusted dependency install。
- arbitrary user code。
- 高风险 repo test。
- 多租户强隔离执行。

不适合：

- 当前 PoC 立即落地。
- 简单 read/glob/status 这类低风险 tool。

测试：

- microVM boot smoke。
- workspace mount and write。
- network deny / allowlist。
- timeout and cleanup。
- crash recovery。
- envelope compatibility。

### 4.6 Phase 6：Wasm Runner for narrow deterministic tools

目标：对小型 deterministic tools 使用更窄的 capability sandbox。

适用：

- formatter。
- static analyzer。
- policy checker。
- schema validator。
- safe transformation。

能力：

- 只授予指定目录。
- 默认无网络。
- resource limit。
- deterministic output。

测试：

- WASI capability filesystem test。
- denied path test。
- no network test。
- deterministic output test。

## 5. 推荐实施顺序

建议按风险收益排序：

1. **Phase 1A：NetworkPolicy default deny**
   先把 sandbox egress 收口，这是当前最大现实缺口。

2. **Phase 1B：ephemeral-storage / log cap / workspace usage check**
   防止单次 tool execution 吃爆节点或 PVC。

3. **Phase 1C：Envelope 扩展**
   把 runtime profile、resource evidence、workspace evidence 记录下来。

4. **Phase 2A：RuntimeClass 配置支持**
   先只支持可选 `runtimeClassName`，不要求环境一定安装 gVisor/Kata。

5. **Phase 2B：gVisor/Kata compatibility tests**
   在支持的集群里验证核心 tools。

6. **Phase 3：per-task sandbox session**
   优化 agent coding workflow 性能，但保持 workspace durable。

7. **Phase 4：WorkspaceProvider 抽象**
   为 per-run PVC、snapshot、microVM disk 做铺垫。

8. **Phase 5：Firecracker Runner**
   长期强隔离路线。

9. **Phase 6：Wasm Runner**
   针对小型 deterministic tools 的补充路线。

## 6. 当前最小落地范围建议

下一轮可以先实现这些，不引入太大复杂度：

1. `SANDBOX_RUNTIME_CLASS` 配置和 Pod manifest 支持。
2. One-shot Pod 增加 `ephemeral-storage` requests/limits。
3. 增加 Sandbox runtime profile metadata。
4. 增加 NetworkPolicy manifests：
   - default deny sandbox tool pod ingress/egress。
   - 明确允许必要组件通信。
5. 增加脚本/测试：
   - manifest contract test。
   - v2 security verification 中加入 network deny 检查。
6. 文档记录：
   - 当前 runtime 是 container。
   - 可选 runtimeClass 支持 gVisor/Kata。
   - Firecracker/Wasm 是后续 backend，不是当前实现。

这一组改动可以把 Sandbox Layer 从“普通 K8s Pod sandbox”推进到“policy-aware, runtime-pluggable K8s sandbox baseline”。

## 7. 验证方案

### 7.1 Unit / Contract Tests

- Pod manifest 必须包含 securityContext、resources、serviceAccountToken disabled。
- 配置 runtime class 时，manifest 必须包含 `runtimeClassName`。
- Workspace path/subPath 不能逃逸。
- Tool capability metadata 能映射到 runtime profile。
- Envelope 包含 runtime profile / isolation metadata。

### 7.2 Manifest Tests

- NetworkPolicy default deny 存在。
- Sandbox Manager RBAC 不允许 secrets / exec。
- Sandbox tool pod label 可被 NetworkPolicy 选择。
- resource requests/limits 包含 CPU、memory、ephemeral-storage。

### 7.3 Runtime Smoke Tests

- Sandbox workspace API 返回 200。
- One-shot Pod 可以执行 `git_status` / `write_file` / `run_python_unittest`。
- Sandbox Pod 不能访问 Session/GitHub Broker/Postgres。
- Sandbox Pod 不能访问 metadata IP。
- 如果配置 gVisor/Kata，Pod runtime class 正确生效。

### 7.4 Chaos / Abuse Tests

- Sandbox Pod 被删除后，返回 failed envelope 或恢复上下文。
- Tool 超时后 Pod 被清理。
- 大量 stdout/stderr 被截断。
- 大文件写入超过 quota 被拒绝。
- 恶意 path/symlink/hardlink 被拒绝。

## 8. 总结

当前 Sandbox Layer 已经具备正确的 PoC 形态：controlled tool API、secretless one-shot Pod、GitHub Broker、user-scoped workspace、runtime envelope 和基础 Pod hardening。

后续升级不应该直接跳到某一种商业技术，而应该形成可插拔、多 profile 的 Sandbox Layer：

```text
Sandbox Manager
  -> Runtime Profile Resolver
  -> KubernetesPodRunner
  -> GVisor/Kata RuntimeClass Pod
  -> Firecracker MicroVM Runner
  -> Wasm Runner

Workspace Provider
  -> Shared PVC subPath
  -> Per-run PVC
  -> Snapshot
  -> MicroVM disk

Network Policy / Egress Proxy
  -> default deny
  -> allowlist
  -> audit

Execution Envelope
  -> runtime evidence
  -> resource evidence
  -> filesystem evidence
  -> network evidence
```

这样 Sandbox Layer 可以从当前 K8s-native PoC，逐步演进为面向 agent coding workflow 的商业化 sandbox runtime。
