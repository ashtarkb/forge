"""KPI definitions and computation for GuideLLM Caliper plugin."""

from __future__ import annotations

from projects.caliper.engine.kpi import (
    Curve,
    Format,
    HigherBetter,
    KPIMetadata,
    LowerBetter,
)


# Token Count Statistics KPIs - static values
@LowerBetter()
@Format("{:.1f}")
@KPIMetadata(help="Average output tokens per request", unit="tokens")
def guidellm_output_tokens_per_request(unified_record) -> float:
    """Output Tokens Per Request KPI."""
    value = unified_record.metrics.get("output_tokens_per_request")
    if value is None:
        raise ValueError("output_tokens_per_request metric not found")
    return float(value)


@HigherBetter()
@Curve(
    x_unit="connections",
    x_help="Input concurrency",
    y_unit="tokens/s",
    y_help="Achieved throughput",
    x_format="{:.0f}",
    y_format="{:.1f}",
)
@KPIMetadata(help="Throughput achieved at different concurrency levels", unit="tokens/s")
def guidellm_throughput_curve(unified_record) -> list[tuple[int, float]]:
    """Throughput vs Concurrency Curve KPI."""
    curves = unified_record.metrics.get("performance_curves", {})
    intended_concurrency = curves.get("intended_concurrency", [])
    tokens_per_sec = curves.get("tokens_per_second", [])

    if (
        not intended_concurrency
        or not tokens_per_sec
        or len(intended_concurrency) != len(tokens_per_sec)
    ):
        return []

    return [
        (int(x), float(y))
        for x, y in zip(intended_concurrency, tokens_per_sec, strict=False)
        if x > 0 and y > 0
    ]


@LowerBetter()
@Curve(
    x_unit="connections",
    x_help="Input concurrency",
    y_unit="s",
    y_help="P95 latency",
    x_format="{:.0f}",
    y_format="{:.4f}",
)
@KPIMetadata(help="P95 latency at different concurrency levels", unit="s")
def guidellm_latency(unified_record) -> list[tuple[int, float]]:
    """P95 Latency vs Concurrency Curve KPI."""
    curves = unified_record.metrics.get("performance_curves", {})
    intended_concurrency = curves.get("intended_concurrency", [])
    p95_latency = curves.get("request_latency_p95", [])

    if not intended_concurrency or not p95_latency or len(intended_concurrency) != len(p95_latency):
        return []

    return [
        (int(x), float(y))
        for x, y in zip(intended_concurrency, p95_latency, strict=False)
        if x > 0 and y > 0
    ]


@LowerBetter()
@Curve(
    x_unit="connections",
    x_help="Input concurrency",
    y_unit="s",
    y_help="TTFT P95",
    x_format="{:.0f}",
    y_format="{:.4f}",
)
@KPIMetadata(help="Time to first token P95 at different concurrency levels", unit="s")
def guidellm_ttft(unified_record) -> list[tuple[int, float]]:
    """TTFT P95 vs Concurrency Curve KPI."""
    curves = unified_record.metrics.get("performance_curves", {})
    intended_concurrency = curves.get("intended_concurrency", [])
    ttft_p95 = curves.get("ttft_p95", [])

    if not intended_concurrency or not ttft_p95 or len(intended_concurrency) != len(ttft_p95):
        return []

    return [
        (int(x), float(y))
        for x, y in zip(intended_concurrency, ttft_p95, strict=False)
        if x > 0 and y > 0
    ]


@LowerBetter()
@Curve(
    x_unit="connections",
    x_help="Input concurrency",
    y_unit="s",
    y_help="TPOT median",
    x_format="{:.0f}",
    y_format="{:.4f}",
)
@KPIMetadata(help="Time per output token median at different concurrency levels", unit="s")
def guidellm_tpot(unified_record) -> list[tuple[int, float]]:
    """TPOT Median vs Concurrency Curve KPI."""
    curves = unified_record.metrics.get("performance_curves", {})
    intended_concurrency = curves.get("intended_concurrency", [])
    tpot_median = curves.get("tpot_median", [])

    if not intended_concurrency or not tpot_median or len(intended_concurrency) != len(tpot_median):
        return []

    return [
        (int(x), float(y))
        for x, y in zip(intended_concurrency, tpot_median, strict=False)
        if x > 0 and y > 0
    ]


@LowerBetter()
@Curve(
    x_unit="connections",
    x_help="Input concurrency",
    y_unit="s",
    y_help="TPOT P95",
    x_format="{:.0f}",
    y_format="{:.4f}",
)
@KPIMetadata(help="Time per output token P95 at different concurrency levels", unit="s")
def guidellm_tpot_p95(unified_record) -> list[tuple[int, float]]:
    """TPOT P95 vs Concurrency Curve KPI."""
    curves = unified_record.metrics.get("performance_curves", {})
    intended_concurrency = curves.get("intended_concurrency", [])
    tpot_p95 = curves.get("tpot_p95", [])

    if not intended_concurrency or not tpot_p95 or len(intended_concurrency) != len(tpot_p95):
        return []

    return [
        (int(x), float(y))
        for x, y in zip(intended_concurrency, tpot_p95, strict=False)
        if x > 0 and y > 0
    ]


@LowerBetter()
@Curve(
    x_unit="connections",
    x_help="Input concurrency",
    y_unit="s",
    y_help="TPOT P99",
    x_format="{:.0f}",
    y_format="{:.4f}",
)
@KPIMetadata(help="Time per output token P99 at different concurrency levels", unit="s")
def guidellm_tpot_p99(unified_record) -> list[tuple[int, float]]:
    """TPOT P99 vs Concurrency Curve KPI."""
    curves = unified_record.metrics.get("performance_curves", {})
    intended_concurrency = curves.get("intended_concurrency", [])
    tpot_p99 = curves.get("tpot_p99", [])

    if not intended_concurrency or not tpot_p99 or len(intended_concurrency) != len(tpot_p99):
        return []

    return [
        (int(x), float(y))
        for x, y in zip(intended_concurrency, tpot_p99, strict=False)
        if x > 0 and y > 0
    ]
