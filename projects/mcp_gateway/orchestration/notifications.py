"""
Per-project Slack notification provider for MCP Gateway.

Sends structured performance summaries with current KPI results.

Channel ID is read from the project's config.yaml at
``notifications.slack.channel_id``.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

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

logger = logging.getLogger(__name__)

TARGET_KPIS = [
    ("mcp_gw_requests_per_second", "RPS", "req/s"),
    ("mcp_gw_p95_ms", "P95 latency", "ms"),
    ("mcp_gw_p99_ms", "P99 latency", "ms"),
    ("mcp_gw_failure_rate", "Failure rate", "%"),
]


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
    """Build KPI comparison table using historical data from S3.

    If historical data is available, shows a comparison table with deltas.
    Falls back to a simple current KPI list if no history is found.
    """
    current_kpis = _load_current_kpis(context)
    if not current_kpis:
        return ""

    previous_kpis, prev_run_name = _load_previous_kpis(context)
    if previous_kpis:
        return build_comparison_table(current_kpis, previous_kpis, prev_run_name, TARGET_KPIS)

    return build_current_kpis_list(current_kpis, TARGET_KPIS)


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


def _extract_kpis_from_file(kpis_file: Path) -> dict[str, float]:
    """Extract target KPI values from a kpis.json file."""
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


def _load_current_kpis(context: NotificationContext) -> dict[str, float]:
    """Read KPI values from kpis.json in the artifact directory."""
    test_root = get_test_artifacts_root(context)
    if not test_root:
        return {}

    kpis_file = _find_kpis_json(test_root)
    if not kpis_file:
        return {}

    return _extract_kpis_from_file(kpis_file)


def _get_current_version() -> str:
    """Get the MCP Gateway version of the current run."""
    version = os.environ.get("MCP_GATEWAY_VERSION", "")
    if not version:
        version = os.environ.get("MCP_GW_VERSION", "")
    return version


def _get_version_from_kpis_json(kpis_file: Path) -> str | None:
    """Extract mcp_gateway_version from a kpis.json file's labels."""
    try:
        with open(kpis_file) as f:
            data = json.load(f)
        for test in data.get("tests", []):
            labels = test.get("labels", {})
            version = labels.get("mcp_gateway_version")
            if version:
                return str(version)
    except Exception as e:
        logger.debug("Could not extract version from %s: %s", kpis_file, e)
    return None


def _load_previous_kpis(context: NotificationContext) -> tuple[dict[str, float], str]:
    """Load the most recent historical KPIs from a *different* version.

    After S3 import, historical kpis.json files are stored at:
        {artifact_dir}/historical_data/{upload_id}/kpis.json

    The upload_id is a timestamp (e.g. "25-07-12_143021_123"), so sorting
    the directories in reverse gives us the most recent previous run.

    To provide meaningful comparisons, this function skips historical runs
    that have the same mcp_gateway_version as the current run. This avoids
    noise from comparing e.g. v0.7.0 against v0.7.0.

    Returns:
        Tuple of (kpi_values_dict, run_name_string).
        Returns ({}, "") if no historical data is available.
    """
    test_root = get_test_artifacts_root(context)
    if not test_root:
        return {}, ""

    historical_dir = test_root / "historical_data"
    if not historical_dir.exists():
        logger.info("No historical_data directory found at %s", historical_dir)
        return {}, ""

    historical_kpi_files = sorted(historical_dir.glob("*/kpis.json"), reverse=True)
    if not historical_kpi_files:
        logger.info("No historical kpis.json files found in %s", historical_dir)
        return {}, ""

    current_version = _get_current_version()

    for kpis_file in historical_kpi_files:
        if current_version:
            hist_version = _get_version_from_kpis_json(kpis_file)
            if hist_version and hist_version == current_version:
                logger.debug(
                    "Skipping %s (same version: %s)", kpis_file.parent.name, hist_version
                )
                continue

        run_name = kpis_file.parent.name
        hist_version = _get_version_from_kpis_json(kpis_file)
        if hist_version:
            run_name = f"{hist_version} ({run_name})"

        logger.info("Using historical data from run: %s", run_name)
        kpis = _extract_kpis_from_file(kpis_file)
        if kpis:
            return kpis, run_name

    logger.info("No historical data from a different version found")
    return {}, ""
