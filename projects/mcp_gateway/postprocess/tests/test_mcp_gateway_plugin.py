"""Tests for the MCP Gateway Caliper PostProcessingPlugin."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from projects.caliper.engine.model import TestBaseNode, UnifiedRunModel
from projects.mcp_gateway.postprocess.mcp_gateway.parsing.kpis import MCPGatewayKpiHandler
from projects.mcp_gateway.postprocess.mcp_gateway.parsing.parsers import MCPGatewayParser
from projects.mcp_gateway.postprocess.mcp_gateway.plugin import MCPGatewayPlugin, get_plugin

SAMPLE_STATS_CSV = (
    "Type,Name,Request Count,Failure Count,Average Response Time,"
    "50%,90%,95%,99%,Max Response Time,Requests/s\n"
    "POST,/mcp/tools,800,2,42.5,35,70,90,150,300,25.0\n"
    "GET,/health,200,0,5.0,4,8,10,15,20,6.5\n"
    ",Aggregated,1000,2,33.5,30,60,80,120,300,31.5\n"
)

TEST_LABELS = {"preset": "smoke", "target": "gateway", "users": "16", "num_servers": "1"}


def _make_test_node(base_dir: Path, name: str, stats_csv: str, labels: dict) -> TestBaseNode:
    """Create a test base directory with stats.csv and __test_labels__.yaml."""
    node_dir = base_dir / name
    node_dir.mkdir(parents=True, exist_ok=True)

    (node_dir / "stats.csv").write_text(stats_csv, encoding="utf-8")
    (node_dir / "master.log").write_text("log output", encoding="utf-8")
    (node_dir / "__test_labels__.yaml").write_text(
        yaml.safe_dump({"version": "1", "labels": labels}, sort_keys=False),
        encoding="utf-8",
    )

    artifact_paths = sorted(
        p for p in node_dir.rglob("*") if p.is_file() and p.name != "__test_labels__.yaml"
    )
    return TestBaseNode(
        directory=node_dir,
        labels={"version": "1", "labels": labels},
        artifact_paths=artifact_paths,
    )


# ---------------------------------------------------------------------------
# Parser tests
# ---------------------------------------------------------------------------


class TestMCPGatewayParser:
    def test_parse_creates_records(self, tmp_path: Path):
        node = _make_test_node(tmp_path, "run-a", SAMPLE_STATS_CSV, TEST_LABELS)
        parser = MCPGatewayParser()

        result = parser.parse(tmp_path, [node])

        assert len(result.records) == 1
        assert result.warnings == []
        record = result.records[0]
        assert record.run_identity == {"mcp_gateway": True}
        assert record.metrics["total_requests"] == 1000
        assert record.metrics["requests_per_second"] == 31.5
        assert record.metrics["p95_ms"] == 80.0

    def test_parse_does_not_write_metrics_json(self, tmp_path: Path):
        """Parser no longer writes metrics.json — caliper handles it generically."""
        node = _make_test_node(tmp_path, "run-a", SAMPLE_STATS_CSV, TEST_LABELS)
        parser = MCPGatewayParser()

        parser.parse(tmp_path, [node])

        assert not (tmp_path / "run-a" / "metrics.json").exists()

    def test_parse_no_stats_csv(self, tmp_path: Path):
        node_dir = tmp_path / "run-empty"
        node_dir.mkdir(parents=True)
        (node_dir / "master.log").write_text("log")
        node = TestBaseNode(
            directory=node_dir,
            labels={"version": "1", "labels": TEST_LABELS},
            artifact_paths=[node_dir / "master.log"],
        )
        parser = MCPGatewayParser()

        result = parser.parse(tmp_path, [node])

        assert len(result.records) == 1
        assert result.records[0].metrics.get("no_stats_csv_found") is True
        assert result.records[0].parse_notes == ["No stats.csv file found"]

    def test_parse_multiple_nodes(self, tmp_path: Path):
        labels_a = {"preset": "smoke", "target": "gateway", "users": "16", "num_servers": "1"}
        labels_b = {"preset": "smoke", "target": "gateway", "users": "64", "num_servers": "2"}
        node_a = _make_test_node(tmp_path, "run-a", SAMPLE_STATS_CSV, labels_a)
        node_b = _make_test_node(tmp_path, "run-b", SAMPLE_STATS_CSV, labels_b)
        parser = MCPGatewayParser()

        result = parser.parse(tmp_path, [node_a, node_b])

        assert len(result.records) == 2
        paths = {r.test_base_path for r in result.records}
        assert "run-a" in paths
        assert "run-b" in paths

        for node_name in ["run-a", "run-b"]:
            assert not (tmp_path / node_name / "metrics.json").exists()
            assert not (tmp_path / node_name / "parameters.json").exists()


# ---------------------------------------------------------------------------
# KPI tests
# ---------------------------------------------------------------------------


class TestMCPGatewayKpis:
    def test_catalog_has_expected_kpis(self):
        handler = MCPGatewayKpiHandler()
        catalog = handler.get_catalog()
        kpi_ids = {k["kpi_id"] for k in catalog}

        assert "mcp_gw_requests_per_second" in kpi_ids
        assert "mcp_gw_p95_ms" in kpi_ids
        assert "mcp_gw_failure_rate" in kpi_ids

    def test_compute_kpis(self, tmp_path: Path):
        node = _make_test_node(tmp_path, "run-a", SAMPLE_STATS_CSV, TEST_LABELS)
        parser = MCPGatewayParser()
        parse_result = parser.parse(tmp_path, [node])

        model = UnifiedRunModel(
            plugin_module="projects.mcp_gateway.postprocess.mcp_gateway.plugin",
            base_directory=str(tmp_path),
            test_nodes=[node],
            unified_result_records=parse_result.records,
        )

        handler = MCPGatewayKpiHandler()
        kpis = handler.compute_kpis(model)

        assert len(kpis) > 0
        kpi_dict = {k["kpi_id"]: k for k in kpis}
        assert kpi_dict["mcp_gw_requests_per_second"]["value"] == 31.5
        assert kpi_dict["mcp_gw_p95_ms"]["value"] == 80.0
        assert kpi_dict["mcp_gw_failure_rate"]["value"] == pytest.approx(0.002, abs=1e-4)

    def test_compute_kpis_skips_missing_records(self):
        from projects.caliper.engine.model import UnifiedResultRecord

        record = UnifiedResultRecord(
            test_base_path="run-empty",
            distinguishing_labels={},
            metrics={"no_stats_csv_found": True},
            run_identity={"mcp_gateway": True},
        )
        model = UnifiedRunModel(
            plugin_module="test",
            base_directory="/tmp",
            test_nodes=[],
            unified_result_records=[record],
        )

        kpis = MCPGatewayKpiHandler.compute_kpis(model)
        assert kpis == []


# ---------------------------------------------------------------------------
# Plugin integration tests
# ---------------------------------------------------------------------------


class TestMCPGatewayPlugin:
    def test_get_plugin_returns_instance(self):
        plugin = get_plugin()
        assert isinstance(plugin, MCPGatewayPlugin)

    def test_plugin_parse_and_kpis(self, tmp_path: Path):
        node = _make_test_node(tmp_path, "run-a", SAMPLE_STATS_CSV, TEST_LABELS)
        plugin = get_plugin()

        parse_result = plugin.parse(tmp_path, [node])
        assert len(parse_result.records) == 1

        model = UnifiedRunModel(
            plugin_module="projects.mcp_gateway.postprocess.mcp_gateway.plugin",
            base_directory=str(tmp_path),
            test_nodes=[node],
            unified_result_records=parse_result.records,
        )

        kpis = plugin.compute_kpis(model)
        assert len(kpis) > 0

        catalog = plugin.kpi_catalog()
        assert len(catalog) > 0

    def test_visualize_returns_empty(self, tmp_path: Path):
        plugin = get_plugin()
        model = UnifiedRunModel(
            plugin_module="test",
            base_directory=str(tmp_path),
            test_nodes=[],
            unified_result_records=[],
        )
        result = plugin.visualize(model, tmp_path, None, None, None)
        assert result == []
