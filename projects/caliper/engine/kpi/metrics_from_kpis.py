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
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

METRICS_FILE = "metrics.json"
PARAMETERS_FILE = "parameters.json"
TEST_LABELS_MARKER = "__test_labels__.yaml"


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
    """Check if a KPI value is a scalar number (not 2D data)."""
    return isinstance(value, int | float) and not isinstance(value, bool)


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
        return {"status": "skipped", "reason": f"kpis.json not found: {kpis_json_path}"}

    try:
        with kpis_json_path.open(encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Failed to read kpis.json at %s: %s", kpis_json_path, e)
        return {"status": "failed", "error": str(e)}

    if not isinstance(data, dict) or data.get("schema_version") != "2":
        return {"status": "skipped", "reason": "Not a schema v2 kpis.json"}

    tests = data.get("tests", [])
    if not tests:
        return {"status": "skipped", "reason": "No tests in kpis.json"}

    run_dir_index = _build_run_dir_index(artifact_tree)
    if not run_dir_index:
        logger.warning("No test run directories found under %s", artifact_tree)
        return {"status": "skipped", "reason": "No run directories with __test_labels__.yaml found"}

    written = 0
    warnings: list[str] = []

    for test_entry in tests:
        run_id = test_entry.get("run_id", "")
        test_base_path = test_entry.get("metadata", {}).get("source", {}).get(
            "test_base_path", run_id
        )

        run_dir = run_dir_index.get(test_base_path) or run_dir_index.get(run_id)
        if run_dir is None:
            warnings.append(f"No matching directory for run_id={run_id!r}")
            continue

        kpis = test_entry.get("kpis", [])
        metrics: dict[str, float] = {}
        for kpi in kpis:
            kpi_id = kpi.get("id", "")
            value = kpi.get("value")
            if kpi_id and _is_scalar(value):
                metrics[kpi_id] = value

        if metrics:
            _write_json(run_dir / METRICS_FILE, metrics)

        labels = test_entry.get("labels", {})
        if labels:
            params = {str(k): ("" if v is None else str(v)) for k, v in labels.items()}
            _write_json(run_dir / PARAMETERS_FILE, params)

        written += 1

    result: dict[str, Any] = {
        "status": "success",
        "tests_processed": written,
        "total_tests": len(tests),
    }
    if warnings:
        result["warnings"] = warnings
        for w in warnings:
            logger.warning("kpis-to-metrics: %s", w)

    logger.info(
        "Generated metrics.json for %d/%d test(s) from %s",
        written,
        len(tests),
        kpis_json_path.name,
    )
    return result
