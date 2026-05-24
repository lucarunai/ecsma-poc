# Testing Strategy

The test suite protects the platform invariants that matter for a long-running
agent harness: durable state is the source of truth, every fresh Agent query gets
the full run contract, handoff JSON contains progress and verification only, and
workspace tools execute inside the Sandbox or trusted GitHub Broker boundary.

## How To Run

```bash
PYTHONPATH=src python3 -m unittest discover -v
```

The default suite uses local fakes and does not call Claude, Postgres,
Kubernetes, or GitHub.

For the Session/Security upgrade testing matrix and v2 runtime verification
plan, see
[`upgrades/session-security-testing-strategy.zh.md`](../upgrades/session-security-testing-strategy.zh.md).

## Test Layers

| Layer | Test files | What is covered |
| --- | --- | --- |
| Planner contract | `tests/test_planner.py` | Prompt-to-task planning schema, requested-vs-optional task filtering, and task acceptance criteria shape. |
| Agent harness contract | `tests/test_claude_agent.py` | Task result schema, `passed` criteria status, legacy `passing` normalization, completed-task validation, and prompt assembly for fresh Agent queries. |
| Orchestrator and handoff | `tests/test_orchestrator.py` | Run-level acceptance criteria persistence, criteria passed into every task query, handoff payload shape, resume behavior, failed tool recovery context, and workspace cleanup behavior. |
| SDK tool router | `tests/test_sdk_tool_execution.py` | Tool-call persistence before execution, Sandbox-vs-Broker routing, runtime envelope recording, tool errors, request failures, and task events. |
| Sandbox runtime and pod manifest | `tests/test_sandbox_runtime.py` | File tool behavior, path escape protection, secretless pod env, disabled service account token, encoded runtime request, and crashed-pod envelopes. |
| Static UI contract | `tests/test_static_ui_contract.py` | Run panel displays only the raw run acceptance criteria JSON and avoids derived task-progress badges in that panel. |
| GitHub workflow tools | `tests/test_github_workflow.py` | GitHub Broker workflow behavior with controlled local fakes. |

## Critical Invariants

- `runs.acceptance_criteria` is the run constitution. Brain stores it after
  planning and sends it into every fresh Task Agent query.
- `tasks.acceptance_criteria` remains task-local. It bounds the current task's
  `criteria_results`.
- `task_handoffs.payload` does not duplicate run acceptance criteria. It stores
  progress snapshots, latest-task verification, and summaries for the next
  query.
- `planned_task_results.status` uses `passed`, `pending`, `blocked`, or
  `failed`; legacy `passing` values are normalized when new handoffs are built.
- A task cannot report `completed` unless every task-local criterion is
  `passed`.
- Failed tool calls are persisted as `tool_calls` plus `tool_executions` when an
  execution envelope exists. On resume, Brain converts failed calls for the
  current task into explicit recovery context for the next Agent query.
- Workspace file, Git, and unittest tools run through the Sandbox boundary.
  Trusted GitHub operations run through the GitHub Broker.
- Sandbox tool pods never receive `GITHUB_TOKEN` or `ANTHROPIC_API_KEY`, and
  service account token mounting is disabled for the one-shot pods.

## What Is Intentionally Faked

The unit suite deliberately avoids live external dependencies:

- Claude Agent SDK calls are represented by fake agents and parser tests.
- Postgres is represented by store fakes that record method calls and payloads.
- Kubernetes is represented by manifest inspection and fake pod-client behavior.
- GitHub is represented by broker fakes and local workflow tests.

Live end-to-end checks should be run separately after deployment because they
exercise credentials, network policy, cluster scheduling, and repository
side effects.

## V2 Runtime Verification

After deploying `k8s-v2` and exposing Web on `http://127.0.0.1:18082`, run:

```bash
./scripts/k8s-smoke-v2.sh
./scripts/k8s-security-verify-v2.sh
```

Use the chaos drills against an existing run when live Agent API quota is
available:

```bash
RUN_ID=run_xxx ./scripts/k8s-chaos-drill-v2.sh brain-crash
RUN_ID=run_xxx ./scripts/k8s-chaos-drill-v2.sh sandbox-pod-crash
RUN_ID=run_xxx ./scripts/k8s-chaos-drill-v2.sh ownership
```
