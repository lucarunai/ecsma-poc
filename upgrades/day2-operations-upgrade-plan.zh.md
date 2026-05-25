# Day 2 Operations 升级计划

本文档讨论 Cloud Agent Platform PoC 在 Day 2 Operations 方向的升级方案。这里的 Day 2 不是指初次部署，而是系统已经跑起来之后，如何观察、诊断、支持用户、形成持续改进闭环。

本文档是 Day 2 Operations 的方案文档。已经落地的内容以 `day2-operations-upgrade-implementation.zh.md` 为准；尚未落地的阶段在本文档中作为后续设计路线。

## 1. 问题定义

Day 2 Operations 需要回答三类问题：

1. 系统现在是否健康？
2. 某个 run / task / tool 为什么慢、失败、卡住或需要人工介入？
3. 平台如何从真实运行数据里持续改进 planner、agent loop、sandbox、security policy 和 customer support 流程？

这和前面三个 upgrade 的关系是：

| 已有方向 | 解决的问题 | Day 2 继续补的问题 |
| --- | --- | --- |
| Session handling | 状态持久化、crash recovery、多 worker claim | 如何知道恢复是否频繁发生、是否有效、哪里最容易卡住 |
| Security isolation | sandbox secretless、network/resource/file 边界 | 如何发现策略拦截、异常 egress、资源滥用、approval 高风险操作趋势 |
| Sandbox layer | one-shot Pod、runtime envelope、resource evidence | 如何衡量 sandbox latency、failure kind、resource pressure、workspace growth |

核心思路：不要先引入复杂 APM 平台，而是先把现有 durable state 变成可查询、可解释、可导出的 operational evidence。

## 2. 当前已经具备的 Day 2 基础

当前系统已经有比较好的原材料。

### 2.1 Durable Event Ledger

`session_events` 已经记录：

- session lifecycle。
- run lifecycle。
- task lifecycle。
- tool call / execution。
- approval requested / approved / denied / expired。
- resume / lease expired / recovery 相关事件。

并且已经有：

- `event_hash` hash chain。
- replay report。
- materialized state 对比。
- SSE timeline。

这意味着系统已经有 audit trail 的雏形。

### 2.2 Materialized State

当前数据库已经有：

- `sessions`
- `runs`
- `tasks`
- `task_attempts`
- `task_handoffs`
- `tool_calls`
- `tool_executions`
- `approval_requests`
- `session_events`

这些表分别保存：

- 当前状态。
- 历史 attempt。
- tool input。
- runtime envelope。
- human approval。
- replay/audit 事件。

### 2.3 Failure Classification

当前已经有 `failure_kind`：

- `brain_crash`
- `sandbox_runtime_error`
- `tool_error`
- `model_error`
- `human_approval_required`
- `approval_denied`
- `approval_timeout`

这对 Day 2 很重要，因为 customer support 和 continuous improvement 不能只看 failed，需要知道失败类型。

### 2.4 Runtime Evidence

Sandbox execution envelope 已经包含：

- runtime profile。
- isolation。
- pod name / phase / exit code。
- network policy / egress policy。
- CPU / memory / ephemeral storage / timeout。
- output bytes / truncated。
- workspace bytes / file count。

这已经可以支撑 sandbox 维度的诊断。

### 2.5 Recovery / Lease Evidence

当前已经有：

- `runs.claimed_by`
- `runs.claim_expires_at`
- `runs.last_heartbeat_at`
- `runs.attempt_count`
- `task_attempts.last_heartbeat_at`
- `task_attempts.heartbeat_expires_at`

这可以支撑 Brain worker health、run 卡住诊断、crash recovery 统计。

### 2.6 Human Approval Evidence

当前已经有：

- `approval_requests`
- approval events。
- tool call 状态 `awaiting_approval`。
- approval timeout / denied。

这可以支撑高风险操作审计和 support 问题定位。

## 3. 当前缺口

虽然 raw data 很多，但 Day 2 还缺几层能力。

### 3.1 缺少 Operational Summary

现在要理解一个 run，需要手动拼：

- run row。
- tasks。
- attempts。
- tool calls。
- tool executions。
- events。
- handoffs。
- replay report。

这对开发者可以，对运营和客户支持不够友好。

需要一个稳定的 summary contract，例如：

```json
{
  "schema_version": "ops_run_summary.v1",
  "run_id": "run_xxx",
  "status": "failed",
  "user_id": "Luca",
  "started_at": "...",
  "ended_at": "...",
  "duration_ms": 12345,
  "task_counts": {
    "total": 3,
    "completed": 2,
    "failed": 1
  },
  "tool_counts": {
    "total": 8,
    "succeeded": 7,
    "failed": 1
  },
  "failure_summary": {
    "failure_kind": "sandbox_runtime_error",
    "failed_component": "sandbox",
    "first_failed_task_id": "task_xxx",
    "first_failed_tool_call_id": "toolcall_xxx"
  },
  "recovery_summary": {
    "attempt_count": 2,
    "lease_expired_count": 1,
    "resume_count": 1
  },
  "approval_summary": {
    "requested": 1,
    "approved": 1,
    "denied": 0,
    "expired": 0
  }
}
```

### 3.2 缺少 Metrics / SLO

当前能看单个 run，但不方便回答：

- 最近 24 小时 run 成功率是多少？
- 平均 run duration / p95 duration 是多少？
- 哪个 tool 最常失败？
- sandbox runtime error 是否增加？
- approval 等待时间是否过长？
- Brain lease expired 是否频繁？
- replay drift 是否出现？

需要从 event/table 派生 metrics。

### 3.3 缺少 Support Bundle

客户说“我的 run 卡住了”，支持人员需要一个 bundle：

- run summary。
- user/session/run/task/tool ids。
- prompt 摘要。
- acceptance criteria。
- task handoff。
- failed tool input。
- failed tool execution envelope。
- replay report。
- approvals。
- recovery context。
- sanitized event timeline。

现在这些信息分散在 DB 和 UI 里，还没有一键导出。

### 3.4 缺少 Redaction / Safe Export

Support bundle 不能直接把所有内容暴露出来。需要考虑：

- prompt 可能含敏感信息。
- tool input 可能含 repo URL / branch / file content。
- tool output 可能含路径、代码、测试输出。
- Git command 里已有 auth header redaction，但 support export 仍要统一做 redaction。

需要明确：

- internal support view。
- customer-visible bundle。
- redaction rules。

### 3.5 缺少 Alert / Drill

现在有 smoke/security/chaos 脚本，但还没有把它们组织成 Day 2 运维信号。

需要定义：

- 哪些情况需要 alert。
- 哪些只是 dashboard 上的 warning。
- 哪些用于 weekly review。

例如：

- replay drift 出现：高优先级。
- Brain lease expired 连续增加：高优先级。
- sandbox runtime error spike：中高优先级。
- approval timeout 高：中优先级。
- output truncation 高：产品/工具体验问题。

## 4. Day 2 数据模型分层

建议按四层来设计。

### 4.1 Raw Evidence

原始事实来源，尽量不可变：

- `session_events`
- `tool_calls`
- `tool_executions`
- `task_handoffs`
- `approval_requests`

原则：

- 不直接修改历史 evidence。
- 可通过 event hash chain 做完整性验证。
- support bundle 优先引用 raw evidence。

### 4.2 Materialized State

面向业务当前状态：

- `runs`
- `tasks`
- `task_attempts`

原则：

- 用于 UI 和调度。
- 可以被 replay report 校验。
- 出现 drift 时优先相信 raw event + replay report，再排查投影逻辑。

### 4.3 Derived Operational Views

从 raw evidence / materialized state 派生：

- run summary。
- task summary。
- tool summary。
- failure summary。
- approval summary。
- worker health summary。
- sandbox runtime summary。

这些可以先不建新表，先用查询/API 生成。等数据量变大后再 materialize。

### 4.4 Export / Support Contract

面向支持流程的稳定 JSON：

- `ops_run_summary.v1`
- `support_bundle.v1`
- `ops_metric_snapshot.v1`
- `customer_visible_timeline.v1`

这些 contract 要版本化，避免后续字段变化影响工具和文档。

## 5. 需要的 Observability 信息

### 5.1 Run 维度

需要：

- run id / session id / user id。
- prompt hash / prompt preview。
- status。
- created / started / ended。
- duration。
- current task。
- completion ratio。
- attempt count。
- resume count。
- lease expired count。
- final failure kind。
- archived state。

用途：

- 看 run 是否卡住。
- 看系统整体成功率。
- 看 crash recovery 是否有效。
- 支持用户查询“我的任务为什么没结束”。

### 5.2 Task 维度

需要：

- task id / seq / title。
- status。
- started / ended。
- duration。
- attempt count。
- acceptance criteria count。
- criteria passed / failed / blocked。
- handoff summary。
- last failure kind。

用途：

- 找出 workflow 哪一步最常失败。
- 判断 planner 拆 task 是否合理。
- 判断 acceptance criteria 是否过细或过宽。

### 5.3 Agent / Brain 维度

需要：

- worker id。
- claimed run count。
- active run count。
- heartbeat freshness。
- lease expired count。
- model error count。
- avg task attempt duration。
- prompt/query size。
- model result status。

用途：

- 多 worker 是否均衡。
- Brain crash 是否频繁。
- 模型是否经常输出不合法 JSON。
- Agent loop 是否因为 prompt 太大变慢。

### 5.4 Sandbox 维度

需要：

- tool name。
- runtime profile。
- pod name。
- pod phase。
- exit code。
- duration。
- runtime failure kind。
- stdout/stderr bytes。
- output truncated。
- workspace bytes / file count。
- resource limits。
- network / egress policy。

用途：

- 找出慢 tool。
- 找出常见 sandbox failure。
- 看资源限制是否过严或过松。
- 看 output cap 是否影响 agent 判断。

### 5.5 Tool 维度

需要：

- tool call id。
- tool name。
- status。
- input schema version。
- input preview/hash。
- execution id。
- execution status。
- failure kind。
- duration。
- retries / orphaned 状态。

用途：

- 判断哪个 tool 最容易失败。
- 判断 agent 是否重复调用高成本工具。
- 支持恢复时解释“上一次 tool 到底做了什么”。

### 5.6 Approval 维度

需要：

- approval id。
- tool name。
- requested at。
- decided at。
- status。
- decision latency。
- requested_by / decided_by。
- decision reason。

用途：

- 看高风险操作频率。
- 看用户是否经常忘记批准。
- 看 approval timeout 是否影响体验。
- 支持审计 push / PR 等外部副作用。

### 5.7 Replay / Audit 维度

需要：

- replay_valid。
- hash_chain_valid。
- consistent。
- drift count。
- first drift event id。
- materialized vs replayed diff。

用途：

- 发现状态投影 bug。
- 支持审计证明。
- 帮助 customer support 判断“UI 显示是否可信”。

## 6. Continuous Improvement Feedback Loop

建议形成五步闭环：

```text
Collect evidence
  -> Aggregate metrics
  -> Classify failure / friction
  -> Improve planner / agent / sandbox / product
  -> Verify impact by trend
```

### 6.1 Planner Improvement

看这些指标：

- task 数量分布。
- optional/unrequested task 被过滤次数。
- planner invalid JSON 次数。
- task blocked rate。
- criteria mismatch rate。

可以改进：

- planner prompt。
- task granularity。
- acceptance criteria schema。
- optional task filtering policy。

### 6.2 Agent Loop Improvement

看这些指标：

- task attempt count。
- model_error rate。
- criteria not_verified / failing rate。
- repeated tool call pattern。
- recovery 后是否能成功。

可以改进：

- query context。
- recovery context。
- handoff structure。
- tool descriptions。

### 6.3 Sandbox Improvement

看这些指标：

- tool duration p50/p95/p99。
- sandbox_runtime_error rate。
- pod timeout count。
- output_truncated rate。
- workspace limit exceeded count。
- network policy block count。

可以改进：

- timeout。
- output cap。
- workspace quota。
- runtime profile。
- tool implementation。

### 6.4 Security Policy Improvement

看这些指标：

- approval requested/approved/denied/expired。
- high-risk tool usage。
- path escape blocked count。
- symlink/hardlink blocked count。
- secret redaction hit count。
- replay/hash chain drift。

可以改进：

- human approval policy。
- tool allowlist。
- sandbox filesystem policy。
- support export redaction。

### 6.5 Product / Support Improvement

看这些指标：

- run failure reason distribution。
- average support bundle size。
- time to first failure。
- time awaiting approval。
- top confusing user prompts。
- top repeated support issues。

可以改进：

- UI messaging。
- prompt examples。
- error explanations。
- support playbook。

## 7. Customer Support 需要的信息

### 7.1 Support Bundle

建议新增 `support_bundle.v1`：

```json
{
  "schema_version": "support_bundle.v1",
  "generated_at": "...",
  "visibility": "internal",
  "run_summary": {},
  "session": {},
  "run": {},
  "tasks": [],
  "task_attempts": [],
  "tool_calls": [],
  "tool_executions": [],
  "approvals": [],
  "handoffs": [],
  "replay_report": {},
  "event_timeline": [],
  "redaction": {
    "profile": "internal",
    "fields_redacted": []
  }
}
```

支持两个版本：

| Bundle | 目标用户 | 内容 |
| --- | --- | --- |
| internal support bundle | 平台工程/支持人员 | 信息更完整，但仍 redacts secret |
| customer-visible bundle | 用户/客户 | 更强 redaction，只保留必要解释 |

### 7.2 Support Triage 信息

支持人员第一眼应该能看到：

- run 是否 completed / failed / blocked / awaiting approval。
- 是否是用户操作问题，例如 approval 未批准。
- 是否是平台问题，例如 brain_crash / replay drift。
- 是否是 sandbox 问题，例如 pod failed / timeout / resource limit。
- 是否是模型问题，例如 invalid planner output / criteria mismatch。
- 是否有外部依赖问题，例如 GitHub clone/push/PR。

建议生成一个 triage conclusion：

```json
{
  "category": "sandbox_runtime_error",
  "severity": "medium",
  "customer_action_required": false,
  "platform_action_required": true,
  "summary": "The run failed while executing run_python_unittest in a sandbox pod.",
  "next_actions": [
    "Inspect tool execution envelope.",
    "Check pod phase and exit code.",
    "Review stdout/stderr truncation flags."
  ]
}
```

### 7.3 Support Timeline

面向 support 的 timeline 不应该只是 raw events，而应该分层：

```text
User submitted prompt
Planner created 3 tasks
Task 1 completed
Task 2 requested push approval
User approved after 42 seconds
Sandbox execution failed: timeout
Run blocked with recovery context
```

每一项可链接到 raw event id。

### 7.4 Redaction 策略

建议第一版支持：

- secret-looking pattern redaction。
- GitHub auth header redaction。
- long file content truncation。
- prompt preview + prompt hash。
- tool input preview + input hash。
- command output preview + original bytes。

不要在 customer-visible bundle 中完整导出：

- full prompt。
- full file content。
- full stdout/stderr。
- raw environment。
- internal service URLs。

## 8. 建议实施阶段

### 8.1 Phase 1：Ops Summary 和 Support Bundle

目标：不引入新基础设施，只基于现有 DB 生成可解释的 run support evidence。

建议实现：

1. 新增 ops contract：
   - `ops_run_summary.v1`
   - `support_bundle.v1`
2. Session Layer 增加内部 API：
   - `GET /internal/runs/{run_id}/ops-summary`
   - `GET /internal/runs/{run_id}/support-bundle`
3. Web Layer 增加 API proxy：
   - `GET /api/runs/{run_id}/ops-summary`
   - `GET /api/runs/{run_id}/support-bundle`
4. 前端 Run 面板增加：
   - Ops Summary。
   - Support Bundle JSON。
5. 单测：
   - summary 聚合正确。
   - failure_kind 分类正确。
   - support bundle 包含 replay report。
   - user ownership enforce。
   - redaction 不泄露 secret-looking values。

这是最适合当前 PoC 的第一步。

### 8.2 Phase 2：Metrics Snapshot

目标：回答“最近一段时间平台表现如何”。

建议实现：

1. 新增 `ops_metric_snapshot.v1`。
2. Session Layer 增加查询：
   - run success rate。
   - failure kind distribution。
   - tool failure distribution。
   - approval latency。
   - sandbox duration。
   - lease expired count。
   - replay drift count。
3. 前端增加 lightweight Ops Dashboard。
4. 测试：
   - fixture 数据下聚合准确。
   - user scope 只看当前 user。
   - admin/internal scope 后续再讨论。

第一版可以不落新表，直接 query 聚合。

### 8.3 Phase 3：Alert / Drill

目标：把 smoke/security/chaos 从手动脚本变成 operational signal。

建议实现：

1. 定义 alert rules：
   - replay drift。
   - lease expired spike。
   - sandbox runtime error spike。
   - approval timeout spike。
   - support bundle redaction failure。
2. 新增 ops check 脚本：
   - `scripts/k8s-ops-verify-v2.sh`
3. CI / local drill 运行：
   - smoke。
   - security。
   - recovery chaos。
   - ops summary/support bundle。
4. 后续再考虑 Prometheus/OpenTelemetry。

### 8.4 Phase 4：External Observability Integration

目标：接入生产常见 observability 栈。

Phase 4 的定位不是替代 Session Layer，也不是把所有状态搬到 APM 工具里。当前系统的 source of truth 仍然是 Postgres durable state：

- `runs`
- `tasks`
- `task_attempts`
- `task_handoffs`
- `tool_calls`
- `tool_executions`
- `approval_requests`
- `session_events`

External observability 的职责是把这些事实转成生产常见的运行信号：

- metrics 用于趋势、SLO、alert。
- logs 用于事件检索和排障细节。
- traces 用于跨服务链路定位。
- dashboard 用于持续改进和支持团队查看。
- alertmanager / paging 用于主动发现问题。

换句话说：

```text
Postgres durable state = truth
Ops contracts = product semantics
Observability stack = external signal and workflow
```

#### 8.4.1 建议总体架构

```mermaid
flowchart LR
  DB[(Postgres durable state)]
  Store[Session Store ops queries]
  Contracts[Ops Contracts<br/>ops_metric_snapshot.v1<br/>ops_alerts.v1<br/>ops_run_summary.v1<br/>support_bundle.v1]
  Metrics[/metrics<br/>Prometheus text]
  Logs[Structured JSON Logs]
  Traces[OpenTelemetry Spans]
  Collector[OpenTelemetry Collector]
  Prom[Prometheus]
  Loki[Loki / log backend]
  Tempo[Tempo / trace backend]
  Grafana[Grafana]
  Alertmanager[Alertmanager]

  DB --> Store
  Store --> Contracts
  Contracts --> Metrics
  Metrics --> Prom
  Logs --> Collector
  Traces --> Collector
  Collector --> Loki
  Collector --> Tempo
  Prom --> Grafana
  Loki --> Grafana
  Tempo --> Grafana
  Prom --> Alertmanager
```

注意边界：

- `support_bundle.v1` 仍然来自 DB evidence，不从 Prometheus 反推。
- replay/audit 仍然来自 `session_events` hash chain，不从 logs 反推。
- Prometheus 只保存低基数聚合指标，不保存 prompt、tool raw output、run 级大对象。
- trace/log 可以带 correlation id，但不能带 secret 或完整 prompt。

#### 8.4.2 Metrics：Prometheus Exporter

第一步建议新增：

```text
GET /metrics
```

初期可以放在 Session Layer，因为 Session Layer 离 durable state 最近，也已经有 `get_ops_metric_snapshot(...)`。

建议从 `ops_metric_snapshot.v1` 派生 Prometheus text format：

```text
cloud_agent_runs_total{status="completed"} 2
cloud_agent_runs_total{status="failed"} 1
cloud_agent_run_success_rate 0.6667
cloud_agent_run_failure_rate 0.3333

cloud_agent_tasks_total{status="completed"} 5
cloud_agent_task_attempts_total{status="completed"} 5

cloud_agent_tool_calls_total{tool_name="git_status",status="succeeded"} 7
cloud_agent_tool_failures_total{tool_name="run_python_unittest"} 1
cloud_agent_tool_duration_ms_p95 2200

cloud_agent_approvals_total{status="pending"} 0
cloud_agent_approval_decision_latency_ms_avg 37271

cloud_agent_replay_drift_total 0
cloud_agent_replay_hash_chain_invalid_total 0

cloud_agent_brain_lease_expired_total 0
cloud_agent_resume_requested_total 1
cloud_agent_orphaned_tool_calls_total 0

cloud_agent_sandbox_output_truncated_total 0
cloud_agent_sandbox_workspace_bytes_max 0
```

低基数 label 可以使用：

- `status`
- `tool_name`
- `failure_kind`
- `runtime_profile`
- `pod_phase`
- `environment`
- `namespace`

不要把这些放进 Prometheus label：

- `run_id`
- `session_id`
- `task_id`
- `task_attempt_id`
- `tool_call_id`
- `prompt`
- `workspace_path`
- raw `user_id`

原因：这些都是高基数字段，会让 Prometheus series 数量爆炸。需要按 id 检索时，用 logs、traces、support bundle。

第一版可以只暴露当前 user scope 之外的 platform-level 聚合，或者 internal-only 的 namespace-level 聚合。因为当前系统已经实现 user-level ownership，后续如果做 multi-tenant production，需要明确 metrics 是否按 tenant/tier 聚合，而不是直接按 raw user_id label。

#### 8.4.3 Structured Logs

建议所有服务输出 JSON logs：

- Web
- Session
- Brain
- Sandbox Manager
- GitHub Broker
- one-shot Sandbox tool Pod

每条日志至少包含：

```json
{
  "timestamp": "...",
  "level": "info",
  "service": "brain",
  "event": "run.claimed",
  "run_id": "run_xxx",
  "task_id": "task_xxx",
  "task_attempt_id": "taskattempt_xxx",
  "tool_call_id": null,
  "trace_id": "...",
  "span_id": "...",
  "worker_id": "cloud-agent-brain-xxx",
  "status": "running"
}
```

日志要覆盖的关键事件：

- `session.created`
- `run.created`
- `run.claimed`
- `run.heartbeat`
- `run.lease.expired`
- `task.started`
- `task.completed`
- `task.failed`
- `agent.query.started`
- `agent.query.completed`
- `tool.call.requested`
- `tool.execution.started`
- `tool.execution.completed`
- `tool.execution.failed`
- `approval.requested`
- `approval.approved`
- `approval.denied`
- `sandbox.pod.created`
- `sandbox.pod.completed`
- `sandbox.pod.failed`
- `github.broker.requested`
- `github.broker.completed`

日志中禁止输出：

- raw `GITHUB_TOKEN`
- raw `ANTHROPIC_API_KEY`
- authorization headers
- full prompt
- full file content
- full stdout/stderr
- raw environment variables

日志可以输出：

- prompt hash / preview
- tool input hash / preview
- output bytes / truncated flag
- failure_kind
- status
- correlation ids

#### 8.4.4 OpenTelemetry Traces

当前系统天然有跨层链路：

```text
Web request
  -> Session API
  -> Brain Worker
  -> Agent query
  -> MCP tool call
  -> Tool Router
  -> Sandbox Manager / GitHub Broker
  -> one-shot Sandbox Pod
  -> tool result
  -> Agent result
  -> handoff
```

建议 trace 设计：

- `run_id` 是业务 correlation id。
- `task_attempt_id` 是一次 fresh agent query 的主要 trace root。
- 每个 tool call 是 child span。
- sandbox pod execution 是 tool call 的 child span。
- GitHub Broker call 是 trusted tool span。

建议 span：

```text
cloud_agent.web.request
cloud_agent.session.create_run
cloud_agent.run.claim
cloud_agent.task_attempt
cloud_agent.agent_query
cloud_agent.tool_call
cloud_agent.sandbox_execution
cloud_agent.github_broker_call
cloud_agent.handoff_persist
cloud_agent.replay_check
cloud_agent.support_bundle_generate
```

建议 span attributes：

```text
run_id
task_id
task_attempt_id
tool_call_id
tool_name
failure_kind
run_status
task_status
sandbox_runtime_profile
pod_phase
approval_required
resume_attempt
user_id_hash
```

注意：trace attributes 可以带 `run_id` 这种检索字段，但仍然不能带 secret、raw prompt、raw tool output。

#### 8.4.5 Grafana Dashboards

建议先做 4 个 dashboard。

**Platform Health**

- run success rate
- run failure rate
- p50/p95 run duration
- queued/running/resume_queued count
- task attempt duration
- model error count
- replay drift count

**Agent Recovery**

- lease expired count
- resume requested count
- resume completed count
- brain crash count
- orphaned tool calls
- task attempt retry count
- recovery success/failure trend

**Sandbox Runtime**

- sandbox execution count by tool
- sandbox failure kind distribution
- pod phase distribution
- p50/p95 sandbox duration
- output truncated count
- workspace bytes max
- file count max
- timeout/resource failure trend

**Customer Support**

- failed runs by failure_kind
- pending approvals
- approval decision latency
- support bundle generated count
- customer-visible bundle redaction failures
- top failing tools
- replay/hash chain invalid count

#### 8.4.6 Alertmanager Rules

Phase 3 已经有 `ops_alerts.v1`，Phase 4 可以把这些业务 alert 转成 Prometheus/Alertmanager rules。

建议初始 alerts：

```text
ReplayDriftDetected
ReplayHashChainInvalid
BrainLeaseExpired
RunFailureRateHigh
SandboxRuntimeErrorSpike
ToolFailureRateHigh
ApprovalPendingTooLong
ApprovalExpiredSpike
SandboxOutputTruncatedSpike
SupportBundleRedactionFailure
```

要区分两类告警：

| 类型 | 示例 | 处理方式 |
| --- | --- | --- |
| Infra alert | pod down、DB unavailable、CPU/memory high | 平台值班处理 |
| Product alert | replay drift、run failure rate high、approval pending too long | 平台/产品/支持共同处理 |

这个系统更有价值的是 product alert，因为它体现 agent workflow 的业务语义，而不是只看 Kubernetes 是否活着。

#### 8.4.7 建议实施拆分

**Phase 4A：Prometheus Metrics Exporter**

目标：把已有 `ops_metric_snapshot.v1` 暴露成生产常见 metrics format。

建议实现：

1. 新增 metrics 转换模块，例如：
   - `src/cloud_agent_poc/observability.py`
   - `render_prometheus_metrics(snapshot: dict) -> str`
2. Session Layer 新增：
   - `GET /internal/metrics`
3. Web 或 internal route 新增：
   - `GET /metrics`
4. 加 contract tests：
   - metrics text 包含关键指标。
   - metrics text 不包含 `run_id` / `prompt` / secret-looking values。
   - label names 合法。
5. 新增 runtime verify：
   - `scripts/k8s-observability-verify-v2.sh`

**Phase 4B：Structured JSON Logs**

目标：让所有服务日志可被 Loki/CloudWatch/Datadog 检索。

建议实现：

1. 增加统一 logging helper。
2. 统一字段：
   - `service`
   - `event`
   - `run_id`
   - `task_id`
   - `task_attempt_id`
   - `tool_call_id`
   - `trace_id`
   - `failure_kind`
3. 对 Brain claim、tool execution、sandbox pod lifecycle、approval lifecycle 增加 structured log。
4. 加 redaction tests。

**Phase 4C：OpenTelemetry Trace Propagation**

目标：可以从一个 run drill down 到 Web -> Session -> Brain -> Tool -> Sandbox/GitHub Broker 的完整链路。

建议实现：

1. 引入 OpenTelemetry SDK。
2. HTTP server/client 自动 instrumentation。
3. 手动给 agent query、tool call、sandbox execution、GitHub Broker call 加 span。
4. 通过 `traceparent` 传播到内部 HTTP calls。
5. K8s 部署 OpenTelemetry Collector。
6. 本地先用 debug exporter，后续接 Tempo/Jaeger。

**Phase 4D：Grafana / Alertmanager**

目标：把 metrics/logs/traces 组织成运行视图和告警。

建议实现：

1. 新增 dashboard JSON 或 Helm values。
2. 新增 alert rules。
3. `k8s-observability-verify-v2.sh` 验证：
   - `/metrics` 可 scrape。
   - collector ready。
   - dashboard config exists。
   - alert rule config exists。

#### 8.4.8 Testing Strategy

Phase 4 测试建议：

| 层级 | 测试内容 |
| --- | --- |
| Unit | `ops_metric_snapshot.v1` -> Prometheus text 转换 |
| Contract | `/metrics` 包含 expected metrics，不包含高基数 labels |
| Redaction | logs/metrics/traces 不含 token、secret、raw prompt |
| Runtime | v2 环境 curl `/metrics`，Prometheus text parse ok |
| Trace | 一次 run 产生同一 `trace_id` 的 Session/Brain/Tool spans |
| Dashboard | dashboard JSON 包含核心 panels |
| Alert | 构造 replay drift / failure spike fixture，alert rule 能触发 |

#### 8.4.9 暂不实现的原因

当前暂时不实现 Phase 4，原因是：

- Phase 1/2/3 已经覆盖 Day 2 的核心业务语义和 support evidence。
- External observability 会引入额外组件，容易把 PoC 复杂度拉高。
- 现在更重要的是让 ops contract 稳定，再接外部系统。
- Prometheus/OTel/Grafana 是承载层，不应该早于数据语义。

## 9. 第一阶段推荐范围

我建议先做 Phase 1，不急着做 Prometheus / OpenTelemetry。

理由：

- 当前系统已经有 durable DB evidence。
- 系统设计里更重要的是说明“哪些数据能解释 agent 行为”。
- Support Bundle 能直接回答 customer support 的问题。
- Ops Summary 能直接支撑 continuous improvement。
- 实现风险小，不改变 Brain/Sandbox 主链路。

第一阶段完成后，系统就能回答：

- 这个 run 为什么失败？
- 是模型、Brain、Sandbox、Tool、Approval 还是用户问题？
- 上次恢复发生在哪里？
- 哪个 tool 慢/失败？
- replay 是否一致？
- support 能否安全导出证据？

## 10. 测试方案

### 10.1 Unit Tests

覆盖：

- run summary 聚合。
- failure kind 分类。
- duration 计算。
- approval latency。
- tool execution duration。
- replay report merge。
- redaction。

### 10.2 Contract Tests

覆盖：

- `ops_run_summary.v1` 必须包含关键字段。
- `support_bundle.v1` schema version。
- bundle 不包含 raw secret。
- customer-visible bundle 比 internal bundle 更严格。

### 10.3 API Tests

覆盖：

- 正常 run 返回 summary。
- failed run 返回 failure summary。
- blocked approval run 返回 approval summary。
- user A 不能读取 user B 的 support bundle。
- 不存在 run 返回 404。

### 10.4 Runtime / K8s Verify

扩展现有脚本：

- `k8s-smoke-v2.sh` 创建 session 后能获取 ops summary。
- `k8s-security-verify-v2.sh` 或新的 `k8s-ops-verify-v2.sh` 验证 support bundle redaction。
- chaos drill 后 support bundle 能解释 lease expired / brain_crash。

## 11. 暂不做的内容

第一阶段暂不做：

- 完整 Prometheus exporter。
- OpenTelemetry trace propagation。
- Grafana dashboard。
- 多租户 admin console。
- 长期数据仓库。
- 自动模型质量评估。

这些都合理，但应该在 ops summary/support bundle 稳定后再做。

## 12. 推荐下一步

建议下一步先讨论 Phase 1 的输出格式：

1. `ops_run_summary.v1` 需要哪些字段。
2. `support_bundle.v1` internal/customer-visible 两种模式要不要都做。
3. 前端是否只显示 summary，还是显示完整 bundle JSON。
4. redaction 第一版做到什么程度。

确认后再开始实现，并在完成后生成 `day2-operations-upgrade-implementation.zh.md`。
