"""
Core dataclasses for Caliper KPI analysis.

Provides strongly-typed data structures for KPI records, regression findings,
and analysis reports used across all Caliper plugins.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class OverallStatus(StrEnum):
    """Overall analysis status."""

    PASS = "PASS"
    REGRESSION_DETECTED = "REGRESSION_DETECTED"
    NO_BASELINE = "NO_BASELINE"
    NO_TEST_PERFORMED = "NO_TEST_PERFORMED"


@dataclass
class SourceInfo:
    """Source information for KPI records."""

    test_base_path: str = ""
    plugin_module: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SourceInfo:
        """Create SourceInfo from dictionary data."""
        return cls(**data)


class Algorithm(StrEnum):
    """Regression testing algorithms."""

    SCALAR_RELATIVE_CHANGE = "SCALAR_RELATIVE_CHANGE"
    CURVE_AUC_CHANGE = "CURVE_AUC_CHANGE"


class Verdict(StrEnum):
    """Individual KPI test verdict."""

    PASS = "PASS"
    REGRESSION = "REGRESSION"
    SKIPPED = "SKIPPED"


@dataclass
class KpiRecord:
    """Core KPI record structure used by all plugins."""

    kpi_id: str
    value: Any  # Can be scalar, list, or complex data structure
    schema_version: str = "1"
    unit: str = ""
    higher_is_better: bool = True
    labels: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    source: dict[str, Any] = field(default_factory=dict)
    timestamp: str = ""
    run_id: str = ""
    is_curve: bool = False
    # Curve KPI support fields
    x_unit: str = ""
    x_help: str = ""
    y_unit: str = ""
    y_help: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        result = asdict(self)

        # Only include curve fields if this is a curve KPI
        if not self.is_curve:
            # Remove curve-specific fields for scalar KPIs
            result.pop("x_unit", None)
            result.pop("x_help", None)
            result.pop("y_unit", None)
            result.pop("y_help", None)

        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> KpiRecord:
        """Create KpiRecord from dictionary data."""
        return cls(**data)


@dataclass
class RegressionFinding:
    """Individual regression test finding."""

    kpi_id: str
    baseline_value: float
    current_value: float
    relative_change: float
    change_percent: float
    is_regression: bool
    higher_is_better: bool
    unit: str = ""
    baseline_labels: dict[str, str] = field(default_factory=dict)
    current_labels: dict[str, str] = field(default_factory=dict)
    threshold_used: float = 0.1

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RegressionFinding:
        """Create RegressionFinding from dictionary data."""
        return cls(**data)


@dataclass
class TestSummary:
    """Summary of KPI test results."""

    total_kpis: int
    pass_count: int = 0
    regression_count: int = 0
    skipped_count: int = 0
    improvement_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)


@dataclass
class ConfigSummary:
    """Summary of analysis configuration."""

    comparison_labels: list[str] = field(default_factory=list)
    ignored_labels: list[str] = field(default_factory=list)
    sorting_labels: list[str] = field(default_factory=list)
    regression_config: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)


@dataclass
class BaselineSummary:
    """Summary of baseline data sources."""

    relevant_sources: list[dict[str, Any]] = field(default_factory=list)
    irrelevant_sources: list[dict[str, Any]] = field(default_factory=list)
    baseline_source_count: int = 0
    baseline_skipped: dict[str, int] = field(default_factory=dict)
    current_source: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)


@dataclass
class AnalysisSummary:
    """Comprehensive analysis summary with structured components."""

    tested: TestSummary
    config: ConfigSummary
    baseline_info: BaselineSummary
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AnalysisSummary:
        """Create AnalysisSummary from dictionary data."""
        # Convert nested dictionaries to their respective dataclass instances
        converted_data = data.copy()

        if "tested" in converted_data:
            converted_data["tested"] = TestSummary(**converted_data["tested"])

        if "config" in converted_data:
            converted_data["config"] = ConfigSummary(**converted_data["config"])

        if "baseline_info" in converted_data:
            converted_data["baseline_info"] = BaselineSummary(**converted_data["baseline_info"])

        return cls(**converted_data)


@dataclass
class ReportMetadata:
    """Analysis report metadata."""

    total_tested: int = 0
    total_skipped: int = 0
    plugin_module: str = ""
    caliper_version: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ReportMetadata:
        """Create ReportMetadata from dictionary data."""
        return cls(**data)


@dataclass
class AnalysisSection:
    """Analysis section of the regression report."""

    status: OverallStatus
    timestamp: str


@dataclass
class TestedSection:
    """Tested section of the regression report."""

    total_kpis: int
    pass_count: int = field(default=0, metadata={"json_name": "pass"})
    regression: int = 0
    skipped: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)


@dataclass
class OverallSection:
    """Overall section of the regression report."""

    verdict: OverallStatus
    regression_count: int
    total_tested: int
    total_skipped: int


@dataclass
class InputDataSection:
    """Input data section of the regression report."""

    current_source: dict[str, Any] = field(default_factory=dict)
    baseline_sources: dict[str, Any] = field(default_factory=dict)
    baseline_source_count: int = 0
    baseline_skipped: dict[str, int] = field(default_factory=dict)


@dataclass
class ResultLabels:
    """Labels section for result entry."""

    comparison_keys: dict[str, Any] = field(default_factory=dict)
    distinct_keys: dict[str, Any] = field(default_factory=dict)
    ignore_keys: dict[str, Any] = field(default_factory=dict)


@dataclass
class ResultCurrentValue:
    """Current value section for result entry."""

    comparison_keys: dict[str, Any] = field(default_factory=dict)
    value: Any = None


@dataclass
class ResultBaselineValue:
    """Baseline value entry for result entry."""

    comparison_keys: dict[str, Any] = field(default_factory=dict)
    value: Any = None


@dataclass
class ResultEntry:
    """Individual result entry in the results array."""

    kpi_id: str
    verdict: Verdict
    labels: ResultLabels = field(default_factory=ResultLabels)
    run_id: str = ""
    is_curve: bool = False
    higher_is_better: bool = True
    current_value: ResultCurrentValue = field(default_factory=ResultCurrentValue)
    baseline_values: list[ResultBaselineValue] = field(default_factory=list)
    baseline_count: int = 0
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class RegressionReport:
    """Regression analysis report matching the original format exactly."""

    analysis: AnalysisSection
    config: dict[str, Any] = field(default_factory=dict)
    tested: TestedSection = field(default_factory=lambda: TestedSection(total_kpis=0))
    overall: OverallSection = field(
        default_factory=lambda: OverallSection(
            verdict=OverallStatus.PASS, regression_count=0, total_tested=0, total_skipped=0
        )
    )
    input_data: InputDataSection = field(default_factory=InputDataSection)
    results: list[ResultEntry] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        result = asdict(self)
        # Handle the TestedSection's special to_dict method
        if hasattr(self.tested, "to_dict"):
            result["tested"] = self.tested.to_dict()
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RegressionReport:
        """Create RegressionReport from dictionary data."""
        # Convert nested sections
        analysis_data = data.get("analysis", {})
        analysis = AnalysisSection(
            status=OverallStatus(analysis_data["status"])
            if isinstance(analysis_data.get("status"), str)
            else analysis_data.get("status", OverallStatus.PASS),
            timestamp=analysis_data.get("timestamp", ""),
        )

        tested_data = data.get("tested", {})
        tested = TestedSection(**tested_data)

        overall_data = data.get("overall", {})
        overall = OverallSection(
            verdict=OverallStatus(overall_data["verdict"])
            if isinstance(overall_data.get("verdict"), str)
            else overall_data.get("verdict", OverallStatus.PASS),
            regression_count=overall_data.get("regression_count", 0),
            total_tested=overall_data.get("total_tested", 0),
            total_skipped=overall_data.get("total_skipped", 0),
        )

        input_data_raw = data.get("input_data", {})
        input_data = InputDataSection(
            current_source=input_data_raw.get("current_source", {}),
            baseline_sources=input_data_raw.get("baseline_sources", {}),
            baseline_source_count=input_data_raw.get("baseline_source_count", 0),
            baseline_skipped=input_data_raw.get("baseline_skipped", {}),
        )

        results_data = data.get("results", [])
        results = [
            ResultEntry(**result) if isinstance(result, dict) else result for result in results_data
        ]

        return cls(
            analysis=analysis,
            config=data.get("config", {}),
            tested=tested,
            overall=overall,
            input_data=input_data,
            results=results,
        )

    def has_regressions(self) -> bool:
        """Check if any regressions were detected."""
        return self.overall.regression_count > 0

    def is_successful(self) -> bool:
        """Check if analysis completed successfully."""
        return self.analysis.status in (OverallStatus.PASS, OverallStatus.NO_BASELINE)


@dataclass
class KpiCatalogEntry:
    """KPI catalog entry for plugin metadata."""

    kpi_id: str
    name: str = ""
    unit: str = ""
    higher_is_better: bool = True
    is_curve: bool = False
    help: str = ""
    x_unit: str = ""
    x_help: str = ""
    y_unit: str = ""
    y_help: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> KpiCatalogEntry:
        """Create KpiCatalogEntry from dictionary data."""
        return cls(**data)


@dataclass
class CaliperTestMetadata:
    """Caliper test metadata structure for __caliper_test_metadata__.yaml files."""

    version: str
    labels: dict[str, str]
    kpi_labels: dict[str, str] | None = None
    mlflow_destination: dict[str, str] | None = None
    timing: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for YAML serialization."""
        result = {
            "version": self.version,
            "labels": self.labels,
        }
        if self.kpi_labels is not None:
            result["kpi_labels"] = self.kpi_labels
        if self.mlflow_destination is not None:
            result["mlflow_destination"] = self.mlflow_destination
        if self.timing is not None:
            result["timing"] = self.timing
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CaliperTestMetadata:
        """Create CaliperTestMetadata from dictionary data."""
        return cls(
            version=data["version"],
            labels=data["labels"],
            kpi_labels=data.get("kpi_labels"),
            mlflow_destination=data.get("mlflow_destination"),
            timing=data.get("timing"),
        )


@dataclass
class CurrentValueInfo:
    """Structured current_value field for RegressionTestResult."""

    value: Any
    comparison_keys: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CurrentValueInfo:
        """Create CurrentValueInfo from dictionary data."""
        return cls(**data)


@dataclass
class RegressionTestResult:
    """Result of an individual KPI regression test."""

    verdict: Verdict
    kpi_id: str = ""
    baseline_count: int = 0
    current_value: CurrentValueInfo | None = None
    details: dict[str, Any] = field(default_factory=dict)
    reason: str = ""
    labels: dict[str, Any] = field(default_factory=dict)
    run_id: str = ""
    is_curve: bool = False
    higher_is_better: bool = True
    baseline_values: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RegressionTestResult:
        """Create RegressionTestResult from dictionary data."""
        # Convert current_value if it's a dict
        current_value = data.get("current_value")
        if isinstance(current_value, dict):
            current_value = CurrentValueInfo.from_dict(current_value)

        return cls(
            verdict=Verdict(data["verdict"])
            if isinstance(data["verdict"], str)
            else data["verdict"],
            kpi_id=data.get("kpi_id", ""),
            baseline_count=data.get("baseline_count", 0),
            current_value=current_value,
            details=data.get("details", {}),
            reason=data.get("reason", ""),
        )


# Export all public classes
__all__ = [
    "OverallStatus",
    "SourceInfo",
    "Algorithm",
    "Verdict",
    "KpiRecord",
    "RegressionFinding",
    "TestSummary",
    "ConfigSummary",
    "BaselineSummary",
    "AnalysisSummary",
    "ReportMetadata",
    "AnalysisSection",
    "TestedSection",
    "OverallSection",
    "InputDataSection",
    "ResultEntry",
    "ResultLabels",
    "ResultCurrentValue",
    "ResultBaselineValue",
    "RegressionReport",
    "KpiCatalogEntry",
    "CaliperTestMetadata",
    "CurrentValueInfo",
    "RegressionTestResult",
]
