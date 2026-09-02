"""KPI generation, OpenSearch, regression."""

from .dataclasses import (
    AnalysisSummary,
    BaselineSummary,
    ConfigSummary,
    KpiCatalogEntry,
    KpiRecord,
    OverallStatus,
    RegressionFinding,
    RegressionReport,
    SourceInfo,
    TestSummary,
)
from .decorators import (
    Curve,
    Format,
    HigherBetter,
    KPIMetadata,
    LowerBetter,
    TestLabelExtractor,
    build_catalog_from_functions,
    create_label_extractor,
    get_kpi_functions,
    is_curve_kpi,
)

__all__ = [
    # Core dataclasses - plugins should use these
    "KpiRecord",
    "KpiCatalogEntry",
    "RegressionFinding",
    "RegressionReport",
    "SourceInfo",
    # Status enum
    "OverallStatus",
    # Summary dataclasses
    "AnalysisSummary",
    "TestSummary",
    "ConfigSummary",
    "BaselineSummary",
    # KPI function decorators and utilities
    "Curve",
    "Format",
    "HigherBetter",
    "KPIMetadata",
    "LowerBetter",
    "TestLabelExtractor",
    "build_catalog_from_functions",
    "create_label_extractor",
    "get_kpi_functions",
    "is_curve_kpi",
]
