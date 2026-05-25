# Real Case: v3 per-task sandbox 执行 demo-2000 分支推送

本文记录一次真实的 `18083 / cloud-agent-poc-v3 / per-task sandbox` 执行链路。数据来自本地前端已经触发过的真实请求、真实 DB 入库记录、真实 sandbox session、真实 tool call、真实 tool execution envelope、真实 approval 和真实 session event 时间线。

本文只解释当前 v3 case 的完整链路，不做版本对比。

## 1. Case 基本信息

用户在 `http://localhost:18083/` 提交的 prompt：

```text
clone https://github.com/lucarunai/demo branch main，create new branch demo-2000，then push github
```

真实 run：

```text
run_id:     run_9024e38630bd4b60bc69092b3745e53a
session_id: sess_4b3c3617fe3645b8ba619d942cfd1b02
user_id:    Luca
status:     completed
created_at: 2026-05-25T11:31:54.889394Z
started_at: 2026-05-25T11:31:55.445145Z
ended_at:   2026-05-25T11:38:13.007897Z
duration_ms: 377563
```

所有时间均为 DB 中的 UTC 时间。Asia/Shanghai 时间需要加 8 小时。

真实 run 数据核心：

```json
{
  "id": "run_9024e38630bd4b60bc69092b3745e53a",
  "session_id": "sess_4b3c3617fe3645b8ba619d942cfd1b02",
  "user_id": "Luca",
  "prompt": "clone https://github.com/lucarunai/demo branch main，create new branch demo-2000，then push github",
  "status": "completed",
  "idempotency_key": "a008bb43-e977-40c0-bfc4-911f28c7336e",
  "attempt_count": 1,
  "created_at": "2026-05-25T11:31:54.889394Z",
  "started_at": "2026-05-25T11:31:55.445145Z",
  "ended_at": "2026-05-25T11:38:13.007897Z",
  "retention_until": "2026-06-01T11:31:54.889394Z",
  "metadata": {
    "user_id": "Luca",
    "workspace_path": "/sandboxes/users/Luca/run_9024e38630bd4b60bc69092b3745e53a",
    "workspace_cleaned": true
  }
}
```

这条链路的执行核心是：

```text
一个 task attempt 创建一个 sandbox session。
一个 sandbox session 绑定一个 sandbox-task-* pod。
同一个 task 内的普通 workspace tool 都带同一个 sandbox_session_id。
sandbox manager 根据 sandbox_session_id 找到同一个 pod，并通过 pod 内 HTTP daemon 执行每一次 tool。
task 完成后关闭 sandbox session，并删除对应 pod。
```

## 2. 真实 task 与验收标准

Planner 把 prompt 拆成 3 个顺序 task。

```json
[
  {
    "id": "task_72140b3eca6340b5a7287967c9d4f4c8",
    "seq": 1,
    "kind": "model_task",
    "title": "Clone repository lucarunai/demo (main branch)",
    "description": "Clone the GitHub repository at https://github.com/lucarunai/demo using the main branch into the sandbox workspace so subsequent Git operations can be performed against it.",
    "acceptance_criteria": [
      "Repository https://github.com/lucarunai/demo is cloned into the sandbox",
      "The active branch after clone is main",
      "Working tree matches the latest commit on main"
    ]
  },
  {
    "id": "task_28f7af598a6a4419af0b2fd856451a3f",
    "seq": 2,
    "kind": "model_task",
    "title": "Create new branch demo-2000",
    "description": "From the main branch of the cloned repository, create a new local branch named demo-2000 and check it out so it becomes the active branch.",
    "acceptance_criteria": [
      "A new branch named demo-2000 is created from main",
      "demo-2000 is checked out as the active branch",
      "Branch history matches main at creation time"
    ]
  },
  {
    "id": "task_d53e77b17cfe49b7b72613b7ff3b4bd2",
    "seq": 3,
    "kind": "model_task",
    "title": "Push demo-2000 branch to GitHub",
    "description": "Push the newly created demo-2000 branch to the origin remote on GitHub so it is available on the lucarunai/demo repository.",
    "acceptance_criteria": [
      "Branch demo-2000 is pushed to origin on github.com/lucarunai/demo",
      "Remote tracking for origin/demo-2000 is established",
      "Push completes without errors"
    ]
  }
]
```

真实 task attempt：

```text
taskattempt_078acc0638ba4a6ba58f4ef4743f816e
  task_id: task_72140b3eca6340b5a7287967c9d4f4c8
  claude_session_id: 85c40a32-4591-43b5-a6b2-91fc1d673a98
  status: completed
  started_at: 2026-05-25T11:32:09.470398Z
  ended_at:   2026-05-25T11:32:30.807077Z

taskattempt_a1f1ad89859a4f81b6e355d4b3593133
  task_id: task_28f7af598a6a4419af0b2fd856451a3f
  claude_session_id: 39db1d20-6820-4a23-900d-789a2d8baf6c
  status: completed
  started_at: 2026-05-25T11:32:30.866576Z
  ended_at:   2026-05-25T11:32:52.645982Z

taskattempt_c350d830791c498e8f7ef248871b29f6
  task_id: task_d53e77b17cfe49b7b72613b7ff3b4bd2
  claude_session_id: 113fb568-9613-48c8-a64d-aba0a0ee81b6
  status: completed
  started_at: 2026-05-25T11:32:52.718259Z
  ended_at:   2026-05-25T11:38:12.940444Z
```

## 3. 代码层参与者

当前 v3 case 的核心代码路径：

```text
前端与 web API:
  src/cloud_agent_poc/static/index.html
  src/cloud_agent_poc/web.py

run/session/task/tool/approval 入库:
  src/cloud_agent_poc/session_app.py
  src/cloud_agent_poc/session_store.py

brain worker 与 task orchestration:
  src/cloud_agent_poc/brain/worker.py
  src/cloud_agent_poc/brain/orchestrator.py

planner:
  src/cloud_agent_poc/brain/planner.py

agent SDK stream:
  src/cloud_agent_poc/brain/claude_agent.py

tool wrapper、runtime policy、approval:
  src/cloud_agent_poc/brain/sdk_tools.py
  src/cloud_agent_poc/tool_policy.py

sandbox client / service / manager / daemon / tools:
  src/cloud_agent_poc/sandbox_client.py
  src/cloud_agent_poc/sandbox_app.py
  src/cloud_agent_poc/sandbox_manager.py
  src/cloud_agent_poc/sandbox_daemon.py
  src/cloud_agent_poc/sandbox_runtime.py
  src/cloud_agent_poc/sandbox_tools.py

GitHub broker / trusted executor:
  src/cloud_agent_poc/github_broker_app.py
  src/cloud_agent_poc/brain/github_workflow.py

Kubernetes:
  k8s-v3/
```

task sandbox session 的关键代码流：

```text
orchestrator._execute_model_task
  -> create_task_attempt
  -> _start_task_sandbox_session
  -> sandbox_client.create_sandbox_session
  -> POST /internal/sandbox-sessions
  -> sandbox_manager.create_session
  -> Kubernetes 创建 sandbox-task-* pod
  -> 等 pod Running + readinessProbe 通过
  -> sandbox_sessions 入库
  -> emit sandbox.session.started
  -> agent.implement(... sandbox_session_id=当前 task 的 session id)
```

普通 workspace tool 执行时的关键代码流：

```text
agent 输出 ToolUseBlock
  -> sdk_tools._execute_tool
  -> create_tool_call
  -> resolve_runtime_policy
  -> runtime_policy=task_attempt_sandbox
  -> sandbox_client.execute_tool(... sandbox_session_id=当前 task 的 session id)
  -> POST /internal/sandbox-sessions/{sandbox_session_id}/tool-executions
  -> sandbox_manager.execute(session_id, request)
  -> 根据 session_id 计算 pod_name
  -> Kubernetes API 查询 pod，拿 pod_ip
  -> POST http://<pod_ip>:8080/execute
  -> sandbox_daemon 执行 ToolExecutionRequest
  -> 返回 ToolExecutionEnvelope
  -> record_tool_execution
  -> update_tool_call
  -> emit tool.execution
  -> tool result 回到 agent
```

## 4. Run 创建、claim 与 planning

前端提交后，系统创建 run，并写入第一条 session event：

```text
seq=1
id=65
time=11:31:54.896
event=user.prompt.accepted
payload:
{
  "run_id": "run_9024e38630bd4b60bc69092b3745e53a",
  "user_id": "Luca",
  "prompt": "clone https://github.com/lucarunai/demo branch main，create new branch demo-2000，then push github"
}
```

brain worker 领取 run：

```text
seq=2
id=66
time=11:31:55.456
event=run.claimed
worker_id=cloud-agent-brain-84f486b885-ld8hw-33926258
lease_seconds=60

seq=3
id=67
time=11:31:55.517
event=run.started

seq=4
id=68
time=11:31:55.548
event=plan.started
planner=agent_task_planner
```

planner 执行：

```text
seq=5   11:31:56.784 planner.sdk.message
seq=6   11:32:00.054 planner.sdk.message
seq=7   11:32:05.958 planner.sdk.message
seq=8   11:32:06.027 planner.sdk.message
seq=9   11:32:08.568 planner.sdk.message
seq=10  11:32:08.582 planner.message
seq=11  11:32:08.695 planner.sdk.message
seq=12  11:32:09.458 tasks.created
```

真实 planner message：

```text
I've split the request into 3 ordered tasks: clone the repo on main, create branch `demo-2000`, then push it to GitHub.
```

从这里开始，orchestrator 按 task seq 逐个执行。

## 5. Task 1 完整流程：clone repository

Task 1 启动：

```text
task_id: task_72140b3eca6340b5a7287967c9d4f4c8
task_attempt_id: taskattempt_078acc0638ba4a6ba58f4ef4743f816e

seq=13
time=11:32:09.492
event=task.started
```

### 5.1 创建 Task 1 sandbox session 与 task pod

task started 后，orchestrator 先为 Task 1 创建 sandbox session。请求逻辑上是：

```json
{
  "run_id": "run_9024e38630bd4b60bc69092b3745e53a",
  "task_id": "task_72140b3eca6340b5a7287967c9d4f4c8",
  "task_attempt_id": "taskattempt_078acc0638ba4a6ba58f4ef4743f816e",
  "sandbox_session_id": "sbxsess_682d390674484b2a98c9a171a3050056",
  "workspace_path": "/sandboxes/users/Luca/run_9024e38630bd4b60bc69092b3745e53a",
  "scope": "task_attempt",
  "runtime_policy": "task_attempt_sandbox"
}
```

sandbox service 接收：

```text
POST /internal/sandbox-sessions
```

sandbox manager 根据 `sandbox_session_id` 创建 pod：

```text
pod_name=sandbox-task-682d390674484b2a98c9
```

pod manifest 中的关键内容：

```text
container command:
  uvicorn cloud_agent_poc.sandbox_daemon:app --host 0.0.0.0 --port 8080

env:
  SANDBOX_WORKSPACE_PATH=/workspace/run_9024e38630bd4b60bc69092b3745e53a
  GIT_AUTHOR_NAME=<configured>
  GIT_AUTHOR_EMAIL=<configured>
  SANDBOX_TOOL_OUTPUT_BYTES_LIMIT=4000
  SANDBOX_WORKSPACE_BYTES_LIMIT=104857600
  SANDBOX_WORKSPACE_FILE_LIMIT=10000

volume:
  workspace PVC mounted at /workspace/run_9024e38630bd4b60bc69092b3745e53a
  subPath points to /sandboxes/users/Luca/run_9024e38630bd4b60bc69092b3745e53a

securityContext:
  runAsNonRoot=true
  runAsUser=10001
  runAsGroup=10001
  allowPrivilegeEscalation=false
  capabilities.drop=["ALL"]
  seccompProfile=RuntimeDefault

automountServiceAccountToken=false
readinessProbe:
  GET /healthz on port 8080
```

manager 等到 pod phase 为 `Running`、pod 有 IP、readinessProbe 通过后，返回 session 信息，并写入 `sandbox_sessions`：

```json
{
  "id": "sbxsess_682d390674484b2a98c9a171a3050056",
  "scope": "task_attempt",
  "run_id": "run_9024e38630bd4b60bc69092b3745e53a",
  "task_id": "task_72140b3eca6340b5a7287967c9d4f4c8",
  "task_attempt_id": "taskattempt_078acc0638ba4a6ba58f4ef4743f816e",
  "status": "closed",
  "runtime_profile": "kubernetes_task_attempt_container",
  "pod_name": "sandbox-task-682d390674484b2a98c9",
  "workspace_path": "/sandboxes/users/Luca/run_9024e38630bd4b60bc69092b3745e53a",
  "created_at": "2026-05-25T11:32:11.565878Z",
  "expires_at": "2026-05-25T11:47:11.565878Z",
  "closed_at": "2026-05-25T11:32:30.774060Z"
}
```

事件：

```text
seq=14
time=11:32:11.580
event=sandbox.session.started
payload:
{
  "sandbox_session_id": "sbxsess_682d390674484b2a98c9a171a3050056",
  "pod_name": "sandbox-task-682d390674484b2a98c9",
  "scope": "task_attempt",
  "runtime_profile": "kubernetes_task_attempt_container",
  "task_attempt_id": "taskattempt_078acc0638ba4a6ba58f4ef4743f816e"
}
```

随后 agent session 启动：

```text
seq=15  11:32:11.993 sdk.message
seq=16  11:32:12.020 agent.session.started
claude_session_id=85c40a32-4591-43b5-a6b2-91fc1d673a98
```

从这一刻开始，Task 1 内所有普通 workspace tool 都会带：

```text
sandbox_session_id=sbxsess_682d390674484b2a98c9a171a3050056
```

这就是后面 `git_status` 与 `glob_workspace_files` 复用同一个 pod 的根因。

### 5.2 Agent 调用 clone_github_repository

agent 第一次决策是 clone repository：

```json
{
  "tool_name": "clone_github_repository",
  "input": {
    "repository_url": "https://github.com/lucarunai/demo",
    "source_branch": "main"
  }
}
```

真实 tool call：

```json
{
  "id": "toolcall_a3f2792a281d4298b2dcaaa4d9c67781",
  "run_id": "run_9024e38630bd4b60bc69092b3745e53a",
  "task_id": "task_72140b3eca6340b5a7287967c9d4f4c8",
  "task_attempt_id": "taskattempt_078acc0638ba4a6ba58f4ef4743f816e",
  "tool_name": "clone_github_repository",
  "input": {
    "source_branch": "main",
    "repository_url": "https://github.com/lucarunai/demo"
  },
  "status": "succeeded",
  "latest_execution_id": "sbxexec_96ad467d6b2d452fa59c84743c01222d",
  "created_at": "2026-05-25T11:32:15.011999Z",
  "ended_at": "2026-05-25T11:32:15.908293Z"
}
```

事件：

```text
seq=17  11:32:14.617 sdk.message
seq=18  11:32:15.011 sdk.message
seq=19  11:32:15.029 tool.started
seq=20  11:32:15.029 tool.call.requested
```

runtime policy 选择：

```text
runtime_policy=broker_only
policy_reason=trusted_github_tools_execute_in_secret_holding_broker
```

执行结果：

```json
{
  "execution_id": "sbxexec_96ad467d6b2d452fa59c84743c01222d",
  "tool_name": "clone_github_repository",
  "execution_status": "succeeded",
  "runtime": {
    "type": "trusted_github_executor",
    "runtime_policy": "broker_only",
    "policy_reason": "trusted_github_tools_execute_in_secret_holding_broker",
    "duration_ms": 840
  },
  "tool_result": {
    "ok": true,
    "summary": "Repository cloned.",
    "data": {
      "command": [
        "git",
        "-c",
        "[redacted-github-auth-header]",
        "clone",
        "--branch",
        "main",
        "--single-branch",
        "https://github.com/lucarunai/demo",
        "."
      ],
      "stdout": "",
      "stderr": "Cloning into '.'...\n",
      "returncode": 0,
      "source_branch": "main",
      "repository_url": "https://github.com/lucarunai/demo"
    }
  }
}
```

事件：

```text
seq=21  11:32:15.918 tool.execution
seq=22  11:32:15.933 sdk.message
```

`seq=22` 表示 clone 的 tool result 已回到 agent。agent 根据 `ok=true` 继续决定验证当前分支。

### 5.3 Agent 调用 git_status，复用 Task 1 sandbox pod

agent 输出：

```json
{
  "tool_name": "git_status",
  "input": {}
}
```

真实 tool call：

```json
{
  "id": "toolcall_f08dea8753684ab7bfef79ec04e50e03",
  "run_id": "run_9024e38630bd4b60bc69092b3745e53a",
  "task_id": "task_72140b3eca6340b5a7287967c9d4f4c8",
  "task_attempt_id": "taskattempt_078acc0638ba4a6ba58f4ef4743f816e",
  "tool_name": "git_status",
  "input": {},
  "status": "succeeded",
  "latest_execution_id": "sbxexec_bc2aa2cbae0645c7a0e6ca5e4ed4c7c7",
  "created_at": "2026-05-25T11:32:17.603843Z",
  "ended_at": "2026-05-25T11:32:17.662767Z"
}
```

事件：

```text
seq=23  11:32:17.604 sdk.message
seq=24  11:32:17.621 tool.call.requested
seq=25  11:32:17.621 tool.started
```

因为当前 task 已有 sandbox session，tool wrapper 组装 ToolExecutionRequest 时带上：

```text
sandbox_session_id=sbxsess_682d390674484b2a98c9a171a3050056
runtime_policy=task_attempt_sandbox
policy_reason=default_workspace_tool_policy
```

请求逻辑上是：

```json
{
  "run_id": "run_9024e38630bd4b60bc69092b3745e53a",
  "tool_call_id": "toolcall_f08dea8753684ab7bfef79ec04e50e03",
  "tool_name": "git_status",
  "args": {},
  "task_attempt_id": "taskattempt_078acc0638ba4a6ba58f4ef4743f816e",
  "workspace_path": "/sandboxes/users/Luca/run_9024e38630bd4b60bc69092b3745e53a",
  "sandbox_session_id": "sbxsess_682d390674484b2a98c9a171a3050056",
  "sandbox_scope": "task_attempt",
  "runtime_policy": "task_attempt_sandbox",
  "policy_reason": "default_workspace_tool_policy"
}
```

sandbox client 发送：

```text
POST /internal/sandbox-sessions/sbxsess_682d390674484b2a98c9a171a3050056/tool-executions
```

sandbox manager 收到后不会创建新 pod，而是：

```text
1. 根据 session_id 计算 pod_name:
   sandbox-task-682d390674484b2a98c9

2. 通过 Kubernetes API 查询这个 pod:
   phase=Running
   pod_ip=<sandbox-task-682... 的 pod IP>

3. 通过 HTTP 调用 pod 内 daemon:
   POST http://<pod_ip>:8080/execute

4. request body 就是 ToolExecutionRequest JSON。

5. pod 内 sandbox_daemon 接收 /execute。

6. sandbox_daemon 使用环境变量:
   SANDBOX_WORKSPACE_PATH=/workspace/run_9024e38630bd4b60bc69092b3745e53a

7. sandbox_runtime dispatch 到 sandbox_tools.git_status。

8. pod 在挂载 workspace 内执行:
   git status --short --branch

9. pod 以 HTTP response 返回 ToolExecutionEnvelope JSON。

10. manager 补 runtime metadata，然后返回 sdk_tools。
```

真实 execution：

```json
{
  "execution_id": "sbxexec_bc2aa2cbae0645c7a0e6ca5e4ed4c7c7",
  "tool_call_id": "toolcall_f08dea8753684ab7bfef79ec04e50e03",
  "tool_name": "git_status",
  "execution_status": "succeeded",
  "sandbox_session_id": "sbxsess_682d390674484b2a98c9a171a3050056",
  "runtime": {
    "type": "sandbox_session_pod",
    "runtime_profile": "kubernetes_task_attempt_container",
    "isolation": "container",
    "sandbox_session_id": "sbxsess_682d390674484b2a98c9a171a3050056",
    "sandbox_scope": "task_attempt",
    "runtime_policy": "task_attempt_sandbox",
    "policy_reason": "default_workspace_tool_policy",
    "pod_name": "sandbox-task-682d390674484b2a98c9",
    "pod_phase": "Running",
    "duration_ms": 11,
    "network_policy": "sandbox-session-default-deny",
    "egress_policy": "default-deny",
    "resource_limits": {
      "cpu": "500m",
      "memory": "512Mi",
      "timeout_seconds": 180,
      "workspace_bytes": 104857600,
      "workspace_files": 10000,
      "ephemeral_storage": "1Gi",
      "runtime_log_bytes": 65536,
      "tool_output_bytes": 4000,
      "session_timeout_seconds": 900
    }
  },
  "tool_result": {
    "ok": true,
    "summary": "Git status read.",
    "data": {
      "command": ["git", "status", "--short", "--branch"],
      "stdout": "## main...origin/main\n",
      "stderr": "",
      "returncode": 0,
      "stdout_bytes": 22,
      "stderr_bytes": 0,
      "stdout_truncated": false,
      "stderr_truncated": false,
      "output_limit_bytes": 4000
    }
  }
}
```

事件：

```text
seq=26  11:32:17.675 tool.execution
seq=27  11:32:17.691 sdk.message
```

`seq=27` 表示 `git_status` result 回到 agent。agent 看到 `## main...origin/main`，继续确认 workspace 文件。

### 5.4 Agent 调用 glob_workspace_files，继续复用 Task 1 sandbox pod

agent 输出：

```json
{
  "tool_name": "glob_workspace_files",
  "input": {
    "pattern": "**/*"
  }
}
```

真实 tool call：

```json
{
  "id": "toolcall_b854d95ad5bd4051b4181c20f7557959",
  "tool_name": "glob_workspace_files",
  "input": {
    "pattern": "**/*"
  },
  "task_attempt_id": "taskattempt_078acc0638ba4a6ba58f4ef4743f816e",
  "latest_execution_id": "sbxexec_ddef1406e06644d5a1a493773c9401ee",
  "created_at": "2026-05-25T11:32:19.599951Z",
  "ended_at": "2026-05-25T11:32:19.664984Z"
}
```

事件：

```text
seq=28  11:32:19.599 sdk.message
seq=29  11:32:19.618 tool.call.requested
seq=30  11:32:19.619 tool.started
```

这次 request 仍然带同一个：

```text
sandbox_session_id=sbxsess_682d390674484b2a98c9a171a3050056
```

所以 sandbox client 仍然调用：

```text
POST /internal/sandbox-sessions/sbxsess_682d390674484b2a98c9a171a3050056/tool-executions
```

sandbox manager 再次定位到同一个 pod：

```text
sandbox-task-682d390674484b2a98c9
```

并再次通过：

```text
POST http://<sandbox-task-682... pod_ip>:8080/execute
```

让同一个 pod 执行 `glob_workspace_files`。

真实 execution：

```json
{
  "execution_id": "sbxexec_ddef1406e06644d5a1a493773c9401ee",
  "tool_name": "glob_workspace_files",
  "sandbox_session_id": "sbxsess_682d390674484b2a98c9a171a3050056",
  "runtime": {
    "type": "sandbox_session_pod",
    "runtime_profile": "kubernetes_task_attempt_container",
    "pod_name": "sandbox-task-682d390674484b2a98c9",
    "pod_phase": "Running",
    "duration_ms": 15,
    "runtime_policy": "task_attempt_sandbox"
  },
  "tool_result": {
    "ok": true,
    "summary": "Workspace glob completed.",
    "data": {
      "matches": [
        ".git/HEAD",
        ".git/config",
        ".git/description",
        ".git/hooks/applypatch-msg.sample",
        ".git/hooks/commit-msg.sample",
        ".git/hooks/post-update.sample",
        ".git/hooks/pre-applypatch.sample",
        ".git/hooks/pre-commit.sample",
        ".git/hooks/pre-merge-commit.sample",
        ".git/hooks/pre-push.sample",
        ".git/hooks/pre-rebase.sample",
        ".git/hooks/pre-receive.sample",
        ".git/hooks/prepare-commit-msg.sample",
        ".git/hooks/push-to-checkout.sample",
        ".git/hooks/sendemail-validate.sample",
        ".git/hooks/update.sample",
        ".git/index",
        ".git/info/exclude",
        ".git/logs/HEAD",
        ".git/logs/refs/heads/main",
        ".git/objects/pack/pack-f2074e1053e6cf9bb86b7aa11ba809fe189b8320.idx",
        ".git/objects/pack/pack-f2074e1053e6cf9bb86b7aa11ba809fe189b8320.pack",
        ".git/objects/pack/pack-f2074e1053e6cf9bb86b7aa11ba809fe189b8320.rev",
        ".git/packed-refs",
        ".git/refs/heads/main",
        "README.md",
        "timestamp_uuid.py"
      ]
    }
  }
}
```

事件：

```text
seq=31  11:32:19.675 tool.execution
seq=32  11:32:19.704 sdk.message
```

这里已经能看到 Task 1 的复用链路：

```text
git_status:
  sandbox_session_id=sbxsess_682d390674484b2a98c9a171a3050056
  pod=sandbox-task-682d390674484b2a98c9

glob_workspace_files:
  sandbox_session_id=sbxsess_682d390674484b2a98c9a171a3050056
  pod=sandbox-task-682d390674484b2a98c9
```

agent 收到 `glob_workspace_files` result 后，输出 task result：

```text
seq=33  11:32:22.555 sdk.message
seq=34  11:32:27.857 sdk.message
seq=35  11:32:27.870 tool.started        StructuredOutput
seq=36  11:32:27.969 sdk.message
seq=37  11:32:30.400 sdk.message
seq=38  11:32:30.413 agent.message
seq=39  11:32:30.457 sdk.message
seq=40  11:32:30.470 agent.result
```

Task 1 result：

```text
Cloned the lucarunai/demo repository at the main branch into the sandbox workspace.
Verified via git status that the active branch is main and is in sync with origin/main,
ready for subsequent branch creation and push operations.
```

Task 1 结束后，orchestrator 关闭 sandbox session：

```text
DELETE /internal/sandbox-sessions/sbxsess_682d390674484b2a98c9a171a3050056
```

sandbox manager 删除 pod：

```text
sandbox-task-682d390674484b2a98c9
```

事件：

```text
seq=41  11:32:30.784 sandbox.session.closed
seq=42  11:32:30.831 task.completed
seq=43  11:32:30.854 task.handoff
```

## 6. Task 2 完整流程：create branch demo-2000

Task 2 启动：

```text
task_id: task_28f7af598a6a4419af0b2fd856451a3f
task_attempt_id: taskattempt_a1f1ad89859a4f81b6e355d4b3593133

seq=44
time=11:32:30.888
event=task.started
```

### 6.1 创建 Task 2 sandbox session 与 task pod

orchestrator 为 Task 2 创建新的 sandbox session：

```json
{
  "id": "sbxsess_36076707075448deb24c11c2303c2631",
  "scope": "task_attempt",
  "run_id": "run_9024e38630bd4b60bc69092b3745e53a",
  "task_id": "task_28f7af598a6a4419af0b2fd856451a3f",
  "task_attempt_id": "taskattempt_a1f1ad89859a4f81b6e355d4b3593133",
  "status": "closed",
  "runtime_profile": "kubernetes_task_attempt_container",
  "pod_name": "sandbox-task-36076707075448deb24c",
  "workspace_path": "/sandboxes/users/Luca/run_9024e38630bd4b60bc69092b3745e53a",
  "created_at": "2026-05-25T11:32:33.928946Z",
  "expires_at": "2026-05-25T11:47:33.928946Z",
  "closed_at": "2026-05-25T11:32:52.604263Z"
}
```

事件：

```text
seq=45
time=11:32:33.941
event=sandbox.session.started
payload:
{
  "sandbox_session_id": "sbxsess_36076707075448deb24c11c2303c2631",
  "pod_name": "sandbox-task-36076707075448deb24c",
  "runtime_profile": "kubernetes_task_attempt_container",
  "task_attempt_id": "taskattempt_a1f1ad89859a4f81b6e355d4b3593133"
}
```

agent session 启动：

```text
seq=46  11:32:34.331 sdk.message
seq=47  11:32:34.357 agent.session.started
claude_session_id=39db1d20-6820-4a23-900d-789a2d8baf6c
```

从这之后，Task 2 内普通 workspace tool 都带：

```text
sandbox_session_id=sbxsess_36076707075448deb24c11c2303c2631
pod_name=sandbox-task-36076707075448deb24c
```

### 6.2 Agent 调用 git_status，确认 main

真实 tool call：

```json
{
  "id": "toolcall_6bb78b8b2d1e4479922dfcf72937f7c9",
  "tool_name": "git_status",
  "input": {},
  "task_attempt_id": "taskattempt_a1f1ad89859a4f81b6e355d4b3593133",
  "latest_execution_id": "sbxexec_a18e529165104de6be9c6294c71034fb",
  "created_at": "2026-05-25T11:32:37.121208Z",
  "ended_at": "2026-05-25T11:32:37.178766Z"
}
```

事件：

```text
seq=48  11:32:37.106 sdk.message
seq=49  11:32:37.121 sdk.message
seq=50  11:32:37.137 tool.call.requested
seq=51  11:32:37.138 tool.started
```

tool wrapper 带着 Task 2 的 sandbox session 调用：

```text
POST /internal/sandbox-sessions/sbxsess_36076707075448deb24c11c2303c2631/tool-executions
```

sandbox manager 用 session id 找到：

```text
pod=sandbox-task-36076707075448deb24c
```

再 POST 到 pod daemon：

```text
POST http://<sandbox-task-360... pod_ip>:8080/execute
```

pod 执行：

```text
git status --short --branch
```

真实 execution：

```json
{
  "execution_id": "sbxexec_a18e529165104de6be9c6294c71034fb",
  "tool_name": "git_status",
  "sandbox_session_id": "sbxsess_36076707075448deb24c11c2303c2631",
  "runtime": {
    "type": "sandbox_session_pod",
    "runtime_profile": "kubernetes_task_attempt_container",
    "pod_name": "sandbox-task-36076707075448deb24c",
    "pod_phase": "Running",
    "duration_ms": 11,
    "runtime_policy": "task_attempt_sandbox"
  },
  "tool_result": {
    "ok": true,
    "summary": "Git status read.",
    "data": {
      "command": ["git", "status", "--short", "--branch"],
      "stdout": "## main...origin/main\n",
      "stderr": "",
      "returncode": 0
    }
  }
}
```

事件：

```text
seq=52  11:32:37.188 tool.execution
seq=53  11:32:37.281 sdk.message
```

agent 收到 `## main...origin/main` 后，决定创建分支。

### 6.3 Agent 调用 create_git_branch，继续复用 Task 2 pod

真实 tool call：

```json
{
  "id": "toolcall_490520676a3c4bd9a7db5ef8d5dd29b4",
  "tool_name": "create_git_branch",
  "input": {
    "branch_name": "demo-2000"
  },
  "task_attempt_id": "taskattempt_a1f1ad89859a4f81b6e355d4b3593133",
  "latest_execution_id": "sbxexec_627ccc8d04fb479e97b0e98ef7070b1c",
  "created_at": "2026-05-25T11:32:39.405842Z",
  "ended_at": "2026-05-25T11:32:39.461848Z"
}
```

事件：

```text
seq=54  11:32:39.406 sdk.message
seq=55  11:32:39.422 tool.call.requested
seq=56  11:32:39.423 tool.started
```

请求仍然进入同一个 session endpoint：

```text
POST /internal/sandbox-sessions/sbxsess_36076707075448deb24c11c2303c2631/tool-executions
```

manager 仍然定位同一个 pod：

```text
sandbox-task-36076707075448deb24c
```

pod 内执行：

```text
git checkout -b demo-2000
```

真实 execution：

```json
{
  "execution_id": "sbxexec_627ccc8d04fb479e97b0e98ef7070b1c",
  "tool_name": "create_git_branch",
  "sandbox_session_id": "sbxsess_36076707075448deb24c11c2303c2631",
  "runtime": {
    "type": "sandbox_session_pod",
    "runtime_profile": "kubernetes_task_attempt_container",
    "pod_name": "sandbox-task-36076707075448deb24c",
    "pod_phase": "Running",
    "duration_ms": 10,
    "runtime_policy": "task_attempt_sandbox"
  },
  "tool_result": {
    "ok": true,
    "summary": "Git branch created.",
    "data": {
      "command": ["git", "checkout", "-b", "demo-2000"],
      "stdout": "",
      "stderr": "Switched to a new branch 'demo-2000'\n",
      "returncode": 0,
      "stderr_bytes": 37,
      "stdout_bytes": 0,
      "output_limit_bytes": 4000
    }
  }
}
```

事件：

```text
seq=57  11:32:39.471 tool.execution
seq=58  11:32:39.497 sdk.message
```

agent 收到 `Git branch created.` 后，继续调用 `git_status` 验证当前分支。

### 6.4 Agent 调用 git_status，继续复用 Task 2 pod

真实 tool call：

```json
{
  "id": "toolcall_3e8d9ea7cc9a49b9a80415e2b8e5090e",
  "tool_name": "git_status",
  "input": {},
  "task_attempt_id": "taskattempt_a1f1ad89859a4f81b6e355d4b3593133",
  "latest_execution_id": "sbxexec_1a03798248b2464c8368b688a3b9c47e",
  "created_at": "2026-05-25T11:32:41.246182Z",
  "ended_at": "2026-05-25T11:32:41.312563Z"
}
```

事件：

```text
seq=59  11:32:41.246 sdk.message
seq=60  11:32:41.267 tool.call.requested
seq=61  11:32:41.268 tool.started
```

仍然是：

```text
sandbox_session_id=sbxsess_36076707075448deb24c11c2303c2631
pod=sandbox-task-36076707075448deb24c
```

真实 execution：

```json
{
  "execution_id": "sbxexec_1a03798248b2464c8368b688a3b9c47e",
  "tool_name": "git_status",
  "sandbox_session_id": "sbxsess_36076707075448deb24c11c2303c2631",
  "runtime": {
    "type": "sandbox_session_pod",
    "runtime_profile": "kubernetes_task_attempt_container",
    "pod_name": "sandbox-task-36076707075448deb24c",
    "pod_phase": "Running",
    "duration_ms": 13,
    "runtime_policy": "task_attempt_sandbox"
  },
  "tool_result": {
    "ok": true,
    "summary": "Git status read.",
    "data": {
      "command": ["git", "status", "--short", "--branch"],
      "stdout": "## demo-2000\n",
      "stderr": "",
      "returncode": 0
    }
  }
}
```

事件：

```text
seq=62  11:32:41.323 tool.execution
seq=63  11:32:41.347 sdk.message
```

这三个普通工具的复用证据是：

```text
git_status before branch:
  sandbox_session_id=sbxsess_36076707075448deb24c11c2303c2631
  pod=sandbox-task-36076707075448deb24c

create_git_branch:
  sandbox_session_id=sbxsess_36076707075448deb24c11c2303c2631
  pod=sandbox-task-36076707075448deb24c

git_status after branch:
  sandbox_session_id=sbxsess_36076707075448deb24c11c2303c2631
  pod=sandbox-task-36076707075448deb24c
```

agent 收到 `## demo-2000` 后输出结果：

```text
seq=64  11:32:43.984 sdk.message
seq=65  11:32:43.999 agent.message
seq=66  11:32:50.094 sdk.message
seq=67  11:32:50.106 tool.started       StructuredOutput
seq=68  11:32:50.147 sdk.message
seq=69  11:32:52.280 sdk.message
seq=70  11:32:52.299 agent.message
seq=71  11:32:52.314 sdk.message
seq=72  11:32:52.326 agent.result
```

Task 2 result：

```text
Created a new local branch named demo-2000 from the main branch of the cloned lucarunai/demo repository and checked it out.
Verified via git_status that demo-2000 is the active branch.
No files were changed; branch history matches main at creation time.
```

Task 2 结束后关闭 session 并删除 pod：

```text
DELETE /internal/sandbox-sessions/sbxsess_36076707075448deb24c11c2303c2631
pod deleted: sandbox-task-36076707075448deb24c

seq=73  11:32:52.616 sandbox.session.closed
seq=74  11:32:52.673 task.completed
seq=75  11:32:52.703 task.handoff
```

## 7. Task 3 完整流程：push demo-2000 branch

Task 3 启动：

```text
task_id: task_d53e77b17cfe49b7b72613b7ff3b4bd2
task_attempt_id: taskattempt_c350d830791c498e8f7ef248871b29f6

seq=76
time=11:32:52.748
event=task.started
```

### 7.1 创建 Task 3 sandbox session 与 task pod

Task 3 sandbox session：

```json
{
  "id": "sbxsess_4b2f58afcc524452834007798fcda2a7",
  "scope": "task_attempt",
  "run_id": "run_9024e38630bd4b60bc69092b3745e53a",
  "task_id": "task_d53e77b17cfe49b7b72613b7ff3b4bd2",
  "task_attempt_id": "taskattempt_c350d830791c498e8f7ef248871b29f6",
  "status": "closed",
  "runtime_profile": "kubernetes_task_attempt_container",
  "pod_name": "sandbox-task-4b2f58afcc5244528340",
  "workspace_path": "/sandboxes/users/Luca/run_9024e38630bd4b60bc69092b3745e53a",
  "created_at": "2026-05-25T11:32:54.802898Z",
  "expires_at": "2026-05-25T11:47:54.802898Z",
  "closed_at": "2026-05-25T11:38:12.905401Z"
}
```

事件：

```text
seq=77  11:32:54.815 sandbox.session.started
seq=78  11:32:55.205 sdk.message
seq=79  11:32:55.227 agent.session.started
claude_session_id=113fb568-9613-48c8-a64d-aba0a0ee81b6
```

Task 3 内普通 workspace tool 会带：

```text
sandbox_session_id=sbxsess_4b2f58afcc524452834007798fcda2a7
pod_name=sandbox-task-4b2f58afcc5244528340
```

### 7.2 Agent 调用 git_status，确认当前 demo-2000

真实 tool call：

```json
{
  "id": "toolcall_c2dfd02453714a80921b7b071334a27b",
  "tool_name": "git_status",
  "input": {},
  "task_attempt_id": "taskattempt_c350d830791c498e8f7ef248871b29f6",
  "latest_execution_id": "sbxexec_e72715378ad74071b9b73b00d9f88d6f",
  "created_at": "2026-05-25T11:32:59.155727Z",
  "ended_at": "2026-05-25T11:32:59.214496Z"
}
```

事件：

```text
seq=80  11:32:59.045 sdk.message
seq=81  11:32:59.155 sdk.message
seq=82  11:32:59.172 tool.call.requested
seq=83  11:32:59.172 tool.started
```

进入 Task 3 的 sandbox session endpoint：

```text
POST /internal/sandbox-sessions/sbxsess_4b2f58afcc524452834007798fcda2a7/tool-executions
```

manager 定位 pod：

```text
sandbox-task-4b2f58afcc5244528340
```

pod 执行：

```text
git status --short --branch
```

真实 execution：

```json
{
  "execution_id": "sbxexec_e72715378ad74071b9b73b00d9f88d6f",
  "tool_name": "git_status",
  "sandbox_session_id": "sbxsess_4b2f58afcc524452834007798fcda2a7",
  "runtime": {
    "type": "sandbox_session_pod",
    "runtime_profile": "kubernetes_task_attempt_container",
    "pod_name": "sandbox-task-4b2f58afcc5244528340",
    "pod_phase": "Running",
    "duration_ms": 11,
    "runtime_policy": "task_attempt_sandbox"
  },
  "tool_result": {
    "ok": true,
    "summary": "Git status read.",
    "data": {
      "command": ["git", "status", "--short", "--branch"],
      "stdout": "## demo-2000\n",
      "stderr": "",
      "returncode": 0
    }
  }
}
```

事件：

```text
seq=84  11:32:59.224 tool.execution
seq=85  11:32:59.239 sdk.message
```

agent 收到 `## demo-2000` 后，决定 push。

### 7.3 Agent 调用 push_current_git_branch，创建 approval 并等待

真实 tool call：

```json
{
  "id": "toolcall_f059a66a32d74a3cb54915f344d3a86e",
  "tool_name": "push_current_git_branch",
  "input": {},
  "task_attempt_id": "taskattempt_c350d830791c498e8f7ef248871b29f6",
  "latest_execution_id": "sbxexec_5f3e53ad7ae240e6a4f4bb75e9e19924",
  "created_at": "2026-05-25T11:33:02.892692Z",
  "ended_at": "2026-05-25T11:37:57.928659Z"
}
```

事件：

```text
seq=86  11:33:02.892 sdk.message
seq=87  11:33:02.909 tool.call.requested
seq=88  11:33:02.910 tool.started
```

runtime policy：

```text
runtime_policy=broker_only
policy_reason=trusted_github_tools_execute_in_secret_holding_broker
```

这个工具先创建 approval：

```json
{
  "id": "approval_e23d1cb0c55c475f892e67129f96bf2d",
  "run_id": "run_9024e38630bd4b60bc69092b3745e53a",
  "session_id": "sess_4b3c3617fe3645b8ba619d942cfd1b02",
  "user_id": "Luca",
  "task_id": "task_d53e77b17cfe49b7b72613b7ff3b4bd2",
  "task_attempt_id": "taskattempt_c350d830791c498e8f7ef248871b29f6",
  "tool_call_id": "toolcall_f059a66a32d74a3cb54915f344d3a86e",
  "tool_name": "push_current_git_branch",
  "tool_input": {},
  "requested_by": "agent",
  "status": "approved",
  "reason": "Push publishes local branch state to the remote GitHub repository.",
  "created_at": "2026-05-25T11:33:02.922155Z",
  "decided_at": "2026-05-25T11:37:55.509415Z",
  "decided_by": "web-ui",
  "decision_reason": null
}
```

事件：

```text
seq=89  11:33:02.933 approval.requested
seq=90  11:37:55.515 approval.approved
```

真实等待时间约 292 秒。审批通过后，trusted executor 执行：

```json
{
  "execution_id": "sbxexec_5f3e53ad7ae240e6a4f4bb75e9e19924",
  "tool_name": "push_current_git_branch",
  "execution_status": "succeeded",
  "runtime": {
    "type": "trusted_github_executor",
    "runtime_policy": "broker_only",
    "policy_reason": "trusted_github_tools_execute_in_secret_holding_broker",
    "duration_ms": 1823
  },
  "tool_result": {
    "ok": true,
    "summary": "Git branch pushed.",
    "data": {
      "command": [
        "git",
        "-c",
        "[redacted-github-auth-header]",
        "push",
        "-u",
        "origin",
        "demo-2000"
      ],
      "stdout": "branch 'demo-2000' set up to track 'origin/demo-2000'.\n",
      "stderr": "remote:\nremote: Create a pull request for 'demo-2000' on GitHub by visiting:\nremote:      https://github.com/lucarunai/demo/pull/new/demo-2000\nremote:\nTo https://github.com/lucarunai/demo\n * [new branch]      demo-2000 -> demo-2000\n",
      "returncode": 0
    }
  }
}
```

事件：

```text
seq=91  11:37:57.942 tool.execution
seq=92  11:37:57.962 sdk.message
```

`seq=92` 表示 push result 回到 agent。agent 继续做最终状态验证。

### 7.4 Agent 调用 git_status，继续复用 Task 3 sandbox pod

真实 tool call：

```json
{
  "id": "toolcall_155e965b2939492e8eeeeefac12e6fee",
  "tool_name": "git_status",
  "input": {},
  "task_attempt_id": "taskattempt_c350d830791c498e8f7ef248871b29f6",
  "latest_execution_id": "sbxexec_a611a4867c6c4fbea0c69749f412ab64",
  "created_at": "2026-05-25T11:38:00.153520Z",
  "ended_at": "2026-05-25T11:38:00.212033Z"
}
```

事件：

```text
seq=93  11:38:00.153 sdk.message
seq=94  11:38:00.170 tool.call.requested
seq=95  11:38:00.171 tool.started
```

这次仍然带 Task 3 的 sandbox session：

```text
sandbox_session_id=sbxsess_4b2f58afcc524452834007798fcda2a7
pod=sandbox-task-4b2f58afcc5244528340
```

manager 再次调用同一个 pod 的 `/execute`：

```text
POST http://<sandbox-task-4b2... pod_ip>:8080/execute
```

pod 执行：

```text
git status --short --branch
```

真实 execution：

```json
{
  "execution_id": "sbxexec_a611a4867c6c4fbea0c69749f412ab64",
  "tool_name": "git_status",
  "sandbox_session_id": "sbxsess_4b2f58afcc524452834007798fcda2a7",
  "runtime": {
    "type": "sandbox_session_pod",
    "runtime_profile": "kubernetes_task_attempt_container",
    "pod_name": "sandbox-task-4b2f58afcc5244528340",
    "pod_phase": "Running",
    "duration_ms": 11,
    "runtime_policy": "task_attempt_sandbox"
  },
  "tool_result": {
    "ok": true,
    "summary": "Git status read.",
    "data": {
      "command": ["git", "status", "--short", "--branch"],
      "stdout": "## demo-2000\n",
      "stderr": "",
      "returncode": 0
    }
  }
}
```

事件：

```text
seq=96  11:38:00.222 tool.execution
seq=97  11:38:00.236 sdk.message
```

Task 3 的复用证据：

```text
git_status before push:
  sandbox_session_id=sbxsess_4b2f58afcc524452834007798fcda2a7
  pod=sandbox-task-4b2f58afcc5244528340

git_status after push:
  sandbox_session_id=sbxsess_4b2f58afcc524452834007798fcda2a7
  pod=sandbox-task-4b2f58afcc5244528340
```

agent 收到最后一次 `## demo-2000`，输出 final result：

```text
seq=98   11:38:09.052 sdk.message
seq=99   11:38:09.064 tool.started        StructuredOutput
seq=100  11:38:09.244 sdk.message
seq=101  11:38:12.331 sdk.message
seq=102  11:38:12.343 agent.message
seq=103  11:38:12.373 sdk.message
seq=104  11:38:12.386 agent.result
```

Task 3 result：

```text
Pushed the local demo-2000 branch to the origin remote on github.com/lucarunai/demo.
The push completed successfully without errors and remote tracking for origin/demo-2000 was established.
No files were changed in this task.
```

Task 3 结束后关闭 session：

```text
DELETE /internal/sandbox-sessions/sbxsess_4b2f58afcc524452834007798fcda2a7
pod deleted: sandbox-task-4b2f58afcc5244528340

seq=105  11:38:12.916 sandbox.session.closed
seq=106  11:38:12.969 task.completed
seq=107  11:38:12.996 task.handoff
```

## 8. Run 完成与 workspace 清理

三个 task 都 completed 后：

```text
seq=108
time=11:38:13.018
event=run.completed

seq=109
time=11:38:13.044
event=workspace.cleaned
payload:
{
  "run_id": "run_9024e38630bd4b60bc69092b3745e53a",
  "status": "deleted"
}
```

最终 run：

```text
status=completed
ended_at=2026-05-25T11:38:13.007897Z
metadata.workspace_cleaned=true
```

## 9. 全部 sandbox session 对照

```text
Task 1:
  task_attempt_id: taskattempt_078acc0638ba4a6ba58f4ef4743f816e
  sandbox_session_id: sbxsess_682d390674484b2a98c9a171a3050056
  pod_name: sandbox-task-682d390674484b2a98c9
  created_at: 2026-05-25T11:32:11.565878Z
  closed_at:  2026-05-25T11:32:30.774060Z

Task 2:
  task_attempt_id: taskattempt_a1f1ad89859a4f81b6e355d4b3593133
  sandbox_session_id: sbxsess_36076707075448deb24c11c2303c2631
  pod_name: sandbox-task-36076707075448deb24c
  created_at: 2026-05-25T11:32:33.928946Z
  closed_at:  2026-05-25T11:32:52.604263Z

Task 3:
  task_attempt_id: taskattempt_c350d830791c498e8f7ef248871b29f6
  sandbox_session_id: sbxsess_4b2f58afcc524452834007798fcda2a7
  pod_name: sandbox-task-4b2f58afcc5244528340
  created_at: 2026-05-25T11:32:54.802898Z
  closed_at:  2026-05-25T11:38:12.905401Z
```

## 10. 全部 tool call / execution / sandbox 对照

```text
1. clone_github_repository
   tool_call_id: toolcall_a3f2792a281d4298b2dcaaa4d9c67781
   execution_id: sbxexec_96ad467d6b2d452fa59c84743c01222d
   runtime: trusted_github_executor
   sandbox_session_id: null
   result: Repository cloned.

2. git_status
   tool_call_id: toolcall_f08dea8753684ab7bfef79ec04e50e03
   execution_id: sbxexec_bc2aa2cbae0645c7a0e6ca5e4ed4c7c7
   sandbox_session_id: sbxsess_682d390674484b2a98c9a171a3050056
   pod: sandbox-task-682d390674484b2a98c9
   stdout: ## main...origin/main

3. glob_workspace_files
   tool_call_id: toolcall_b854d95ad5bd4051b4181c20f7557959
   execution_id: sbxexec_ddef1406e06644d5a1a493773c9401ee
   sandbox_session_id: sbxsess_682d390674484b2a98c9a171a3050056
   pod: sandbox-task-682d390674484b2a98c9
   result: Workspace glob completed.

4. git_status
   tool_call_id: toolcall_6bb78b8b2d1e4479922dfcf72937f7c9
   execution_id: sbxexec_a18e529165104de6be9c6294c71034fb
   sandbox_session_id: sbxsess_36076707075448deb24c11c2303c2631
   pod: sandbox-task-36076707075448deb24c
   stdout: ## main...origin/main

5. create_git_branch
   tool_call_id: toolcall_490520676a3c4bd9a7db5ef8d5dd29b4
   execution_id: sbxexec_627ccc8d04fb479e97b0e98ef7070b1c
   sandbox_session_id: sbxsess_36076707075448deb24c11c2303c2631
   pod: sandbox-task-36076707075448deb24c
   command: git checkout -b demo-2000

6. git_status
   tool_call_id: toolcall_3e8d9ea7cc9a49b9a80415e2b8e5090e
   execution_id: sbxexec_1a03798248b2464c8368b688a3b9c47e
   sandbox_session_id: sbxsess_36076707075448deb24c11c2303c2631
   pod: sandbox-task-36076707075448deb24c
   stdout: ## demo-2000

7. git_status
   tool_call_id: toolcall_c2dfd02453714a80921b7b071334a27b
   execution_id: sbxexec_e72715378ad74071b9b73b00d9f88d6f
   sandbox_session_id: sbxsess_4b2f58afcc524452834007798fcda2a7
   pod: sandbox-task-4b2f58afcc5244528340
   stdout: ## demo-2000

8. push_current_git_branch
   tool_call_id: toolcall_f059a66a32d74a3cb54915f344d3a86e
   approval_id: approval_e23d1cb0c55c475f892e67129f96bf2d
   execution_id: sbxexec_5f3e53ad7ae240e6a4f4bb75e9e19924
   runtime: trusted_github_executor
   sandbox_session_id: null
   result: Git branch pushed.

9. git_status
   tool_call_id: toolcall_155e965b2939492e8eeeeefac12e6fee
   execution_id: sbxexec_a611a4867c6c4fbea0c69749f412ab64
   sandbox_session_id: sbxsess_4b2f58afcc524452834007798fcda2a7
   pod: sandbox-task-4b2f58afcc5244528340
   stdout: ## demo-2000
```

## 11. Approval 真实数据

```json
{
  "id": "approval_e23d1cb0c55c475f892e67129f96bf2d",
  "run_id": "run_9024e38630bd4b60bc69092b3745e53a",
  "session_id": "sess_4b3c3617fe3645b8ba619d942cfd1b02",
  "user_id": "Luca",
  "task_id": "task_d53e77b17cfe49b7b72613b7ff3b4bd2",
  "task_attempt_id": "taskattempt_c350d830791c498e8f7ef248871b29f6",
  "tool_call_id": "toolcall_f059a66a32d74a3cb54915f344d3a86e",
  "tool_name": "push_current_git_branch",
  "tool_input": {},
  "reason": "Push publishes local branch state to the remote GitHub repository.",
  "status": "approved",
  "requested_by": "agent",
  "created_at": "2026-05-25T11:33:02.922155Z",
  "decided_at": "2026-05-25T11:37:55.509415Z",
  "decided_by": "web-ui"
}
```

## 12. Ops summary

```text
schema_version: ops_run_summary.v1
run_id: run_9024e38630bd4b60bc69092b3745e53a
status: completed

task_counts:
  total: 3
  completed: 3

task_attempt_counts:
  total: 3
  completed: 3

tool_counts:
  total: 9
  succeeded: 9

tool_execution_counts:
  total: 9
  succeeded: 9

approval_summary:
  total: 1
  approved: 1
  decision_latency_ms_avg: 292587

sandbox_summary:
  runtime_profiles:
    kubernetes_task_attempt_container: 7
    unknown: 2
  pod_phases:
    Running: 7
    unknown: 2
  output_truncated_count: 0
  workspace_bytes_max: 25098
  duration_ms_avg: 305
  duration_ms_max: 1823

replay_summary:
  available: true
  replay_valid: true
  hash_chain_valid: true
  consistent: true
  error_count: 0
  difference_count: 0
```

`unknown:2` 对应两个 trusted GitHub executor 工具：`clone_github_repository` 和 `push_current_git_branch`，它们没有 task sandbox pod。

## 13. Security 设计如何嵌入本 case

用户边界：

```text
run、session、event、approval 都绑定 user_id=Luca。
```

task sandbox 边界：

```text
每个 task attempt 有独立 sandbox_session_id 与 sandbox-task-* pod。
普通 workspace tool 必须通过 sandbox_session_id 进入对应 task pod。
```

pod 权限边界：

```text
runAsNonRoot=true
runAsUser=10001
runAsGroup=10001
allowPrivilegeEscalation=false
capabilities.drop=["ALL"]
seccompProfile=RuntimeDefault
automountServiceAccountToken=false
```

workspace 边界：

```text
host/workspace path:
  /sandboxes/users/Luca/run_9024e38630bd4b60bc69092b3745e53a

pod 内 mount:
  /workspace/run_9024e38630bd4b60bc69092b3745e53a
```

网络边界：

```text
network_policy=sandbox-session-default-deny
egress_policy=default-deny
```

工具边界：

```text
普通 workspace tool:
  runtime_policy=task_attempt_sandbox
  进入 task sandbox pod

GitHub credential tool:
  runtime_policy=broker_only
  policy_reason=trusted_github_tools_execute_in_secret_holding_broker
  通过 trusted GitHub executor 执行
```

GitHub token 边界：

```text
普通 task sandbox pod 不直接持有 GitHub token。
clone/push command 中 auth header 被 redacted:
  [redacted-github-auth-header]
```

human approval：

```text
push_current_git_branch 修改远端仓库。
执行前必须创建 approval request。
真实 approval 由 web-ui 在 2026-05-25T11:37:55.509415Z 批准。
```

审计：

```text
session_events 共 109 条。
关键节点包括 run.claimed、plan.started、tasks.created、sandbox.session.started、tool.call.requested、tool.execution、approval.requested、approval.approved、sandbox.session.closed、task.completed、run.completed、workspace.cleaned。
replay/hash chain 校验通过。
```

## 14. 完整一句话版

这条 v3 real case 的完整链路是：

```text
前端提交 demo-2000 prompt
  -> session 层创建 run_9024e38630bd4b60bc69092b3745e53a
  -> 写 user.prompt.accepted
  -> brain worker claim run
  -> planner 拆成 3 个 task
  -> Task 1 started
      -> 创建 sandbox session sbxsess_682... 和 pod sandbox-task-682...
      -> agent 启动
      -> agent 调 clone_github_repository
      -> trusted executor clone main
      -> result 回 agent
      -> agent 调 git_status
      -> tool wrapper 带 sbxsess_682... 调 sandbox manager
      -> manager POST 到 sandbox-task-682.../execute
      -> pod 返回 main
      -> result 回 agent
      -> agent 调 glob_workspace_files
      -> 同一个 sbxsess_682... / 同一个 pod 执行
      -> result 回 agent
      -> agent.result
      -> close sbxsess_682... / 删除 pod
      -> task.completed / handoff
  -> Task 2 started
      -> 创建 sandbox session sbxsess_360... 和 pod sandbox-task-360...
      -> agent 启动
      -> agent 调 git_status
      -> sbxsess_360... 对应 pod 返回 main
      -> result 回 agent
      -> agent 调 create_git_branch demo-2000
      -> 同一个 sbxsess_360... / 同一个 pod 执行 git checkout -b demo-2000
      -> result 回 agent
      -> agent 调 git_status
      -> 同一个 pod 返回 demo-2000
      -> result 回 agent
      -> agent.result
      -> close sbxsess_360... / 删除 pod
      -> task.completed / handoff
  -> Task 3 started
      -> 创建 sandbox session sbxsess_4b2... 和 pod sandbox-task-4b2...
      -> agent 启动
      -> agent 调 git_status
      -> sbxsess_4b2... 对应 pod 返回 demo-2000
      -> result 回 agent
      -> agent 调 push_current_git_branch
      -> approval.requested
      -> web-ui approval.approved
      -> trusted executor push demo-2000
      -> result 回 agent
      -> agent 调 git_status
      -> 同一个 sbxsess_4b2... / 同一个 pod 返回 demo-2000
      -> result 回 agent
      -> agent.result
      -> close sbxsess_4b2... / 删除 pod
      -> task.completed / handoff
  -> run.completed
  -> workspace.cleaned
```

最关键的实现事实：

```text
sandbox pod 的复用不是隐式状态，而是 sandbox_session_id 贯穿 orchestrator、agent tool server、sdk_tools、sandbox_client、sandbox_manager、tool_executions。

同一个 task attempt 内的普通 workspace tool 都使用同一个 sandbox_session_id。

sandbox manager 根据 sandbox_session_id 定位同一个 sandbox-task-* pod，然后通过 pod_ip:8080/execute 执行每一次 ToolExecutionRequest。

每一次 tool execution 的结果都会写入 DB，并作为 tool result 回到 agent。agent 再根据这个 result 决定下一步。

task 完成后，orchestrator close sandbox session，sandbox manager 删除对应 task pod。
```
