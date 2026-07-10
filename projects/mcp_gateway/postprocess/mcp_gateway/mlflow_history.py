"""
Shared MLflow history querying for MCP Gateway.

Provides comparison logic for both regular (single) and matrix (nested) runs.
Used by notifications and historical data import for regression analysis.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypedDict

from projects.core.library import config

logger = logging.getLogger(__name__)

_RUN_NAME_PREFIX = "forge-mcp-gateway-"
_CHILD_PREFIX_RE = re.compile(r"^\d{3}__")


# ---------------------------------------------------------------------------
# Typed result structures
# ---------------------------------------------------------------------------


class RegularComparison(TypedDict, total=False):
    type: str
    skip: bool
    current_metrics: dict[str, float]
    previous_metrics: dict[str, float]
    previous_run_name: str


class MatrixComparisonEntry(TypedDict, total=False):
    config: str
    current_metrics: dict[str, float]
    previous_metrics: dict[str, float]
    current_child_name: str
    previous_child_name: str


class MatrixComparison(TypedDict, total=False):
    type: str
    skip: bool
    comparisons: list[MatrixComparisonEntry]
    previous_run_name: str


ComparisonResult = RegularComparison | MatrixComparison


# ---------------------------------------------------------------------------
# MLflow connection
# ---------------------------------------------------------------------------


@dataclass
class MLflowConnection:
    """Resolved MLflow connection details."""

    secrets: Any = field(repr=False)
    experiment_name: str = ""


def get_mlflow_connection() -> MLflowConnection | None:
    """Connect to MLflow using vault secrets.

    Returns an MLflowConnection on success, or None if config/secrets are unavailable.
    """
    try:
        from projects.caliper.engine.file_export.mlflow_secrets import (
            load_mlflow_secrets_yaml,
        )
        from projects.core.library import vault as vault_lib

        vault_name = config.project.get_config(
            "caliper.export.backend.mlflow.secrets.vault.name", None, print=False, warn=False
        )
        vault_secret = config.project.get_config(
            "caliper.export.backend.mlflow.secrets.vault.mlflow_secret",
            None,
            print=False,
            warn=False,
        )
        experiment_name = config.project.get_config(
            "caliper.export.backend.mlflow.config.experiment", None, print=False, warn=False
        )

        if not all([vault_name, vault_secret, experiment_name]):
            logger.info("MLflow config incomplete")
            return None

        secrets_path = vault_lib.get_vault_content_path(vault_name, vault_secret)
        if not secrets_path or not secrets_path.exists():
            logger.info("MLflow secrets not available")
            return None

        secrets = load_mlflow_secrets_yaml(secrets_path)
        return MLflowConnection(secrets=secrets, experiment_name=experiment_name)

    except Exception as e:
        logger.warning("Failed to get MLflow connection: %s", type(e).__name__)
        return None


# ---------------------------------------------------------------------------
# Run name parsing
# ---------------------------------------------------------------------------


def parse_run_name(run_name: str) -> dict | None:
    """Parse a forge-mcp-gateway run name into components.

    Returns dict with keys: config, is_sha, version, preset_in_name
    - config: e.g. "s150-u500" for regular runs, None for matrix parents
    - is_sha: True if version is a SHA
    - version: the version string or SHA hash
    - preset_in_name: preset extracted from parent run name (for matrix runs)

    Examples of supported run name formats:

        Regular runs (contain a config like s<N>-u<N>):
          "forge-mcp-gateway-s150-u500-v1.2.3-20240301-120000"
            -> config="s150-u500", version="1.2.3", is_sha=False
          "forge-mcp-gateway-s1-u500-vsha-abc123-20240301-120000"
            -> config="s1-u500", version="abc123", is_sha=True

        Matrix parent runs (no config, have preset):
          "forge-mcp-gateway-stress-sha-abc123-20240301-120000"
            -> config=None, preset_in_name="stress", version="abc123", is_sha=True
          "forge-mcp-gateway-baseline-v2.0.0-20240301-120000"
            -> config=None, preset_in_name="baseline", version="2.0.0", is_sha=False

        Child runs (prefixed with NNN__):
          "001__forge-mcp-gateway-s150-u500-v1.2.3-20240301-120000"
            -> same as the regular run after stripping prefix
    """
    name = _CHILD_PREFIX_RE.sub("", run_name)

    if not name.startswith(_RUN_NAME_PREFIX):
        return None

    rest = name[len(_RUN_NAME_PREFIX):]

    config_str = None
    config_match = re.match(r"(s\d+-u\d+)-", rest)
    if config_match:
        config_str = config_match.group(1)
        rest = rest[config_match.end():]

    is_sha = False
    version = None
    preset_in_name = None

    if config_str is None:
        preset_match = re.match(r"(.+?)-(sha-[a-f0-9]+|v?.+?)-\d{8}-\d{6}$", rest)
        if preset_match:
            preset_in_name = preset_match.group(1)
            version_part = preset_match.group(2)
            if version_part.startswith("sha-"):
                is_sha = True
                version = version_part[4:]
            else:
                version = version_part.lstrip("v")
        else:
            version_match = re.match(r"(.+?)-\d{8}-\d{6}$", rest)
            if version_match:
                preset_in_name = version_match.group(1)
    else:
        if rest.startswith("vsha-"):
            is_sha = True
            sha_match = re.match(r"vsha-([a-f0-9]+)-\d{8}-\d{6}$", rest)
            if sha_match:
                version = sha_match.group(1)
        else:
            version_match = re.match(r"v?(.+?)-\d{8}-\d{6}$", rest)
            if version_match:
                version = version_match.group(1)

    return {
        "config": config_str,
        "is_sha": is_sha,
        "version": version,
        "preset_in_name": preset_in_name,
    }


def is_matrix_run(parsed: dict) -> bool:
    """A matrix run has no config (s<N>-u<N>) and has a preset in the name."""
    return parsed.get("config") is None and parsed.get("preset_in_name") is not None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _get_run_name(run) -> str:
    return getattr(run.info, "run_name", "") or run.info.run_id[:8]


def _build_mlflow_to_kpi(kpi_to_mlflow_metric: dict[str, str]) -> dict[str, str]:
    """Invert the kpi->mlflow mapping once for reuse."""
    return {v: k for k, v in kpi_to_mlflow_metric.items()}


def _extract_kpi_metrics(
    run, mlflow_to_kpi: dict[str, str],
) -> dict[str, float]:
    """Extract KPI values from a run's metrics using the pre-computed inverted mapping."""
    raw_metrics = run.data.metrics or {}
    result = {}
    for mlflow_key, value in raw_metrics.items():
        kpi_id = mlflow_to_kpi.get(mlflow_key)
        if kpi_id:
            result[kpi_id] = value
    return result


# ---------------------------------------------------------------------------
# Query previous runs (for notifications)
# ---------------------------------------------------------------------------


def query_previous_runs(
    current_run_id: str | None = None,
    kpi_to_mlflow_metric: dict[str, str] | None = None,
) -> ComparisonResult:
    """Query MLflow for comparison data, handling both regular and matrix runs.

    Returns a typed dict with:
      - "type": "regular" | "matrix" | "none"
      - "skip": True if duplicate (same version already notified)

    For "regular":
      - "current_metrics": {kpi_id: value}
      - "previous_metrics": {kpi_id: value}
      - "previous_run_name": str

    For "matrix":
      - "comparisons": list of dicts, each with:
          - "config": str (e.g. "s1-u500")
          - "current_metrics": {kpi_id: value}
          - "previous_metrics": {kpi_id: value}
          - "current_child_name": str
          - "previous_child_name": str
    """
    if kpi_to_mlflow_metric is None:
        kpi_to_mlflow_metric = {}

    conn = get_mlflow_connection()
    if conn is None:
        return {"type": "none", "skip": False}

    mlflow_to_kpi = _build_mlflow_to_kpi(kpi_to_mlflow_metric)

    try:
        from projects.caliper.engine.file_export.mlflow_secrets import mlflow_connection_env

        with mlflow_connection_env(conn.secrets):
            import mlflow

            client = mlflow.tracking.MlflowClient()
            exp = client.get_experiment_by_name(conn.experiment_name)
            if not exp:
                logger.info("MLflow experiment '%s' not found", conn.experiment_name)
                return {"type": "none", "skip": False}

            runs = client.search_runs(
                experiment_ids=[exp.experiment_id],
                order_by=["start_time DESC"],
                max_results=100,
            )

            if not runs:
                return {"type": "none", "skip": False}

            current_run = _find_current_run(runs, current_run_id)
            if current_run is None:
                return {"type": "none", "skip": False}

            current_name = _get_run_name(current_run)
            current_parsed = parse_run_name(current_name)
            if not current_parsed:
                logger.info("Cannot parse current run name '%s'", current_name)
                return {"type": "none", "skip": False}

            current_preset = (current_run.data.params or {}).get("preset", "")

            if is_matrix_run(current_parsed):
                return _compare_matrix_runs(
                    client, exp.experiment_id, runs,
                    current_run, current_parsed, current_preset,
                    mlflow_to_kpi,
                )
            else:
                return _compare_regular_runs(
                    runs, current_run, current_parsed, current_preset,
                    mlflow_to_kpi,
                )

    except Exception as e:
        logger.warning("MLflow comparison unavailable: %s", type(e).__name__)
        return {"type": "none", "skip": False}


def _find_current_run(runs, current_run_id: str | None):
    """Locate the current run by ID or fall back to most recent."""
    if current_run_id:
        for run in runs:
            if run.info.run_id == current_run_id:
                return run
        logger.info("Current run_id '%s' not found, falling back to runs[0]", current_run_id)
    return runs[0] if runs else None


def _compare_regular_runs(
    runs, current_run, current_parsed, current_preset, mlflow_to_kpi,
) -> ComparisonResult:
    """Compare a regular (non-matrix) run against its previous match."""
    for run in runs:
        if run.info.run_id == current_run.info.run_id:
            continue
        candidate_name = _get_run_name(run)
        candidate_parsed = parse_run_name(candidate_name)
        if not candidate_parsed:
            continue
        if candidate_parsed["config"] != current_parsed["config"]:
            continue
        if candidate_parsed["is_sha"] != current_parsed["is_sha"]:
            continue
        candidate_preset = (run.data.params or {}).get("preset", "")
        if candidate_preset != current_preset:
            continue

        if candidate_parsed["version"] == current_parsed["version"]:
            logger.info("Previous run has same version '%s', skip", current_parsed["version"])
            return {"type": "regular", "skip": True}

        return {
            "type": "regular",
            "skip": False,
            "current_metrics": _extract_kpi_metrics(current_run, mlflow_to_kpi),
            "previous_metrics": _extract_kpi_metrics(run, mlflow_to_kpi),
            "previous_run_name": candidate_name,
        }

    logger.info("No previous regular run matches config=%s, preset=%s",
                current_parsed["config"], current_preset)
    return {"type": "none", "skip": False}


def _compare_matrix_runs(
    client, experiment_id, runs,
    current_run, current_parsed, current_preset, mlflow_to_kpi,
) -> ComparisonResult:
    """Compare a matrix run's children against the previous matrix run's children."""
    previous_parent = None
    for run in runs:
        if run.info.run_id == current_run.info.run_id:
            continue
        candidate_name = _get_run_name(run)
        candidate_parsed = parse_run_name(candidate_name)
        if not candidate_parsed:
            continue
        if not is_matrix_run(candidate_parsed):
            continue
        if candidate_parsed["preset_in_name"] != current_parsed["preset_in_name"]:
            continue
        if candidate_parsed["is_sha"] != current_parsed["is_sha"]:
            continue

        if candidate_parsed["version"] == current_parsed["version"]:
            logger.info("Previous matrix run has same version '%s', skip", current_parsed["version"])
            return {"type": "matrix", "skip": True, "comparisons": []}

        previous_parent = run
        break

    if previous_parent is None:
        logger.info("No previous matrix run for preset=%s", current_parsed["preset_in_name"])
        return {"type": "matrix", "skip": False, "comparisons": []}

    current_children = _get_child_runs(client, experiment_id, current_run.info.run_id)
    previous_children = _get_child_runs(client, experiment_id, previous_parent.info.run_id)

    prev_by_config: dict[str, Any] = {}
    for child in previous_children:
        child_name = _get_run_name(child)
        child_parsed = parse_run_name(child_name)
        if child_parsed and child_parsed["config"]:
            prev_by_config[child_parsed["config"]] = child

    comparisons: list[MatrixComparisonEntry] = []
    for child in sorted(current_children, key=lambda r: _get_run_name(r)):
        child_name = _get_run_name(child)
        child_parsed = parse_run_name(child_name)
        if not child_parsed or not child_parsed["config"]:
            continue

        cfg = child_parsed["config"]
        prev_child = prev_by_config.get(cfg)

        comparison: MatrixComparisonEntry = {
            "config": cfg,
            "current_metrics": _extract_kpi_metrics(child, mlflow_to_kpi),
            "current_child_name": child_name,
        }

        if prev_child:
            comparison["previous_metrics"] = _extract_kpi_metrics(prev_child, mlflow_to_kpi)
            comparison["previous_child_name"] = _get_run_name(prev_child)
        else:
            comparison["previous_metrics"] = {}
            comparison["previous_child_name"] = ""

        comparisons.append(comparison)

    return {
        "type": "matrix",
        "skip": False,
        "comparisons": comparisons,
        "previous_run_name": _get_run_name(previous_parent),
    }


def _get_child_runs(client, experiment_id: str, parent_run_id: str) -> list:
    """Query child runs nested under a parent."""
    return client.search_runs(
        experiment_ids=[experiment_id],
        filter_string=f"tags.mlflow.parentRunId = '{parent_run_id}'",
        order_by=["start_time ASC"],
        max_results=200,
    )


# ---------------------------------------------------------------------------
# Write historical KPIs (for regression analysis)
# ---------------------------------------------------------------------------


def write_historical_kpis(
    output_dir: Path,
    current_run_id: str | None = None,
    kpi_to_mlflow_metric: dict[str, str] | None = None,
    max_runs: int = 10,
) -> int:
    """Query MLflow for historical runs and write kpis.json files for analyze.

    Writes one kpis.json per historical data point into output_dir.
    For regular runs: one file per previous matching run.
    For matrix runs: files are grouped by config subdirectory to avoid
    cross-config pollution in regression analysis.

    Returns number of historical KPI files written.
    """
    if kpi_to_mlflow_metric is None:
        kpi_to_mlflow_metric = {}

    conn = get_mlflow_connection()
    if conn is None:
        return 0

    mlflow_to_kpi = _build_mlflow_to_kpi(kpi_to_mlflow_metric)

    try:
        from projects.caliper.engine.file_export.mlflow_secrets import mlflow_connection_env

        with mlflow_connection_env(conn.secrets):
            import mlflow

            client = mlflow.tracking.MlflowClient()
            exp = client.get_experiment_by_name(conn.experiment_name)
            if not exp:
                return 0

            runs = client.search_runs(
                experiment_ids=[exp.experiment_id],
                order_by=["start_time DESC"],
                max_results=200,
            )

            if not runs:
                return 0

            current_run = _find_current_run(runs, current_run_id)
            if current_run is None:
                return 0

            current_name = _get_run_name(current_run)
            current_parsed = parse_run_name(current_name)
            if not current_parsed:
                return 0

            current_preset = (current_run.data.params or {}).get("preset", "")
            output_dir.mkdir(parents=True, exist_ok=True)

            if is_matrix_run(current_parsed):
                return _write_matrix_history(
                    client, exp.experiment_id, runs, output_dir,
                    current_run, current_parsed, mlflow_to_kpi, max_runs,
                )
            else:
                return _write_regular_history(
                    runs, output_dir,
                    current_run, current_parsed, current_preset,
                    mlflow_to_kpi, max_runs,
                )

    except Exception as e:
        logger.warning("Failed to write historical KPIs: %s", type(e).__name__)
        return 0


def _kpi_metrics_to_kpis_json(metrics: dict[str, float], run_name: str = "", params: dict | None = None) -> dict:
    """Convert already-mapped KPI metrics to caliper kpis.json schema v2 format.

    Produces output compatible with caliper's analyze step which requires
    schema_version "2".
    """
    kpi_records = [{"id": kpi_id, "value": value} for kpi_id, value in metrics.items()]

    labels = {}
    if params:
        for key in ("preset", "target", "users", "num_servers", "mock_server",
                    "mcp_gateway_version", "tools_per_server"):
            if key in params:
                labels[key] = str(params[key])

    test_entry: dict[str, Any] = {
        "run_id": run_name or "historical",
        "labels": labels,
        "kpis": kpi_records,
    }

    return {
        "schema_version": "2",
        "tests": [test_entry],
    }


def _write_regular_history(
    runs, output_dir, current_run, current_parsed, current_preset,
    mlflow_to_kpi, max_runs,
) -> int:
    """Write historical KPI files for regular (non-matrix) runs.

    Only includes runs where ALL expected KPI metrics are present.
    Deduplicates by version -- keeps only the most recent run per version
    (runs are already ordered by start_time DESC from MLflow).
    """
    count = 0
    seen_versions: set[str] = set()
    required_kpis = set(mlflow_to_kpi.values())

    for run in runs:
        if count >= max_runs:
            break
        if run.info.run_id == current_run.info.run_id:
            continue
        candidate_name = _get_run_name(run)
        candidate_parsed = parse_run_name(candidate_name)
        if not candidate_parsed:
            continue
        if candidate_parsed["config"] != current_parsed["config"]:
            continue
        if candidate_parsed["is_sha"] != current_parsed["is_sha"]:
            continue
        candidate_preset = (run.data.params or {}).get("preset", "")
        if candidate_preset != current_preset:
            continue
        if candidate_parsed["version"] == current_parsed["version"]:
            continue

        version = candidate_parsed["version"]
        if version and version in seen_versions:
            continue
        if version:
            seen_versions.add(version)

        kpi_metrics = _extract_kpi_metrics(run, mlflow_to_kpi)
        if not kpi_metrics:
            continue
        if required_kpis and not required_kpis.issubset(kpi_metrics.keys()):
            logger.info("Skipping run '%s': incomplete KPIs (has %d/%d)",
                        candidate_name, len(kpi_metrics), len(required_kpis))
            continue

        run_name = _get_run_name(run)
        run_params = run.data.params or {}
        kpis_data = _kpi_metrics_to_kpis_json(kpi_metrics, run_name=run_name, params=run_params)
        run_dir = output_dir / f"run_{count:03d}"
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "kpis.json").write_text(json.dumps(kpis_data, indent=2))
        count += 1

    logger.info("Wrote %d historical KPI files (regular)", count)
    return count


def _write_matrix_history(
    client, experiment_id, runs, output_dir,
    current_run, current_parsed, mlflow_to_kpi, max_runs,
) -> int:
    """Write historical KPI files for matrix runs, grouped by config.

    Output structure:
        output_dir/<config>/run_000/kpis.json
        output_dir/<config>/run_001/kpis.json
        ...

    Only includes child runs where ALL expected KPI metrics are present.
    Deduplicates parent runs by version -- keeps only the most recent parent
    per version (runs are already ordered by start_time DESC from MLflow).

    This ensures regression analysis only compares data points
    from the same load configuration.
    """
    counters: dict[str, int] = {}
    parents_found = 0
    seen_versions: set[str] = set()
    required_kpis = set(mlflow_to_kpi.values())

    for run in runs:
        if parents_found >= max_runs:
            break
        if run.info.run_id == current_run.info.run_id:
            continue
        candidate_name = _get_run_name(run)
        candidate_parsed = parse_run_name(candidate_name)
        if not candidate_parsed:
            continue
        if not is_matrix_run(candidate_parsed):
            continue
        if candidate_parsed["preset_in_name"] != current_parsed["preset_in_name"]:
            continue
        if candidate_parsed["is_sha"] != current_parsed["is_sha"]:
            continue
        if candidate_parsed["version"] == current_parsed["version"]:
            continue

        version = candidate_parsed["version"]
        if version and version in seen_versions:
            continue
        if version:
            seen_versions.add(version)

        children = _get_child_runs(client, experiment_id, run.info.run_id)
        for child in children:
            child_name = _get_run_name(child)
            child_parsed = parse_run_name(child_name)
            if not child_parsed or not child_parsed["config"]:
                continue

            cfg = child_parsed["config"]
            kpi_metrics = _extract_kpi_metrics(child, mlflow_to_kpi)
            if not kpi_metrics:
                continue
            if required_kpis and not required_kpis.issubset(kpi_metrics.keys()):
                logger.info("Skipping child '%s': incomplete KPIs (has %d/%d)",
                            child_name, len(kpi_metrics), len(required_kpis))
                continue

            child_run_name = _get_run_name(child)
            child_params = child.data.params or {}
            kpis_data = _kpi_metrics_to_kpis_json(kpi_metrics, run_name=child_run_name, params=child_params)

            cfg_count = counters.get(cfg, 0)
            run_dir = output_dir / cfg / f"run_{cfg_count:03d}"
            run_dir.mkdir(parents=True, exist_ok=True)
            (run_dir / "kpis.json").write_text(json.dumps(kpis_data, indent=2))
            counters[cfg] = cfg_count + 1

        parents_found += 1

    total = sum(counters.values())
    logger.info("Wrote %d historical KPI files (matrix, %d parent runs, %d configs)",
                total, parents_found, len(counters))
    return total
