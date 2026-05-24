# Session 与 Security Testing Strategy

本文档记录当前系统针对 Session Management 和 Security Isolation 的测试方案。目标不是只列出已有测试文件，而是建立一套可证明的验证闭环：代码逻辑、数据契约、K8s manifest、真实 runtime、故障恢复和权限隔离都能被验证。

## 1. 测试目标

当前系统围绕两类核心能力做验证。

Session Management:

- Easy 阶段：证明 session/run/task/handoff/tool/event 状态不依赖内存，Pod crash 后可以从持久化状态恢复。
- Median 阶段：证明 Brain crash 后可以通过 lease/heartbeat 自动恢复，不依赖用户手动 wake。
- Advanced 阶段：证明关键状态有 versioned contracts、hash-chain audit、replay consistency、human approval 和 user-level ownership。

Security Isolation:

- Level 1：证明 Agent 只能通过 controlled tools 产生副作用，Sandbox 无 secret，workspace path 不逃逸，高风险外部写操作需要 human approval。
- Level 2：证明 K8s runtime 具备基础强隔离：non-root、no privilege escalation、drop capabilities、resources、RBAC 最小化、PVC 持久化和 workspace 权限初始化。

## 2. 测试分层

| 层级 | 类型 | 目标 |
| --- | --- | --- |
| Layer 1 | Unit / Contract Tests | 验证纯逻辑、状态机、JSON contract、planner/orchestrator 行为 |
| Layer 2 | Store / API Integration Tests | 验证 Session Layer 的持久化、ownership、idempotency、recovery bundle |
| Layer 3 | K8s Manifest Security Tests | 验证 YAML 不会回退：non-root、RBAC、resources、PVC、service account token |
| Layer 4 | K8s Runtime Smoke Tests | 验证真实 v2 namespace 可运行：pods、PVC、workspace、API、Brain replicas |
| Layer 5 | Chaos / Recovery / Security Drills | 验证真实故障场景：Brain crash、Sandbox pod crash、跨用户访问、approval、RBAC can-i |

这五层对应三类证据：

- Unit/contract 证明代码逻辑正确。
- Manifest tests 证明部署声明保留安全边界。
- Runtime/chaos tests 证明真实 K8s 环境里可以恢复、隔离和审计。

## 3. Session Management 测试矩阵

### 3.1 Easy 阶段

| 能力 | 测试内容 | 当前覆盖 |
| --- | --- | --- |
| Run 状态机 | 合法状态转换通过，非法转换拒绝，例如 `completed -> running` | `tests/test_session_policy.py` |
| Idempotency | 同一个 `session_id + idempotency_key` 只创建一个 run | `tests/test_schema_contract.py`，建议后续补 API/store integration |
| Archive 而不是删除 | 过期 session/run 只 archive，不 hard delete | `tests/test_session_policy.py`，`tests/test_schema_contract.py` |
| Run acceptance criteria | 初始 run criteria 持久化，不被 task handoff 覆盖 | `tests/test_orchestrator.py` |
| Task handoff | 每个 task 完成后生成 handoff JSON，后续 task 可以读取 | `tests/test_orchestrator.py`，`tests/test_claude_agent.py` |
| Tool call 持久化 | tool requested/executed/failed 都落库并可审计 | `tests/test_sdk_tool_execution.py` |
| Event ledger | session_events 可用于 SSE、audit 和 replay | `tests/test_session_contracts.py`，`tests/test_session_replay.py` |

### 3.2 Median 阶段

| 能力 | 测试内容 | 当前覆盖 |
| --- | --- | --- |
| Claim lock | 多个 worker 抢同一个 run，只有一个成功 claim | `tests/test_brain_worker.py` |
| Heartbeat | running run 刷新 `last_heartbeat_at` 和 `claim_expires_at` | `tests/test_brain_worker.py` |
| Expired lease | `claim_expires_at < now()` 后自动 requeue | `tests/test_brain_worker.py` |
| Brain crash | running run 自动变 `resume_queued`，running task/attempt 标记 crash | `tests/test_brain_worker.py`，`scripts/k8s-chaos-drill-v2.sh` |
| Orphaned tool | Brain crash 时未完成 tool call 标记 orphaned | `tests/test_orchestrator.py` |
| Recovery context | 下一次 fresh query 带上 failed/orphaned tool context | `tests/test_orchestrator.py`，`tests/test_claude_agent.py` |
| Multi-worker recovery | worker A crash 后 worker B 接手同一个 run | `scripts/k8s-chaos-drill-v2.sh` |

### 3.3 Advanced 阶段

| 能力 | 测试内容 | 当前覆盖 |
| --- | --- | --- |
| Versioned contract | handoff/envelope/recovery context 必须有 `schema_version` | `tests/test_session_contracts.py` |
| Hash chain | 篡改 event payload 后 replay 报错 | `tests/test_session_replay.py` |
| Replay consistency | event replay 状态和 materialized tables 一致 | `tests/test_session_replay.py` |
| Projection drift | 人为制造状态漂移，replay 报 differences | `tests/test_session_replay.py` |
| User ownership | Luca 的 session/run，Josephine 查询不到 | `tests/test_ownership.py`，`scripts/k8s-chaos-drill-v2.sh ownership` |
| Human approval | requested/approved/denied 都有状态和 event | `tests/test_sdk_tool_execution.py`，`tests/test_static_ui_contract.py` |

## 4. Security Management 测试矩阵

### 4.1 Security Level 1

| 能力 | 测试内容 | 当前覆盖 |
| --- | --- | --- |
| Sandbox 无 secret | one-shot pod env 不包含 `GITHUB_TOKEN` / `ANTHROPIC_API_KEY` | `tests/test_sandbox_runtime.py` |
| GitHub secret 隔离 | GitHub token 只在 GitHub Broker 使用 | `tests/test_github_workflow.py`，`tests/test_sdk_tool_execution.py` |
| Controlled tools | Agent tool call 必须进入 SDK tool layer 并落库 | `tests/test_sdk_tool_execution.py` |
| Path escape | `../outside.txt`、`/tmp/outside` 被拒绝 | `tests/test_sandbox_runtime.py` |
| User workspace | Pod subPath 是 `users/{user_id}/{run_id}` | `tests/test_sandbox_runtime.py`，`tests/test_ownership.py` |
| Approval | push / PR 必须等待 human approval | `tests/test_sdk_tool_execution.py`，`tests/test_static_ui_contract.py` |
| Audit | tool call、execution、approval 都有 event | `tests/test_sdk_tool_execution.py`，`tests/test_session_replay.py` |

### 4.2 Security Level 2

| 能力 | 测试内容 | 当前覆盖 |
| --- | --- | --- |
| Non-root | 长期服务和 one-shot pod 必须 `runAsNonRoot` | `tests/test_k8s_security_manifests.py`，`tests/test_sandbox_runtime.py` |
| No privilege escalation | `allowPrivilegeEscalation: false` | `tests/test_k8s_security_manifests.py`，`tests/test_sandbox_runtime.py` |
| Drop capabilities | `capabilities.drop: ["ALL"]` | `tests/test_k8s_security_manifests.py`，`tests/test_sandbox_runtime.py` |
| Seccomp | `seccompProfile: RuntimeDefault` | `tests/test_k8s_security_manifests.py`，`tests/test_sandbox_runtime.py` |
| Resource limits | 所有 workload 有 requests/limits | `tests/test_k8s_security_manifests.py`，`tests/test_sandbox_runtime.py` |
| No SA token | 非 K8s client workload 禁用 service account token | `tests/test_k8s_security_manifests.py`，`tests/test_sandbox_runtime.py` |
| Sandbox Manager RBAC | 只能 pods create/get/delete 和 pods/log get | `scripts/k8s-security-verify-v2.sh` |
| Postgres PVC | 不允许回到 `emptyDir` | `tests/test_k8s_security_manifests.py` |
| Workspace PVC permission | initContainer 初始化 `/sandboxes/users` | `tests/test_k8s_security_manifests.py`，`scripts/k8s-smoke-v2.sh` |
| Runtime workspace API | K8s 中真实 `POST /internal/workspaces/{run_id}` 返回 200 | `scripts/k8s-smoke-v2.sh` |

## 5. 当前已有测试映射

| 测试文件 | 覆盖内容 |
| --- | --- |
| `tests/test_session_policy.py` | run 状态机、TTL/retention helper |
| `tests/test_schema_contract.py` | schema 字段、idempotency、retention、heartbeat、approval 表结构 |
| `tests/test_brain_worker.py` | lease、heartbeat、expired run requeue |
| `tests/test_orchestrator.py` | run acceptance criteria、handoff、recovery context、workspace cleanup |
| `tests/test_claude_agent.py` | fresh query prompt、criteria results、handoff schema、task completion validation |
| `tests/test_session_contracts.py` | versioned contracts、event hash、payload 校验 |
| `tests/test_session_replay.py` | replay、hash chain、projection drift |
| `tests/test_ownership.py` | user_id normalization、API ownership、workspace path scoping |
| `tests/test_sdk_tool_execution.py` | tool execution envelope、approval、trusted broker routing、失败处理 |
| `tests/test_k8s_security_manifests.py` | v2 K8s securityContext、resources、Postgres PVC、Claude config、workspace initContainer |
| `tests/test_sandbox_runtime.py` | one-shot pod manifest、无 secret、无 service account token、path escape、workspace subPath |
| `tests/test_static_ui_contract.py` | 前端 replay、approval、user switcher、run acceptance criteria UI contract |
| `tests/test_testing_strategy_artifacts.py` | 测试策略文档和 v2 runtime verification 脚本存在性/关键检查 |

## 6. 新增 Runtime Verification 脚本

### 6.1 v2 Smoke Test

命令：

```bash
./scripts/k8s-smoke-v2.sh
```

验证内容：

- `cloud-agent-poc-v2` namespace 存在。
- v2 deployments rollout 成功。
- Brain replicas 为 3。
- `postgres-data` 和 `sandbox-workspaces` PVC 为 `Bound`。
- Web health 和首页可访问。
- Web API 可以用 `X-User-Id` 创建 session。
- Sandbox Manager 主容器以 UID `10001` 运行。
- `/sandboxes/users` 对 UID `10001` 可写。
- Sandbox Manager workspace API 可以创建并删除 `/sandboxes/users/{user_id}/{run_id}`。

### 6.2 v2 Security Verification

命令：

```bash
./scripts/k8s-security-verify-v2.sh
```

验证内容：

- Sandbox Manager 可以 `create/get/delete pods` 和 `get pods/log`。
- Sandbox Manager 不能 `get/list secrets`。
- Sandbox Manager 不能 `create pods/exec`。
- Brain/Web/Session/GitHub Broker 没有 service account token。
- Sandbox Manager 生成的 one-shot tool pod manifest 仍然无 service account token、无 secret env、non-root、drop capabilities、resources、`/tmp` emptyDir。

### 6.3 v2 Chaos / Recovery Drill

命令：

```bash
./scripts/k8s-chaos-drill-v2.sh brain-crash RUN_ID=run_xxx
./scripts/k8s-chaos-drill-v2.sh sandbox-pod-crash RUN_ID=run_xxx
./scripts/k8s-chaos-drill-v2.sh ownership RUN_ID=run_xxx OWNER_USER=Luca OTHER_USER=Josephine
```

说明：

- Chaos drill 需要真实运行中的 run，通常在 Claude API 可用时执行。
- `brain-crash` 会删除当前 claimed Brain Pod，然后等待 `run.lease.expired` 和新的 claim。
- `sandbox-pod-crash` 会删除当前 run 的 one-shot Sandbox tool pod，然后检查 tool call 是否出现 failed/orphaned 类状态。
- `ownership` 会验证非 owner 用户读取 run/replay/wake 时返回 404。

## 7. 推荐执行顺序

日常本地开发：

```bash
PYTHONPATH=src python3 -m unittest discover -v
```

v2 部署后：

```bash
./scripts/k8s-smoke-v2.sh
./scripts/k8s-security-verify-v2.sh
```

需要验证恢复能力时：

```bash
./scripts/k8s-chaos-drill-v2.sh brain-crash RUN_ID=run_xxx
./scripts/k8s-chaos-drill-v2.sh sandbox-pod-crash RUN_ID=run_xxx
./scripts/k8s-chaos-drill-v2.sh ownership RUN_ID=run_xxx
```

## 8. 当前限制和后续补强

当前已经覆盖大部分核心路径，但仍有一些后续可以加强的方向：

- 为 idempotency 增加更明确的 API/store integration test。
- 为多个 Brain Worker 同时 claim 同一个 run 增加更高并发的压力测试。
- 为 NetworkPolicy 增加 manifest 和 runtime 验证。当前 Level 2 尚未实现 NetworkPolicy。
- 为 Human Approval 增加 K8s E2E drill，覆盖 UI approve/deny 后 Brain 继续执行的真实路径。
- 为 replay 增加数据库修复工具前的 dry-run test。当前 replay 只做 consistency report，不自动修复 materialized tables。
- 为 GitHub Broker 增加 short-lived token 或 installation token 后的 secret rotation test。

## 9. 总结

当前测试方案把 Session 和 Security 的设计拆成了可验证的证据链：

- 代码层：状态机、contract、replay、ownership、approval。
- 部署层：K8s manifest 不回退安全设置。
- 运行层：v2 namespace 真实可用，workspace 权限、RBAC 和 service account token 边界可验证。
- 故障层：Brain crash、Sandbox pod crash、跨用户访问可以通过 drill 复现和检查。

这样系统不是只“声称”支持 durable session 和 sandbox isolation，而是可以通过测试、脚本和事件记录证明这些能力存在。
