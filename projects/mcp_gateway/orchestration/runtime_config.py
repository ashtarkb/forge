from __future__ import annotations

import copy
import logging
from pathlib import Path
from typing import Any

from projects.agentic_tools.base_runtime_config import BaseRuntimeConfig
from projects.core.library import config

logger = logging.getLogger(__name__)


class MCPGatewayConfig(BaseRuntimeConfig):
    """Runtime configuration for MCP Gateway performance tests."""

    def __init__(self) -> None:
        super().__init__(
            orchestration_dir=Path(__file__).resolve().parent,
            namespace_prefix="mcp-gw",
        )

    # --- MCP Gateway configuration accessors ---

    def get_mock_server_key(self) -> str:
        return config.project.get_config("runtime.mock_server")

    def get_protocol_mode(self) -> str:
        """Return MCP protocol mode: stateful (2025) or stateless (2026)."""
        return config.project.get_config("runtime.protocol_mode", "stateful")

    def get_mock_server_config(self) -> dict[str, Any]:
        key = self.get_mock_server_key()
        return copy.deepcopy(config.project.get_config(f"mock_servers.{key}"))

    def get_duration_seconds(self) -> int:
        return config.project.get_config("experiment.duration_seconds")

    def get_warmup_seconds(self) -> int:
        return config.project.get_config("experiment.warmup_seconds")

    def get_calls_per_session(self) -> int:
        return config.project.get_config("runtime.calls_per_session")

    def get_spawn_rate(self) -> int | None:
        return config.project.get_config("runtime.spawn_rate", None)

    def get_users_per_worker(self) -> int:
        return config.project.get_config("runtime.users_per_worker")

    # --- Infrastructure accessors ---

    def get_deployed_version(self) -> str:
        """Detect the deployed MCP Gateway version from the container image tag."""
        import re

        from projects.core.dsl.utils.k8s import oc as run_oc

        result = run_oc(
            "get",
            "deployment",
            "mcp-gateway",
            "-n",
            "mcp-system",
            "-o",
            "jsonpath={.spec.template.spec.containers[0].image}",
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            match = re.search(r":v?(.+)$", result.stdout.strip())
            if match:
                return match.group(1)

        raise RuntimeError(
            "Could not detect MCP Gateway version from deployment mcp-gateway in mcp-system. "
            "Is the platform installed?"
        )

    def get_gateway_url(self) -> str:
        return config.project.get_config("infrastructure.gateway_url")

    def get_server_url(self) -> str:
        template = config.project.get_config("infrastructure.server_url_template")
        return template.format(
            mock_server=self.get_mock_server_key(),
            namespace=self.get_namespace(),
        )

    def get_host_header(self) -> str:
        return config.project.get_config("infrastructure.host_header")

    def get_tool_prefix(self) -> str:
        return config.project.get_config("infrastructure.tool_prefix")

    def get_api_group(self) -> str:
        """Return the API group for MCPServerRegistration CRDs on the cluster.

        Auto-detects by checking which CRD is installed. Falls back to config if
        cluster detection fails.
        """
        from projects.core.dsl.utils.k8s import oc

        result = oc(
            "get",
            "crd",
            "mcpserverregistrations.mcp.kuadrant.io",
            "--ignore-not-found",
            "-o",
            "name",
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            return "mcp.kuadrant.io"

        result = oc(
            "get",
            "crd",
            "mcpserverregistrations.mcp.kagenti.com",
            "--ignore-not-found",
            "-o",
            "name",
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            return "mcp.kagenti.com"

        return config.project.get_config("infrastructure.api_group", "mcp.kuadrant.io")

    def get_install_platform(self) -> bool:
        return bool(config.project.get_config("infrastructure.install_platform", False))

    def get_cleanup_platform(self) -> bool:
        return bool(config.project.get_config("infrastructure.cleanup_platform", False))

    def get_platform_config(self) -> dict[str, Any]:
        import os

        platform = copy.deepcopy(config.project.get_config("infrastructure.platform", {}))

        if not platform.get("kustomize_base"):
            env_path = os.environ.get("MCP_GATEWAY_KUSTOMIZE_BASE")
            if env_path:
                platform["kustomize_base"] = env_path

        return platform

    def get_scheduling_config(self) -> dict[str, Any]:
        return copy.deepcopy(config.project.get_config("infrastructure.scheduling", {}))

    def get_locust_config(self) -> dict[str, Any]:
        return copy.deepcopy(config.project.get_config("infrastructure.locust", {}))

    # --- Experiment accessors ---

    def get_experiment_servers(self) -> list[int]:
        raw = config.project.get_config("experiment.servers")
        return raw if isinstance(raw, list) else [raw]

    def get_experiment_concurrency(self) -> list[int]:
        raw = config.project.get_config("experiment.concurrency")
        return raw if isinstance(raw, list) else [raw]

    def get_experiment_targets(self) -> list[str]:
        raw = config.project.get_config("experiment.target")
        return raw if isinstance(raw, list) else [raw]

    def get_tools_per_server(self) -> int:
        return config.project.get_config("experiment.tools_per_server", 10)

    # --- Path helpers ---

    def get_mcp_client_path(self) -> Path:
        """Path to the shared MCP client used by locustfiles."""
        from projects.agentic_tools.mcp import clients

        return Path(clients.__file__).parent / "mcp_client.py"

    # --- Locust config builder ---

    def build_locust_kwargs(self, *, users: int, target: str, num_servers: int) -> dict[str, Any]:
        """Assemble plain keyword arguments for run_distributed.run()."""
        import math

        from projects.agentic_tools.locust import locust_runtime, locust_users

        namespace = self.get_namespace()
        preset = self.get_preset_name()
        duration_seconds = self.get_duration_seconds()
        warmup_seconds = self.get_warmup_seconds()
        scheduling = self.get_scheduling_config()

        workers = max(1, math.ceil(users / self.get_users_per_worker()))
        job_name = f"mcp-{preset}-s{num_servers}-u{users}-{target}"[:63]

        if num_servers == 1:
            host_url = self.get_gateway_url() if target == "gateway" else self.get_server_url()
            tool_prefix = self.get_tool_prefix() if target == "gateway" else ""
            host_header = self.get_host_header() if target == "gateway" else ""
        else:
            host_url = self.get_gateway_url()
            tool_prefix = ""
            host_header = ""

        protocol_mode = self.get_protocol_mode()
        runtime_dir = Path(locust_runtime.__file__).parent
        users_dir = Path(locust_users.__file__).parent

        if protocol_mode == "stateless":
            user_class = "MCP2026User"
            extra_files = [
                str(users_dir / "mcp_2026_user.py"),
            ]
        else:
            user_class = "MCPSessionUser"
            extra_files = [
                str(users_dir / "mcp_session_user.py"),
                str(self.get_mcp_client_path()),
            ]

        env_vars = {
            "USER_CLASS": user_class,
            "TARGET": target,
            "TOOL_PREFIX": tool_prefix,
            "HOST_HEADER": host_header,
            "CALLS_PER_SESSION": str(self.get_calls_per_session()),
            "WARMUP_SECONDS": str(warmup_seconds),
            "PROTOCOL_MODE": protocol_mode,
        }
        if num_servers > 1:
            env_vars["NUM_SERVERS"] = str(num_servers)

        return dict(
            job_name=job_name,
            namespace=namespace,
            host_url=host_url,
            users=users,
            workers=workers,
            duration_seconds=duration_seconds + warmup_seconds,
            spawn_rate=self.get_spawn_rate(),
            configmap_name=f"locust-scripts-mcp-{preset}"[:63],
            locustfiles_dir=str(runtime_dir),
            locustfile_names=["locustfile_main.py", "metrics_hook.py"],
            extra_files=extra_files,
            env_vars=env_vars,
            labels={"forge.openshift.io/project": "mcp_gateway"},
            node_selector=scheduling.get("node_selector"),
            tolerations=scheduling.get("tolerations"),
        )


cfg = MCPGatewayConfig()
