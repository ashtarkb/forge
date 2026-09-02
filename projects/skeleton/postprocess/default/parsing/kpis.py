"""KPI definitions and computation for Skeleton Caliper plugin."""

from __future__ import annotations

import inspect
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from projects.caliper.engine.kpi import (
    # KPI function decorators and utilities
    Format,
    HigherBetter,
    # Core dataclasses from Caliper engine
    KpiCatalogEntry,
    KPIMetadata,
    KpiRecord,
    LowerBetter,
    SourceInfo,
    build_catalog_from_functions,
    create_label_extractor,
    get_kpi_functions,
    is_curve_kpi,
)
from projects.caliper.engine.model import UnifiedRunModel


# Throughput KPIs
@HigherBetter()
@Format("{:.2f}")
@KPIMetadata(help="Number of requests processed per second", unit="req/s")
def kpi_skeleton_throughput_rps(unified_record) -> float:
    """Throughput KPI."""
    raw_value = unified_record.metrics.get("throughput")
    if raw_value is None:
        raise ValueError("throughput metric not found")
    return float(raw_value)


# Latency KPIs
@LowerBetter()
@Format("{:.2f}")
@KPIMetadata(help="Average response latency in milliseconds", unit="ms")
def kpi_skeleton_latency_ms(unified_record) -> float:
    """Latency KPI."""
    raw_value = unified_record.metrics.get("latency_ms")
    if raw_value is None:
        raise ValueError("latency_ms metric not found")
    return float(raw_value)


class SkeletonKpiHandler:
    """Handles KPI catalog and computation for skeleton project."""

    # Create label extractor for test condition labels including version
    LABEL_EXTRACTOR = create_label_extractor(
        {
            "scenario": "distinguishing_labels.scenario",
            "workload": "distinguishing_labels.workload",
            "version": lambda record: SkeletonKpiHandler.extract_version(record),
        }
    )

    @staticmethod
    def extract_metadata(record) -> dict[str, Any]:
        """Extract metadata fields from test record."""
        return {
            "test_config": record.run_identity.get("test_config", {}),
            "environment": record.run_identity.get("environment", "unknown"),
        }

    @staticmethod
    def extract_version(record) -> str:
        """Extract version (date) from test record for analysis.

        Tries multiple sources for date information:
        1. 'version' or 'date' from distinguishing labels
        2. Date pattern (YYYY-MM-DD) from test path
        3. Current date as fallback

        Returns:
            Date string in YYYY-MM-DD format
        """
        # Check if version/date is explicitly in labels
        labels = record.distinguishing_labels
        if "version" in labels:
            return str(labels["version"])
        if "date" in labels:
            return str(labels["date"])

        # Try to extract date from test path
        test_path = record.test_base_path
        date_pattern = r"\b(\d{4}-\d{2}-\d{2})\b"
        match = re.search(date_pattern, test_path)
        if match:
            return match.group(1)

        # Try to extract date from directory structure
        path_parts = Path(test_path).parts
        for part in path_parts:
            match = re.search(date_pattern, part)
            if match:
                return match.group(1)

        # Fallback: use current date
        return datetime.now(UTC).strftime("%Y-%m-%d")

    @staticmethod
    def get_catalog() -> list[dict[str, Any]]:
        """
        Return the KPI catalog for skeleton metrics using dataclasses.

        Returns:
            List of KPI catalog entries as dictionaries
        """
        current_module = inspect.getmodule(SkeletonKpiHandler)
        raw_catalog = build_catalog_from_functions(current_module)

        # Convert to structured dataclass format
        catalog_entries = []
        for entry in raw_catalog:
            catalog_entry = KpiCatalogEntry(
                kpi_id=entry.get("kpi_id", ""),
                name=entry.get("name", ""),
                unit=entry.get("unit", ""),
                higher_is_better=entry.get("higher_is_better", True),
                is_curve=entry.get("is_curve", False),
                help=entry.get("help", ""),
                x_unit=entry.get("x_unit", ""),
                x_help=entry.get("x_help", ""),
                y_unit=entry.get("y_unit", ""),
                y_help=entry.get("y_help", ""),
            )
            catalog_entries.append(catalog_entry.to_dict())

        return catalog_entries

    @staticmethod
    def compute_kpis(model: UnifiedRunModel) -> list[dict[str, Any]]:
        """
        Compute KPI values from the unified model.

        Args:
            model: Unified model containing parsed test results

        Returns:
            List of KPI records
        """
        ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        out: list[dict[str, Any]] = []
        current_module = inspect.getmodule(SkeletonKpiHandler)
        kpi_functions = get_kpi_functions(current_module)

        for r in model.unified_result_records:
            base_labels = {**r.distinguishing_labels}

            # Extract test condition labels (same for all KPIs in this test)
            test_condition_labels = SkeletonKpiHandler.LABEL_EXTRACTOR.extract(r)

            # Extract metadata fields
            metadata_fields = SkeletonKpiHandler.extract_metadata(r)

            # Compute each KPI using the decorated functions
            for kpi_id, kpi_func in kpi_functions.items():
                try:
                    value = kpi_func(r)
                except (TypeError, ValueError, KeyError):
                    if is_curve_kpi(kpi_func):
                        value = []  # Empty list for failed curve KPIs
                    else:
                        value = None  # None for missing/failed scalar KPIs

                # Skip KPIs with null/empty values
                if value is None or (isinstance(value, list) and not value):
                    continue

                # Extract date for version label
                version_label = SkeletonKpiHandler.extract_version(r)

                # Merge base labels, test condition labels, and system labels
                all_labels = {
                    **base_labels,
                    **test_condition_labels,
                    "version": version_label,  # Include date as version for analysis
                    "higher_is_better": kpi_func._kpi_higher_is_better,
                }

                # Create structured KPI record using core dataclass
                kpi_record = KpiRecord(
                    schema_version="1",
                    kpi_id=kpi_id,
                    value=value,  # Core enforces int|float only
                    unit=kpi_func._kpi_unit,
                    run_id=r.test_base_path,
                    timestamp=ts,
                    labels=all_labels,
                    metadata=metadata_fields,
                    is_curve=False,  # Scalar KPI
                    source=SourceInfo(
                        test_base_path=r.test_base_path,
                        plugin_module=model.plugin_module,
                    ),
                )

                # Add 2D-specific metadata if applicable
                if is_curve_kpi(kpi_func):
                    kpi_record.x_unit = kpi_func._kpi_x_unit
                    kpi_record.x_help = kpi_func._kpi_x_help
                    kpi_record.y_unit = getattr(kpi_func, "_kpi_y_unit", None) or kpi_func._kpi_unit
                    kpi_record.y_help = getattr(kpi_func, "_kpi_y_help", None) or kpi_func._kpi_help

                out.append(kpi_record.to_dict())

        return out
