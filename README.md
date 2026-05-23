# Cloud Agent Platform PoC

This repository contains a V0 coding-agent platform PoC with four explicit
layers:

- Web layer: a prompt UI plus an SSE event timeline.
- Brain layer: Planner and Task Agents backed by Claude Agent SDK plus SDK MCP
  tool facades for coding workflows.
- Session layer: an explicit service owning Postgres-backed sessions, runs,
  tasks, task attempts, tool calls/executions, durable platform events, task
  handoffs, and agent transcript indexes.
- Sandbox layer: a manager service owning run workspaces and launching one-shot
  tool Pods for untrusted workspace file, Git, and Python test operations.

The trusted GitHub Broker is a credential boundary beside those layers. It owns
authenticated GitHub operations without exposing long-lived tokens to Sandbox
tool Pods.

The first supported business flow is intentionally narrow:

1. Accept a prompt from the browser.
2. Allocate an empty run workspace.
3. Ask a Planner Agent to split the prompt into ordered tasks.
4. Ask Task Agents to complete those tasks one at a time with sandbox-backed
   SDK MCP coding tools.

## Architecture

```text
Browser
  -> Web service
     -> Session service queued run + session events
  -> Brain service claims queued runs from Session service
     -> Brain Orchestrator
        -> AgentTaskPlanner through Claude Agent SDK
        -> Claude Agent SDK adapter + MCP tool facade
           -> Sandbox Manager
              -> one-shot Sandbox tool Pod
                 -> mounted run workspace
           -> GitHub Broker
              -> mounted run workspace
              -> GitHubWorkflowService
     -> Session service events + transcript/handoff indexes
        -> Postgres
        -> Session artifact volume
```

`session_events` is the platform event ledger. Claude SDK output is normalized
into platform events before it reaches SSE, so the frontend does not depend on
SDK message shapes. The Web service streams events from the Session service; the
Brain service does not share in-memory state with Web.

`task_attempts` stores each Task Agent query attempt and captures the fresh
Claude session id as soon as the SDK init message arrives for transcript
indexing. Brain does not use that provider session id to resume the next Agent
SDK query. Every task query starts a new model session and receives durable
`task_handoffs.payload` JSON from earlier task queries instead. The run row
stores a global `acceptance_criteria` JSON document after planning; this is the
run-level constitution that every fresh Task Agent query receives. Each task row
also keeps its own local `acceptance_criteria`, which bounds the current query.
The handoff payload is `task_handoff.v1`: it stores the run-level acceptance
criteria, a `planned_task_results` snapshot with every task's description,
criteria, status, and summary, the latest completed task, per-criterion
verification status, and sparse verification evidence such as commands run,
files changed, or published artifacts. Brain sends the global criteria, latest
run progress snapshot, and prior task summaries into the next fresh query.
`tool_calls` stores Agent-requested tool inputs, while `tool_executions` stores
runtime envelopes that return from Sandbox or the trusted broker. Session events
put tool calls, Sandbox runtime failures, and task state under the Task list and
SSE timeline.

When a tool runtime failure blocks or fails a run, Web calls
`POST /api/runs/{run_id}/wake`. Wake re-queues the run; Brain reloads existing
tasks, durable task handoff JSON, tool failure context, and the durable
workspace before starting a new Task Agent query. V1 leaves retry decisions to
the new Agent query instead of replaying side-effecting tools automatically.

Claude Code writes provider transcripts under `/root/.claude`. In Kubernetes
that path is mounted from the `session-artifacts` PVC on the Brain pod. The
Brain process is the physical writer, but the volume is treated as Session
Layer state and is indexed in Postgres through `agent_transcripts`.
Transcript paths and artifact ids are audit/debug metadata; they are not used as
handoff context for the next Agent query.

Agent tools do not access a Brain workspace. Brain exposes controlled SDK MCP
tools; untrusted file reads/writes/edits/searches, local Git operations, and
Python unittest calls flow through the Sandbox Manager. Authenticated GitHub
clone, checkout, push, and pull-request calls flow through the trusted GitHub
Broker. Sandbox Pods and the broker mount the current run workspace through
their own execution boundary. Brain keeps the Claude harness and transcript
mount; Session Layer indexes durable state.

## V0 Boundaries

- The repository, source branch, and PR target branch are configuration, not
  arbitrary browser inputs.
- The model does not receive Claude local file tools. The Brain exposes SDK MCP
  sandbox file tools for workspace inspection and editing.
- The Brain exposes SDK MCP coding tools that forward GitHub clone, existing
  branch checkout, Git branch creation, Python unittest, Git status/diff,
  commit, push, and pull-request creation into the Sandbox Manager as
  `run_id + tool_name + tool args`.
- Tests, commits, pushes, and PR creation are dynamic Agent tasks, not fixed
  platform workflow steps.
- This PoC launches one-shot Sandbox tool Pods but is not a hardened isolation
  system.
  Do not expose it to untrusted prompts or users until the execution boundary,
  network policy, resource quotas, and credential isolation are hardened.

## SDK Tools

Task Agents receive sandbox-backed SDK MCP file tools:

```text
mcp__coding__read_workspace_file
mcp__coding__write_workspace_file
mcp__coding__edit_workspace_file
mcp__coding__glob_workspace_files
mcp__coding__grep_workspace_files
```

The Brain also registers an in-process SDK MCP server named `coding`:

```text
mcp__coding__clone_github_repository
mcp__coding__checkout_git_branch
mcp__coding__create_git_branch
mcp__coding__git_status
mcp__coding__git_diff_stat
mcp__coding__run_python_unittest
mcp__coding__commit_git_changes
mcp__coding__push_current_git_branch
mcp__coding__create_github_pull_request
```

Those SDK tool names stay unified for the Task Agent. Brain routes credentialed
GitHub operations to the GitHub Broker and routes untrusted workspace operations
to one-shot Sandbox Pods. The Task Agent can request a push or pull request
through a tool call, but Sandbox tool Pods do not receive GitHub credentials and
the token is not written to the checkout.

## Configuration

Copy the values below into your environment before starting the services:

```bash
export DATABASE_URL=postgresql://postgres:postgres@localhost:5432/ecsma_poc
export SESSION_LAYER_URL=http://localhost:8002
export SANDBOX_LAYER_URL=http://localhost:8003
export GITHUB_BROKER_URL=http://localhost:8004
export SANDBOX_EXECUTION_MODE=direct
export GITHUB_TOKEN=github-token-with-pr-permission
export ANTHROPIC_API_KEY=anthropic-api-key
export GITHUB_REPO_URL=https://github.com/lucarunai/demo.git
export GITHUB_SOURCE_BRANCH=test
export GITHUB_TARGET_BRANCH=main
```

Optional settings:

```bash
export WORKSPACE_ROOT=/private/tmp/ecsma-poc-workspaces
export CLAUDE_CONFIG_DIR="$HOME/.claude"
export CLAUDE_MODEL=claude-sonnet-4-5
export GIT_AUTHOR_NAME="Cloud Agent PoC"
export GIT_AUTHOR_EMAIL=cloud-agent-poc@example.local
export SANDBOX_RUNTIME_IMAGE=cloud-agent-poc:local
export SANDBOX_TOOL_TIMEOUT_SECONDS=180
```

`GITHUB_TOKEN` is used only by the trusted GitHub Broker for authenticated
clones, fetches, pushes, and PR creation. It is not passed to Claude Agent SDK
prompts or Sandbox tool Pods. Local development uses
`SANDBOX_EXECUTION_MODE=direct`; Kubernetes sets it to `kubernetes` so the
Manager creates one Pod per sandbox execution.

## Run Locally

Start Postgres:

```bash
docker compose up -d postgres
```

Install dependencies:

```bash
uv sync --extra dev
```

Run Session, Sandbox, GitHub Broker, Web, and Brain in separate terminals:

```bash
uv run uvicorn cloud_agent_poc.session_app:app --reload --port 8002
uv run uvicorn cloud_agent_poc.sandbox_app:app --reload --port 8003
uv run uvicorn cloud_agent_poc.github_broker_app:app --reload --port 8004
uv run uvicorn cloud_agent_poc.web:app --reload --port 8000
uv run uvicorn cloud_agent_poc.brain_app:app --reload --port 8001
```

Open the Web URL. Session service creates the idempotent Postgres schema at
startup.

## Kubernetes

The `k8s` directory targets the active local Kubernetes cluster. Use a local
port-forward for the Web UI so the browser entrypoint is stable across local
Kubernetes load-balancer behavior. The included Dockerfile uses the locally
cached `public.ecr.aws/docker/library/python:3.12-alpine` base image and
installs Node.js plus Claude Code CLI because Claude Agent SDK invokes that
runtime from the Brain process.

Build the shared service image first:

```bash
docker build -t cloud-agent-poc:local .
```

Create the namespace, runtime secrets, and workloads:

```bash
kubectl apply -f k8s/namespace.yaml
kubectl -n cloud-agent-poc create secret generic cloud-agent-poc-secrets \
  --from-literal=GITHUB_TOKEN="$GITHUB_TOKEN" \
  --from-literal=ANTHROPIC_API_KEY="$ANTHROPIC_API_KEY" \
  --dry-run=client -o yaml | kubectl apply -f -
kubectl apply -f k8s/
```

Kubernetes creates these deployments:

```text
cloud-agent-web
cloud-agent-session
cloud-agent-sandbox
cloud-agent-github-broker
cloud-agent-brain
postgres
```

Only `cloud-agent-brain` mounts the `session-artifacts` PVC at `/root/.claude`.
That lets Claude Agent SDK produce transcript JSONL files in a durable artifact
space while Session Layer remains the logical owner through DB metadata.
`cloud-agent-sandbox` mounts the `sandbox-workspaces` PVC at `/sandboxes` to
create run workspace directories. Each one-shot Sandbox tool Pod mounts only its
run directory at `/workspace`, so untrusted file and test side effects stay out
of Brain and out of the Manager process.

`cloud-agent-github-broker` is the only workload that receives `GITHUB_TOKEN`.
Brain still exposes one MCP tool schema to the Agent, but it routes trusted
GitHub operations (`clone`, authenticated `checkout`, `push`, and PR creation)
to the broker. Sandbox tool Pods never receive GitHub credentials.

Start the Web UI port-forward after deployment:

```bash
./scripts/port-forward-web.sh
```

Keep that command running, then open `http://localhost:18080`.

## API

```text
POST /api/sessions
POST /api/sessions/{session_id}/runs
GET  /api/sessions/{session_id}/events?after_event_id=0
GET  /api/runs/{run_id}
```

## Verification

The repository unit tests are standard-library tests:

```bash
PYTHONPATH=src python3 -m unittest discover -v
```

## Next Iterations

- Keep provider transcripts as audit artifacts while task resume stays based on
  durable handoff JSON.
- Move transcript artifact storage from PVC to object storage when leaving PoC.
- Replace Postgres polling with a durable queue or notification channel.
- Harden the Sandbox Pod boundary with resource quotas, network policy, and
  stronger admission controls before accepting arbitrary users.
- Add a stronger platform-side verifier for Agent-reported criteria evidence.
