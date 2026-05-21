#!/usr/bin/env sh
set -eu

exec kubectl -n cloud-agent-poc port-forward svc/cloud-agent-web 18080:18081
