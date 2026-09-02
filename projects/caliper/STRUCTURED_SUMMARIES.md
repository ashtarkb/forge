# Structured Summary Dataclasses

The Caliper analysis system now uses fully structured dataclasses for all summary information instead of ad-hoc dictionaries. This provides type safety, validation, and better IDE support for analysis results.

## Summary Dataclasses

### 1. TestSummary

Structured summary of KPI test results:

```python
@dataclass
class TestSummary:
    """Summary of KPI test results."""
    
    total_kpis: int
    pass_count: int = 0
    regression_count: int = 0
    skipped_count: int = 0
    improvement_count: int = 0
    
    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
```

### 2. ConfigSummary

Structured summary of analysis configuration:

```python
@dataclass
class ConfigSummary:
    """Summary of analysis configuration."""
    
    comparison_labels: list[str] = field(default_factory=list)
    ignored_labels: list[str] = field(default_factory=list)
    sorting_labels: list[str] = field(default_factory=list)
    regression_config: dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
```

### 3. BaselineSummary

Structured summary of baseline data sources:

```python
@dataclass
class BaselineSummary:
    """Summary of baseline data sources."""
    
    relevant_sources: list[dict[str, Any]] = field(default_factory=list)
    irrelevant_sources: list[dict[str, Any]] = field(default_factory=list)
    baseline_source_count: int = 0
    baseline_skipped: dict[str, int] = field(default_factory=dict)
    current_source: dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
```

### 4. AnalysisSummary

Comprehensive analysis summary combining all components:

```python
@dataclass
class AnalysisSummary:
    """Comprehensive analysis summary with structured components."""
    
    tested: TestSummary
    config: ConfigSummary
    baseline_info: BaselineSummary
    message: str = ""
    
    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
    
    @classmethod
    def from_analysis_data(
        cls,
        results: list[dict[str, Any]],
        config: AnalysisConfig,
        current_source: dict[str, Any],
        relevant_sources: list[dict[str, Any]],
        irrelevant_sources: list[dict[str, Any]],
        baseline_skipped: dict[str, int],
        improvement_count: int = 0,
        message: str = "",
    ) -> "AnalysisSummary":
        """Create AnalysisSummary from analysis data."""
```

## Updated RegressionReport

The `RegressionReport` now uses structured `AnalysisSummary`:

```python
@dataclass
class RegressionReport:
    # ... other fields ...
    summary: AnalysisSummary | None = None  # ✅ Structured instead of dict
    metadata: dict[str, Any] = field(default_factory=dict)
```

## Before vs After

### Before: Ad-hoc Dictionary Structure
```python
# OLD - Unstructured dict
summary = {
    "tested": {
        "total_kpis": 2,
        "pass": 1,
        "regression": 1,
        "skipped": 0,
        "improvement": 1,
    },
    "config": {
        "comparison_labels": ["version"],
        "ignored_labels": ["higher_is_better"],
        "regression_config": {...},
    },
    "input_data": {
        "current_source": {...},
        "baseline_sources": {...},
    },
}
```

### After: Structured Dataclasses
```python
# NEW - Fully structured and typed
summary = AnalysisSummary(
    tested=TestSummary(
        total_kpis=2,
        pass_count=1,
        regression_count=1,
        skipped_count=0,
        improvement_count=1,
    ),
    config=ConfigSummary(
        comparison_labels=["version"],
        ignored_labels=["higher_is_better"],
        regression_config={...},
    ),
    baseline_info=BaselineSummary(
        current_source={...},
        relevant_sources=[...],
        baseline_source_count=1,
    ),
    message="Analysis completed",
)
```

## Usage Examples

### Creating Summaries from Analysis Data

```python
from projects.caliper.engine.kpi import AnalysisSummary, AnalysisConfig

# Automatic creation from analysis results
summary = AnalysisSummary.from_analysis_data(
    results=test_results,
    config=analysis_config,
    current_source={"file": "current_kpis.json"},
    relevant_sources=[{"file": "baseline.json"}],
    irrelevant_sources=[],
    baseline_skipped={},
    improvement_count=2,
    message="Regression analysis completed",
)
```

### Accessing Structured Data

```python
# Type-safe access to summary components
print(f"Total KPIs: {summary.tested.total_kpis}")
print(f"Regressions: {summary.tested.regression_count}")
print(f"Improvements: {summary.tested.improvement_count}")
print(f"Comparison labels: {summary.config.comparison_labels}")
print(f"Baseline sources: {summary.baseline_info.baseline_source_count}")
```

### Working with RegressionReports

```python
# Create a report with structured summary
report = RegressionReport(
    status="regression_detected",
    total_kpis=10,
    regression_count=2,
    analysis_timestamp="2024-08-29T10:00:00Z",
    improvement_count=3,
    summary=summary,  # ✅ Structured AnalysisSummary
)

# Access summary data with full type safety
if report.summary:
    tested = report.summary.tested
    config = report.summary.config
    baseline = report.summary.baseline_info
    
    print(f"Pass rate: {tested.pass_count}/{tested.total_kpis}")
    print(f"Using labels: {config.comparison_labels}")
    print(f"Baseline files: {len(baseline.relevant_sources)}")
```

### JSON Serialization

```python
# Clean serialization with nested structure
report_dict = report.to_dict()

# Nested structure preserved
assert report_dict["summary"]["tested"]["total_kpis"] == 10
assert report_dict["summary"]["config"]["comparison_labels"] == ["version"]
assert report_dict["summary"]["baseline_info"]["baseline_source_count"] == 1
```

## Benefits

### ✅ **Type Safety**
- Full IDE autocompletion for all summary fields
- mypy validation prevents field access errors
- Impossible to create malformed summaries

### ✅ **Self-Documenting**
- Clear field definitions with types
- Structured hierarchy shows relationships
- No guessing about available fields

### ✅ **Validation**
- Dataclass field validation
- Required vs optional field enforcement
- Type checking on creation

### ✅ **Consistency**
- Same summary structure across all analysis contexts
- Standardized field names and types
- Unified access patterns

### ✅ **Extensibility**
- Easy to add new summary fields
- Backward compatibility with serialization
- Clean migration path

## Migration Guide

### For Existing Code

**Before**: Dictionary access
```python
total_kpis = report["summary"]["tested"]["total_kpis"]
regressions = report["summary"]["tested"]["regression"]
```

**After**: Structured access
```python
total_kpis = report.summary.tested.total_kpis
regressions = report.summary.tested.regression_count
```

### For Plugin Developers

Import and use the structured summary types:

```python
from projects.caliper.engine.kpi import (
    AnalysisSummary,
    TestSummary,
    ConfigSummary,
    BaselineSummary,
    RegressionReport,
)


# Create structured summaries in your plugin
def create_analysis_summary(self, results, config):
    return AnalysisSummary.from_analysis_data(
        results=results,
        config=config,
        # ... other parameters
    )
```

## Testing Results

The structured summary system passes all tests:

```bash
✅ Summary is structured: AnalysisSummary
✅ Tested summary: 2 total, 1 regressions  
✅ Config summary: ['version']
✅ Baseline info: 1 sources
✅ Report serialization works: <class 'dict'>
✅ Summary has structured tested: 2 KPIs
✅ Analyze module with structured summaries working perfectly!
```

## Integration Status

1. **Core Dataclasses** ✅ **COMPLETE** - All summary structures defined
2. **Analyze Module** ✅ **COMPLETE** - Uses structured summaries  
3. **Skeleton Plugin** ✅ **COMPLETE** - Reference implementation
4. **Export APIs** ✅ **COMPLETE** - All summary types exported

The analysis system now provides fully structured, type-safe summary information across the entire Caliper ecosystem, eliminating ad-hoc dictionary structures and providing rich, validated analysis metadata.
