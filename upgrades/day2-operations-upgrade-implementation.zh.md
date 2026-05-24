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
  DB[(Postgres durable state)]
  Store[SessionStore ops queries]
  Ops[cloud_agent_poc.ops pure aggregators]
  SessionAPI[Session Layer internal API]
  WebAPI[Web Layer /api proxy]
  OpsUI[/ops independent dashboard]
  Script[scripts/k8s-ops-verify-v2.sh]

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

## 11. 本轮自验证结果

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
