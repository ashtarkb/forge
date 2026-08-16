"""Summarize Prometheus capture JSON into scalar KPI inputs.

Capture files are written by ``caliper.prometheus_metrics.capture`` as
``{query_key}.json`` with a Prometheus ``query_range`` response inside.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

BROKER_NAMESPACE = "mcp-system"
ENVOY_NAMESPACE = "gateway-system"

_PROM_FILES = ("cpu_usage.json", "memory_usage.json", "http_4xx_rate.json")


def summarize_prom_artifacts(artifact_paths: list[Path]) -> dict[str, float]:
    """Return flattened broker/envoy CPU+memory and Istio 4xx scalars."""
    by_name = {path.name: path for path in artifact_paths if path.name in _PROM_FILES}
    out: dict[str, float] = {}

    cpu_series = _load_matrix(by_name.get("cpu_usage.json"))
    mem_series = _load_matrix(by_name.get("memory_usage.json"))
    http_4xx_series = _load_matrix(by_name.get("http_4xx_rate.json"))

    _merge_ns_stats(out, "broker_cpu", cpu_series, BROKER_NAMESPACE, unit_suffix="cores")
    _merge_ns_stats(out, "broker_memory", mem_series, BROKER_NAMESPACE, unit_suffix="bytes")
    _merge_ns_stats(out, "envoy_cpu", cpu_series, ENVOY_NAMESPACE, unit_suffix="cores")
    _merge_ns_stats(out, "envoy_memory", mem_series, ENVOY_NAMESPACE, unit_suffix="bytes")

    fourxx_values = _summed_values(http_4xx_series)
    if fourxx_values:
        out["http_4xx_rate"] = sum(fourxx_values) / len(fourxx_values)
    elif "http_4xx_rate.json" in by_name:
        out["http_4xx_rate"] = 0.0

    return out


def _load_matrix(path: Path | None) -> list[dict[str, Any]]:
    if path is None or not path.is_file():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Failed to read Prometheus capture %s: %s", path, exc)
        return []

    response = payload.get("response") or {}
    if response.get("status") != "success":
        logger.warning(
            "Prometheus capture %s status=%s",
            path.name,
            response.get("status", "missing"),
        )
        return []
    data = response.get("data") or {}
    result = data.get("result")
    return result if isinstance(result, list) else []


def _merge_ns_stats(
    out: dict[str, float],
    prefix: str,
    series: list[dict[str, Any]],
    namespace: str,
    *,
    unit_suffix: str,
) -> None:
    matched = [item for item in series if _series_namespace(item) == namespace]
    values = _summed_values(matched)
    if not values:
        return
    out[f"{prefix}_avg_{unit_suffix}"] = sum(values) / len(values)
    out[f"{prefix}_max_{unit_suffix}"] = max(values)


def _series_namespace(series: dict[str, Any]) -> str:
    metric = series.get("metric") or {}
    return str(metric.get("namespace") or metric.get("destination_workload_namespace") or "")


def _summed_values(series_list: list[dict[str, Any]]) -> list[float]:
    """Sum matching series at each timestamp, then return the time series."""
    by_ts: dict[float, float] = {}
    for series in series_list:
        for pair in series.get("values") or []:
            if not isinstance(pair, (list, tuple)) or len(pair) < 2:
                continue
            try:
                ts = float(pair[0])
                value = float(pair[1])
            except (TypeError, ValueError):
                continue
            by_ts[ts] = by_ts.get(ts, 0.0) + value
    return [by_ts[ts] for ts in sorted(by_ts)]
