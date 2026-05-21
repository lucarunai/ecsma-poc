# Cloud Agent Platform PoC

This repository contains a V0 coding-agent platform PoC with three explicit
layers:

- Web layer: a prompt UI plus an SSE event timeline.
- Brain layer: Planner and Task Agents backed by Claude Agent SDK plus SDK MCP
  tools for coding workflows.
- Session layer: an explicit service owning Postgres-backed sessions, runs,
  tasks, durable platform events, task handoffs, and agent transcript indexes.

The first supported business flow is intentionally narrow:

1. Accept a prompt from the browser.
2. Allocate an empty run workspace.
3. Ask a Planner Agent to split the prompt into ordered tasks.
4. Ask Task Agents to complete those tasks one at a time with SDK file tools
   and SDK MCP coding tools.

## Architecture

```text
Browser
  -> Web service
     -> Session service queued run + session events
  -> Brain service claims queued runs from Session service
     -> Brain Orchestrator
        -> AgentTaskPlanner through Claude Agent SDK
        -> Claude Agent SDK adapter
        -> GitHubWorkflowService
     -> Session service events + transcript/handoff indexes
        -> Postgres
        -> Session artifact volume
```

`session_events` is the platform event ledger. Claude SDK output is normalized
into platform events before it reaches SSE, so the frontend does not depend on
SDK message shapes. The Web service streams events from the Session service; the
Brain service does not share in-memory state with Web.

Claude Code writes provider transcripts under `/root/.claude`. In Kubernetes
that path is mounted from the `session-artifacts` PVC on the Brain pod. The
Brain process is the physical writer, but the volume is treated as Session
Layer state and is indexed in Postgres through `agent_transcripts`.

## V0 Boundaries

- The repository, source branch, and PR target branch are configuration, not
  arbitrary browser inputs.
- The model receives SDK file tools for code inspection and editing:
  `Read`, `Write`, `Edit`, `Glob`, and `Grep`.
- The Brain exposes SDK MCP coding tools for GitHub clone, Git branch creation,
  Python unittest, Git status/diff, commit, push, and pull-request creation.
- Tests, commits, pushes, and PR creation are dynamic Agent tasks, not fixed
  platform workflow steps.
- This PoC does not include sandbox isolation. Do not expose it to untrusted
  prompts or users until the execution boundary and credential isolation are
  redesigned.

## SDK Tools

Task Agents receive Claude Agent SDK file tools:

```text
Read
Write
Edit
Glob
Grep
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

Those GitHub tools hold credentials in the Brain process. The Task Agent can
request a push or pull request through a tool call, but the token is not written
to the checked-out repository.

## Configuration

Copy the values below into your environment before starting Web and Brain:

```bash
export DATABASE_URL=postgresql://postgres:postgres@localhost:5432/ecsma_poc
export SESSION_LAYER_URL=http://localhost:8002
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
```

`GITHUB_TOKEN` is used by Brain GitHub tools for authenticated Git
pushes and PR creation. It is not passed to Claude Agent SDK prompts.

## Run Locally

Start Postgres:

```bash
docker compose up -d postgres
```

Install dependencies:

```bash
uv sync --extra dev
```

Run Session, Web, and Brain in separate terminals:

```bash
uv run uvicorn cloud_agent_poc.session_app:app --reload --port 8002
uv run uvicorn cloud_agent_poc.web:app --reload --port 8000
uv run uvicorn cloud_agent_poc.brain_app:app --reload --port 8001
```

Open the Web URL. Both services create the idempotent Postgres schema at
startup.

## Kubernetes

The `k8s` directory targets the active local Kubernetes cluster. Use a local
port-forward for the Web UI so the browser entrypoint is stable across local
Kubernetes load-balancer behavior. The included Dockerfile uses the locally
cached `public.ecr.aws/docker/library/python:3.12-alpine` base image and
installs Node.js plus Claude Code CLI because Claude Agent SDK invokes that
runtime from the Brain process.

Build the shared Web/Brain image first:

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

Kubernetes creates four application deployments:

```text
cloud-agent-web
cloud-agent-session
cloud-agent-brain
postgres
```

Only `cloud-agent-brain` mounts the `session-artifacts` PVC at `/root/.claude`.
That lets Claude Agent SDK produce transcript JSONL files in a durable artifact
space while Session Layer remains the logical owner through DB metadata.

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
- Add a real sandbox or remote execution hand before accepting arbitrary users.
- Add checkpoints, artifacts, and structured verification evidence for
  long-running work.
