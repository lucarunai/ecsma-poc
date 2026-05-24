# Session Handling 与 Security Isolation 实际实现记录

本文档记录 `session-security-upgrade-plan.zh.md` 之后，当前 PoC 在 Session Management、Security Isolation、Human Approval、Replay Audit 和 User Ownership 方面已经实际落地的改动。

原计划文档只保留方案讨论，不在本文档中修改或覆盖。

## 1. 总览

这轮实现围绕三个阶段推进：

| 阶段 | 原目标 | 当前实际实现 |
| --- | --- | --- |
| Easy | 证明 session 状态不在内存里，Pod crash 后可以恢复 | 增加 run 状态机、idempotency key、TTL/archive、resume event、run-level acceptance criteria 持久化 |
| Median | Brain Pod crash 后自动恢复，不依赖用户手动 wake | 增加 run lease、heartbeat、attempt heartbeat、expired lease requeue、结构化 recovery context、多 Brain Worker 基础支持 |
| Advanced | 多租户、可审计、强一致的 Agent Session 平台 | 增加 versioned contracts、hash-chain events、replay consistency report、human approval、user_id ownership 隔离 |

当前系统仍然是 PoC，不是完整 production platform。已实现的是能演示关键架构能力的基础版。

## 2. Easy 阶段实际实现

### 2.1 Run 状态机

新增 `src/cloud_agent_poc/session_policy.py`，集中定义 run 状态转换：

```text
queued -> running
resume_queued -> running / failed
running -> completed / blocked / failed / resume_queued
blocked -> resume_queued
failed -> resume_queued
completed -> terminal
```

核心逻辑：

- `RUN_STATUS_TRANSITIONS` 定义合法状态跳转。
- `validate_run_status_transition()` 拒绝非法转换。
- `PostgresSessionStore.update_run()` 更新 run 前会读取当前状态并校验。

效果：

- 防止 `completed -> running`、`completed -> resume_queued` 这类不合理跳转。
- 让状态变更从“随便写字符串”变成显式状态机。

相关代码：

- `src/cloud_agent_poc/session_policy.py`
- `src/cloud_agent_poc/session_store.py`
- `tests/test_session_policy.py`

### 2.2 Run idempotency key

为 `runs` 增加：

```text
idempotency_key
```

并增加唯一索引：

```sql
CREATE UNIQUE INDEX IF NOT EXISTS runs_session_id_idempotency_key_idx
    ON runs (session_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL;
```

创建 run 时：

- 前端生成 `idempotency_key`。
- Web API 传给 Session API。
- Session Store 用 `ON CONFLICT (session_id, idempotency_key)` 返回已有 run。

效果：

- 用户重复点击 Run 时，不会创建两个重复 run。
- 同一个 session 下同一个 idempotency key 只对应一个 run。

相关代码：

- `src/cloud_agent_poc/static/index.html`
- `src/cloud_agent_poc/web.py`
- `src/cloud_agent_poc/session_app.py`
- `src/cloud_agent_poc/session_client.py`
- `src/cloud_agent_poc/session_store.py`
- `src/cloud_agent_poc/schema.sql`
- `db/schema.sql`

### 2.3 TTL 与 Archive

新增字段：

```text
sessions.expires_at
sessions.archived_at
runs.retention_until
runs.archived_at
runs.archive_reason
```

当前最终选择是 archive，不做 purge，不删除历史数据。

Archive 逻辑：

- 过期 session 设置 `status = archived`、`archived_at = now()`。
- 过期 run 设置 `archived_at = now()`、`archive_reason = retention_expired`。
- 不删除 `tasks`、`session_events`、`task_handoffs`、`tool_calls`、`tool_executions` 等审计数据。

接口：

```text
POST /internal/retention/archive
```

效果：

- 可以表达数据生命周期。
- 保留审计链路，不因为清理破坏 replay 或排查能力。

相关代码：

- `src/cloud_agent_poc/session_policy.py`
- `src/cloud_agent_poc/session_app.py`
- `src/cloud_agent_poc/session_store.py`
- `src/cloud_agent_poc/schema.sql`

### 2.4 Resume events

补充了更明确的恢复事件：

```text
run.resume.requested
run.resume.started
run.resume.context_loaded
run.resume.completed
run.resume.exhausted
run.lease.expired
```

效果：

- 前端 timeline 更容易解释恢复过程。
- replay/audit 可以区分“用户手动 wake”和“lease 过期自动恢复”。

相关代码：

- `src/cloud_agent_poc/session_app.py`
- `src/cloud_agent_poc/brain/orchestrator.py`
- `src/cloud_agent_poc/session_store.py`

## 3. Median 阶段实际实现

### 3.1 Run lease 与 heartbeat

为 `runs` 增加：

```text
claimed_by
claim_expires_at
last_heartbeat_at
attempt_count
```

Brain Worker claim run 时：

- 从 `queued` / `resume_queued` 中取一个 run。
- 用 `FOR UPDATE SKIP LOCKED` 防止多个 worker 抢到同一个 run。
- 设置 `status = running`。
- 写入 `claimed_by`、`claim_expires_at`、`last_heartbeat_at`。
- `attempt_count = attempt_count + 1`。

Brain Worker 执行时：

- 后台 heartbeat 定期刷新 `last_heartbeat_at` 和 `claim_expires_at`。
- 如果 heartbeat 失败，当前 worker 停止继续处理这个 run。

效果：

- Brain Pod 正在执行时如果 crash，lease 会过期。
- 下一轮 worker 可以发现并把 run 自动放回 `resume_queued`。

相关代码：

- `src/cloud_agent_poc/brain/worker.py`
- `src/cloud_agent_poc/session_store.py`
- `src/cloud_agent_poc/session_app.py`
- `src/cloud_agent_poc/session_client.py`

### 3.2 Expired lease 自动恢复

新增自动扫描逻辑：

```text
running run + claim_expires_at < now() -> resume_queued
```

自动 requeue 时还会：

- 把当前 running task 设为 `resume_queued`。
- 把 running task attempt 标记为 `failed`。
- 写入 `failure_kind = brain_crash`。
- 把 requested / running / awaiting_approval / approved 的 tool call 标记为 `orphaned`。
- 把 pending approval 标记为 `expired`。
- 发出 `run.lease.expired`、`tool.call.orphaned`、`approval.expired`、`run.resume.requested` 事件。

效果：

- Brain crash 后不依赖用户点 wake。
- 新 worker 会带着 recovery bundle 重新发起 fresh Agent SDK query。
- 如果 crash 发生在 tool 调用中间，下次 agent 会看到 orphaned tool call 和 workspace 状态，由 agent 决定验证、重试或继续。

相关代码：

- `src/cloud_agent_poc/session_store.py`
- `src/cloud_agent_poc/session_app.py`
- `src/cloud_agent_poc/brain/worker.py`

### 3.3 Task attempt heartbeat

为 `task_attempts` 增加：

```text
last_heartbeat_at
heartbeat_expires_at
failure_kind
failure_reason
```

Run heartbeat 会同步刷新当前 running task attempt 的 heartbeat 字段。

效果：

- 不只知道 run 卡住，也能知道是哪一次 fresh agent query 卡住。
- recovery context 能把最近 attempts 的失败类型、heartbeat 时间一起喂给 agent。

相关代码：

- `src/cloud_agent_poc/session_store.py`
- `src/cloud_agent_poc/brain/orchestrator.py`
- `src/cloud_agent_poc/schema.sql`

### 3.4 Failure kind 区分

当前基础版已经区分：

```text
brain_crash
model_error
human_denied
approval_timeout
```

Tool 相关失败也会通过 tool call / tool execution envelope 保留：

```text
tool_error
sandbox_runtime_error
```

效果：

- 恢复时 agent 可以知道“这是 Brain crash，不一定是业务失败”。
- 前端和 audit 可以区分模型失败、人工拒绝、审批超时、tool runtime 失败。

相关代码：

- `src/cloud_agent_poc/session_policy.py`
- `src/cloud_agent_poc/brain/orchestrator.py`
- `src/cloud_agent_poc/brain/sdk_tools.py`
- `src/cloud_agent_poc/session_store.py`

### 3.5 结构化 recovery context

Recovery context 从自由文本升级为 JSON 契约：

```text
schema_version = recovery_context.v1
```

内容包含：

- 当前 task。
- 最近 task attempts。
- failed / orphaned / requested / running / awaiting_approval tool calls。
- 最近 handoff。
- resume guidance。

每次 fresh Agent SDK query 会把这个结构化上下文放进 prompt，让 agent 自己决定下一步。

效果：

- Harness 提供事实和证据。
- Agent 保持决策权。
- 符合 “Close the loop: AI agents must be able to verify their own work” 的方向。

相关代码：

- `src/cloud_agent_poc/brain/orchestrator.py`
- `src/cloud_agent_poc/brain/claude_agent.py`
- `src/cloud_agent_poc/session_contracts.py`

### 3.6 多 Brain Worker 基础支持

当前 v2 部署中 Brain Worker 副本数调整为 3。

并发控制依赖：

- Postgres `FOR UPDATE SKIP LOCKED`。
- Run lease。
- `claimed_by` worker id。
- Heartbeat 续租。

效果：

- 多个 worker 可以同时处理不同 run。
- 同一个 run 在同一时刻只会被一个 worker claim。
- worker crash 后，lease 过期再由其他 worker 接手。

相关代码：

- `src/cloud_agent_poc/brain/worker.py`
- `src/cloud_agent_poc/session_store.py`
- `k8s-v2/brain.yaml`

## 4. Advanced 阶段实际实现

### 4.1 Versioned data contracts

新增 `src/cloud_agent_poc/session_contracts.py`，统一定义关键 JSON 契约：

```text
session_event.v1
run_acceptance_criteria.v1
task_handoff.v1
tool_execution_envelope.v1
recovery_context.v1
```

当前校验范围：

- `task_handoff` 写入前校验 `schema_version` 和核心字段。
- `tool_execution_envelope` 写入前校验 execution id、run id、tool call id、tool name、execution status。
- `recovery_context` 生成时带 schema version。
- replayable events 定义 required fields。

效果：

- 关键 JSON 不再只是任意 dict。
- 后续演进时可以通过 schema version 做兼容。

相关代码：

- `src/cloud_agent_poc/session_contracts.py`
- `src/cloud_agent_poc/session_store.py`
- `src/cloud_agent_poc/brain/orchestrator.py`
- `tests/test_session_contracts.py`

### 4.2 Append-only event envelope 与 hash chain

`session_events` 增加：

```text
user_id
seq
schema_version
actor_type
actor_id
payload_hash
previous_event_hash
event_hash
```

写入事件时：

- 根据 run 或 session scope 生成单调递增 `seq`。
- 对 payload 做 canonical JSON hash。
- 用 `previous_event_hash + event_type + schema_version + seq + payload_hash` 生成当前 event hash。

效果：

- 可以检查事件是否被篡改。
- 可以验证事件顺序。
- 可以为 replay consistency check 提供基础。

相关代码：

- `src/cloud_agent_poc/session_store.py`
- `src/cloud_agent_poc/session_contracts.py`
- `src/cloud_agent_poc/schema.sql`

### 4.3 Replay consistency check

新增 `src/cloud_agent_poc/session_replay.py`。

Replay 当前做的是 audit / consistency check，不重新执行 agent 或 tool。

Replay 输入：

- `session_events`
- materialized tables: `runs`、`tasks`、`tool_calls`

Replay 输出：

```text
schema_version = replay_report.v1
run_id
replay_valid
hash_chain_valid
consistent
errors
differences
replayed_state
materialized_state
```

检查内容：

- event payload 是否满足契约。
- hash chain 是否连续。
- run/task/tool_call 状态转换是否合法。
- replay 得出的状态是否和数据库 materialized state 一致。

接口：

```text
GET /internal/runs/{run_id}/replay
GET /api/runs/{run_id}/replay
```

前端：

- Run 面板增加 Replay 区域。
- 支持手动点击 Replay。
- Run 到 terminal event 后自动刷新 replay report。

效果：

- 能证明 event ledger 和当前表状态一致。
- 发现 projection drift 时可以展示差异。
- 为后续 event-sourcing 或修复工具打基础。

相关代码：

- `src/cloud_agent_poc/session_replay.py`
- `src/cloud_agent_poc/session_store.py`
- `src/cloud_agent_poc/session_app.py`
- `src/cloud_agent_poc/web.py`
- `src/cloud_agent_poc/static/index.html`
- `tests/test_session_replay.py`

### 4.4 Human-in-the-loop approval

新增 `approval_requests` 表。

字段包括：

```text
id
session_id
run_id
user_id
task_id
task_attempt_id
tool_call_id
tool_name
tool_input
reason
status
requested_by
decided_by
decision_reason
created_at
decided_at
```

当前高风险操作：

```text
push_current_git_branch
create_github_pull_request
```

处理流程：

1. Agent 调用高风险 tool。
2. SDK tool layer 创建 `approval_requests`。
3. 对应 `tool_calls.status` 变成 `awaiting_approval`。
4. Session event 发出 `approval.requested`。
5. 前端展示 approval card。
6. 用户点击 Approve 或 Deny。
7. Session API 更新 approval status。
8. Brain polling 到 approval 结果后继续执行或失败退出。

Crash 处理：

- 如果 Brain crash 时 approval 仍 pending，expired lease requeue 会把 approval 标记为 `expired`。
- 对应 tool call 标记为 orphaned / failed，并写入事件。

效果：

- 高风险 GitHub 写操作不会直接执行。
- 用户可以在 UI 中批准或拒绝。
- 审计事件记录完整审批链路。

相关代码：

- `src/cloud_agent_poc/tool_policy.py`
- `src/cloud_agent_poc/brain/sdk_tools.py`
- `src/cloud_agent_poc/session_store.py`
- `src/cloud_agent_poc/session_app.py`
- `src/cloud_agent_poc/web.py`
- `src/cloud_agent_poc/static/index.html`
- `tests/test_sdk_tool_execution.py`
- `tests/test_static_ui_contract.py`

### 4.5 User ownership 隔离

当前先实现一层 user-level ownership，没有引入 tenant/project。

新增 `src/cloud_agent_poc/ownership.py`：

```text
DEFAULT_USER_ID = demo-user
normalize_user_id()
```

新增或使用 `user_id` 的表：

```text
sessions.user_id
runs.user_id
session_events.user_id
approval_requests.user_id
```

API 约定：

- Web/API 使用 `X-User-Id` header。
- SSE/EventSource 不能带自定义 header，所以 events endpoint 使用 `?user_id=...`。
- 跨用户访问按当前设计返回 `404` 或等价的“查不到”，不返回 `403`，避免泄露资源是否存在。

前端：

- 增加 User input。
- 默认 `Luca`。
- 点击 Switch 会创建该 user 下的新 session，并重置 run/task/timeline/replay/approval 状态。

数据库隔离：

- `create_run` 要求 session 属于当前 user。
- `get_run`、`wake_run`、`list_events`、`replay`、`approval decision` 都按 `user_id` 过滤。

Workspace 隔离：

Sandbox workspace path 从：

```text
/sandboxes/{run_id}
```

升级为：

```text
/sandboxes/users/{user_id}/{run_id}
```

Sandbox 和 GitHub Broker 接收 `workspace_path`，并校验路径必须位于 workspace root 内，防止任意路径逃逸。

效果：

- Luca 创建的 run，Josephine 通过同一个 run id 查询不到。
- 不同 user 的 workspace path 隔离。
- Approval decision 也按 user 隔离。

相关代码：

- `src/cloud_agent_poc/ownership.py`
- `src/cloud_agent_poc/session_app.py`
- `src/cloud_agent_poc/session_client.py`
- `src/cloud_agent_poc/session_store.py`
- `src/cloud_agent_poc/web.py`
- `src/cloud_agent_poc/static/index.html`
- `src/cloud_agent_poc/sandbox_app.py`
- `src/cloud_agent_poc/github_broker_app.py`
- `src/cloud_agent_poc/sandbox_protocol.py`
- `tests/test_ownership.py`

## 5. Security Isolation 实际实现补充

### 5.1 GitHub secret 边界

当前仍保持 GitHub secret 不进入 sandbox pod。

分层：

- Sandbox Layer 执行无 secret 的 workspace/local git/test/edit/read 操作。
- GitHub Broker 持有 GitHub token，处理 clone/fetch/push/PR 相关 trusted 操作。
- Brain 只通过 tool abstraction 调用，不直接读取 secret。

效果：

- 不可信 workspace tool 看不到 GitHub token。
- 高风险写操作再叠加 human approval。

相关代码：

- `src/cloud_agent_poc/github_broker_app.py`
- `src/cloud_agent_poc/github_broker_client.py`
- `src/cloud_agent_poc/sandbox_app.py`
- `src/cloud_agent_poc/brain/sdk_tools.py`

### 5.2 Sandbox workspace path 校验

Sandbox 和 GitHub Broker 都会把传入的 workspace path resolve 后校验：

- 必须在配置的 workspace root 内。
- 不允许 `../` 或绝对路径逃逸。

效果：

- 即使 agent/tool input 试图传入异常路径，也不会跳出 sandbox workspace root。

相关代码：

- `src/cloud_agent_poc/sandbox_app.py`
- `src/cloud_agent_poc/github_broker_app.py`
- `src/cloud_agent_poc/sandbox_protocol.py`

### 5.3 Brain Pod Claude config 隔离

多 Brain Worker 后，Claude config/session/transcript 根目录不再共享同一个路径。

当前设计：

- 每个 Brain Pod 使用自己的 volume/config path。
- Provider transcript/session id 不作为恢复主链路。
- 系统恢复依赖 Session Layer 中的 durable state、handoff、recovery bundle。

效果：

- 3 个 Brain Worker 不会互相写同一个 Claude 根目录。
- Transcript 主要用于审计和调试，不参与核心恢复。

相关文件：

- `k8s-v2/brain.yaml`
- `src/cloud_agent_poc/brain/claude_agent.py`

## 6. 测试覆盖

当前新增或扩展的测试方向：

| 测试文件 | 覆盖内容 |
| --- | --- |
| `tests/test_session_policy.py` | run 状态机、TTL/retention helper |
| `tests/test_session_contracts.py` | JSON 契约、hash、payload 校验 |
| `tests/test_session_replay.py` | replay 状态重建、hash chain、projection drift |
| `tests/test_ownership.py` | user id normalization、API user scoping、workspace path user scoping |
| `tests/test_sdk_tool_execution.py` | tool call envelope、approval、workspace path 传递 |
| `tests/test_static_ui_contract.py` | 前端 replay、approval、user switcher 关键 UI contract |
| `tests/test_schema_contract.py` | schema 字段和索引存在性 |

推荐本地验证命令：

```bash
PYTHONPATH=src python3 -m unittest discover -v
```

## 7. 当前保留的限制

当前仍未实现或只实现基础版的能力：

- 没有完整 tenant/project 三层 ownership，只做了 user_id。
- 没有 Postgres RLS，ownership enforcement 在 API/store 层。
- Replay 只做 consistency check，不自动修复 materialized tables。
- Approval policy 目前是硬编码 tool 级规则，不是动态策略引擎。
- Sandbox 网络隔离和 Kubernetes NetworkPolicy 仍可继续加强。
- Event hash chain 可以证明应用层事件被修改，但不是外部不可篡改存证。

## 8. 面试叙述建议

可以这样总结：

当前 PoC 已经从“一个 agent workflow demo”升级为“durable session harness demo”。

Session Layer 是 source of truth，保存 run/task/attempt/handoff/tool/event/approval 状态；Brain Layer 是 stateless-ish orchestrator，通过 lease/heartbeat claim run，crash 后由其他 worker 自动恢复；Sandbox Layer 只执行无 secret 的 workspace tool，GitHub Broker 独立持有 secret；Advanced 阶段又补了 schema version、hash-chain audit、replay consistency、human approval 和 user-level ownership。

这不是完整 production SaaS，但已经覆盖面试官强调的两个核心点：

1. Session handling：状态持久化、自动恢复、多 worker claim、fresh query recovery context。
2. Security isolation：sandbox 无 secret、workspace/user 隔离、高风险操作 human approval、可审计事件链。
