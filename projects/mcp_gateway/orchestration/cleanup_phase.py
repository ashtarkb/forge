"""
Cleanup phases for MCP Gateway performance tests.

run() = pre-cleanup (test resources only):
- Locust Jobs, Services, ConfigMaps
- Mock server Deployment + Service
- Infrastructure (HTTPRoute, DestinationRule, MCPServerRegistration)

run_platform_cleanup() = post-cleanup (full platform teardown):
- All test resources (same as pre-cleanup)
- Platform operators, CRs, Helm releases, namespaces
"""

from __future__ import annotations

import logging

from projects.core.dsl.utils.k8s import oc, oc_resource_exists
from projects.mcp_gateway.orchestration.runtime_config import cfg
from projects.mcp_gateway.toolbox.cleanup_test_resources import main as cleanup_test_resources

logger = logging.getLogger(__name__)


def run() -> int:
    """Pre-cleanup: remove test-level resources only."""
    namespace = cfg.get_namespace()
    mock_server = cfg.get_mock_server_key()

    logger.info("=== MCP Gateway Pre-Cleanup Phase ===")
    logger.info("Namespace: %s", namespace)

    if not oc_resource_exists("namespace", namespace):
        logger.info("Namespace %s does not exist, nothing to clean", namespace)
        return 0

    cleanup_test_resources.run(
        namespace=namespace,
        mock_server_name=mock_server,
    )

    logger.info("=== Pre-cleanup phase complete ===")
    return 0


def run_platform_cleanup() -> int:
    """Post-cleanup: remove test resources + full platform teardown."""
    from projects.mcp_gateway.toolbox.platform_helpers import wait_for_namespace_termination

    namespace = cfg.get_namespace()
    cleanup_platform = cfg.get_cleanup_platform()

    logger.info("=== MCP Gateway Post-Cleanup Phase ===")

    if oc_resource_exists("namespace", namespace):
        try:
            mock_server = cfg.get_mock_server_key()
        except Exception:
            mock_server = "perf-mock-server"
        cleanup_test_resources.run(
            namespace=namespace,
            mock_server_name=mock_server,
        )

    if cleanup_platform:
        from projects.mcp_gateway.toolbox.cleanup_platform import main as cleanup_platform_mod
        from projects.mcp_gateway.toolbox.platform_helpers import (
            cleanup_platform_clone,
            get_platform_clone_path,
        )

        platform_cfg = cfg.get_platform_config()

        if not platform_cfg.get("kustomize_base"):
            subdir = platform_cfg.get("platform_repo_subdir")
            clone_path = get_platform_clone_path(subdir) if subdir else get_platform_clone_path()
            if clone_path:
                platform_cfg["kustomize_base"] = str(clone_path)
            else:
                logger.warning(
                    "No platform clone found from the prepare phase. "
                    "Kustomize-based cleanup steps will be skipped."
                )

        scheduling = cfg.get_scheduling_config()
        cleanup_platform_mod.run(
            platform_config=platform_cfg,
            scheduling_node_selector=scheduling.get("node_selector"),
        )
        cleanup_platform_clone()
    else:
        logger.info("Platform cleanup not enabled (cleanup_platform=false)")

    if oc_resource_exists("namespace", namespace):
        logger.info("Deleting test namespace %s", namespace)
        oc("delete", "namespace", namespace, "--wait=false", check=False)
        try:
            wait_for_namespace_termination([namespace])
        except RuntimeError:
            logger.warning("Namespace %s still terminating after timeout", namespace)

    logger.info("=== Post-cleanup phase complete ===")
    return 0
