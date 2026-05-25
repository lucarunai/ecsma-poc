# Day 2 Operations 升级实际实现记录

本文档记录 Day 2 Operations 的实际落地结果。计划文档仍然是 `day2-operations-upgrade-plan.zh.md`；本文档只描述当前代码已经实现的能力、数据契约、交互入口和验证方式。

## 1. 当前实现范围

已完成：

- Phase 1：单个 run 的 `ops_run_summary.v1` 和 `support_bundle.v1`。
- Phase 2：按用户和时间窗口聚合的 `ops_metric_snapshot.v1`。
- Phase 3：基于 metrics snapshot 的 `ops_alerts.v1` 和 v2 运行时 ops 验证脚本。
- 独立 Ops Dashboard：`GET /ops`，从主 Agent Console 解耦。
- Session Layer 内部 API 和 Web Layer 用户态 API proxy。
- customer/internal 两种 support bundle visibility。
- Redaction、preview、hash/fingerprint。
- 单元测试、静态 UI contract tests、文档 contract tests、K8s ops verification script。

暂未实现：

- Prometheus exporter。
- OpenTelemetry traces。
- Grafana dashboard。
- Alertmanager。
- admin-level cross-user dashboard。

当前版本继续保持 PoC 的轻量实现：不引入新的观测基础设施，先把已经落库的 durable state 组织成可解释、可验证、可支持的 Day 2 evidence。

## 2. 设计目标

Day 2 Operations 需要回答三类问题：

1. Continuous improvement：最近一段时间平台表现如何，失败主要集中在哪里。
2. Customer support：某个 run 出问题时，支持人员能否拿到足够证据，并且不会泄露 secret。
3. Operational drill：smoke/security/chaos/replay/handoff/support 能否形成可重复的验证闭环。

当前可用的数据来源包括：

- `runs`
- `tasks`
- `task_attempts`
- `task_handoffs`
- `tool_calls`
- `tool_executions`
- `approval_requests`
- `session_events`
- replay report
- runtime envelope

这些表仍然由 Session Layer 持久化；Ops 只做读侧聚合，不成为新的 source of truth。

## 3. 数据流

```mermaid
flowchart LR
  DB[("Postgres durable state")]
  Store["SessionStore ops queries"]
  Ops["cloud_agent_poc.ops pure aggregators"]
  SessionAPI["Session Layer internal API"]
  WebAPI["Web Layer /api proxy"]
  OpsUI["/ops independent dashboard"]
  Script["scripts/k8s-ops-verify-v2.sh"]

  DB --> Store
  Store --> Ops
  Ops --> SessionAPI
  SessionAPI --> WebAPI
  WebAPI --> OpsUI
  WebAPI --> Script
```

关键原则：

- user scope 由 `X-User-Id` 传入，Session Store 查询时按 `user_id` 过滤。
- run-level support API 先做 ownership check；不属于当前 user 的 run 返回 404。
- window-level metrics 不跨用户聚合，默认只看当前 user。
- support bundle 的 customer visibility 会做 redaction 和内容缩略。

## 4. 新增代码结构

### 4.1 Ops 聚合模块

新增：

```text
src/cloud_agent_poc/ops.py
```

核心函数：

```text
build_ops_run_summary(...)
build_support_bundle(...)
build_ops_metric_snapshot(...)
build_ops_alerts(...)
```

Schema version：

```text
ops_run_summary.v1
support_bundle.v1
ops_metric_snapshot.v1
ops_alerts.v1
```

该模块是纯函数模块，不依赖 Postgres/FastAPI/Kubernetes，因此可以用普通 unit test 构造 fixture 数据验证聚合逻辑。

### 4.2 Session Store

更新：

```text
src/cloud_agent_poc/session_store.py
```

新增读侧方法：

```text
get_run_operational_records(run_id, user_id=None)
get_run_ops_summary(run_id, user_id=None)
get_run_support_bundle(run_id, user_id=None, visibility="internal")
get_ops_metric_snapshot(user_id=None, window_hours=24)
get_ops_alerts(user_id=None, window_hours=24)
```

`get_run_operational_records` 会先通过 `get_run(run_id, user_id=...)` 做 ownership check。run 不属于当前 user 时返回 `None`，上层 API 转成 404。

`get_ops_metric_snapshot` 以 `runs.created_at` 做窗口过滤，并显式限定：

```text
WHERE user_id = <current user>
  AND created_at >= NOW() - (<window_hours> * INTERVAL '1 hour')
```

随后基于这些 `run_id` 批量读取 tasks、attempts、tool calls、tool executions、approvals、events，并对最近 10 个 run 生成 replay report summary。

### 4.3 Session Layer API

更新：

```text
src/cloud_agent_poc/session_app.py
```

新增内部 API：

```text
GET /internal/runs/{run_id}/ops-summary
GET /internal/runs/{run_id}/support-bundle?visibility=internal|customer
GET /internal/ops/metrics?window_hours=24
GET /internal/ops/alerts?window_hours=24
```

所有接口都读取 `X-User-Id`，并通过 Session Store 做 user-level ownership enforcement。

### 4.4 Session Client

更新：

```text
src/cloud_agent_poc/session_client.py
```

新增：

```text
get_run_ops_summary(run_id, user_id=None)
get_run_support_bundle(run_id, user_id=None, visibility="internal")
get_ops_metric_snapshot(user_id=None, window_hours=24)
get_ops_alerts(user_id=None, window_hours=24)
```

### 4.5 Web API

更新：

```text
src/cloud_agent_poc/web.py
```

新增页面：

```text
GET /ops
```

新增用户态 API：

```text
GET /api/runs/{run_id}/ops-summary
GET /api/runs/{run_id}/support-bundle?visibility=internal|customer
GET /api/ops/metrics?window_hours=24
GET /api/ops/alerts?window_hours=24
```

Web API 继续从 `X-User-Id` 读取当前用户，并转发给 Session Layer。

## 5. Phase 1：Run Summary 和 Support Bundle

### 5.1 `ops_run_summary.v1`

用途：快速解释某个 run 当前状态、失败点、恢复事件、sandbox runtime 和 replay 一致性。

核心字段：

```json
{
  "schema_version": "ops_run_summary.v1",
  "run_id": "run_xxx",
  "session_id": "sess_xxx",
  "user_id": "Luca",
  "status": "failed",
  "prompt": {
    "preview": "clone repo...",
    "sha256": "...",
    "bytes": 42
  },
  "duration_ms": 2000,
  "task_counts": {
    "total": 2,
    "completed": 1,
    "failed": 1
  },
  "tool_counts": {
    "total": 3,
    "succeeded": 2,
    "failed": 1
  },
  "failure_summary": {
    "failure_kind": "sandbox_runtime_error",
    "failed_component": "sandbox",
    "first_failed_task_id": "task_xxx",
    "first_failed_tool_call_id": "toolcall_xxx",
    "message": "pod failed"
  },
  "recovery_summary": {
    "attempt_count": 2,
    "resume_requested_count": 1,
    "lease_expired_count": 1,
    "orphaned_tool_calls": 0
  },
  "sandbox_summary": {
    "runtime_profiles": {
      "kubernetes_container": 1
    },
    "pod_phases": {
      "Failed": 1
    },
    "output_truncated_count": 1,
    "workspace_bytes_max": 42,
    "duration_ms_avg": 1200,
    "duration_ms_max": 1200
  },
  "approval_summary": {
    "total": 1,
    "approved": 1
  },
  "replay_summary": {
    "available": true,
    "replay_valid": true,
    "hash_chain_valid": true,
    "consistent": true,
    "error_count": 0,
    "difference_count": 0
  }
}
```

### 5.2 `support_bundle.v1`

用途：给 support/debug 场景导出一份完整但受控的诊断包。

包含：

- `triage`
- `run_summary`
- sanitized run
- tasks
- task attempts
- tool calls
- tool executions
- approvals
- handoffs
- replay report
- event timeline
- redaction metadata

customer visibility 会处理：

- prompt/file content 只保留 preview/hash/fingerprint。
- secret-looking values redacted。
- token/password/authorization 相关 key redacted。
- list 最多保留前 200 项。
- long text 截断。

## 6. Phase 2：Metrics Snapshot

### 6.1 `ops_metric_snapshot.v1`

用途：回答“最近一段时间平台表现如何”。

入口：

```text
GET /internal/ops/metrics?window_hours=24
GET /api/ops/metrics?window_hours=24
```

核心结构：

```json
{
  "schema_version": "ops_metric_snapshot.v1",
  "generated_at": "...",
  "user_id": "Luca",
  "window_hours": 24,
  "run_counts": {
    "total": 10,
    "completed": 8,
    "failed": 1,
    "blocked": 1,
    "success_rate": 0.8,
    "failure_rate": 0.2
  },
  "run_duration_ms": {
    "avg": 12000,
    "p50": 9000,
    "p95": 30000,
    "max": 45000
  },
  "task_counts": {},
  "task_attempt_counts": {},
  "tool_counts": {
    "total": 20,
    "by_tool": {
      "git_status": 8
    },
    "failures_by_tool": {
      "run_python_unittest": 1
    }
  },
  "tool_execution_counts": {},
  "tool_duration_ms": {
    "avg": 900,
    "p50": 600,
    "p95": 2200,
    "max": 3000
  },
  "failure_kinds": {
    "sandbox_runtime_error": 1
  },
  "approval_summary": {},
  "recovery_summary": {
    "resume_requested_count": 1,
    "resume_started_count": 1,
    "lease_expired_count": 1,
    "brain_crash_count": 1,
    "orphaned_tool_calls": 0
  },
  "sandbox_summary": {},
  "replay_summary": {
    "checked_runs": 10,
    "drift_count": 0,
    "hash_chain_invalid_count": 0,
    "invalid_replay_count": 0
  },
  "recent_runs": []
}
```

第一版不落新表，直接从 Postgres durable state 聚合。这样好处是：

- 不引入额外一致性问题。
- 可以立即复用当前 Session Layer 数据。
- 后续如果需要 Prometheus/Grafana，可以从这个 contract 再拆 exporter。

## 7. Phase 3：Alerts 和 Drill

### 7.1 `ops_alerts.v1`

用途：把常见异常从“人工看 JSON”变成可解释的 operational signal。

入口：

```text
GET /internal/ops/alerts?window_hours=24
GET /api/ops/alerts?window_hours=24
```

当前 alert rules：

- `replay_drift`：replay inconsistent 或 hash chain invalid。
- `brain_lease_expired`：窗口内出现 Brain lease expired。
- `sandbox_runtime_error`：出现 sandbox runtime failure kind。
- `approval_attention`：存在 pending/expired approval。
- `output_truncated`：sandbox stdout/stderr 被截断。
- `run_failure_rate`：窗口内 failed/blocked 比例达到 25%。

返回结构：

```json
{
  "schema_version": "ops_alerts.v1",
  "generated_at": "...",
  "user_id": "Luca",
  "window_hours": 24,
  "status": "attention_required",
  "alert_count": 2,
  "alerts": [
    {
      "type": "brain_lease_expired",
      "severity": "high",
      "summary": "Brain lease expiration occurred in the selected window.",
      "next_action": "Check Brain worker health and recovery events.",
      "evidence": {}
    }
  ]
}
```

### 7.2 Ops 验证脚本

新增：

```text
scripts/k8s-ops-verify-v2.sh
```

验证内容：

- v2 namespace 存在。
- web/session deployments rollout ready。
- `/ops` 独立页面可访问。
- 页面包含 metrics、alerts、support-bundle API 入口。
- `/api/ops/metrics` 返回 `ops_metric_snapshot.v1`。
- `/api/ops/alerts` 返回 `ops_alerts.v1`。
- 如果数据库里存在 run，则验证：
  - `/api/runs/{run_id}/ops-summary` 返回 `ops_run_summary.v1`。
  - `/api/runs/{run_id}/support-bundle?visibility=customer` 返回 `support_bundle.v1`。

这个脚本和现有脚本形成 v2 runtime 验证组合：

```text
scripts/k8s-smoke-v2.sh
scripts/k8s-security-verify-v2.sh
scripts/k8s-chaos-drill-v2.sh
scripts/k8s-ops-verify-v2.sh
```

## 8. 独立 Ops Dashboard

新增：

```text
src/cloud_agent_poc/static/ops.html
```

入口：

```text
GET /ops
```

主 Agent Console 只保留一个跳转链接，不再内嵌 Ops Summary / Support Bundle 面板。这样主工作流页面保持专注，Ops 页面专门展示 Day 2 能力。

Dashboard 交互能力：

- 用户输入框：模拟不同 `X-User-Id` 的用户视角。
- 时间窗口选择：1h / 6h / 24h / 72h / 7d。
- 手动刷新。
- 15 秒自动刷新。
- Metrics cards：
  - runs
  - failed/blocked
  - p95 run duration
  - tool count / p95 tool duration
  - approvals
  - recovery
  - replay drift
  - sandbox output truncation
- Alerts list：展示 severity、summary、next action。
- Recent runs：点击 run 后自动加载 support detail。
- Failure kinds bar chart。
- Tool usage bar chart。
- Run Support lookup：
  - 输入 run id。
  - 选择 internal/customer bundle。
  - 加载 ops summary 和 support bundle JSON。
  - 复制 support bundle JSON。

这个页面主要体现系统能力，而不是作为生产级 NOC dashboard。它足够展示 feedback loop：

```text
durable state -> metrics -> alerts -> drill-down -> support bundle -> fix/improve
```

## 9. 测试

### 9.1 Unit Tests

新增/更新：

```text
tests/test_ops.py
```

覆盖：

- `ops_run_summary.v1` 聚合 task/tool/failure/recovery/sandbox/replay。
- `support_bundle.v1` customer visibility 下 redaction 生效。
- `ops_metric_snapshot.v1` 聚合 run failure rate、tool failure distribution、replay drift。
- `ops_alerts.v1` 对 replay drift、lease expired、sandbox runtime error、approval pending、output truncation、failure rate 产生 alerts。

### 9.2 Static UI Contract Tests

更新：

```text
tests/test_static_ui_contract.py
```

覆盖：

- 主 Console 包含 `/ops` 跳转。
- 主 Console 不再包含 ops/support 的 JSON 面板。
- 独立 Ops Dashboard 包含 metrics、alerts、recent runs、support lookup。
- 独立 Ops Dashboard 调用：
  - `/api/ops/metrics`
  - `/api/ops/alerts`
  - `/api/runs/${runId}/ops-summary`
  - `/api/runs/${runId}/support-bundle`

### 9.3 Documentation / Script Contract Tests

更新：

```text
tests/test_testing_strategy_artifacts.py
```

覆盖：

- Day 2 plan 和 implementation 文档包含关键 contract 名称。
- v2 runtime verification scripts 都是 executable。
- `scripts/k8s-ops-verify-v2.sh` 覆盖 ops 页面、metrics、alerts、summary、support bundle。

### 9.4 Runtime Verification

建议本地或 v2 环境运行：

```bash
PYTHONPATH=src python3 -m unittest discover -v
./scripts/k8s-smoke-v2.sh
./scripts/k8s-ops-verify-v2.sh
```

如果需要完整运行时 drill，再追加：

```bash
./scripts/k8s-security-verify-v2.sh
./scripts/k8s-chaos-drill-v2.sh
```

## 10. 当前限制和后续方向

当前仍然不做：

- 跨用户 admin dashboard。
- Prometheus/OpenTelemetry/Grafana。
- long-term metrics warehouse。
- 自动 paging/notification。
- support bundle 下载文件化。

后续可以继续做：

- 把 `ops_metric_snapshot.v1` 暴露为 Prometheus-friendly metrics。
- 给 alert rules 增加阈值配置。
- 把 replay drift、sandbox failure、approval timeout 接入自动 regression drill。
- 为 support bundle 增加下载按钮和 bundle id。
- 增加 customer-facing 的最小化 support view。

## 11. Phase 4：External Observability Integration 设计边界

Phase 4 当前没有实现。这里记录已经讨论清楚的设计方向，避免后续接 Prometheus / OpenTelemetry / Grafana 时偏离现有架构。

### 11.1 基本原则

External observability 不替代 Session Layer。

当前系统的事实来源仍然是：

- Postgres durable state。
- `session_events` hash chain。
- replay report。
- `tool_executions.envelope`。
- `ops_run_summary.v1`。
- `support_bundle.v1`。
- `ops_metric_snapshot.v1`。
- `ops_alerts.v1`。

Observability stack 只负责把这些事实转成外部系统可消费的信号：

```text
Metrics：趋势 / SLO / alert
Logs：检索 / 排障细节
Traces：跨层调用链路
Dashboards：持续改进视图
Alertmanager：主动通知
```

不能从 Prometheus 或 logs 反推系统事实；需要审计和 support 时，仍然回到 DB evidence 和 support bundle。

### 11.2 建议目标架构

```mermaid
flowchart LR
  DB[("Postgres durable state")]
  OpsContracts["Ops contracts<br/>run summary / metrics / alerts / bundle"]
  Metrics["/metrics<br/>Prometheus text"]
  Logs["Structured JSON logs"]
  Traces["OpenTelemetry spans"]
  Collector["OpenTelemetry Collector"]
  Prom["Prometheus"]
  Loki["Loki / log backend"]
  Tempo["Tempo / trace backend"]
  Grafana["Grafana"]
  Alertmanager["Alertmanager"]

  DB --> OpsContracts
  OpsContracts --> Metrics
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

### 11.3 Phase 4A：Prometheus Metrics Exporter

建议第一步只做 metrics exporter。

新增：

```text
GET /metrics
```

从 `ops_metric_snapshot.v1` 派生 Prometheus text format，例如：

```text
cloud_agent_runs_total{status="completed"} 2
cloud_agent_runs_total{status="failed"} 1
cloud_agent_run_success_rate 0.6667
cloud_agent_run_failure_rate 0.3333
cloud_agent_tool_calls_total{tool_name="git_status",status="succeeded"} 7
cloud_agent_replay_drift_total 0
cloud_agent_brain_lease_expired_total 0
cloud_agent_sandbox_output_truncated_total 0
```

允许的低基数 labels：

- `status`
- `tool_name`
- `failure_kind`
- `runtime_profile`
- `pod_phase`
- `environment`
- `namespace`

禁止作为 metrics label：

- `run_id`
- `session_id`
- `task_id`
- `task_attempt_id`
- `tool_call_id`
- `prompt`
- `workspace_path`
- raw `user_id`

原因：这些字段基数太高，会让 Prometheus series 爆炸。需要按 id drill down 时，应该使用 logs、traces、support bundle。

建议测试：

- metrics text 包含核心指标。
- metrics text 不包含 `run_id` / `prompt` / secret-looking values。
- label names 合法。
- v2 runtime script 能 curl `/metrics` 并校验 Prometheus text。

### 11.4 Phase 4B：Structured JSON Logs

建议所有长期服务和 one-shot Sandbox Pod 输出结构化 JSON logs。

统一字段：

```json
{
  "timestamp": "...",
  "level": "info",
  "service": "brain",
  "event": "tool.execution.completed",
  "run_id": "run_xxx",
  "task_id": "task_xxx",
  "task_attempt_id": "taskattempt_xxx",
  "tool_call_id": "toolcall_xxx",
  "trace_id": "...",
  "span_id": "...",
  "failure_kind": null,
  "status": "succeeded"
}
```

需要覆盖的事件：

- run claim / heartbeat / lease expired。
- task started / completed / failed。
- agent query started / completed。
- tool call requested / completed / failed。
- sandbox pod created / completed / failed。
- GitHub Broker request / completed。
- approval requested / approved / denied / expired。

日志 redaction 要求：

- 不写 raw token。
- 不写 authorization header。
- 不写完整 prompt。
- 不写完整 file content。
- 不写完整 stdout/stderr。
- 可以写 hash、preview、bytes、truncated flag。

### 11.5 Phase 4C：OpenTelemetry Trace Propagation

建议 trace 以 `task_attempt_id` 或 `run_id` 作为主要 correlation anchor。

推荐 spans：

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

推荐 attributes：

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

注意：

- trace 可以带 `run_id` 用于检索。
- trace 不能带 secret、raw prompt、raw tool output。
- 内部 HTTP 调用需要传播 `traceparent`。
- Sandbox one-shot Pod 可以通过 env 或 request envelope 接收 trace context。

### 11.6 Phase 4D：Grafana / Alertmanager

建议 dashboard 分成 4 个：

| Dashboard | 核心内容 |
| --- | --- |
| Platform Health | run success rate、failure rate、p95 duration、queued/running/resume_queued |
| Agent Recovery | lease expired、resume requested/completed、brain crash、orphaned tool calls |
| Sandbox Runtime | tool duration、pod phase、runtime failure、output truncated、workspace size |
| Customer Support | failed runs by failure_kind、pending approvals、support bundle、replay drift |

建议 Alertmanager rules：

- `ReplayDriftDetected`
- `ReplayHashChainInvalid`
- `BrainLeaseExpired`
- `RunFailureRateHigh`
- `SandboxRuntimeErrorSpike`
- `ToolFailureRateHigh`
- `ApprovalPendingTooLong`
- `ApprovalExpiredSpike`
- `SandboxOutputTruncatedSpike`
- `SupportBundleRedactionFailure`

这里要区分：

- Infra alert：Pod / DB / CPU / memory / K8s 资源问题。
- Product alert：run failure rate、replay drift、approval pending、sandbox runtime error 等 agent platform 业务语义问题。

当前系统更应该强调 product alert，因为它体现的是 agent workflow harness 的能力，而不只是 Kubernetes 存活状态。

### 11.7 Phase 4 暂不实现原因

当前先不实现 Phase 4：

- Phase 1/2/3 已经覆盖当前 PoC 的 Day 2 核心能力。
- External observability 会引入 Prometheus、Collector、Grafana 等额外组件，容易让 PoC 运维面膨胀。
- 现在最重要的是稳定 ops contract 和 support evidence。
- Prometheus / OpenTelemetry / Grafana 是承载层，不应该早于业务语义。

后续如果开始做，推荐顺序是：

```text
4A Prometheus /metrics
  -> 4B Structured JSON logs
  -> 4C OpenTelemetry traces
  -> 4D Grafana dashboards + Alertmanager
```

## 12. 本轮自验证结果

本轮实现完成后已执行：

```bash
python3 -m py_compile src/cloud_agent_poc/ops.py src/cloud_agent_poc/session_store.py src/cloud_agent_poc/session_app.py src/cloud_agent_poc/session_client.py src/cloud_agent_poc/web.py
PYTHONPATH=src python3 -m unittest tests.test_ops tests.test_static_ui_contract tests.test_testing_strategy_artifacts -v
PYTHONPATH=src python3 -m unittest discover -v
docker build -t cloud-agent-poc-v2:local .
kubectl -n cloud-agent-poc-v2 rollout restart deploy/cloud-agent-session
kubectl -n cloud-agent-poc-v2 rollout restart deploy/cloud-agent-web
kubectl -n cloud-agent-poc-v2 rollout status deploy/cloud-agent-session --timeout=120s
kubectl -n cloud-agent-poc-v2 rollout status deploy/cloud-agent-web --timeout=120s
./scripts/k8s-smoke-v2.sh
./scripts/k8s-ops-verify-v2.sh
RUN_ID=run_7ca6c60aca5a40f2b2bbe48501e3059b OWNER_USER=Luca OTHER_USER=Josephine ./scripts/k8s-chaos-drill-v2.sh ownership
```

验证结果：

- Python 语法检查通过。
- 重点 ops/static/doc tests：18 tests passed。
- 全量 unittest：105 tests passed。
- v2 image build 成功。
- v2 `cloud-agent-session` 和 `cloud-agent-web` rollout 成功。
- `scripts/k8s-smoke-v2.sh` passed。
- `scripts/k8s-ops-verify-v2.sh` passed。
- `scripts/k8s-security-verify-v2.sh` passed。
- ownership drill passed：owner user 可以访问自己的 run，other user 对 run/get、replay、wake 都返回 404。
- `/ops` 页面、`ops_metric_snapshot.v1`、`ops_alerts.v1`、`ops_run_summary.v1`、`support_bundle.v1` 均在 v2 runtime 环境完成 contract verification。

本轮自验证还发现并修复了一个 ownership 顺序问题：`wake_run` 过去会把“不属于当前用户”和“状态不允许 wake”都折叠成 409。现在 Session Layer 会先按 `X-User-Id` 做 run ownership check；run 不存在或不属于当前用户时返回 404，只有属于当前用户但状态不是 `blocked` / `failed` 时才返回 409。
