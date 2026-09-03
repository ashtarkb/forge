"""Shared GuideLLM helpers for dashboard-compatible CSV exports."""

from __future__ import annotations

import csv
import json
import logging
import re
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from projects.caliper.engine.kpi import (
    KpiCatalogEntry,
    KpiRecord,
    SourceInfo,
)
from projects.caliper.engine.model import (
    ParseResult,
    TestBaseNode,
    UnifiedResultRecord,
    UnifiedRunModel,
)

logger = logging.getLogger(__name__)


# suffix, curve key, dashboard column, unit, higher-is-better
DASHBOARD_METRICS: tuple[tuple[str, str, str, str, bool | None], ...] = (
    ("output_tok_per_sec", "output_tok_per_sec", "output_tok/sec", "tokens/s", True),
    ("total_tok_per_sec", "total_tok_per_sec", "total_tok/sec", "tokens/s", True),
    ("measured_concurrency", "request_concurrency", "measured concurrency", "count", None),
    ("measured_rps", "measured_rps", "measured rps", "req/s", True),
    ("intended_concurrency", "intended_concurrency", "intended concurrency", "count", None),
    ("completed_requests", "successful_requests", "successful_requests", "count", True),
    ("failed_requests", "errored_requests", "errored_requests", "count", False),
    ("ttft_median", "ttft_median", "ttft_median", "s", False),
    ("ttft_p95", "ttft_p95", "ttft_p95", "s", False),
    ("ttft_p99", "ttft_p99", "ttft_p99", "s", False),
    ("ttft_p1", "ttft_p1", "ttft_p1", "s", False),
    ("ttft_p999", "ttft_p999", "ttft_p999", "s", False),
    ("ttft_mean", "ttft_mean", "ttft_mean", "s", False),
    ("tpot_median", "tpot_median", "tpot_median", "s", False),
    ("tpot_p95", "tpot_p95", "tpot_p95", "s", False),
    ("tpot_p99", "tpot_p99", "tpot_p99", "s", False),
    ("tpot_p1", "tpot_p1", "tpot_p1", "s", False),
    ("tpot_p999", "tpot_p999", "tpot_p999", "s", False),
    ("itl_median", "itl_median", "itl_median", "s", False),
    ("itl_p95", "itl_p95", "itl_p95", "s", False),
    ("itl_p99", "itl_p99", "itl_p99", "s", False),
    ("itl_p1", "itl_p1", "itl_p1", "s", False),
    ("itl_p999", "itl_p999", "itl_p999", "s", False),
    ("itl_mean", "itl_mean", "itl_mean", "s", False),
    ("request_latency_median", "request_latency_median", "request_latency_median", "s", False),
    ("request_latency_min", "request_latency_min", "request_latency_min", "s", False),
    ("request_latency_max", "request_latency_max", "request_latency_max", "s", False),
    (
        "prompt_token_count_mean",
        "prompt_token_count_mean",
        "prompt_token_count_mean",
        "tokens",
        None,
    ),
    ("prompt_token_count_p99", "prompt_token_count_p99", "prompt_token_count_p99", "tokens", None),
    (
        "output_token_count_mean",
        "output_token_count_mean",
        "output_token_count_mean",
        "tokens",
        None,
    ),
    ("output_token_count_p99", "output_token_count_p99", "output_token_count_p99", "tokens", None),
)

SECONDS_TO_MS_COLUMNS = frozenset(
    column
    for _, _, column, unit, _ in DASHBOARD_METRICS
    if unit == "s" and not column.startswith("request_latency_")
)

# Metadata that can be emitted as dashboard labels. Runtime/test labels are
# merged last by ``dashboard_metadata_labels`` and therefore take precedence
# over values recovered from artifacts.
DASHBOARD_METADATA_LABEL_KEYS = frozenset(
    {
        "product_version",
        "deployment_profile",
        "model_name",
        "hf_model_id",
        "cluster",
        "benchmark_key",
        "replicas",
        "tensor_parallel_size",
        "runtime_args",
        "image_tag",
        "router_config",
        "gpu_type",
        "mlflow_run_id",
        "mlflow_experiment_id",
    }
)


def canonical_json(value: Any) -> str:
    """Serialize structured metadata deterministically for labels and CSVs."""
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def normalize_product_version(value: Any) -> str:
    """Normalize RHOAI/KServe versions to the dashboard naming convention."""
    text = str(value or "")
    match = re.fullmatch(r"v(\d+)\.(\d+)\.\d+-ea\.(\d+)", text, re.IGNORECASE)
    if match:
        major, minor, early_access = match.groups()
        return f"RHOAI-{major}.{minor}-EA{early_access}"
    return text


def deployment_metadata_from_profile(
    profile: dict[str, Any], *, profile_name: str | None = None
) -> dict[str, Any]:
    """Extract shared deployment metadata from a resolved deployment profile."""
    metadata: dict[str, Any] = {}
    if profile_name:
        metadata["deployment_profile"] = profile_name
    if "scheduler" in profile:
        metadata["router_config"] = canonical_json(profile["scheduler"])
    elif profile.get("scheduler_manifest"):
        metadata["router_config"] = canonical_json(
            {"scheduler_manifest": profile["scheduler_manifest"]}
        )
    return metadata


def dashboard_metadata_labels(record_metrics: dict[str, Any]) -> dict[str, str]:
    """Build metadata labels with explicit runtime KPI labels taking precedence."""
    labels = {
        key: str(value)
        for key, value in record_metrics.items()
        if key in DASHBOARD_METADATA_LABEL_KEYS and value is not None
    }
    kpi_labels = record_metrics.get("kpi_labels", {})
    if isinstance(kpi_labels, dict):
        labels.update({key: str(value) for key, value in kpi_labels.items()})
    return labels


def validate_dashboard_fieldnames(fieldnames: list[str] | tuple[str, ...]) -> None:
    """Reject CSV schemas that would silently drop a dashboard metric."""
    missing = sorted({column for *_, column, _, _ in DASHBOARD_METRICS} - set(fieldnames))
    if missing:
        raise ValueError(f"Dashboard CSV fieldnames omit dashboard metrics: {', '.join(missing)}")


def _successful_stat(metrics: dict[str, Any], name: str, key: str) -> Any:
    return metrics.get(name, {}).get("successful", {}).get(key)


def _successful_percentile(metrics: dict[str, Any], name: str, key: str) -> Any:
    return metrics.get(name, {}).get("successful", {}).get("percentiles", {}).get(key)


def _milliseconds_to_seconds(value: Any) -> Any:
    return value / 1000.0 if value is not None else None


def enrich_guidellm_parse_result(
    base_result: ParseResult, nodes: list[TestBaseNode]
) -> ParseResult:
    """Preserve dashboard metrics from raw GuideLLM files on parsed records."""
    nodes_by_path = {str(node.test_path): node for node in nodes}
    records: list[UnifiedResultRecord] = []
    for record in base_result.records:
        node = nodes_by_path.get(record.test_base_path)
        if node is None or record.metrics.get("no_benchmarks_found"):
            records.append(record)
            continue
        extra, curves = _extract_dashboard_metrics(node)
        metrics = {**record.metrics, **extra}
        metrics["performance_curves"] = {
            **metrics.get("performance_curves", {}),
            **curves,
        }
        records.append(
            UnifiedResultRecord(
                test_base_path=record.test_base_path,
                distinguishing_labels=record.distinguishing_labels,
                metrics=metrics,
                run_identity=record.run_identity,
                parse_notes=record.parse_notes,
            )
        )
    return ParseResult(records=records, warnings=base_result.warnings)


def _extract_dashboard_metrics(node: TestBaseNode) -> tuple[dict[str, Any], dict[str, list]]:
    files = sorted(
        path
        for path in node.artifact_paths
        if path.name == "benchmarks.json"
        or (path.name.startswith("benchmarks-rate-") and path.suffix == ".json")
    )
    benchmarks: list[dict[str, Any]] = []
    metadata: dict[str, Any] = {}
    args: dict[str, Any] = {}
    for path in files:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        benchmarks.extend(payload.get("benchmarks", []))
        metadata = metadata or payload.get("metadata", {})
        args = args or payload.get("args", {})
    if not benchmarks:
        return {}, {}

    benchmarks.sort(
        key=lambda benchmark: float(
            benchmark.get("metrics", {})
            .get("requests_per_second", {})
            .get("successful", {})
            .get("mean", 0)
            or 0
        )
    )
    data_values = args.get("data", []) if isinstance(args, dict) else []
    if not data_values:
        fallback_data = (
            benchmarks[0]
            .get("benchmarker", {})
            .get("requests", {})
            .get("attributes", {})
            .get("data")
        )
        data_values = [fallback_data] if fallback_data else []
    data_value = data_values[0] if isinstance(data_values, list) and data_values else data_values
    tokens: dict[str, Any] = {}
    if isinstance(data_value, dict):
        tokens = data_value
    elif data_value:
        data_text = str(data_value)
        try:
            parsed_data = json.loads(data_text)
            if isinstance(parsed_data, dict):
                tokens = parsed_data
        except json.JSONDecodeError:
            tokens = dict(re.findall(r"(\w+)=([\d.]+)", data_text))
    starts = [
        b.get("scheduler_metrics", {}).get("start_time", b.get("start_time")) for b in benchmarks
    ]
    ends = [b.get("scheduler_metrics", {}).get("end_time", b.get("end_time")) for b in benchmarks]
    starts = [value for value in starts if value is not None]
    ends = [value for value in ends if value is not None]
    extra = {
        "guidellm_version": metadata.get("guidellm_version", ""),
        "prompt_toks": int(float(tokens["prompt_tokens"])) if "prompt_tokens" in tokens else "",
        "output_toks": int(float(tokens["output_tokens"])) if "output_tokens" in tokens else "",
        "guidellm_start_time_ms": int(min(starts) * 1000) if starts else "",
        "guidellm_end_time_ms": int(max(ends) * 1000) if ends else "",
    }
    curves = {curve_key: [] for _, curve_key, _, _, _ in DASHBOARD_METRICS}
    run_uuids: list[str] = []
    for benchmark in benchmarks:
        metrics = benchmark.get("metrics", {})
        strategy = benchmark.get("config", {}).get("strategy", {}) or benchmark.get(
            "scheduler", {}
        ).get("strategy", {})

        totals = (
            benchmark.get("scheduler_metrics", {}).get("requests_made", {})
            or benchmark.get("request_totals", {})
            or benchmark.get("run_stats", {}).get("requests_made", {})
            or metrics.get("request_totals", {})
        )
        run_uuids.append(
            str(benchmark.get("config", {}).get("run_id") or benchmark.get("run_id") or "")
        )
        values = {
            "output_tok_per_sec": metrics.get("output_tokens_per_second", {})
            .get("total", {})
            .get("mean"),
            "total_tok_per_sec": metrics.get("tokens_per_second", {}).get("total", {}).get("mean"),
            "request_concurrency": _successful_stat(metrics, "request_concurrency", "mean"),
            "measured_rps": _successful_stat(metrics, "requests_per_second", "mean"),
            "intended_concurrency": strategy.get("streams", strategy.get("max_concurrency")),
            "successful_requests": totals.get("successful", 0),
            "errored_requests": totals.get("errored", 0),
            "ttft_median": _milliseconds_to_seconds(
                _successful_stat(metrics, "time_to_first_token_ms", "median")
            ),
            "ttft_p95": _milliseconds_to_seconds(
                _successful_percentile(metrics, "time_to_first_token_ms", "p95")
            ),
            "ttft_p99": _milliseconds_to_seconds(
                _successful_percentile(metrics, "time_to_first_token_ms", "p99")
            ),
            "ttft_p1": _milliseconds_to_seconds(
                _successful_percentile(metrics, "time_to_first_token_ms", "p01")
            ),
            "ttft_p999": _milliseconds_to_seconds(
                _successful_percentile(metrics, "time_to_first_token_ms", "p999")
            ),
            "ttft_mean": _milliseconds_to_seconds(
                _successful_stat(metrics, "time_to_first_token_ms", "mean")
            ),
            "tpot_median": _milliseconds_to_seconds(
                _successful_stat(metrics, "time_per_output_token_ms", "median")
            ),
            "tpot_p95": _milliseconds_to_seconds(
                _successful_percentile(metrics, "time_per_output_token_ms", "p95")
            ),
            "tpot_p99": _milliseconds_to_seconds(
                _successful_percentile(metrics, "time_per_output_token_ms", "p99")
            ),
            "tpot_p1": _milliseconds_to_seconds(
                _successful_percentile(metrics, "time_per_output_token_ms", "p01")
            ),
            "tpot_p999": _milliseconds_to_seconds(
                _successful_percentile(metrics, "time_per_output_token_ms", "p999")
            ),
            "itl_median": _milliseconds_to_seconds(
                _successful_stat(metrics, "inter_token_latency_ms", "median")
            ),
            "itl_p95": _milliseconds_to_seconds(
                _successful_percentile(metrics, "inter_token_latency_ms", "p95")
            ),
            "itl_p99": _milliseconds_to_seconds(
                _successful_percentile(metrics, "inter_token_latency_ms", "p99")
            ),
            "itl_p1": _milliseconds_to_seconds(
                _successful_percentile(metrics, "inter_token_latency_ms", "p01")
            ),
            "itl_p999": _milliseconds_to_seconds(
                _successful_percentile(metrics, "inter_token_latency_ms", "p999")
            ),
            "itl_mean": _milliseconds_to_seconds(
                _successful_stat(metrics, "inter_token_latency_ms", "mean")
            ),
            "request_latency_median": _successful_stat(metrics, "request_latency", "median"),
            "request_latency_min": _successful_stat(metrics, "request_latency", "min"),
            "request_latency_max": _successful_stat(metrics, "request_latency", "max"),
            "prompt_token_count_mean": _successful_stat(metrics, "prompt_token_count", "mean"),
            "prompt_token_count_p99": _successful_percentile(metrics, "prompt_token_count", "p99"),
            "output_token_count_mean": _successful_stat(metrics, "output_token_count", "mean"),
            "output_token_count_p99": _successful_percentile(metrics, "output_token_count", "p99"),
        }
        for key in curves:
            curves[key].append(values.get(key))
    extra["run_uuids"] = run_uuids
    return extra, curves


def compute_dashboard_kpis(model: UnifiedRunModel, *, prefix: str) -> list[dict[str, Any]]:
    """Emit one scalar KPI per dashboard metric and rate point."""
    timestamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    output: list[dict[str, Any]] = []
    for record in model.unified_result_records:
        curves = record.metrics.get("performance_curves", {})
        rates = record.metrics.get("request_rate", [])
        if not record.run_identity.get("guidellm") or not rates:
            continue
        metadata_labels = {
            "guidellm_version": str(record.metrics.get("guidellm_version", "")),
            "prompt_toks": str(record.metrics.get("prompt_toks", "")),
            "output_toks": str(record.metrics.get("output_toks", "")),
            "guidellm_start_time_ms": str(record.metrics.get("guidellm_start_time_ms", "")),
            "guidellm_end_time_ms": str(record.metrics.get("guidellm_end_time_ms", "")),
        }
        metadata_labels.update(dashboard_metadata_labels(record.metrics))
        run_uuids = record.metrics.get("run_uuids", [])
        for index in range(len(rates)):
            labels = {**record.distinguishing_labels, **metadata_labels, "rate_index": str(index)}
            if index < len(run_uuids):
                labels["run_uuid"] = run_uuids[index]
            for suffix, curve_key, _, unit, higher_is_better in DASHBOARD_METRICS:
                values = curves.get(curve_key, [])
                if index >= len(values) or values[index] is None:
                    continue
                kpi_labels = dict(labels)
                if higher_is_better is not None:
                    kpi_labels["higher_is_better"] = higher_is_better

                # Create structured KPI record using core dataclass
                kpi_record = KpiRecord(
                    schema_version="1",
                    kpi_id=f"{prefix}_{suffix}",
                    value=float(values[index]),
                    unit=unit,
                    run_id=record.test_base_path,
                    timestamp=timestamp,
                    labels=kpi_labels,
                    metadata={"run_path": record.test_base_path},  # Move run_path to metadata
                    is_curve=False,  # Scalar KPI
                    source=SourceInfo(
                        test_base_path=record.test_base_path,
                        plugin_module=model.plugin_module,
                    ),
                )
                output.append(kpi_record.to_dict())
    return output


def dashboard_kpi_catalog(*, prefix: str) -> list[dict[str, Any]]:
    """Return catalog entries for the shared dashboard KPI set using dataclasses."""
    catalog_entries = []
    for suffix, _, _, unit, higher_is_better in DASHBOARD_METRICS:
        catalog_entry = KpiCatalogEntry(
            kpi_id=f"{prefix}_{suffix}",
            name=f"{prefix}_{suffix}",
            unit=unit,
            higher_is_better=higher_is_better if higher_is_better is not None else True,
            is_curve=False,
            help=f"Dashboard metric: {suffix}",
        )
        catalog_entries.append(catalog_entry.to_dict())
    return catalog_entries


def export_dashboard_kpis_to_csv(
    kpi_records: list[dict[str, Any]],
    output_path: Path,
    *,
    prefix: str,
    fieldnames: list[str],
    metadata_row: Callable[[dict[str, Any]], dict[str, Any]],
) -> str:
    """Pivot scalar per-rate KPIs into a dashboard-compatible CSV."""
    validate_dashboard_fieldnames(fieldnames)
    kpi_to_column = {f"{prefix}_{suffix}": column for suffix, _, column, _, _ in DASHBOARD_METRICS}
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    labels_by_group: dict[tuple[str, str], dict[str, Any]] = {}
    for kpi in kpi_records:
        labels = kpi.get("labels", {})
        metadata = kpi.get("metadata", {})
        key = (
            str(metadata.get("run_path") or kpi.get("run_id", "")),
            str(labels.get("rate_index", "0")),
        )
        column = kpi_to_column.get(kpi.get("kpi_id", ""))
        if column:
            groups.setdefault(key, {})[column] = kpi.get("value")
        if key not in labels_by_group or len(labels) > len(labels_by_group[key]):
            labels_by_group[key] = labels

    rows: list[dict[str, Any]] = []
    for key in sorted(groups, key=lambda k: (k[0], int(k[1]) if k[1].isdigit() else k[1])):
        metrics = groups[key]
        labels = labels_by_group.get(key, {})
        row: dict[str, Any] = dict.fromkeys(fieldnames, "")
        row.update(metadata_row(labels))
        row.update(metrics)
        if "intended concurrency" in row and row["intended concurrency"] in ("", None):
            row["intended concurrency"] = labels.get("intended_concurrency", "")
        for column in SECONDS_TO_MS_COLUMNS:
            value = row.get(column)
            if value not in ("", None):
                row[column] = float(value) * 1000.0
        rows.append(row)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    logger.info("Exported %d dashboard CSV rows to %s", len(rows), output_path)
    return str(output_path)
