"""Generic kpis.json -> metrics.json + parameters.json conversion.

Reads a hierarchical kpis.json (schema v2) and writes per-test-run
metrics.json and parameters.json files into the matching artifact tree
directories. The MLflow export backend picks these up automatically via
``_log_metrics_and_params_from_tree``.

This replaces project-specific metrics.json generation (e.g. in
mcp_gateway parsers) with a single generic caliper mechanism that works
for every project producing a kpis.json.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .dataclasses import SourceInfo

logger = logging.getLogger(__name__)

METRICS_FILE = "metrics.json"
PARAMETERS_FILE = "parameters.json"
TEST_LABELS_MARKER = "__test_labels__.yaml"


@dataclass
class HierarchicalKpi:
    """KPI entry in hierarchical format (schema v2)."""

    id: str  # KPI identifier (maps to kpi_id in flat format)  # noqa: A003
    value: Any  # KPI value (scalar or structured)
    name: str = ""
    unit: str = ""
    higher_is_better: bool = False
    is_curve: bool = False
    help: str = ""  # noqa: A003
    description: str = ""
    category: str = ""
    tags: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> HierarchicalKpi:
        """Create HierarchicalKpi from dictionary data."""
        return cls(
            id=data.get("id", ""),
            value=data.get("value"),
            name=data.get("name", ""),
            unit=data.get("unit", ""),
            higher_is_better=data.get("higher_is_better", False),
            is_curve=data.get("is_curve", False),
            help=data.get("help", ""),
            description=data.get("description", ""),
            category=data.get("category", ""),
            tags=data.get("tags", []),
        )


@dataclass
class TestMetadata:
    """Test metadata in hierarchical format."""

    timestamp: str | None = None
    source: SourceInfo | None = None
    run_id: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TestMetadata:
        """Create TestMetadata from dictionary data."""
        source_data = data.get("source")
        source = SourceInfo.from_dict(source_data) if isinstance(source_data, dict) else None
        return cls(
            timestamp=data.get("timestamp"),
            source=source,
            run_id=data.get("run_id", ""),
        )


@dataclass
class HierarchicalTest:
    """Test entry in hierarchical format (schema v2)."""

    run_id: str
    labels: dict[str, Any]
    metadata: TestMetadata
    kpis: list[HierarchicalKpi] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> HierarchicalTest:
        """Create HierarchicalTest from dictionary data."""
        metadata_data = data.get("metadata", {})
        metadata = TestMetadata.from_dict(metadata_data)

        kpis_data = data.get("kpis", [])
        kpis = [HierarchicalKpi.from_dict(kpi) for kpi in kpis_data]

        return cls(
            run_id=data.get("run_id", ""),
            labels=data.get("labels", {}),
            metadata=metadata,
            kpis=kpis,
        )


@dataclass
class HierarchicalKpiData:
    """Hierarchical KPI document structure (schema v2)."""

    schema_version: str
    tests: list[HierarchicalTest] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> HierarchicalKpiData:
        """Create HierarchicalKpiData from dictionary data."""
        tests_data = data.get("tests", [])
        tests = [HierarchicalTest.from_dict(test) for test in tests_data]

        return cls(
            schema_version=data.get("schema_version", ""),
            tests=tests,
        )


def _build_run_dir_index(artifact_tree: Path) -> dict[str, Path]:
    """Map run directory names to their paths using __test_labels__.yaml markers."""
    index: dict[str, Path] = {}
    for marker in sorted(artifact_tree.rglob(TEST_LABELS_MARKER)):
        if marker.is_file():
            run_dir = marker.parent
            try:
                rel = run_dir.relative_to(artifact_tree)
            except ValueError:
                rel = Path(run_dir.name)
            index[str(rel)] = run_dir
            index[run_dir.name] = run_dir
    return index


def _is_scalar(value: Any) -> bool:
    """Check if a KPI value is a scalar number (not curve data)."""
    return isinstance(value, int | float) and not isinstance(value, bool)


def _extract_curve_points(value: Any) -> list[dict[str, float]] | None:
    """Extract sorted (x, y) data points from a curve KPI value.

    Returns a list of ``{"x": ..., "y": ...}`` dicts sorted by x,
    or ``None`` if the value is not a valid curve structure.
    """
    if not isinstance(value, dict):
        return None
    data_points = value.get("data_points")
    if not isinstance(data_points, list) or not data_points:
        return None
    points = []
    for pt in data_points:
        if isinstance(pt, dict) and _is_scalar(pt.get("x")) and _is_scalar(pt.get("y")):
            points.append({"x": float(pt["x"]), "y": float(pt["y"])})
    if not points:
        return None
    points.sort(key=lambda p: p["x"])
    return points


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
        f.write("\n")


def generate_metrics_from_kpis(
    kpis_json_path: Path,
    artifact_tree: Path,
) -> dict[str, Any]:
    """Convert kpis.json into per-run metrics.json and parameters.json files.

    For each test entry in kpis.json, finds the matching directory under
    ``artifact_tree`` (via ``__test_labels__.yaml`` markers) and writes:

    - ``metrics.json``: ``{kpi_id: value}`` for all scalar KPIs
    - ``parameters.json``: test-level labels as string key-value pairs

    Args:
        kpis_json_path: Path to the kpis.json file (schema v2).
        artifact_tree: Root of the caliper artifact tree containing
            test run directories with ``__test_labels__.yaml`` markers.

    Returns:
        Status dict with counts and any warnings.
    """
    if not kpis_json_path.is_file():
        raise FileNotFoundError(f"kpis.json not found: {kpis_json_path}")

    with kpis_json_path.open(encoding="utf-8") as f:
        raw_data = json.load(f)

    if not isinstance(raw_data, dict) or raw_data.get("schema_version") != "2":
        return {"status": "skipped", "reason": "Not a schema v2 kpis.json"}

    # Parse into typed dataclass structure
    try:
        kpi_data = HierarchicalKpiData.from_dict(raw_data)
    except Exception as e:
        logger.error("Failed to parse KPI data: %s", e)
        return {"status": "skipped", "reason": f"Invalid KPI data structure: {e}"}

    if not kpi_data.tests:
        return {"status": "skipped", "reason": "No tests in kpis.json"}

    run_dir_index = _build_run_dir_index(artifact_tree)
    if not run_dir_index:
        logger.warning("No test run directories found under %s", artifact_tree)
        return {"status": "skipped", "reason": "No run directories with __test_labels__.yaml found"}

    written = 0
    warnings: list[str] = []

    for test in kpi_data.tests:
        # Determine test base path for directory matching
        test_base_path = test.run_id
        if test.metadata.source:
            test_base_path = test.metadata.source.test_base_path or test.run_id

        run_dir = run_dir_index.get(test_base_path) or run_dir_index.get(test.run_id)
        if run_dir is None:
            warnings.append(f"No matching directory for run_id={test.run_id!r}")
            continue

        # Process KPIs with type safety
        metrics: dict[str, Any] = {}
        for kpi in test.kpis:
            if not kpi.id:
                continue

            if kpi.is_curve:
                points = _extract_curve_points(kpi.value)
                if points:
                    metrics[kpi.id] = points
            elif _is_scalar(kpi.value):
                metrics[kpi.id] = kpi.value

        if metrics:
            _write_json(run_dir / METRICS_FILE, metrics)
        else:
            warnings.append(f"No scalar metrics extracted for run_id={run_id!r}")

        # Process labels with type safety
        if test.labels:
            params = {str(k): ("" if v is None else str(v)) for k, v in test.labels.items()}
            _write_json(run_dir / PARAMETERS_FILE, params)

        written += 1

    result: dict[str, Any] = {
        "status": "success",
        "tests_processed": written,
        "total_tests": len(kpi_data.tests),
    }
    if warnings:
        result["warnings"] = warnings
        for w in warnings:
            logger.warning("kpis-to-metrics: %s", w)

    logger.info(
        "Generated metrics.json for %d/%d test(s) from %s",
        written,
        len(kpi_data.tests),
        kpis_json_path.name,
    )
    return result
