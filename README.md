# Cloud Agent Platform PoC

This repository contains a V0 coding-agent platform PoC with four explicit
layers:

- Web layer: a prompt UI plus an SSE event timeline.
- Brain layer: Planner and Task Agents backed by Claude Agent SDK plus SDK MCP
  tool facades for coding workflows.
- Session layer: an explicit service owning Postgres-backed sessions, runs,
  tasks, durable platform events, task handoffs, tool executions, and agent
  transcript indexes.
- Sandbox layer: a manager service owning run workspaces and launching one-shot
  tool Pods for file, Git, Python test, push, and pull-request operations.

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
                 -> GitHubWorkflowService
     -> Session service events + transcript/handoff indexes
        -> Postgres
        -> Session artifact volume
```

`session_events` is the platform event ledger. Claude SDK output is normalized
into platform events before it reaches SSE, so the frontend does not depend on
SDK message shapes. The Web service streams events from the Session service; the
Brain service does not share in-memory state with Web.

`tool_executions` stores the Sandbox execution envelope for each Brain tool
call that returns from the Sandbox Manager. A matching `tool.execution` session
event puts the execution under its Task in the Web UI for early inspection.

Claude Code writes provider transcripts under `/root/.claude`. In Kubernetes
that path is mounted from the `session-artifacts` PVC on the Brain pod. The
Brain process is the physical writer, but the volume is treated as Session
Layer state and is indexed in Postgres through `agent_transcripts`.

Agent tools do not access a Brain workspace. Brain exposes controlled SDK MCP
tools, but file reads/writes/edits/searches, Git operations, Python unittest,
pushes, and pull-request creation flow through the Sandbox Manager. Each tool
execution is handed to a temporary Sandbox Pod that mounts the current run
workspace. Brain keeps the Claude harness and transcript mount; Sandbox owns the
mutable checkout.

## V0 Boundaries

- The repository, source branch, and PR target branch are configuration, not
  arbitrary browser inputs.
- The model does not receive Claude local file tools. The Brain exposes SDK MCP
  sandbox file tools for workspace inspection and editing.
- The Brain exposes SDK MCP coding tools that forward GitHub clone, Git branch
  creation, Python unittest, Git status/diff, commit, push, and pull-request
  creation into the Sandbox Manager as `run_id + tool_name + tool args`.
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
mcp__coding__create_git_branch
mcp__coding__git_status
mcp__coding__git_diff_stat
mcp__coding__run_python_unittest
mcp__coding__commit_git_changes
mcp__coding__push_current_git_branch
mcp__coding__create_github_pull_request
```

Those GitHub tools execute in one-shot Sandbox Pods. The Manager injects the
GitHub token only into Pods for tools that require GitHub credentials. The Task
Agent can request a push or pull request through a tool call, but Brain and the
Sandbox Manager do not hold GitHub credentials and the token is not written to
the checkout.

## Configuration

Copy the values below into your environment before starting the services:

```bash
export DATABASE_URL=postgresql://postgres:postgres@localhost:5432/ecsma_poc
export SESSION_LAYER_URL=http://localhost:8002
export SANDBOX_LAYER_URL=http://localhost:8003
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

`GITHUB_TOKEN` is used by Sandbox GitHub tools for authenticated clones, Git
pushes, and PR creation. It is not passed to Claude Agent SDK prompts. Local
development uses `SANDBOX_EXECUTION_MODE=direct`; Kubernetes sets it to
`kubernetes` so the Manager creates one Pod per tool execution.

## Run Locally

Start Postgres:

```bash
docker compose up -d postgres
```

Install dependencies:

```bash
uv sync --extra dev
```

Run Session, Sandbox, Web, and Brain in separate terminals:

```bash
uv run uvicorn cloud_agent_poc.session_app:app --reload --port 8002
uv run uvicorn cloud_agent_poc.sandbox_app:app --reload --port 8003
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
cloud-agent-brain
postgres
```

Only `cloud-agent-brain` mounts the `session-artifacts` PVC at `/root/.claude`.
That lets Claude Agent SDK produce transcript JSONL files in a durable artifact
space while Session Layer remains the logical owner through DB metadata.
`cloud-agent-sandbox` mounts the `sandbox-workspaces` PVC at `/sandboxes` to
create run workspace directories. Each one-shot Sandbox tool Pod mounts only its
run directory at `/workspace`, so agent checkout and tool side effects stay out
of Brain and out of the Manager process.

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

- Restore provider transcripts into a fresh Brain pod before cross-pod resume.
- Move transcript artifact storage from PVC to object storage when leaving PoC.
- Replace Postgres polling with a durable queue or notification channel.
- Harden the Sandbox Pod boundary with resource quotas, network policy, and
  stronger admission controls before accepting arbitrary users.
- Add checkpoints, artifacts, and structured verification evidence for
  long-running work.
