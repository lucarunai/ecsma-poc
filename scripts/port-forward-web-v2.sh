#!/usr/bin/env bash
set -euo pipefail

exec kubectl -n cloud-agent-poc-v2 port-forward svc/cloud-agent-web 18082:18082
