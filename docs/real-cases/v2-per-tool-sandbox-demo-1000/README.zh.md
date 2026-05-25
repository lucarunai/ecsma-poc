# Real Case: v2 per-tool sandbox pod 执行 demo-1000 分支推送

本文记录一次真实的 `18082 / v2 / per-tool sandbox pod` 执行链路。数据来自本地前端已经模拟提交过的真实请求、真实 DB 入库记录、真实 tool execution envelope、真实 approval 记录和真实 session event 时间线。

本文只分析 v2 这条 case，不混入 v3 的 task sandbox session 设计。

## 1. Case 基本信息

用户在 `http://localhost:18082/` 提交的 prompt：

```text
clone https://github.com/lucarunai/demo branch main，create new branch demo-1000，then push github
```

真实 run：

```text
run_id:     run_af542a86a0794f6bb5acb305f764efdd
session_id: sess_13d480a0d4b048da876dbc259e530e47
user_id:    Luca
status:     completed
created_at: 2026-05-25T11:31:38.428624Z
started_at: 2026-05-25T11:31:38.714427Z
ended_at:   2026-05-25T11:37:34.867260Z
```

所有时间均为 DB 中的 UTC 时间。Asia/Shanghai 时间需要加 8 小时。

这条 run 的关键性质：

```text
系统版本: v2
入口服务: http://localhost:18082/
sandbox 模式: per-tool sandbox pod
普通 sandbox tool: 一次 tool call 创建一个 Kubernetes pod
GitHub credential tool: trusted_github_executor / broker
human approval: push_current_git_branch 需要审批
```

非常关键的一点：这条 v2 case 不是 per-task sandbox session。

真实 DB 验证结果：

```text
select count(*) from sandbox_sessions ...
ERROR: relation "sandbox_sessions" does not exist

tool_executions.sandbox_session_id
ERROR: column "sandbox_session_id" does not exist
```

因此本文不会使用 `sandbox_session_id` 解释这条链路。这里的普通工具执行模型是：

```text
一次 agent tool call
  -> 一条 tool_calls 记录
  -> 一次 sandbox manager 执行
  -> 一个一次性 sandbox pod
  -> 一条 tool_executions 记录
  -> tool result 回到 agent
```

## 2. 真实主数据

`runs` 表中的核心数据：

```json
{
  "id": "run_af542a86a0794f6bb5acb305f764efdd",
  "session_id": "sess_13d480a0d4b048da876dbc259e530e47",
  "user_id": "Luca",
  "prompt": "clone https://github.com/lucarunai/demo branch main，create new branch demo-1000，then push github",
  "status": "completed",
  "idempotency_key": "53ba81cc-3de9-401d-a326-5f74d273bae0",
  "attempt_count": 1,
  "created_at": "2026-05-25T11:31:38.428624Z",
  "started_at": "2026-05-25T11:31:38.714427Z",
  "ended_at": "2026-05-25T11:37:34.867260Z",
  "retention_until": "2026-06-01T11:31:38.428624Z",
  "metadata": {
    "user_id": "Luca",
    "workspace_path": "/sandboxes/users/Luca/run_af542a86a0794f6bb5acb305f764efdd",
    "workspace_cleaned": true
  }
}
```

planner 拆出来的真实 task：

```json
[
  {
    "id": "task_5956a8896e824d6d806840e76c7587fa",
    "seq": 1,
    "kind": "model_task",
    "title": "Clone repository",
    "description": "Clone the repository https://github.com/lucarunai/demo on branch main into the sandbox workspace.",
    "acceptance_criteria": [
      "Repository https://github.com/lucarunai/demo is cloned locally",
      "Active branch is main"
    ]
  },
  {
    "id": "task_4e905142ca9f4ac5ad80ee54e5c936cf",
    "seq": 2,
    "kind": "model_task",
    "title": "Create new branch demo-1000",
    "description": "From the main branch, create a new branch named demo-1000 and check it out.",
    "acceptance_criteria": [
      "Branch demo-1000 is created from main",
      "Working tree is switched to demo-1000"
    ]
  },
  {
    "id": "task_ad5c1d0589e342f6b9e614d5de7b86e8",
    "seq": 3,
    "kind": "model_task",
    "title": "Push demo-1000 branch to GitHub",
    "description": "Push the newly created demo-1000 branch to the origin remote on GitHub so it is available in the lucarunai/demo repository.",
    "acceptance_criteria": [
      "Branch demo-1000 exists on the remote origin",
      "Remote tracking is configured for demo-1000"
    ]
  }
]
```

真实 task attempt：

```text
taskattempt_da82b5380a5b40b4b85455bbc71d9fa7
  task: task_5956a8896e824d6d806840e76c7587fa
  status: completed
  claude_session_id: 2064354d-3473-4932-a5ec-6769f37e7e55
  started_at: 2026-05-25T11:31:51.055399Z
  ended_at:   2026-05-25T11:32:08.614823Z

taskattempt_c47f8cdd9ebe478390b173e7a3da8140
  task: task_4e905142ca9f4ac5ad80ee54e5c936cf
  status: completed
  claude_session_id: c62b068f-d540-4e58-9766-da0f034a6d3f
  started_at: 2026-05-25T11:32:08.667374Z
  ended_at:   2026-05-25T11:32:37.259727Z

taskattempt_1b60ce9adf104987a555b99729c6be7a
  task: task_ad5c1d0589e342f6b9e614d5de7b86e8
  status: completed
  claude_session_id: c63c23a4-940a-4ece-b3e9-a66a5bc5b23e
  started_at: 2026-05-25T11:32:37.316976Z
  ended_at:   2026-05-25T11:37:34.807140Z
```

## 3. 代码层参与者

这条链路可以按 layer 看：

```text
frontend
  -> web/session API
  -> DB/session store
  -> brain worker
  -> orchestrator
  -> planner
  -> Claude agent
  -> MCP tool wrapper
  -> tool runtime policy
  -> sandbox client 或 GitHub broker client
  -> sandbox service / sandbox manager 或 trusted GitHub executor
  -> Kubernetes sandbox pod
  -> pod runtime / sandbox tools
  -> DB tool_executions + session_events
  -> tool result 回到 agent
```

相关代码位置：

```text
前端提交:
  src/cloud_agent_poc/static/index.html
  src/cloud_agent_poc/web.py

session/run 入库与事件:
  src/cloud_agent_poc/session_app.py
  src/cloud_agent_poc/session_store.py

brain worker 与 orchestration:
  src/cloud_agent_poc/brain/worker.py
  src/cloud_agent_poc/brain/orchestrator.py

planner:
  src/cloud_agent_poc/brain/planner.py

agent 与 SDK stream:
  src/cloud_agent_poc/brain/claude_agent.py

MCP tool wrapper / approval / tool execution:
  src/cloud_agent_poc/brain/sdk_tools.py
  src/cloud_agent_poc/tool_policy.py

sandbox client/service/manager/runtime:
  src/cloud_agent_poc/sandbox_client.py
  src/cloud_agent_poc/sandbox_app.py
  src/cloud_agent_poc/sandbox_manager.py
  src/cloud_agent_poc/sandbox_runtime.py
  src/cloud_agent_poc/sandbox_tools.py

GitHub broker / trusted executor:
  src/cloud_agent_poc/github_broker_app.py
  src/cloud_agent_poc/brain/github_workflow.py

Kubernetes v2:
  k8s-v2/sandbox.yaml
  k8s-v2/github-broker.yaml
  k8s-v2/network-policy.yaml
```

## 4. Agent 和 tool 的真实交互模型

一个 task 不是 “多个 tool 全部执行完后 agent 才看结果”。真实行为是：

```text
task.started
  -> agent session started
  -> agent 输出 ToolUseBlock
  -> tool wrapper 创建 tool_call
  -> tool 执行
  -> tool_execution 入库
  -> tool result 回到 agent
  -> agent 基于 tool result 再决定下一步
  -> 可能继续调用下一个 tool
  -> agent 输出 structured result
  -> task.completed
```

所以 task 内的粒度是 tool-call 级交互：

```text
agent -> tool
tool -> result
agent -> next tool
tool -> result
agent -> final task result
```

`tool.execution` 后面紧跟的 `sdk.message`，就是 tool result 回灌 agent 后，agent 继续推理的证据。

## 5. Run 创建、claim 与 planning

前端提交 prompt 后，系统创建 run，并写入第一条 event：

```text
seq=1
id=435
time=11:31:38.434
event=user.prompt.accepted
```

随后 brain worker claim run：

```text
seq=2  id=436  time=11:31:38.723  event=run.claimed
seq=3  id=437  time=11:31:38.769  event=run.started
seq=4  id=438  time=11:31:38.797  event=plan.started
```

planner 通过模型把用户 prompt 拆成 3 个 task：

```text
seq=5-11  planner.sdk.message / planner.message
seq=12    id=446  time=11:31:51.044  event=tasks.created
```

到这里，run 已经从 “一个自然语言请求” 变成可执行的 task DAG/sequence。本 case 是简单顺序执行：

```text
Task 1: Clone repository
  -> Task 2: Create new branch demo-1000
    -> Task 3: Push demo-1000 branch to GitHub
```

## 6. Task 1 完整流程：Clone repository

Task 1 启动：

```text
task_id: task_5956a8896e824d6d806840e76c7587fa
attempt_id: taskattempt_da82b5380a5b40b4b85455bbc71d9fa7
claude_session_id: 2064354d-3473-4932-a5ec-6769f37e7e55

seq=13  time=11:31:51.075  event=task.started
seq=15  time=11:31:51.467  event=agent.session.started
```

agent 收到 task 1 的目标：

```text
Clone the repository https://github.com/lucarunai/demo on branch main into the sandbox workspace.
```

验收标准：

```text
Repository https://github.com/lucarunai/demo is cloned locally
Active branch is main
```

### 6.1 agent 第一次决策：调用 clone_github_repository

agent 输出的 tool use：

```json
{
  "tool_name": "clone_github_repository",
  "input": {
    "repository_url": "https://github.com/lucarunai/demo",
    "source_branch": "main"
  }
}
```

系统写入 `tool_calls`：

```json
{
  "id": "toolcall_446c4b0146414ffa9b5101c6a5278568",
  "run_id": "run_af542a86a0794f6bb5acb305f764efdd",
  "task_id": "task_5956a8896e824d6d806840e76c7587fa",
  "task_attempt_id": "taskattempt_da82b5380a5b40b4b85455bbc71d9fa7",
  "tool_name": "clone_github_repository",
  "input": {
    "source_branch": "main",
    "repository_url": "https://github.com/lucarunai/demo"
  },
  "status": "succeeded",
  "latest_execution_id": "sbxexec_55b9191b368c4a98b5eae9ffc1f674b4",
  "created_at": "2026-05-25T11:31:54.513766Z",
  "ended_at": "2026-05-25T11:31:55.332228Z"
}
```

事件：

```text
seq=16  time=11:31:54.230  event=sdk.message
seq=17  time=11:31:54.514  event=sdk.message
seq=18  time=11:31:54.531  event=tool.call.requested
seq=19  time=11:31:54.531  event=tool.started
```

这次 tool 不创建 sandbox pod。原因是 `clone_github_repository` 需要 GitHub credential，走 trusted GitHub executor / broker。普通 sandbox pod 不直接拿 GitHub token。

trusted executor 执行：

```json
{
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
  "returncode": 0,
  "stderr": "Cloning into '.'...\n"
}
```

写入 `tool_executions` 的 envelope 核心：

```json
{
  "execution_id": "sbxexec_55b9191b368c4a98b5eae9ffc1f674b4",
  "run_id": "run_af542a86a0794f6bb5acb305f764efdd",
  "tool_call_id": "toolcall_446c4b0146414ffa9b5101c6a5278568",
  "tool_name": "clone_github_repository",
  "execution_status": "succeeded",
  "runtime": {
    "type": "trusted_github_executor",
    "pod_name": null,
    "pod_phase": null,
    "exit_code": null,
    "duration_ms": 768
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
      "returncode": 0,
      "stderr": "Cloning into '.'...\n"
    }
  }
}
```

事件：

```text
seq=20  time=11:31:55.341  event=tool.execution
seq=21  time=11:31:55.356  event=sdk.message
```

`seq=21` 表示 clone 的结果已经回到 agent。agent 读到 `ok=true` 和 `Repository cloned.` 后，没有马上完成 task，而是继续判断：还需要确认 active branch 是 `main`。

### 6.2 agent 第二次决策：调用 git_status 验证 branch

agent 输出第二次 tool use：

```json
{
  "tool_name": "git_status",
  "input": {}
}
```

写入 `tool_calls`：

```json
{
  "id": "toolcall_88118fc24eef4c6fa6b7fadf645626ec",
  "run_id": "run_af542a86a0794f6bb5acb305f764efdd",
  "task_id": "task_5956a8896e824d6d806840e76c7587fa",
  "task_attempt_id": "taskattempt_da82b5380a5b40b4b85455bbc71d9fa7",
  "tool_name": "git_status",
  "input": {},
  "status": "succeeded",
  "latest_execution_id": "sbxexec_7cd35c8cc8de4626bedc47803595ea9c",
  "created_at": "2026-05-25T11:31:57.083643Z",
  "ended_at": "2026-05-25T11:32:00.185757Z"
}
```

事件：

```text
seq=22  time=11:31:57.083  event=sdk.message
seq=23  time=11:31:57.105  event=tool.call.requested
seq=24  time=11:31:57.105  event=tool.started
```

这次是普通 sandbox tool，因此执行时内嵌了一次完整的 sandbox manager -> pod 数据流：

```text
MCP tool wrapper 接收 agent 的 git_status 调用
  -> 创建 tool_call
  -> 组装 ToolExecutionRequest
  -> sandbox_client 调 sandbox service
  -> sandbox manager 创建一次性 pod sandbox-tool-7cd35c8cc8de4626bedc
  -> manager 将 ToolExecutionRequest JSON base64 编码
  -> 注入 pod 环境变量 SANDBOX_TOOL_REQUEST_B64
  -> 注入 SANDBOX_WORKSPACE_PATH=/workspace/run_af542a86a0794f6bb5acb305f764efdd
  -> 挂载同一个 run workspace
  -> pod 内 sandbox_runtime 解码 request
  -> dispatch 到 sandbox_tools.git_status
  -> pod 内执行 git status --short --branch
  -> runtime 将 ToolExecutionEnvelope JSON 输出到 stdout
  -> pod 退出
  -> manager 读取 pod phase / exit code / logs
  -> manager 从 logs 解析 envelope，并补 pod_name/pod_phase/exit_code/duration_ms
  -> envelope 返回 sdk_tools
  -> sdk_tools 写 tool_executions、更新 tool_calls、emit tool.execution
  -> tool result 回到 agent
```

pod 逻辑上收到的 request：

```json
{
  "run_id": "run_af542a86a0794f6bb5acb305f764efdd",
  "tool_call_id": "toolcall_88118fc24eef4c6fa6b7fadf645626ec",
  "tool_name": "git_status",
  "args": {},
  "task_attempt_id": "taskattempt_da82b5380a5b40b4b85455bbc71d9fa7",
  "workspace_path": "/sandboxes/users/Luca/run_af542a86a0794f6bb5acb305f764efdd"
}
```

真实 execution envelope 核心：

```json
{
  "execution_id": "sbxexec_7cd35c8cc8de4626bedc47803595ea9c",
  "tool_call_id": "toolcall_88118fc24eef4c6fa6b7fadf645626ec",
  "tool_name": "git_status",
  "execution_status": "succeeded",
  "runtime": {
    "type": "sandbox_pod",
    "pod_name": "sandbox-tool-7cd35c8cc8de4626bedc",
    "pod_phase": "Succeeded",
    "exit_code": 0,
    "duration_ms": 3045
  },
  "tool_result": {
    "ok": true,
    "data": {
      "command": ["git", "status", "--short", "--branch"],
      "stdout": "## main...origin/main\n",
      "returncode": 0
    }
  }
}
```

事件：

```text
seq=25  time=11:32:00.196  event=tool.execution
seq=26  time=11:32:00.212  event=sdk.message
```

`seq=26` 表示 `git_status` 的 result 已经回到 agent。agent 读取：

```text
## main...origin/main
```

然后判断 task 1 的两个 acceptance criteria 都满足，输出 task result：

```text
seq=27  time=11:32:02.619  event=sdk.message
seq=28  time=11:32:02.634  event=agent.message
seq=29  time=11:32:06.610  event=sdk.message
seq=30  time=11:32:06.622  event=tool.started        StructuredOutput
seq=31  time=11:32:06.634  event=sdk.message
seq=32  time=11:32:08.254  event=sdk.message
seq=33  time=11:32:08.268  event=agent.result
seq=34  time=11:32:08.634  event=task.completed
seq=35  time=11:32:08.656  event=task.handoff
```

Task 1 结果：

```text
Cloned https://github.com/lucarunai/demo into the sandbox workspace on branch main.
Verified the active branch is main via git_status.
No files changed and no tests are required for this task.
```

## 7. Task 2 完整流程：Create new branch demo-1000

Task 2 启动：

```text
task_id: task_4e905142ca9f4ac5ad80ee54e5c936cf
attempt_id: taskattempt_c47f8cdd9ebe478390b173e7a3da8140
claude_session_id: c62b068f-d540-4e58-9766-da0f034a6d3f

seq=36  time=11:32:08.687  event=task.started
seq=38  time=11:32:09.114  event=agent.session.started
```

agent 收到 task 2 的目标：

```text
From the main branch, create a new branch named demo-1000 and check it out.
```

它同时拿到 task 1 的 handoff：repo 已 clone，并且当前 branch 是 main。agent 仍然先验证当前 workspace 状态。

### 7.1 agent 第一次决策：git_status 确认当前 branch

tool call：

```json
{
  "id": "toolcall_c841334f27cf43f485b604404165339a",
  "tool_name": "git_status",
  "input": {},
  "task_attempt_id": "taskattempt_c47f8cdd9ebe478390b173e7a3da8140",
  "latest_execution_id": "sbxexec_6fd3128a67c04b03911040d1eb4d5f23"
}
```

事件：

```text
seq=39  time=11:32:11.867  event=sdk.message
seq=40  time=11:32:11.887  event=sdk.message
seq=41  time=11:32:11.887  event=tool.call.requested
seq=42  time=11:32:11.905  event=tool.started
```

这是 per-tool pod。manager 创建：

```text
pod_name=sandbox-tool-6fd3128a67c04b039110
```

这次 pod 通过 `SANDBOX_TOOL_REQUEST_B64` 收到的 request 逻辑上是：

```json
{
  "run_id": "run_af542a86a0794f6bb5acb305f764efdd",
  "tool_call_id": "toolcall_c841334f27cf43f485b604404165339a",
  "tool_name": "git_status",
  "args": {},
  "task_attempt_id": "taskattempt_c47f8cdd9ebe478390b173e7a3da8140",
  "workspace_path": "/sandboxes/users/Luca/run_af542a86a0794f6bb5acb305f764efdd"
}
```

pod 内执行：

```text
git status --short --branch
```

返回并入库：

```json
{
  "execution_id": "sbxexec_6fd3128a67c04b03911040d1eb4d5f23",
  "runtime": {
    "type": "sandbox_pod",
    "pod_name": "sandbox-tool-6fd3128a67c04b039110",
    "pod_phase": "Succeeded",
    "exit_code": 0
  },
  "tool_result": {
    "ok": true,
    "data": {
      "command": ["git", "status", "--short", "--branch"],
      "stdout": "## main...origin/main\n",
      "returncode": 0
    }
  }
}
```

事件：

```text
seq=43  time=11:32:14.982  event=tool.execution
seq=44  time=11:32:14.998  event=sdk.message
```

agent 收到 `## main...origin/main`，判断可以从 main 创建分支。

### 7.2 agent 第二次决策：create_git_branch

tool call：

```json
{
  "id": "toolcall_5531a0eb4b564265a7711b4c151fee61",
  "tool_name": "create_git_branch",
  "input": {
    "branch_name": "demo-1000"
  },
  "task_attempt_id": "taskattempt_c47f8cdd9ebe478390b173e7a3da8140",
  "latest_execution_id": "sbxexec_3045711a85c349898826d5177670ca40",
  "created_at": "2026-05-25T11:32:18.592662Z",
  "ended_at": "2026-05-25T11:32:22.187272Z"
}
```

事件：

```text
seq=45  time=11:32:18.593  event=sdk.message
seq=46  time=11:32:18.612  event=tool.call.requested
seq=47  time=11:32:18.613  event=tool.started
```

进入 per-tool pod。manager 创建：

```text
pod_name=sandbox-tool-3045711a85c349898826
```

manager 将 request base64 后注入 pod 环境变量。逻辑 request：

```json
{
  "run_id": "run_af542a86a0794f6bb5acb305f764efdd",
  "tool_call_id": "toolcall_5531a0eb4b564265a7711b4c151fee61",
  "tool_name": "create_git_branch",
  "args": {
    "branch_name": "demo-1000"
  },
  "task_attempt_id": "taskattempt_c47f8cdd9ebe478390b173e7a3da8140",
  "workspace_path": "/sandboxes/users/Luca/run_af542a86a0794f6bb5acb305f764efdd"
}
```

pod 内 runtime 解码 request 后，dispatch 到 git branch 工具，在挂载的 workspace 中执行：

```text
git checkout -b demo-1000
```

pod stdout 输出 envelope JSON。manager 读取 pod logs，解析出 tool result，再补充 Kubernetes 层 runtime 证据。

真实入库 envelope 核心：

```json
{
  "execution_id": "sbxexec_3045711a85c349898826d5177670ca40",
  "tool_call_id": "toolcall_5531a0eb4b564265a7711b4c151fee61",
  "tool_name": "create_git_branch",
  "execution_status": "succeeded",
  "runtime": {
    "type": "sandbox_pod",
    "pod_name": "sandbox-tool-3045711a85c349898826",
    "pod_phase": "Succeeded",
    "exit_code": 0,
    "duration_ms": 3538
  },
  "tool_result": {
    "ok": true,
    "summary": "Git branch created.",
    "data": {
      "command": ["git", "checkout", "-b", "demo-1000"],
      "stderr": "Switched to a new branch 'demo-1000'\n",
      "returncode": 0
    }
  }
}
```

事件：

```text
seq=48  time=11:32:22.197  event=tool.execution
seq=49  time=11:32:22.212  event=sdk.message
```

这里的 `seq=49` 很关键：`create_git_branch` 成功并不是 task 直接完成，而是 result 回到 agent。agent 基于 `Git branch created.` 决定再查一次状态。

### 7.3 agent 第三次决策：git_status 验证 demo-1000

tool call：

```json
{
  "id": "toolcall_ef8bc65a753844c094aa004c4d92258b",
  "tool_name": "git_status",
  "input": {},
  "task_attempt_id": "taskattempt_c47f8cdd9ebe478390b173e7a3da8140",
  "latest_execution_id": "sbxexec_e642beebf7014992a807013bb9939744"
}
```

事件：

```text
seq=50  time=11:32:24.513  event=sdk.message
seq=51  time=11:32:24.533  event=tool.started
seq=52  time=11:32:24.533  event=tool.call.requested
```

manager 创建 per-tool pod：

```text
pod_name=sandbox-tool-e642beebf7014992a807
```

pod 执行：

```text
git status --short --branch
```

真实结果：

```json
{
  "execution_id": "sbxexec_e642beebf7014992a807013bb9939744",
  "runtime": {
    "type": "sandbox_pod",
    "pod_name": "sandbox-tool-e642beebf7014992a807",
    "pod_phase": "Succeeded",
    "exit_code": 0
  },
  "tool_result": {
    "ok": true,
    "data": {
      "command": ["git", "status", "--short", "--branch"],
      "stdout": "## demo-1000\n",
      "returncode": 0
    }
  }
}
```

事件：

```text
seq=53  time=11:32:27.637  event=tool.execution
seq=54  time=11:32:27.652  event=sdk.message
```

agent 收到 `## demo-1000`，判断 task 2 的 acceptance criteria 均满足，然后输出 structured result：

```text
seq=55  time=11:32:29.611  event=sdk.message
seq=56  time=11:32:29.624  event=agent.message
seq=57  time=11:32:34.776  event=sdk.message
seq=58  time=11:32:34.789  event=tool.started       StructuredOutput
seq=59  time=11:32:34.825  event=sdk.message
seq=60  time=11:32:36.964  event=sdk.message
seq=61  time=11:32:36.980  event=agent.result
seq=62  time=11:32:37.281  event=task.completed
seq=63  time=11:32:37.304  event=task.handoff
```

Task 2 结果：

```text
Created a new branch named demo-1000 from main and checked it out.
Verified via git_status that the active branch is now demo-1000.
No files were changed and no tests are required for this task.
```

## 8. Task 3 完整流程：Push demo-1000 branch to GitHub

Task 3 启动：

```text
task_id: task_ad5c1d0589e342f6b9e614d5de7b86e8
attempt_id: taskattempt_1b60ce9adf104987a555b99729c6be7a
claude_session_id: c63c23a4-940a-4ece-b3e9-a66a5bc5b23e

seq=64  time=11:32:37.337  event=task.started
seq=66  time=11:32:37.737  event=agent.session.started
```

agent 收到 task 3 目标：

```text
Push the newly created demo-1000 branch to the origin remote on GitHub so it is available in the lucarunai/demo repository.
```

它同时拿到 task 2 handoff：当前 branch 是 `demo-1000`。但 agent 仍然先验证当前状态。

### 8.1 agent 第一次决策：git_status 确认 demo-1000

tool call：

```json
{
  "id": "toolcall_e86fcc768e3342b49d951a225bf5dd36",
  "tool_name": "git_status",
  "input": {},
  "task_attempt_id": "taskattempt_1b60ce9adf104987a555b99729c6be7a",
  "latest_execution_id": "sbxexec_bd7ee766171e4b66a1ed8463e7199626"
}
```

事件：

```text
seq=67  time=11:32:40.165  event=sdk.message
seq=68  time=11:32:40.193  event=sdk.message
seq=69  time=11:32:40.210  event=tool.started
seq=70  time=11:32:40.210  event=tool.call.requested
```

manager 创建 per-tool pod：

```text
pod_name=sandbox-tool-bd7ee766171e4b66a1ed
```

pod 内执行：

```text
git status --short --branch
```

真实结果：

```json
{
  "execution_id": "sbxexec_bd7ee766171e4b66a1ed8463e7199626",
  "runtime": {
    "type": "sandbox_pod",
    "pod_name": "sandbox-tool-bd7ee766171e4b66a1ed",
    "pod_phase": "Succeeded",
    "exit_code": 0
  },
  "tool_result": {
    "ok": true,
    "data": {
      "command": ["git", "status", "--short", "--branch"],
      "stdout": "## demo-1000\n",
      "returncode": 0
    }
  }
}
```

事件：

```text
seq=71  time=11:32:43.289  event=tool.execution
seq=72  time=11:32:43.307  event=sdk.message
```

agent 收到 `## demo-1000` 后，判断可以 push 当前分支。

### 8.2 agent 第二次决策：push_current_git_branch，触发 approval

tool call：

```json
{
  "id": "toolcall_f895165bd5814530b68b006c4aa2800d",
  "tool_name": "push_current_git_branch",
  "input": {},
  "task_attempt_id": "taskattempt_1b60ce9adf104987a555b99729c6be7a",
  "latest_execution_id": "sbxexec_3ee14cfc1e454c6ab26db18d4975a038",
  "created_at": "2026-05-25T11:32:45.714603Z",
  "ended_at": "2026-05-25T11:37:18.957416Z"
}
```

事件：

```text
seq=73  time=11:32:45.715  event=sdk.message
seq=74  time=11:32:45.736  event=tool.started
seq=75  time=11:32:45.737  event=tool.call.requested
```

这次不会进入 sandbox pod。原因有两个：

```text
1. push_current_git_branch 需要 GitHub credential
2. push_current_git_branch 会修改远端仓库，是高风险副作用操作
```

因此 tool wrapper 先根据 policy 创建 approval request：

```json
{
  "id": "approval_289cc073337a434daf4233974a2c4e99",
  "run_id": "run_af542a86a0794f6bb5acb305f764efdd",
  "session_id": "sess_13d480a0d4b048da876dbc259e530e47",
  "user_id": "Luca",
  "task_id": "task_ad5c1d0589e342f6b9e614d5de7b86e8",
  "task_attempt_id": "taskattempt_1b60ce9adf104987a555b99729c6be7a",
  "tool_call_id": "toolcall_f895165bd5814530b68b006c4aa2800d",
  "tool_name": "push_current_git_branch",
  "tool_input": {},
  "requested_by": "agent",
  "status": "approved",
  "reason": "Push publishes local branch state to the remote GitHub repository.",
  "created_at": "2026-05-25T11:32:45.753177Z",
  "decided_at": "2026-05-25T11:37:15.215323Z",
  "decided_by": "web-ui",
  "decision_reason": null
}
```

事件：

```text
seq=76  time=11:32:45.769  event=approval.requested
seq=77  time=11:37:15.231  event=approval.approved
```

真实等待时间约 269 秒。审批通过之前，push 不会真正执行。

审批通过后进入 trusted GitHub executor。执行命令：

```json
{
  "command": [
    "git",
    "-c",
    "[redacted-github-auth-header]",
    "push",
    "-u",
    "origin",
    "demo-1000"
  ],
  "stdout": "branch 'demo-1000' set up to track 'origin/demo-1000'.\n",
  "stderr": "* [new branch]      demo-1000 -> demo-1000\n",
  "returncode": 0
}
```

入库 envelope：

```json
{
  "execution_id": "sbxexec_3ee14cfc1e454c6ab26db18d4975a038",
  "tool_call_id": "toolcall_f895165bd5814530b68b006c4aa2800d",
  "tool_name": "push_current_git_branch",
  "execution_status": "succeeded",
  "runtime": {
    "type": "trusted_github_executor",
    "pod_name": null,
    "pod_phase": null,
    "exit_code": null,
    "duration_ms": 3535
  },
  "tool_result": {
    "ok": true,
    "data": {
      "command": [
        "git",
        "-c",
        "[redacted-github-auth-header]",
        "push",
        "-u",
        "origin",
        "demo-1000"
      ],
      "stdout": "branch 'demo-1000' set up to track 'origin/demo-1000'.\n",
      "stderr": "* [new branch]      demo-1000 -> demo-1000\n",
      "returncode": 0
    }
  }
}
```

事件：

```text
seq=78  time=11:37:18.967  event=tool.execution
seq=79  time=11:37:18.985  event=sdk.message
```

`seq=79` 表示 push result 已经回到 agent。agent 读到 push 成功后，继续决定：再查一次 git status 做最终确认。

### 8.3 agent 第三次决策：git_status 最终验证

tool call：

```json
{
  "id": "toolcall_bc20bb0554aa4c519fea29a880a76d0c",
  "tool_name": "git_status",
  "input": {},
  "task_attempt_id": "taskattempt_1b60ce9adf104987a555b99729c6be7a",
  "latest_execution_id": "sbxexec_ce26051a97134f3898b80f1144cf50c0",
  "created_at": "2026-05-25T11:37:20.843669Z",
  "ended_at": "2026-05-25T11:37:24.463835Z"
}
```

事件：

```text
seq=80  time=11:37:20.842  event=sdk.message
seq=81  time=11:37:20.878  event=tool.call.requested
seq=82  time=11:37:20.882  event=tool.started
```

这是最后一次 per-tool sandbox pod。manager 创建：

```text
pod_name=sandbox-tool-ce26051a97134f3898b8
```

pod 通过 `SANDBOX_TOOL_REQUEST_B64` 收到本次 `git_status` request，通过 workspace mount 看到同一个 run workspace，执行：

```text
git status --short --branch
```

真实结果：

```json
{
  "execution_id": "sbxexec_ce26051a97134f3898b80f1144cf50c0",
  "runtime": {
    "type": "sandbox_pod",
    "pod_name": "sandbox-tool-ce26051a97134f3898b8",
    "pod_phase": "Succeeded",
    "exit_code": 0
  },
  "tool_result": {
    "ok": true,
    "data": {
      "command": ["git", "status", "--short", "--branch"],
      "stdout": "## demo-1000\n",
      "returncode": 0
    }
  }
}
```

事件：

```text
seq=83  time=11:37:24.478  event=tool.execution
seq=84  time=11:37:24.493  event=sdk.message
```

agent 收到最后一次 `## demo-1000` 后，输出最终 task result：

```text
seq=85  time=11:37:31.815  event=sdk.message
seq=86  time=11:37:31.827  event=tool.started       StructuredOutput
seq=87  time=11:37:31.861  event=sdk.message
seq=88  time=11:37:34.235  event=sdk.message
seq=89  time=11:37:34.247  event=agent.message
seq=90  time=11:37:34.280  event=sdk.message
seq=91  time=11:37:34.293  event=agent.result
seq=92  time=11:37:34.828  event=task.completed
seq=93  time=11:37:34.855  event=task.handoff
```

Task 3 结果：

```text
Pushed the demo-1000 branch to the origin remote on GitHub.
Confirmed via git_status that the active branch remains demo-1000 and the push tool reported success.
No files were changed and no tests are required for this task.
```

## 9. Run 完成与 workspace 清理

三个 task 都 completed 后，orchestrator 将 run 标记为 completed：

```text
seq=94  time=11:37:34.877  event=run.completed
seq=95  time=11:37:34.905  event=workspace.cleaned
```

最终 run 状态：

```text
status=completed
ended_at=2026-05-25T11:37:34.867260Z
metadata.workspace_cleaned=true
```

## 10. 真实 tool call / execution / pod 对照

完整 8 次 tool call：

```text
1. clone_github_repository
   tool_call_id: toolcall_446c4b0146414ffa9b5101c6a5278568
   execution_id: sbxexec_55b9191b368c4a98b5eae9ffc1f674b4
   runtime: trusted_github_executor
   pod: null

2. git_status
   tool_call_id: toolcall_88118fc24eef4c6fa6b7fadf645626ec
   execution_id: sbxexec_7cd35c8cc8de4626bedc47803595ea9c
   runtime: sandbox_pod
   pod: sandbox-tool-7cd35c8cc8de4626bedc
   stdout: ## main...origin/main

3. git_status
   tool_call_id: toolcall_c841334f27cf43f485b604404165339a
   execution_id: sbxexec_6fd3128a67c04b03911040d1eb4d5f23
   runtime: sandbox_pod
   pod: sandbox-tool-6fd3128a67c04b039110
   stdout: ## main...origin/main

4. create_git_branch
   tool_call_id: toolcall_5531a0eb4b564265a7711b4c151fee61
   execution_id: sbxexec_3045711a85c349898826d5177670ca40
   runtime: sandbox_pod
   pod: sandbox-tool-3045711a85c349898826
   command: git checkout -b demo-1000

5. git_status
   tool_call_id: toolcall_ef8bc65a753844c094aa004c4d92258b
   execution_id: sbxexec_e642beebf7014992a807013bb9939744
   runtime: sandbox_pod
   pod: sandbox-tool-e642beebf7014992a807
   stdout: ## demo-1000

6. git_status
   tool_call_id: toolcall_e86fcc768e3342b49d951a225bf5dd36
   execution_id: sbxexec_bd7ee766171e4b66a1ed8463e7199626
   runtime: sandbox_pod
   pod: sandbox-tool-bd7ee766171e4b66a1ed
   stdout: ## demo-1000

7. push_current_git_branch
   tool_call_id: toolcall_f895165bd5814530b68b006c4aa2800d
   approval_id: approval_289cc073337a434daf4233974a2c4e99
   execution_id: sbxexec_3ee14cfc1e454c6ab26db18d4975a038
   runtime: trusted_github_executor
   pod: null

8. git_status
   tool_call_id: toolcall_bc20bb0554aa4c519fea29a880a76d0c
   execution_id: sbxexec_ce26051a97134f3898b80f1144cf50c0
   runtime: sandbox_pod
   pod: sandbox-tool-ce26051a97134f3898b8
   stdout: ## demo-1000
```

真实 sandbox pod 数量：

```text
6
```

真实 trusted GitHub executor 次数：

```text
2
```

真实 approval 数量：

```text
1
```

## 11. 关键数据流总结

### 11.1 agent 与 tool wrapper

agent 输出的是 ToolUseBlock：

```json
{
  "tool_name": "git_status",
  "input": {}
}
```

tool wrapper 补上下文并落库：

```json
{
  "run_id": "run_af542a86a0794f6bb5acb305f764efdd",
  "task_id": "task_5956a8896e824d6d806840e76c7587fa",
  "task_attempt_id": "taskattempt_da82b5380a5b40b4b85455bbc71d9fa7",
  "tool_call_id": "toolcall_88118fc24eef4c6fa6b7fadf645626ec",
  "tool_name": "git_status",
  "input": {}
}
```

tool 执行完成后，tool wrapper 将 result 回传给 agent：

```json
{
  "ok": true,
  "summary": "Git status collected.",
  "data": {
    "stdout": "## main...origin/main\n"
  }
}
```

agent 基于这个 result 决定下一步，而不是由 orchestrator 直接硬编码下一步。

### 11.2 sandbox manager 与 pod

普通 sandbox tool 时，manager 传给 pod 的不是完整 agent 上下文，而是单次 `ToolExecutionRequest`：

```json
{
  "run_id": "run_af542a86a0794f6bb5acb305f764efdd",
  "tool_call_id": "toolcall_5531a0eb4b564265a7711b4c151fee61",
  "tool_name": "create_git_branch",
  "args": {
    "branch_name": "demo-1000"
  },
  "task_attempt_id": "taskattempt_c47f8cdd9ebe478390b173e7a3da8140",
  "workspace_path": "/sandboxes/users/Luca/run_af542a86a0794f6bb5acb305f764efdd"
}
```

传输方式：

```text
manager -> pod:
  SANDBOX_TOOL_REQUEST_B64=<base64 encoded ToolExecutionRequest>
  SANDBOX_WORKSPACE_PATH=/workspace/run_af542a86a0794f6bb5acb305f764efdd
  workspace PVC/subPath mount

pod -> manager:
  stdout log 中的一行 ToolExecutionEnvelope JSON
  Kubernetes pod_phase
  container exit_code
```

manager 解析后返回完整 envelope：

```json
{
  "execution_id": "sbxexec_3045711a85c349898826d5177670ca40",
  "tool_name": "create_git_branch",
  "execution_status": "succeeded",
  "tool_result": {
    "ok": true,
    "summary": "Git branch created.",
    "data": {
      "command": ["git", "checkout", "-b", "demo-1000"],
      "stderr": "Switched to a new branch 'demo-1000'\n",
      "returncode": 0
    }
  },
  "runtime": {
    "type": "sandbox_pod",
    "pod_name": "sandbox-tool-3045711a85c349898826",
    "pod_phase": "Succeeded",
    "exit_code": 0,
    "duration_ms": 3538
  }
}
```

这个 envelope 同时用于：

```text
1. 写 tool_executions.envelope
2. 更新 tool_calls.latest_execution_id/status
3. emit session_events.tool.execution
4. 转成 MCP tool result 回给 agent
```

## 12. Security 设计如何嵌入本 case

这条真实链路里的安全点如下。

用户边界：

```text
run/session/events/approval 都绑定 user_id=Luca。
```

工具白名单：

```text
agent 只能调用 MCP 暴露的工具，例如 git_status、create_git_branch、push_current_git_branch。
agent 不能任意执行 shell。
```

per-tool pod 隔离：

```text
普通 sandbox tool 每次创建一个独立 pod。
pod 只执行一次 ToolExecutionRequest。
pod 结束后 manager 收集结果并释放生命周期。
```

workspace 隔离：

```text
workspace_path=/sandboxes/users/Luca/run_af542a86a0794f6bb5acb305f764efdd
pod 内看到的是挂载后的 /workspace/run_af542a86a0794f6bb5acb305f764efdd。
```

path 防逃逸：

```text
workspace 文件类工具会校验路径必须留在 workspace 内。
```

输出限制：

```text
pod 输出通过 ToolExecutionEnvelope 结构化返回。
stdout/stderr 有截断和大小限制设计。
```

GitHub token 隔离：

```text
clone_github_repository 和 push_current_git_branch 走 trusted_github_executor。
普通 sandbox pod 不直接拿 GitHub token。
命令中的 auth header 被 redacted 为 [redacted-github-auth-header]。
```

human approval：

```text
push_current_git_branch 会修改远端仓库。
真实执行前创建 approval_289cc073337a434daf4233974a2c4e99。
web-ui 在 2026-05-25T11:37:15.215323Z 批准。
审批通过后才真正 push。
```

审计与 replay：

```text
session_events 共 95 条。
每一步 tool call、tool execution、approval、task result、run completed 都有事件。
ops replay 结果:
  replay_valid=true
  consistent=true
  hash_chain_valid=true
```

## 13. 完整一句话版

这条 `18082 / v2 / per-tool sandbox pod` 真实 case 的完整链路是：

```text
前端提交真实 prompt
  -> session 层创建 run_af542a86a0794f6bb5acb305f764efdd 并写 user.prompt.accepted
  -> brain worker claim run
  -> planner 拆成 3 个 task
  -> task 1 agent 调 clone_github_repository，trusted executor clone 成功后 result 回 agent，agent 再调 git_status，sandbox manager 创建 pod 验证 main，result 回 agent，task 1 completed
  -> task 2 agent 先调 git_status，sandbox pod 返回 main，result 回 agent，agent 调 create_git_branch，sandbox pod 执行 git checkout -b demo-1000，result 回 agent，agent 再调 git_status，sandbox pod 返回 demo-1000，task 2 completed
  -> task 3 agent 先调 git_status，sandbox pod 返回 demo-1000，result 回 agent，agent 调 push_current_git_branch，系统创建 approval，web-ui 批准后 trusted executor push，result 回 agent，agent 再调 git_status，sandbox pod 返回 demo-1000，task 3 completed
  -> orchestrator 标记 run.completed
  -> workspace.cleaned
```

本 case 最重要的实现事实：

```text
agent 和 tool 是 tool-call 级交互，不是 task 结束才交互。
sandbox manager 与 pod 的 request/result 数据交换发生在每一次普通 sandbox tool 内部。
普通 sandbox tool 通过 env/base64 request 进入 pod，通过 stdout JSON envelope 返回 manager。
clone/push 不走普通 pod，走 trusted GitHub executor。
push 有真实 human approval。
v2 这条链路没有 sandbox_session_id。
```
