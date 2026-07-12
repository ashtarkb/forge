"""MCP Gateway KPI analysis: version-aware, config-aware regression detection.

Comparison rules (matching the MLflow-based logic):
- Never compare the same version against itself
- SHA-based versions only compare against other SHA-based versions
- Semver versions only compare against other semver versions
- Runs must match by load config (num_servers + users)
- Runs must match by preset
- For matrix runs (multiple tests in one kpis.json), each test entry
  is compared independently against its matching historical counterpart
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class TestConfig:
    """Identifies a unique test configuration for matching."""

    preset: str
    num_servers: str
    users: str
    version: str
    is_sha: bool

    @property
    def load_config(self) -> str:
        """The load configuration key (equivalent to s<N>-u<N>)."""
        return f"s{self.num_servers}-u{self.users}"


@dataclass
class TestKPIs:
    """KPI values for a single test entry with its config."""

    config: TestConfig
    values: dict[str, float]
    labels: dict[str, Any]


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def analyze_kpis(
    current_kpis: dict[str, Any],
    historical_kpis: list[dict[str, Any]],
    output_dir: Path,
) -> dict[str, Any]:
    """Analyze current KPIs against historical data with full matching logic.

    Args:
        current_kpis: Parsed kpis.json from the current run (schema v2).
        historical_kpis: List of parsed kpis.json dicts from historical runs.
        output_dir: Directory where analysis output files should be written.

    Returns:
        Analysis result dict with status and findings.
    """
    if not historical_kpis:
        return {
            "status": "warning",
            "message": "no historical KPI data available for analysis",
        }

    current_tests = _extract_tests(current_kpis)
    if not current_tests:
        return {
            "status": "warning",
            "message": "no KPI values found in current run",
        }

    historical_tests = []
    for hist_data in historical_kpis:
        historical_tests.extend(_extract_tests(hist_data))

    if not historical_tests:
        return {
            "status": "warning",
            "message": "no valid historical test entries found",
        }

    is_matrix = len(current_tests) > 1
    all_findings = []

    for current_test in current_tests:
        baseline = _find_matching_baseline(current_test, historical_tests)
        if not baseline:
            logger.info(
                "No matching baseline for config=%s preset=%s version_type=%s",
                current_test.config.load_config,
                current_test.config.preset,
                "sha" if current_test.config.is_sha else "semver",
            )
            continue

        findings = _compare_kpis(current_test, baseline)
        all_findings.append({
            "load_config": current_test.config.load_config,
            "preset": current_test.config.preset,
            "current_version": current_test.config.version,
            "baseline_version": baseline.config.version,
            "kpis": findings,
        })

    if not all_findings:
        return {
            "status": "warning",
            "message": "no matching historical runs found for comparison",
            "current_configs": [t.config.load_config for t in current_tests],
            "current_version": current_tests[0].config.version if current_tests else "",
        }

    regressions = []
    for group in all_findings:
        for kpi in group["kpis"]:
            if kpi["status"] == "regression":
                regressions.append({
                    "load_config": group["load_config"],
                    "kpi_id": kpi["kpi_id"],
                    "delta_pct": kpi["delta_pct"],
                })

    return {
        "status": "success",
        "is_matrix": is_matrix,
        "comparisons": all_findings,
        "regressions_count": len(regressions),
        "regressions": regressions,
        "completed_at": time.time(),
    }


# ---------------------------------------------------------------------------
# Extraction helpers
# ---------------------------------------------------------------------------


def _is_sha_version(version: str) -> bool:
    """Determine if a version string is SHA-based."""
    if not version:
        return False
    if version.startswith("sha-"):
        return True
    # Pure hex string of sufficient length (40 chars for full SHA, or 7+ for short)
    if len(version) >= 7 and all(c in "0123456789abcdef" for c in version):
        return True
    return False


def _normalize_version(version: str) -> str:
    """Strip common prefixes for comparison."""
    if version.startswith("sha-"):
        return version[4:]
    return version.lstrip("v")


def _extract_tests(kpis_data: dict[str, Any]) -> list[TestKPIs]:
    """Extract all test entries from a kpis.json as TestKPIs objects."""
    results = []

    for test in kpis_data.get("tests", []):
        labels = test.get("labels", {})
        version = str(labels.get("mcp_gateway_version", ""))

        config = TestConfig(
            preset=str(labels.get("preset", "")),
            num_servers=str(labels.get("num_servers", "")),
            users=str(labels.get("users", "")),
            version=version,
            is_sha=_is_sha_version(version),
        )

        values: dict[str, float] = {}
        for kpi in test.get("kpis", []):
            kpi_id = kpi.get("id")
            value = kpi.get("value")
            if kpi_id and value is not None:
                values[kpi_id] = float(value)

        if values:
            results.append(TestKPIs(config=config, values=values, labels=labels))

    return results


# ---------------------------------------------------------------------------
# Matching logic
# ---------------------------------------------------------------------------


def _find_matching_baseline(
    current: TestKPIs,
    historical_tests: list[TestKPIs],
) -> TestKPIs | None:
    """Find the best matching historical test entry.

    Matching rules:
    1. Same preset
    2. Same load config (num_servers + users)
    3. Same version type (SHA vs semver)
    4. Different version (skip duplicates)
    """
    for hist in historical_tests:
        # Must match preset
        if hist.config.preset != current.config.preset:
            continue

        # Must match load config (num_servers + users)
        if hist.config.load_config != current.config.load_config:
            continue

        # Must match version type (SHA vs semver)
        if hist.config.is_sha != current.config.is_sha:
            continue

        # Must be a different version
        cur_normalized = _normalize_version(current.config.version)
        hist_normalized = _normalize_version(hist.config.version)
        if cur_normalized == hist_normalized:
            continue

        return hist

    return None


# ---------------------------------------------------------------------------
# Comparison logic
# ---------------------------------------------------------------------------

# Threshold for marking a change as regression or improvement (percentage)
REGRESSION_THRESHOLD_PCT = 5.0


def _compare_kpis(
    current: TestKPIs,
    baseline: TestKPIs,
) -> list[dict[str, Any]]:
    """Compare current KPIs against baseline and detect regressions."""
    findings = []

    for kpi_id, cur_val in current.values.items():
        base_val = baseline.values.get(kpi_id)
        if base_val is None:
            continue

        # Get direction from the kpis.json data
        higher_is_better = _get_kpi_direction(current, kpi_id)

        if base_val == 0:
            delta_pct = 0.0
        else:
            delta_pct = ((cur_val - base_val) / abs(base_val)) * 100

        status = _classify_change(delta_pct, higher_is_better)

        findings.append({
            "kpi_id": kpi_id,
            "current_value": cur_val,
            "baseline_value": base_val,
            "delta_pct": round(delta_pct, 2),
            "higher_is_better": higher_is_better,
            "status": status,
        })

    return findings


def _get_kpi_direction(test: TestKPIs, kpi_id: str) -> bool:
    """Look up higher_is_better for a KPI from the raw test data."""
    # We stored labels but not the full kpi metadata in TestKPIs.
    # Re-check via a known mapping for mcp_gateway KPIs.
    _LOWER_IS_BETTER = {
        "mcp_gw_avg_response_time_ms",
        "mcp_gw_p50_ms",
        "mcp_gw_p95_ms",
        "mcp_gw_p99_ms",
        "mcp_gw_failure_rate",
    }
    return kpi_id not in _LOWER_IS_BETTER


def _classify_change(delta_pct: float, higher_is_better: bool) -> str:
    """Classify a percentage change as regression, improvement, or stable."""
    if abs(delta_pct) < REGRESSION_THRESHOLD_PCT:
        return "stable"

    if higher_is_better:
        return "improvement" if delta_pct > 0 else "regression"
    else:
        return "improvement" if delta_pct < 0 else "regression"
