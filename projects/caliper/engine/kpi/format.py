"""KPI output format transformations and utilities."""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def transform_kpis_to_hierarchical_format(kpis: list[dict], model) -> dict:
    """
    Transform flat KPI list into hierarchical JSON structure.

    Groups KPIs by test (run_id), extracting common labels and organizing
    KPI metadata (name, help, unit, etc.) for improved readability.

    Args:
        kpis: List of flat KPI records from compute_kpis
        model: Unified model for accessing plugin metadata

    Returns:
        Hierarchical JSON structure organized by test
    """

    if not kpis:
        return {"schema_version": "2", "tests": []}

    # Group KPIs by test (run_id)
    tests_data = defaultdict(lambda: {"kpis": [], "labels": {}, "metadata": {}})

    # Get KPI function metadata from the plugin module

    plugin_module_obj = __import__(model.plugin_module, fromlist=[""])
    kpi_catalog = plugin_module_obj.get_plugin().kpi_catalog()

    kpi_models = {e["kpi_id"]: e for e in kpi_catalog}

    # First pass: determine which labels vary across KPIs in the same run
    run_label_values: dict[str, dict[str, set]] = defaultdict(lambda: defaultdict(set))
    for kpi in kpis:
        run_id = kpi.get("run_id", "unknown")
        for k, v in kpi.get("labels", {}).items():
            run_label_values[run_id][k].add(str(v))

    for kpi in kpis:
        kpi_id = kpi.get("kpi_id")
        if kpi_id not in kpi_models:
            logger.warning(f"{kpi_id} not found in the KPI metadata, ignoring.")
            continue
        kpi_model = kpi_models[kpi_id]

        run_id = kpi.get("run_id", "unknown")
        test_data = tests_data[run_id]

        if "labels" not in test_data:
            test_data["labels"] = {}

        test_data["labels"].update(kpi["labels"])

        # Store test metadata from first KPI
        if not test_data["metadata"]:
            test_data["metadata"] = {
                "timestamp": kpi.get("timestamp"),
                "source": kpi.get("source", {}),
                "run_id": run_id,
            }

        raw_value = kpi.get("value")
        is_2d = kpi_model["is_2d"]

        # Apply tuple-pair structural transform only for confirmed 2D KPIs
        if is_2d:
            final_value = {
                "data_points": [{"x": float(x), "y": float(y)} for x, y in raw_value],
                "count": len(raw_value),
            }
        else:
            final_value = raw_value

        kpi_record: dict[str, Any] = kpi_model.copy()
        kpi_record["value"] = final_value
        # Hierarchical schema v2 nested KPIs use "id" (catalog uses "kpi_id").
        if "kpi_id" in kpi_record:
            kpi_record["id"] = kpi_record.pop("kpi_id")

        for fmt_key in "x_format", "y_format", "format":
            kpi_record.pop(fmt_key, None)

        test_data["kpis"].append(kpi_record)

    # Convert to final structure
    tests_list = []
    for run_id, test_data in tests_data.items():
        tests_list.append(
            {
                "run_id": run_id,
                "labels": test_data["labels"],
                "metadata": test_data["metadata"],
                "kpis": test_data["kpis"],
            }
        )

    return {"schema_version": "2", "tests": tests_list}


def write_kpis_in_format(
    kpis: list[dict], output_file: Path, format_type: str = "hierarchical", model: Any = None
) -> None:
    """
    Write KPIs to file in the specified format.

    Args:
        kpis: List of KPI records
        output_file: Path to output file
        format_type: Format type - "hierarchical" (default) or "jsonl"
        model: Unified model (required for hierarchical format)
    """
    output_file.parent.mkdir(parents=True, exist_ok=True)

    if format_type == "hierarchical":
        if model is None:
            raise ValueError("Model is required for hierarchical format")

        # Transform to hierarchical format (schema v2)
        hierarchical_data = transform_kpis_to_hierarchical_format(kpis, model)

        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(hierarchical_data, f, indent=2, ensure_ascii=False)
            # Add EOL at EOF if we have data
            if kpis:
                f.write("\n")

    elif format_type == "jsonl":
        # Write as JSONL (schema v1) - matches original behavior
        text = "\n".join(json.dumps(kpi, ensure_ascii=False) for kpi in kpis) + (
            "\n" if kpis else ""
        )
        with open(output_file, "w", encoding="utf-8") as f:
            f.write(text)

    else:
        raise ValueError(f"Unknown format type: {format_type}. Use 'hierarchical' or 'jsonl'")


def flatten_hierarchical_kpis(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Lossless conversion from schema_version=2 hierarchical KPI doc to flat records.

    Each output record contains:
    - All test-level fields: run_id, labels, metadata
    - All kpi-level fields verbatim, with 'id' renamed to 'kpi_id'

    The value is kept as-is (no conversion). Callers that need schema-v1 value
    representation should apply _convert_to_schema_v1_value() separately.
    """
    records: list[dict[str, Any]] = []
    for test in data.get("tests", []):
        test_base = {
            "run_id": test.get("run_id"),
            "labels": test.get("labels", {}),
            "metadata": test.get("metadata", {}),
        }
        for kpi in test.get("kpis", []):
            record = dict(test_base)
            record["kpi_id"] = kpi.get("id") or kpi.get("kpi_id")
            for k, v in kpi.items():
                if k in ("id", "kpi_id"):
                    pass
                elif k == "labels":
                    record[k] = {} | test_base["labels"] | v
                else:
                    record[k] = v

            records.append(record)
    return records


def _convert_to_schema_v1_value(raw_value: Any) -> Any:
    """
    Convert structured KPI value back to schema-v1 list-of-pairs representation.

    Args:
        raw_value: The KPI value, either scalar or structured with data_points/count

    Returns:
        Converted value - list of pairs for 2D data, scalar values unchanged
    """
    if isinstance(raw_value, dict):
        # Check if this is a structured value with data_points
        if "data_points" in raw_value and isinstance(raw_value["data_points"], list):
            # Convert data_points list back to list-of-pairs format
            data_points = raw_value["data_points"]
            return [
                [point.get("x"), point.get("y")]
                for point in data_points
                if isinstance(point, dict) and "x" in point and "y" in point
            ]
        # For other dict structures, return as-is (preserve existing format)
        return raw_value
    else:
        # Preserve scalar values unchanged
        return raw_value


def read_kpis_from_file(file_path: Path) -> list[dict]:
    """
    Read KPIs from a file, handling both JSONL and hierarchical JSON formats.

    Args:
        file_path: Path to the KPI file

    Returns:
        List of KPI records in flat format
    """
    kpis = []

    with open(file_path, encoding="utf-8") as f:
        content = f.read().strip()

    if not content:
        return kpis

    try:
        # Try to parse as JSON (hierarchical format)
        data = json.loads(content)

        if isinstance(data, dict) and data.get("schema_version") == "2":
            for rec in flatten_hierarchical_kpis(data):
                rec["value"] = _convert_to_schema_v1_value(rec.get("value"))
                kpis.append(rec)
        else:
            # Unknown JSON format
            raise ValueError("Unknown JSON format")

    except json.JSONDecodeError:
        # Try to parse as JSONL
        for line in content.splitlines():
            line = line.strip()
            if line:
                try:
                    kpi = json.loads(line)
                    kpis.append(kpi)
                except json.JSONDecodeError:
                    continue  # Skip invalid lines

    return kpis
