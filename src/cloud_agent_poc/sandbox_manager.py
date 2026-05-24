from __future__ import annotations

import asyncio
import base64
import json
import os
import time
from pathlib import Path
from uuid import uuid4

import httpx

from .config import Settings
from .sandbox_protocol import SandboxRuntimeMetadata, ToolExecutionEnvelope
from .sandbox_protocol import ToolExecutionRequest
from .sandbox_runtime import execute_runtime_request


class SandboxManagerError(RuntimeError):
    pass


class ToolExecutionRunner:
    async def execute(
        self,
        request: ToolExecutionRequest,
        *,
        workspace_path: Path,
    ) -> ToolExecutionEnvelope:
        raise NotImplementedError


class DirectToolExecutionRunner(ToolExecutionRunner):
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def execute(
        self,
        request: ToolExecutionRequest,
        *,
        workspace_path: Path,
    ) -> ToolExecutionEnvelope:
        started = time.monotonic()
        envelope = await execute_runtime_request(
            request,
            settings=self.settings,
            workspace_path=workspace_path,
        )
        envelope.runtime.duration_ms = _duration_ms(started)
        envelope.runtime.type = "direct"
        envelope.runtime.pod_phase = "Direct"
        return envelope


class KubernetesToolPodRunner(ToolExecutionRunner):
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.namespace = _service_account_namespace()
        self.api_server = _kubernetes_api_server()
        self.token_path = Path("/var/run/secrets/kubernetes.io/serviceaccount/token")
        self.ca_path = Path("/var/run/secrets/kubernetes.io/serviceaccount/ca.crt")

    async def execute(
        self,
        request: ToolExecutionRequest,
        *,
        workspace_path: Path,
    ) -> ToolExecutionEnvelope:
        execution_id = request.execution_id or f"sbxexec_{uuid4().hex}"
        request = request.model_copy(
            update={
                "execution_id": execution_id,
                "workspace_path": str(workspace_path),
            }
        )
        pod_name = f"sandbox-tool-{execution_id.removeprefix('sbxexec_')[:20]}"
        started = time.monotonic()
        async with self._client() as client:
            await self._create_pod(client, pod_name, request)
            try:
                try:
                    pod = await self._wait_for_pod(client, pod_name)
                    phase = pod.get("status", {}).get("phase", "Unknown")
                    exit_code = _container_exit_code(pod)
                    logs = await self._read_logs(client, pod_name)
                    envelope = _parse_runtime_envelope(logs, request)
                    envelope.runtime = SandboxRuntimeMetadata(
                        type="sandbox_pod",
                        pod_name=pod_name,
                        pod_phase=phase,
                        exit_code=exit_code,
                        duration_ms=_duration_ms(started),
                    )
                    return envelope
                except SandboxManagerError as exc:
                    return _runtime_failure_envelope(
                        request,
                        pod_name=pod_name,
                        failure_message=str(exc),
                        duration_ms=_duration_ms(started),
                    )
            finally:
                await self._delete_pod(client, pod_name)

    async def _create_pod(
        self,
        client: httpx.AsyncClient,
        pod_name: str,
        request: ToolExecutionRequest,
    ) -> None:
        response = await client.post(
            f"/api/v1/namespaces/{self.namespace}/pods",
            json=self._pod_manifest(pod_name, request),
        )
        if not response.is_success:
            raise SandboxManagerError(
                f"Sandbox Pod creation failed with HTTP {response.status_code}: "
                f"{_response_detail(response)}"
            )

    async def _wait_for_pod(
        self,
        client: httpx.AsyncClient,
        pod_name: str,
    ) -> dict:
        deadline = time.monotonic() + self.settings.sandbox_tool_timeout_seconds + 15
        while time.monotonic() < deadline:
            response = await client.get(
                f"/api/v1/namespaces/{self.namespace}/pods/{pod_name}"
            )
            if not response.is_success:
                raise SandboxManagerError(
                    f"Sandbox Pod lookup failed with HTTP {response.status_code}: "
                    f"{_response_detail(response)}"
                )
            pod = response.json()
            phase = pod.get("status", {}).get("phase")
            if phase in {"Succeeded", "Failed"}:
                return pod
            await asyncio.sleep(0.5)
        raise SandboxManagerError("Sandbox Pod timed out before reporting completion.")

    async def _read_logs(self, client: httpx.AsyncClient, pod_name: str) -> str:
        response = await client.get(
            f"/api/v1/namespaces/{self.namespace}/pods/{pod_name}/log"
        )
        if not response.is_success:
            raise SandboxManagerError(
                f"Sandbox Pod log read failed with HTTP {response.status_code}: "
                f"{_response_detail(response)}"
            )
        return response.text

    async def _delete_pod(self, client: httpx.AsyncClient, pod_name: str) -> None:
        response = await client.delete(
            f"/api/v1/namespaces/{self.namespace}/pods/{pod_name}",
            params={"gracePeriodSeconds": "0"},
        )
        if response.status_code not in {200, 202, 404}:
            raise SandboxManagerError(
                f"Sandbox Pod cleanup failed with HTTP {response.status_code}: "
                f"{_response_detail(response)}"
            )

    def _pod_manifest(self, pod_name: str, request: ToolExecutionRequest) -> dict:
        request_b64 = base64.b64encode(
            request.model_dump_json().encode("utf-8")
        ).decode("ascii")
        workspace_mount_path = f"/workspace/{request.run_id}"
        workspace_sub_path = self._workspace_volume_sub_path(request)
        env: list[dict] = [
            {"name": "SANDBOX_TOOL_REQUEST_B64", "value": request_b64},
            {"name": "SANDBOX_WORKSPACE_PATH", "value": workspace_mount_path},
            {"name": "GIT_AUTHOR_NAME", "value": self.settings.git_author_name},
            {"name": "GIT_AUTHOR_EMAIL", "value": self.settings.git_author_email},
        ]
        return {
            "apiVersion": "v1",
            "kind": "Pod",
            "metadata": {
                "name": pod_name,
                "labels": {
                    "app": "cloud-agent-sandbox-tool",
                    "cloud-agent-run-id": request.run_id,
                },
            },
            "spec": {
                "restartPolicy": "Never",
                "automountServiceAccountToken": False,
                "activeDeadlineSeconds": self.settings.sandbox_tool_timeout_seconds,
                "containers": [
                    {
                        "name": "tool-runtime",
                        "image": self.settings.sandbox_runtime_image,
                        "imagePullPolicy": "IfNotPresent",
                        "command": ["python", "-m", "cloud_agent_poc.sandbox_runtime"],
                        "env": env,
                        "securityContext": {
                            "runAsNonRoot": True,
                            "runAsUser": 10001,
                            "runAsGroup": 10001,
                            "allowPrivilegeEscalation": False,
                            "capabilities": {"drop": ["ALL"]},
                            "seccompProfile": {"type": "RuntimeDefault"},
                        },
                        "resources": {
                            "requests": {"cpu": "100m", "memory": "128Mi"},
                            "limits": {"cpu": "500m", "memory": "512Mi"},
                        },
                        "volumeMounts": [
                            {
                                "name": "workspace",
                                "mountPath": workspace_mount_path,
                                "subPath": workspace_sub_path,
                            },
                            {
                                "name": "tmp",
                                "mountPath": "/tmp",
                            }
                        ],
                    }
                ],
                "volumes": [
                    {
                        "name": "workspace",
                        "persistentVolumeClaim": {
                            "claimName": self.settings.sandbox_workspace_claim
                        },
                    },
                    {
                        "name": "tmp",
                        "emptyDir": {},
                    },
                ],
            },
        }

    def _workspace_volume_sub_path(self, request: ToolExecutionRequest) -> str:
        workspace_path = (
            Path(request.workspace_path)
            if request.workspace_path
            else self.settings.workspace_root / request.run_id
        ).resolve()
        workspace_root = self.settings.workspace_root.resolve()
        try:
            relative_path = workspace_path.relative_to(workspace_root)
        except ValueError as exc:
            raise SandboxManagerError("Workspace path escaped sandbox root.") from exc
        if not relative_path.parts:
            raise SandboxManagerError("Workspace path must point to a run workspace.")
        return relative_path.as_posix()

    def _client(self) -> httpx.AsyncClient:
        token = self.token_path.read_text(encoding="utf-8").strip()
        return httpx.AsyncClient(
            base_url=self.api_server,
            headers={"Authorization": f"Bearer {token}"},
            verify=str(self.ca_path),
            timeout=30,
        )


def create_tool_execution_runner(settings: Settings) -> ToolExecutionRunner:
    if settings.sandbox_execution_mode == "kubernetes":
        return KubernetesToolPodRunner(settings)
    if settings.sandbox_execution_mode == "direct":
        return DirectToolExecutionRunner(settings)
    raise SandboxManagerError(
        f"Unsupported SANDBOX_EXECUTION_MODE: {settings.sandbox_execution_mode}"
    )


def _parse_runtime_envelope(
    logs: str,
    request: ToolExecutionRequest,
) -> ToolExecutionEnvelope:
    for line in reversed([line for line in logs.splitlines() if line.strip()]):
        try:
            return ToolExecutionEnvelope.model_validate_json(line)
        except ValueError:
            continue
    return ToolExecutionEnvelope(
        execution_id=request.execution_id or f"sbxexec_{uuid4().hex}",
        run_id=request.run_id,
        tool_call_id=request.tool_call_id,
        tool_name=request.tool_name,
        execution_status="failed",
        failure_message=f"Sandbox runtime did not emit a result: {logs[-1200:]}",
    )


def _runtime_failure_envelope(
    request: ToolExecutionRequest,
    *,
    pod_name: str,
    failure_message: str,
    duration_ms: int,
) -> ToolExecutionEnvelope:
    return ToolExecutionEnvelope(
        execution_id=request.execution_id or f"sbxexec_{uuid4().hex}",
        run_id=request.run_id,
        tool_call_id=request.tool_call_id,
        tool_name=request.tool_name,
        execution_status="failed",
        failure_message=failure_message,
        runtime=SandboxRuntimeMetadata(
            type="sandbox_pod",
            pod_name=pod_name,
            pod_phase="Unknown",
            duration_ms=duration_ms,
        ),
    )


def _container_exit_code(pod: dict) -> int | None:
    statuses = pod.get("status", {}).get("containerStatuses") or []
    if not statuses:
        return None
    terminated = statuses[0].get("state", {}).get("terminated")
    return terminated.get("exitCode") if terminated else None


def _service_account_namespace() -> str:
    path = Path("/var/run/secrets/kubernetes.io/serviceaccount/namespace")
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    return os.getenv("POD_NAMESPACE", "default")


def _kubernetes_api_server() -> str:
    host = os.getenv("KUBERNETES_SERVICE_HOST")
    port = os.getenv("KUBERNETES_SERVICE_PORT", "443")
    if not host:
        raise SandboxManagerError("Kubernetes service host is not available.")
    return f"https://{host}:{port}"


def _duration_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


def _response_detail(response: httpx.Response) -> str:
    try:
        return str(response.json().get("message") or response.text)
    except json.JSONDecodeError:
        return response.text
