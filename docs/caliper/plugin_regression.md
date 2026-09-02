# KPI Regression Analysis Configuration

This document explains how to configure KPI regression analysis in Caliper plugins. Regression analysis compares current KPI values against historical baselines to detect performance regressions.

## Naming Convention

Throughout this document and the codebase:
- **labels** — a list of label **names** (strings), e.g. `["version", "platform"]`
- **keys** — a dict of label **key/value pairs**, e.g. `{"version": "1.0", "platform": "OCP"}`

## Overview

KPI regression analysis:
- Detects when KPIs deteriorate compared to historical baselines
- Compares current results against previous versions or configurations
- Enforces acceptable thresholds for performance changes
- Produces detailed JSON reports with per-KPI verdicts

## AnalysisConfig

Every plugin must provide an `AnalysisConfig`:

```python
@dataclass
class AnalysisConfig:
    comparison_labels: list[str] = field(default_factory=list)
    ignored_labels: list[str] = field(default_factory=list)
    sorting_labels: list[str] = field(default_factory=list)
    max_relative_regression: float = 0.1
    min_baseline_points: int = 1
```

### Fields

**`comparison_labels`** *(default: `[]`)*
Label names whose values are expected to differ between current and baseline. Baselines must have at least one differing comparison label to be eligible.
Example: `["product_version"]` — compare the current version against older versions.

**`ignored_labels`** *(default: `[]`)*
Label names excluded from the match key. Baselines with different values for these labels are still matched.
Example: `["cluster", "hostname"]` — ignore which machine ran the test.

**`sorting_labels`** *(default: `[]`)*
Label names used to order result entries in the report.
Example: `["deployment_profile", "model_name"]`

**`max_relative_regression`** *(default: `0.1`)*
Relative change threshold above which a regression is flagged (0.1 = 10%).
- `higher_is_better=True`: regression when `relative_change < -threshold`
- `higher_is_better=False`: regression when `relative_change > threshold`

**`min_baseline_points`** *(default: `1`)*
Minimum number of scalar baseline data points required. KPIs with fewer baselines are skipped.

## Providing Configuration in Plugins

### Static attribute

```python
from projects.caliper.engine.kpi.analyze import AnalysisConfig

analysis_config = AnalysisConfig(
    comparison_labels=["product_version"],
    ignored_labels=["cluster", "hostname"],
    sorting_labels=["deployment_profile", "model_name"],
    max_relative_regression=0.1,
    min_baseline_points=2,
)
```

### Dynamic function

```python
from projects.caliper.engine.kpi.analyze import AnalysisConfig


def get_analysis_config() -> AnalysisConfig:
    return AnalysisConfig(
        comparison_labels=["product_version"],
        ignored_labels=["cluster"],
        max_relative_regression=0.05,
    )
```

### Dictionary (auto-converted)

```python
analysis_config = {
    "comparison_labels": ["product_version"],
    "ignored_labels": ["cluster"],
    "max_relative_regression": 0.1,
}
```

## How Matching Works

For each current KPI record, baselines are looked up by a **match key** built from the record's labels, with `ignored_labels` and `comparison_labels` excluded. A baseline must:

1. Share the same match key (same values for all non-excluded labels)
2. Differ on at least one `comparison_labels` value
3. Not have label names absent from the current data (unexpected labels)
4. Not have label values absent from the current data for non-excluded labels (irrelevant values)

### Example

Config:
```python
AnalysisConfig(
    comparison_labels=["product_version"],
    ignored_labels=["cluster"],
    max_relative_regression=0.1,
)
```

Current record:
```json
{
  "kpi_id": "throughput",
  "value": 90.0,
  "labels": {"product_version": "v2.0", "deployment_profile": "simple", "cluster": "worker-1"}
}
```

Matching baselines (same `deployment_profile`, different `product_version`, `cluster` ignored):
```json
[
  {"value": 100.0, "labels": {"product_version": "v1.9", "deployment_profile": "simple", "cluster": "worker-2"}},
  {"value": 85.0,  "labels": {"product_version": "v1.8", "deployment_profile": "simple", "cluster": "worker-1"}}
]
```

Result:
- Baseline mean: 92.5
- Relative change: (90.0 − 92.5) / 92.5 = −2.7% → **PASS** (below 10% threshold)

## Report Structure

The KPI analysis report uses dataclasses with a flatter structure:

```json
{
  "status": "PASS",
  "regression_count": 0,
  "summary": {
    "tested": {
      "total_kpis": 1,
      "pass_count": 1,
      "skipped_count": 0
    }
  },
  "findings": [
    {
      "kpi_id": "throughput",
      "labels": {"product_version": "v2.0", "deployment_profile": "simple"},
      "comparison_keys": {"product_version": "v2.0"},
      "is_regression": false,
      "higher_is_better": true,
      "baseline_count": 2,
      "current_value": {"comparison_keys": {"product_version": "v2.0"}, "value": 90.0},
      "baseline_values": [
        {"comparison_keys": {"product_version": "v1.9"}, "value": 100.0},
        {"comparison_keys": {"product_version": "v1.8"}, "value": 85.0}
      ],
      "baseline_mean": 92.5,
      "relative_change": -0.027
    }
  ]
}
```

### Key Changes from Previous Format

- **Top-level status**: Uses `OverallStatus` enum (`PASS`, `REGRESSION_DETECTED`, `NO_BASELINE`)
- **Flatter structure**: Moved from nested `analysis`/`overall` to top-level fields
- **Boolean flags**: `is_regression` replaces string `verdict` field
- **Summary restructuring**: Test counts moved to `summary.tested` object
- **Findings array**: Report details moved from `results` to `findings`
- **Direct fields**: `relative_change` and other details moved to top level of findings

SKIPPED entries (non-scalar value, insufficient baselines) are excluded from `findings` but counted in `summary.tested.skipped_count`.

## Orchestration Integration

```yaml
caliper:
  postprocess:
    analyze:
      enabled: true
      fail_on_regression: false
      current_kpis: kpis/kpis.json
      historical_kpis: historical_data
      output: regression_analyze/kpi_analyze.json
```

When `analyze.enabled: true`, Caliper:
1. Loads the plugin's `AnalysisConfig`
2. Filters baseline entries that are irrelevant to the current data
3. Runs regression tests and generates a JSON report
4. Optionally fails the step if regressions are detected (`fail_on_regression: true`)
