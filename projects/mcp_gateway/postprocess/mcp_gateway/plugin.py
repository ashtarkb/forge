"""MCP Gateway Caliper PostProcessingPlugin."""

from __future__ import annotations

import logging
from typing import Any

from projects.caliper.engine.kpi.analyze import AnalysisConfig
from projects.caliper.engine.model import (
    ParseResult,
    PostProcessingPlugin,
    TestBaseNode,
    UnifiedRunModel,
)

from .parsing import MCPGatewayKpiHandler, MCPGatewayParser

logger = logging.getLogger(__name__)

# Compare versions while matching on load shape / target / protocol.
analysis_config = AnalysisConfig(
    comparison_keys=["mcp_gateway_version"],
    ignored_keys=[],
    sorting_keys=["num_servers", "users", "target"],
    max_relative_regression=0.10,
    min_baseline_points=1,
)


class MCPGatewayPlugin(PostProcessingPlugin):
    """Parses Locust stats.csv artifacts from MCP Gateway performance tests."""

    def __init__(self):
        self.parser = MCPGatewayParser()
        self.kpi_handler = MCPGatewayKpiHandler()

    def parse(self, nodes: list[TestBaseNode]) -> ParseResult:
        return self.parser.parse(nodes)

    def compute_kpis(self, model: UnifiedRunModel) -> list[dict[str, Any]]:
        return self.kpi_handler.compute_kpis(model)


def get_plugin() -> PostProcessingPlugin:
    """Return the MCP Gateway plugin instance."""
    return MCPGatewayPlugin()
