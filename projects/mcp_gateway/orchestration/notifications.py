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


def _load_previous_kpis(context: NotificationContext) -> tuple[dict[str, float], str]:
    """Load the most recent historical KPIs matching current run's config.

    Uses the same matching logic as the analyze step:
    - Same preset, same load config (num_servers + users)
    - Same version type (SHA vs semver)
    - Different version

    After S3 import, historical kpis.json files are stored at:
        {artifact_dir}/historical_data/{upload_id}/kpis.json

    Returns:
        Tuple of (kpi_values_dict, run_name_string).
        Returns ({}, "") if no historical data is available.
    """
    from projects.mcp_gateway.postprocess.mcp_gateway.analyze import (
        _extract_tests,
        _find_matching_baseline,
    )

    test_root = get_test_artifacts_root(context)
    if not test_root:
        return {}, ""

    # Load current run's kpis.json to get its config
    current_kpis_file = _find_kpis_json(test_root)
    if not current_kpis_file:
        return {}, ""

    try:
        with open(current_kpis_file) as f:
            current_data = json.load(f)
    except Exception as e:
        logger.warning("Failed to load current kpis.json: %s", e)
        return {}, ""

    current_tests = _extract_tests(current_data)
    if not current_tests:
        return {}, ""

    # Use first test entry as the reference for matching
    current_test = current_tests[0]

    # Load all historical test entries
    historical_dir = test_root / "historical_data"
    if not historical_dir.exists():
        logger.info("No historical_data directory found at %s", historical_dir)
        return {}, ""

    historical_kpi_files = sorted(historical_dir.glob("*/kpis.json"), reverse=True)
    if not historical_kpi_files:
        logger.info("No historical kpis.json files found in %s", historical_dir)
        return {}, ""

    # Collect all historical test entries (ordered by most recent first)
    all_historical_tests = []
    for kpis_file in historical_kpi_files:
        try:
            with open(kpis_file) as f:
                hist_data = json.load(f)
            tests = _extract_tests(hist_data)
            all_historical_tests.extend(tests)
        except Exception as e:
            logger.debug("Failed to load %s: %s", kpis_file, e)

    # Find matching baseline using the analyze module's logic
    baseline = _find_matching_baseline(current_test, all_historical_tests)
    if not baseline:
        logger.info(
            "No matching historical run (config=%s, preset=%s, type=%s)",
            current_test.config.load_config,
            current_test.config.preset,
            "sha" if current_test.config.is_sha else "semver",
        )
        return {}, ""

    # Build run name for display
    run_name = baseline.config.version
    if not run_name:
        run_name = "previous run"

    # Extract target KPIs from baseline
    target_ids = {k[0] for k in TARGET_KPIS}
    kpis = {kpi_id: val for kpi_id, val in baseline.values.items() if kpi_id in target_ids}

    if kpis:
        logger.info("Using baseline: version=%s config=%s", run_name, baseline.config.load_config)
        return kpis, run_name

    return {}, ""
