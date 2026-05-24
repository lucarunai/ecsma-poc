# Session Handling 与 Security Isolation 升级方案

这份文档整理当前 PoC 在 session management 和 security isolation 方面已有的设计，并给出后续可以作为 bonus 加分项的分阶段升级方案。目标不是把 PoC 说成已经 production-ready，而是清楚说明：当前系统已经做到哪里，下一步如何演进，以及如何用测试证明这些能力真实有效。

## 1. 当前 Session 设计分析

当前系统的 Session Layer 已经不是简单的内存态 session，而是以 Postgres 为 source of truth 的持久化状态层。

核心表如下：

| 表 | 作用 |
| --- | --- |
| `sessions` | 一次用户会话的生命周期。 |
| `runs` | 一次用户 prompt 触发的 agent run，保存 prompt、status、run-level `acceptance_criteria`、workspace metadata。 |
| `tasks` | Planner 拆出来的有序任务，每个 task 有自己的 description、task-local `acceptance_criteria` 和 status。 |
| `task_attempts` | 每一次 fresh Agent SDK query，也就是一次独立的 agent attempt。 |
| `task_handoffs` | 每个 task 完成后写入的 durable handoff JSON，用于下一次 fresh query 的上下文恢复。 |
| `tool_calls` | Agent 请求调用 tool 的原始输入。 |
| `tool_executions` | Sandbox 或 GitHub Broker 返回的 runtime envelope。 |
| `session_events` | 用于 SSE replay、timeline、audit 的 append-only event ledger。 |
| `agent_transcripts` | Claude transcript artifact 的索引，只用于审计和调试，不作为恢复主上下文。 |

当前 Session 设计里的几个关键点：

1. 状态不依赖 Brain 内存。
   Brain 或 Sandbox 崩溃后，恢复依赖 Postgres 中的 run、task、handoff、tool call、workspace metadata。

2. 不依赖 Claude provider session resume。
   每个 task 是一次新的 Agent SDK query。`claude_session_id` 主要用于 transcript 索引，不是恢复主链路。

3. Handoff 是 durable context。
   下一个 task 的 fresh query 会拿到之前 task 的 handoff，而不是依赖内存里的 conversation history。

4. Run-level acceptance criteria 是整个 run 的“宪法”。
   `runs.acceptance_criteria` 保存完整任务目标，每次 task query 都会传给 agent。`tasks.acceptance_criteria` 是当前 task 自己要验证的 criteria。

5. Event 可以回放。
   `session_events` 支持前端通过 `after_event_id` 拉取后续事件，用于 UI timeline 和恢复展示。

主要代码位置：

- `src/cloud_agent_poc/session_app.py`: Session API，包括 session/run 创建、wake、events、internal update。
- `src/cloud_agent_poc/session_store.py`: Postgres 持久化读写。
- `src/cloud_agent_poc/brain/orchestrator.py`: 组装 recovery bundle、handoff、criteria，并驱动 task loop。
- `src/cloud_agent_poc/brain/worker.py`: claim queued / resume_queued run。

## 2. Session Management 三阶段升级方案

### 2.1 Easy 阶段：当前 PoC 加小幅补强

目标：证明 session 状态不在内存里，Pod crash 后可以恢复。

当前实现状态：

- 已增加 run 状态机校验，拒绝 `completed -> running`、`completed -> resume_queued` 等非法跳转。
- 已增加可选 `idempotency_key`，并通过 `session_id + idempotency_key` 唯一索引避免重复创建 run。
- 已增加 `sessions.expires_at` 和 `runs.retention_until`，并提供 expired state archive 入口。过期只归档，不做 hard delete，保留 events、handoffs、tool evidence 等审计数据。
- 已补充更明确的 resume events：`run.resume.requested`、`run.resume.started`、`run.resume.context_loaded`、`run.resume.completed`。

当前已经满足：

- `runs.status` 记录 run 状态，例如 `queued`、`running`、`completed`、`blocked`、`failed`、`resume_queued`。
- `tasks.status` 记录每个 task 状态。
- `task_attempts` 记录每次 fresh agent query。
- `task_handoffs` 记录 task 完成后的 handoff JSON。
- `tool_calls` / `tool_executions` 记录 tool 调用和执行结果。
- `wake_run` 可以把 failed/blocked run 重新放回 `resume_queued`。
- Brain 重新执行时可以读取 recovery bundle。

建议补强：

1. 明确状态机校验。

   当前 status 多数是字符串更新，缺少统一约束。建议定义合法状态转换：

   ```text
   queued -> running
   running -> completed
   running -> blocked
   running -> failed
   blocked -> resume_queued
   failed -> resume_queued
   resume_queued -> running
   ```

   不允许非法跳转，例如：

   ```text
   completed -> running
   completed -> resume_queued
   ```

2. 增加 run idempotency key。

   防止用户重复点击 Run，创建两个重复 run。可以在 `runs` 表增加：

   ```text
   idempotency_key
   ```

   并约束同一个 `session_id + idempotency_key` 只能创建一次 run。

3. 增加 session TTL 和 archive 策略。

   当前 PoC 没有明确过期策略。可以增加：

   ```text
   sessions.expires_at
   runs.retention_until
   ```

   已完成 run 和过期 session 可以按策略归档。归档只更新 `archived_at` / `archive_reason`，不删除 `session_events`、`task_handoffs`、`tool_calls`、`tool_executions` 等审计数据。

4. 补充更明确的 recovery events。

   例如：

   ```text
   run.resume.requested
   run.resume.started
   run.resume.context_loaded
   run.resume.completed
   ```

   这样 event ledger 更容易解释和审计。

### 2.2 Median 阶段：生产级恢复能力

目标：解决 Brain Pod 正在执行时突然 crash，系统可以自动恢复，不依赖用户手动 wake。

当前实现状态：

- 已增加 run lease 字段：`claimed_by`、`claim_expires_at`、`last_heartbeat_at`、`attempt_count`。
- Brain Worker claim run 时会写入 worker id、lease 过期时间、heartbeat 时间，并递增 `attempt_count`。
- Brain Worker 执行 run 时会启动后台 heartbeat，定期刷新 `last_heartbeat_at` 和 `claim_expires_at`。
- 同一次 heartbeat 也会刷新当前 `running` 的 `task_attempts.last_heartbeat_at` 和 `task_attempts.heartbeat_expires_at`。
- Brain Worker 每轮 claim 前会调用 expired lease requeue，把过期的 `running` run 自动转成 `resume_queued`。
- 自动 requeue 会把未结束的 `running` task attempt 标记为 `failed`，并写入 `failure_kind = brain_crash`。
- 自动 requeue 会把未完成的 `requested` / `running` tool call 标记为 `orphaned`，并写入 `failure_kind = brain_crash`，下次 agent query 会先验证 workspace 状态再决定是否重试。
- Agent query 异常会把当前 task attempt 标记为 `failed`，并写入 `failure_kind = model_error`。
- 恢复时传给 agent 的 `recovery_context` 已升级为 `recovery_context.v1` JSON，包含当前 task、最近 attempts、需要关注的 failed/orphaned/requested/running tool calls 和恢复指导。
- Brain Worker 每轮 claim 前会执行最大尝试次数保护，超过 `RUN_MAX_ATTEMPTS` 的 `resume_queued` run 会转成 `failed`，并发出 `run.resume.exhausted` 事件，避免无限自动恢复。
- 自动 requeue 会发出 `run.lease.expired` 和 `run.resume.requested` 事件，前端 timeline 可以展示。

当前主要问题：

- Brain claim run 后会把 run 设成 `running`。
- 如果 Brain Pod 直接 crash，run 可能一直卡在 `running`。
- 当前更多依赖用户手动 wake，或者异常被捕获后写成 failed。

建议引入 lease / heartbeat 模型。

给 `runs` 增加字段：

```text
claimed_by
claim_expires_at
last_heartbeat_at
attempt_count
```

Brain Worker claim run 时：

```sql
UPDATE runs
SET status = 'running',
    claimed_by = '<brain-pod-id>',
    claim_expires_at = now() + interval '60 seconds',
    last_heartbeat_at = now()
WHERE status IN ('queued', 'resume_queued');
```

Brain 执行时定期 heartbeat：

```sql
UPDATE runs
SET last_heartbeat_at = now(),
    claim_expires_at = now() + interval '60 seconds'
WHERE id = '<run_id>'
  AND claimed_by = '<current_worker_id>';
```

Recovery worker 定期扫描：

```sql
SELECT id
FROM runs
WHERE status = 'running'
  AND claim_expires_at < now();
```

然后自动转成：

```text
resume_queued
```

这样 Brain Pod crash 后，不需要用户点 wake，系统会自动恢复。

Median 阶段还建议：

1. Task attempt 也加 heartbeat。（已实现）
   不只 run 级别，`task_attempts` 也记录 `last_heartbeat_at` 和 `heartbeat_expires_at`，用于判断某个 task query 是否卡住。

2. 区分 crash 和业务失败。（已完成基础版）

   例如：

   ```text
   failure_kind = brain_crash
   failure_kind = sandbox_runtime_error
   failure_kind = tool_error
   failure_kind = model_error
   ```

3. 恢复时使用结构化 recovery context。（已实现）
   当前传给 agent 的恢复信息是 `recovery_context.v1` JSON，而不是自由文本；它包含 `prior_attempts`、`tool_calls_requiring_attention` 和 `resume_guidance`。

4. 支持多个 Brain Worker。（已完成基础版）
   当前 `FOR UPDATE SKIP LOCKED` 加 run lease 可以避免多个 worker 同时 claim 同一个 run；lease 过期后由下一轮 worker 自动 requeue。

### 2.3 Advanced 阶段：多租户、可审计、强一致的 Agent Session 平台

目标：面向真正 production platform。

建议方向：

1. Event sourcing。
   `session_events` 不只是 UI timeline，而是事实来源之一。可以通过 replay events 重建 run/task 状态，并和 materialized tables 对比。

2. 版本化数据契约。

   所有关键 JSON 都加 schema version：

   ```text
   run_acceptance_criteria.v1
   task_handoff.v1
   tool_execution_envelope.v1
   recovery_context.v1
   ```

3. 多租户隔离。

   表里增加：

   ```text
   tenant_id
   user_id
   project_id
   ```

   所有 API 都 enforce ownership。

4. 审计不可篡改。

   对 prompt、tool input、tool output、handoff 做 hash chain：

   ```text
   event_hash = hash(previous_event_hash + payload)
   ```

   这样可以证明执行历史没有被篡改。

5. Human-in-the-loop checkpoint。

   对高风险操作，例如 push、PR、删除文件，可以进入：

   ```text
   awaiting_approval
   ```

   用户批准后继续执行。

## 3. 当前 Security 设计分析

当前系统已经有比较清晰的安全边界雏形。

### 3.1 Agent 工具边界

Agent 不能直接使用 Claude Code 默认本地工具，例如：

```text
Bash
Read
Write
Edit
Glob
Grep
Task
```

Agent 只能使用平台暴露的 controlled MCP tools。

### 3.2 Tool 分层

工具分为两类：

Sandbox tools：

```text
read_workspace_file
write_workspace_file
edit_workspace_file
glob_workspace_files
grep_workspace_files
create_git_branch
git_status
git_diff_stat
run_python_unittest
commit_git_changes
```

Trusted GitHub Broker tools：

```text
clone_github_repository
checkout_git_branch
push_current_git_branch
create_github_pull_request
```

### 3.3 Secret 边界

当前设计中：

- `GITHUB_TOKEN` 只注入 GitHub Broker。
- `ANTHROPIC_API_KEY` 只注入 Brain。
- Sandbox Pod 不注入 `GITHUB_TOKEN`。
- Sandbox Pod 不注入 `ANTHROPIC_API_KEY`。

### 3.4 Sandbox 执行边界

当前 Sandbox 设计：

- 每次 tool execution 创建一个 one-shot Sandbox Pod。
- Pod 执行完成后删除。
- Sandbox Pod 使用 run workspace 的 subPath mount。
- Sandbox Pod 使用 `automountServiceAccountToken: false`。
- 文件工具校验 path，防止 `../outside.txt` 这类路径逃逸。

## 4. Security Management 分层方案

### 4.1 Security Level 1：当前 PoC 安全模型

目标：证明 sandbox 不能接触 secret，agent 不能直接操作 Brain 文件系统。

包含：

- Agent 只能用 controlled MCP tools。
- 禁用 Claude 默认 Bash/Read/Write/Edit 等工具。
- Sandbox Pod 不注入 GitHub token。
- Sandbox Pod 不注入 Anthropic API key。
- GitHub token 只在 GitHub Broker。
- Sandbox Pod 使用 one-shot execution。
- Tool call 和 tool execution 都落库审计。
- 文件路径必须 stay inside workspace。
- Git command 输出 redacts auth header。

这是一个合理的 PoC baseline。

### 4.2 Security Level 2：Kubernetes 强隔离模型

目标：即使 Sandbox Pod 被恶意代码控制，也尽量不能横向移动、不能打内网服务、不能拿 Kubernetes API token。

建议增加：

1. NetworkPolicy default deny。

   默认禁止 namespace 内所有 pod ingress/egress。

2. 只开放必要流量。

   例如：

   ```text
   Web -> Session
   Brain -> Session
   Brain -> Sandbox Manager
   Brain -> GitHub Broker
   Sandbox Manager -> Kubernetes API
   GitHub Broker -> github.com
   Sandbox tool Pod -> 按需访问 GitHub，或者默认完全禁止外网
   ```

3. Pod securityContext。

   所有 workload 建议加：

   ```yaml
   securityContext:
     runAsNonRoot: true
     allowPrivilegeEscalation: false
     readOnlyRootFilesystem: true
     capabilities:
       drop: ["ALL"]
     seccompProfile:
       type: RuntimeDefault
   ```

   如果某些容器需要写 `/tmp`，可以挂 `emptyDir` 给 `/tmp`。

4. Resource requests / limits。

   防止恶意或失控测试吃爆节点资源：

   ```yaml
   resources:
     requests:
       cpu: 100m
       memory: 128Mi
     limits:
       cpu: 500m
       memory: 512Mi
   ```

5. Sandbox tool Pod 更严格。

   对 one-shot Pod 加强：

   ```yaml
   activeDeadlineSeconds: 180
   automountServiceAccountToken: false
   restartPolicy: Never
   ```

   当前已有其中一部分，可以继续补齐 securityContext 和 resource limits。

6. RBAC 最小化。

   Sandbox Manager 只需要：

   ```text
   pods create/get/delete
   pods/log get
   ```

   不应该有 `get secrets`、`list configmaps`、`exec pods` 等权限。

### 4.3 Security Level 3：高级 Zero-Trust / 多租户模型

目标：面向真正多租户、不可信代码执行场景。

建议：

1. 每个 run 一个 namespace。

   当前是所有 run 共享 namespace + PVC subPath。更强的是：

   ```text
   namespace per run
   service account per run
   network policy per run
   pvc per run
   ```

2. 使用 gVisor / Kata / Firecracker。

   Kubernetes Pod 本身不是最强隔离。不可信代码执行可以引入 microVM 或 sandboxed container runtime。

3. 短期 GitHub token。

   不使用长期 `GITHUB_TOKEN`，改成 short-lived installation token。

4. OPA / Kyverno policy。

   所有 Pod manifest 必须满足安全策略，否则集群拒绝创建。

5. Secret scanning。

   在 commit/push/PR 前扫描 workspace，防止 agent 把 secret 写进代码。

6. Signed tool envelope。

   Tool execution envelope 做签名或 hash，防止执行记录被篡改。

## 5. 测试方案

### 5.1 Session Management 测试

Easy 阶段测试：

1. 状态转换测试。

   验证合法状态可以转换：

   ```text
   queued -> running
   running -> completed
   blocked -> resume_queued
   failed -> resume_queued
   ```

   验证非法状态被拒绝：

   ```text
   completed -> running
   completed -> resume_queued
   ```

2. Handoff 持久化测试。

   Task1 完成后验证：

   - `task_handoffs.payload` 被保存。
   - Task2 query 可以拿到 Task1 handoff。
   - Handoff 不重复保存 run acceptance criteria。

3. Run acceptance criteria 测试。

   验证：

   - Planner 生成完整 criteria 后写入 `runs.acceptance_criteria`。
   - 每次 agent query 都传完整 run criteria。
   - 当前 task 仍然只校验自己的 task-local criteria。

4. Event replay 测试。

   创建 session/run 后验证：

   - `session_events` 有 `session.created`。
   - 有 `user.prompt.accepted`。
   - 有 `tasks.created`。
   - 前端用 `after_event_id` 能继续拉后续事件。

Median 阶段测试：

1. Brain crash auto resume 测试。

   模拟：

   - run 已经 `running`。
   - `claim_expires_at` 过期。
   - recovery worker 扫描后把 run 改成 `resume_queued`。

2. Task attempt heartbeat 测试。

   模拟：

   - `task_attempts.status = running`。
   - heartbeat 过期。
   - attempt 标记为 failed/stale。
   - 对应 task 标记为 `resume_queued`。

3. Failed tool recovery context 测试。

   模拟：

   - tool1 成功。
   - tool2 执行中 Sandbox Pod crash。
   - DB 里有 failed `tool_calls`。
   - 下一次 query 收到结构化 recovery context。

4. Idempotency key 测试。

   同一个 session 重复提交相同 idempotency key：

   - 只创建一个 run。
   - 第二次返回已有 run。

Advanced 阶段测试：

1. Event replay consistency 测试。
   从 `session_events` replay 出来的状态，要和 `runs/tasks` 当前状态一致。

2. Schema version compatibility 测试。
   老版本 handoff 可以被读取，新版本 handoff 可以被写入。

3. Audit hash chain 测试。
   修改中间 event payload 后，hash chain 校验失败。

### 5.2 Security Management 测试

Level 1 测试：

1. Agent tool allowlist 测试。

   验证 Agent options 只允许：

   ```text
   mcp__coding__*
   ```

   并且禁用：

   ```text
   Bash
   Read
   Write
   Edit
   Glob
   Grep
   ```

2. Sandbox secret injection 测试。

   检查 Sandbox Pod manifest：

   - 没有 `GITHUB_TOKEN`。
   - 没有 `ANTHROPIC_API_KEY`。
   - `automountServiceAccountToken = false`。

3. Broker secret boundary 测试。

   验证：

   - GitHub Broker 有 `GITHUB_TOKEN`。
   - Brain 没有 `GITHUB_TOKEN`。
   - Sandbox 没有 `GITHUB_TOKEN`。

4. 路径逃逸测试。

   调用：

   ```text
   read_workspace_file("../outside.txt")
   ```

   应该失败。

5. Tool routing 测试。

   验证：

   - `clone_github_repository` 走 GitHub Broker。
   - `push_current_git_branch` 走 GitHub Broker。
   - `write_workspace_file` 走 Sandbox。
   - `run_python_unittest` 走 Sandbox。

Level 2 测试：

1. NetworkPolicy manifest 测试。

   CI 检查必须存在：

   - default deny ingress。
   - default deny egress。
   - Brain allow Session/Sandbox/Broker。
   - Web allow Session。
   - Sandbox Manager allow Kubernetes API。

2. SecurityContext manifest 测试。

   每个 deployment 必须包含：

   ```yaml
   allowPrivilegeEscalation: false
   runAsNonRoot: true
   capabilities.drop: ["ALL"]
   seccompProfile.type: RuntimeDefault
   ```

3. Runtime network negative test。

   在 Sandbox Pod 中尝试访问：

   ```text
   http://cloud-agent-session:8000/healthz
   http://cloud-agent-github-broker:8000/healthz
   Kubernetes API
   ```

   应该失败，除非 policy 明确允许。

4. RBAC negative test。

   用 Sandbox Manager ServiceAccount 检查：

   ```bash
   kubectl auth can-i get secrets
   kubectl auth can-i create pods
   kubectl auth can-i get pods/log
   ```

   期望：

   ```text
   get secrets -> no
   create pods -> yes
   get pods/log -> yes
   ```

Level 3 测试：

1. Per-run namespace 测试。
   Run 创建后 namespace 存在，run 结束后 namespace 被清理。

2. Secret scanning 测试。
   Workspace 中写入假 token，push/PR 前必须被阻止。

3. Policy admission 测试。
   提交一个缺少 securityContext 的 Pod manifest，应被 OPA/Kyverno 拒绝。

## 6. 推荐实施顺序

如果目标是面试 bonus 加分，建议优先做两块。

第一块：Session Median 最小实现。

- `runs.claimed_by`
- `runs.claim_expires_at`
- `runs.last_heartbeat_at`
- Brain heartbeat
- stale running auto requeue
- 对应单元测试和 crash recovery 测试

第二块：Security Level 2 的 manifest 和测试。

- NetworkPolicy
- securityContext
- resource limits
- manifest 单元测试
- RBAC `can-i` 验证脚本或文档

这两块最贴合面试官的问题：

- Session handling: 不只是能 resume，而是有 lease、heartbeat、auto recovery。
- Security isolation: 不只是说 sandbox 没有 secret，而是有 Kubernetes 网络、RBAC、Pod security 的分层模型。
