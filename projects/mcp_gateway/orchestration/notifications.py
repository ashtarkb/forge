"""
Per-project Slack notification provider for MCP Gateway.

Sends structured performance summaries with KPI comparison against the
previous MLflow run. Handles both regular (single) and matrix (nested) runs.

Channel ID is read from the project's config.yaml at
``notifications.slack.channel_id``.
"""

from __future__ import annotations

import json
import logging
import os

from projects.core.library import config
from projects.core.notifications.helpers import (
    build_comparison_table,
    build_current_kpis_list,
    extract_mlflow_url,
    get_label_value,
    get_test_artifacts_root,
    read_test_duration,
)
from projects.core.notifications.provider import NotificationContext, SlackNotificationProvider
from projects.core.notifications.send import get_ocpci_link
from projects.mcp_gateway.postprocess.mcp_gateway.mlflow_history import query_previous_runs

logger = logging.getLogger(__name__)

TARGET_KPIS = [
    ("mcp_gw_requests_per_second", "RPS", "req/s"),
    ("mcp_gw_p95_ms", "P95 latency", "ms"),
    ("mcp_gw_p99_ms", "P99 latency", "ms"),
    ("mcp_gw_failure_rate", "Failure rate", "%"),
]

KPI_TO_MLFLOW_METRIC = {
    "mcp_gw_requests_per_second": "requests_per_second",
    "mcp_gw_p95_ms": "p95_ms",
    "mcp_gw_p99_ms": "p99_ms",
    "mcp_gw_failure_rate": "failure_rate",
}


class MCPGatewaySlackProvider(SlackNotificationProvider):
    """Slack notification provider for the mcp_gateway project."""

    def get_channel_id(self) -> str:
        channel_id = config.project.get_config(
            "notifications.slack.channel_id", None, print=False, warn=False
        )
        if not channel_id:
            raise ValueError("notifications.slack.channel_id must be set in config.yaml")
        return channel_id

    def format_message(self, context: NotificationContext) -> str:
        header = _format_header(context)
        metadata = _format_metadata(context)
        kpi_table = _format_kpi_table(context)
        links = _format_standard_links(context)
        failure_info = _format_failure_info(context)

        parts = [header, metadata, kpi_table, links, failure_info]
        return "\n\n".join(filter(None, parts))

    def get_thread_anchor(self, context: NotificationContext) -> str:
        if context.pr_number:
            return f"Thread for mcp_gateway PR #{context.pr_number}"

        job_name = os.environ.get("FJOB_NAME") or os.environ.get("JOB_NAME_SAFE", "")
        if job_name:
            return f"Thread for mcp_gateway `{job_name}`"

        return "Thread for mcp_gateway run"


# ---------------------------------------------------------------------------
# Message sections (MCP Gateway specific)
# ---------------------------------------------------------------------------


def _format_header(context: NotificationContext) -> str:
    status_icon = ":done-circle-check:" if context.finish_reason == "success" else ":no-red-circle:"
    duration = read_test_duration(context)
    duration_str = f" after {duration}" if duration else ""
    return f"{status_icon} *mcp_gateway test finished{duration_str}* {status_icon}"


def _format_metadata(context: NotificationContext) -> str:
    version = os.environ.get("MCP_GATEWAY_VERSION", "")
    preset = os.environ.get("MCP_GATEWAY_PRESET", "")

    test_root = get_test_artifacts_root(context)
    if not version:
        version = get_label_value(test_root, "mcp_gateway_version") or "unknown"
    if not preset:
        preset = get_label_value(test_root, "preset") or "default"

    return f"*Version*: `{version}`  |  *Preset*: `{preset}`"


def _format_kpi_table(context: NotificationContext) -> str:
    """Build comparison table(s): current KPIs vs previous MLflow run."""
    current_run_id = None
    if isinstance(context.status, dict):
        backends = context.status.get("caliper_artifacts_export", {}).get("backends", {})
        current_run_id = backends.get("mlflow", {}).get("run_id")

    result = query_previous_runs(
        current_run_id=current_run_id,
        kpi_to_mlflow_metric=KPI_TO_MLFLOW_METRIC,
    )

    if result.get("skip"):
        context.extra["_skip_notification"] = True
        return ""

    if result["type"] == "regular":
        previous = result.get("previous_metrics", {})
        current = result.get("current_metrics", {})
        prev_name = result.get("previous_run_name", "")

        if not current:
            current = _load_current_kpis(context)
        if not current:
            return ""

        if previous:
            return build_comparison_table(current, previous, prev_name, TARGET_KPIS)
        else:
            return build_current_kpis_list(current, TARGET_KPIS)

    elif result["type"] == "matrix":
        comparisons = result.get("comparisons", [])
        if not comparisons:
            current_kpis = _load_current_kpis(context)
            if current_kpis:
                return build_current_kpis_list(current_kpis, TARGET_KPIS)
            return ""

        prev_parent_name = result.get("previous_run_name", "previous")
        sections = []
        for comp in comparisons:
            cfg = comp["config"]
            current_m = comp["current_metrics"]
            previous_m = comp.get("previous_metrics", {})

            if not current_m:
                continue

            if previous_m:
                table = build_comparison_table(
                    current_m, previous_m,
                    f"{prev_parent_name} / {cfg}",
                    TARGET_KPIS,
                )
            else:
                table = f"*{cfg}* (no previous data)\n" + build_current_kpis_list(
                    current_m, TARGET_KPIS
                )
            sections.append(f"*Config: `{cfg}`*\n{table}")

        return "\n\n".join(sections)

    else:
        current_kpis = _load_current_kpis(context)
        if current_kpis:
            return build_current_kpis_list(current_kpis, TARGET_KPIS)
        return ""


def _format_standard_links(context: NotificationContext) -> str:
    """Generate artifact links pointing to MLflow."""
    mlflow_url = extract_mlflow_url(context)
    if not mlflow_url:
        return ""

    return f"\u2022 <{mlflow_url}|MLflow run (results & logs)>"


def _format_failure_info(context: NotificationContext) -> str:
    """Include structured failure details when test failed."""
    if context.finish_reason == "success":
        return ""
    if not context.artifact_dir:
        return ""

    try:
        from projects.core.notifications.send import _get_notification_content

        def get_link(name, path, **kwargs):
            return f"<{get_ocpci_link(path, **kwargs)}|{name}>"

        def get_bold(text):
            return f"*{text}*"

        return _get_notification_content(context.artifact_dir, get_link, get_bold)
    except Exception as e:
        logger.warning("Failed to extract failure info: %s", e)
        return ""


# ---------------------------------------------------------------------------
# KPI loading (MCP Gateway specific)
# ---------------------------------------------------------------------------


def _find_kpis_json(artifact_dir):
    """Find kpis.json in artifact tree."""
    direct = artifact_dir / "kpis.json"
    if direct.exists():
        return direct
    for f in artifact_dir.glob("**/kpis.json"):
        return f

    return None


def _load_current_kpis(context: NotificationContext) -> dict[str, float]:
    """Read KPI values from kpis.json in the artifact directory."""
    test_root = get_test_artifacts_root(context)
    if not test_root:
        return {}

    kpis_file = _find_kpis_json(test_root)
    if not kpis_file:
        return {}

    target_ids = {k[0] for k in TARGET_KPIS}
    kpis: dict[str, float] = {}

    try:
        with open(kpis_file) as f:
            data = json.load(f)
            for test in data.get("tests", []):
                for kpi_record in test.get("kpis", []):
                    kpi_id = kpi_record.get("id", "")
                    if kpi_id in target_ids:
                        value = kpi_record.get("value")
                        if value is not None:
                            kpis[kpi_id] = float(value)
    except Exception as e:
        logger.warning("Failed to read KPI file %s: %s", kpis_file, e)

    return kpis
