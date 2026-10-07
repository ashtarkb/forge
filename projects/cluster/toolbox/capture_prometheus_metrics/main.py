#!/usr/bin/env python3

"""
Capture Prometheus Metrics via In-Cluster Queries

Spawns a temporary Python pod in openshift-monitoring, installs
prometheus_api_client, and runs PromQL range queries or raw metric
fetches. Results are saved as JSON files.

Can be run standalone:
    ./bin/run_toolbox cluster capture_prometheus_metrics \\
        --queries-file /path/to/queries.yaml \\
        --start-time "2026-07-26T10:00:00+00:00" \\
        --end-time "2026-07-26T10:20:00+00:00" \\
        --output-dir /path/to/output
"""

from __future__ import annotations

import base64
import json
import logging
import shlex
from datetime import UTC, datetime
from pathlib import Path

import yaml

from projects.core.dsl import always, entrypoint, execute_tasks, task
from projects.core.dsl.utils.k8s import best_effort_oc, oc, oc_exec

logger = logging.getLogger("DSL")

PYTHON_IMAGE = "python:3.12-slim"
FETCH_SCRIPT_PATH = "/tmp/fetch_metrics.py"
FETCH_SCRIPT_LOCAL = Path(__file__).resolve().parent / "fetch_metrics.py"
PIP_USER_BASE = "/tmp/pip"

PROM_NAMESPACE = "openshift-monitoring"
PROM_URL = "https://thanos-querier.openshift-monitoring.svc:9091"
PROM_SERVICE_ACCOUNT = "prometheus-k8s"


@entrypoint
def run(
    queries_file: str | None = None,
    start_time: datetime = None,
    end_time: datetime = None,
    output_dir: str | Path | None = None,
    *,
    step_seconds: int = 15,
    raw_metrics_file: str | None = None,
    prom_namespace: str | None = None,
    prom_url: str | None = None,
    prom_service_account: str | None = None,
) -> Path:
    """
    Query Prometheus from inside the cluster and save raw results as JSON.

    Creates a temporary Python pod with prometheus_api_client, executes
    PromQL range queries and/or raw metric fetches against the
    Prometheus/Thanos service, and writes the raw JSON response to the
    output directory.

    Args:
        queries_file: Path to a YAML file with a name:promql dict.
        start_time: Start of the query window (UTC).
        end_time: End of the query window (UTC).
        step_seconds: Query resolution step in seconds (for PromQL range queries).
        raw_metrics_file: Path to a YAML file with a name:selector dict for raw metric capture.
        output_dir: Directory to write results into (default: artifact_dir/prometheus_metrics).
        prom_namespace: Override the namespace for the curl pod.
        prom_url: Override the Prometheus/Thanos URL.
        prom_service_account: Override the service account for the curl pod.
    """
    ctx = execute_tasks(locals())
    return ctx.output_dir


def _parse_time(value) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
    dt = datetime.fromisoformat(str(value))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _load_input_file(file_input, *, label, src_dir):
    if not file_input:
        return {}

    import shutil

    file_path = Path(file_input)
    if not file_path.exists():
        raise FileNotFoundError(f"{label} file not found: {file_path}")

    shutil.copy2(file_path, src_dir / file_path.name)

    with file_path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if not isinstance(data, dict) or not data:
        raise ValueError(f"{label} file must contain a non-empty mapping, got: {type(data)}")
    return data


@task
def validate_parameters(args, ctx):
    """Validate inputs, parse times, and load queries."""

    ctx.start_time = _parse_time(args.start_time)
    ctx.end_time = _parse_time(args.end_time)

    if ctx.end_time <= ctx.start_time:
        raise ValueError("end_time must be after start_time")

    ctx.prom_namespace = args.prom_namespace or PROM_NAMESPACE
    ctx.prom_url = args.prom_url or PROM_URL
    ctx.prom_service_account = args.prom_service_account or PROM_SERVICE_ACCOUNT

    if args.output_dir is not None:
        ctx.output_dir = Path(args.output_dir)
    else:
        ctx.output_dir = args.artifact_dir / "prometheus_metrics"
    ctx.output_dir.mkdir(parents=True, exist_ok=True)

    src_dir = args.artifact_dir / "src"
    src_dir.mkdir(parents=True, exist_ok=True)

    ctx.queries = _load_input_file(args.queries_file, label="queries", src_dir=src_dir)
    ctx.raw_metrics = _load_input_file(args.raw_metrics_file, label="raw_metrics", src_dir=src_dir)

    if not ctx.queries and not ctx.raw_metrics:
        raise ValueError("No queries or raw metrics to capture")

    ctx.start_epoch = f"{ctx.start_time.timestamp():.3f}"
    ctx.end_epoch = f"{ctx.end_time.timestamp():.3f}"
    ctx.duration_seconds = int((ctx.end_time - ctx.start_time).total_seconds())
    ctx.pod_name = f"prom-metrics-query-{int(ctx.start_time.timestamp() * 1000)}"

    duration_min = ctx.duration_seconds / 60
    return (
        f"Loaded {len(ctx.queries)} queries and {len(ctx.raw_metrics)} raw metrics, window: "
        f"{ctx.start_time:%H:%M:%S} -> {ctx.end_time:%H:%M:%S} ({duration_min:.1f} min)"
    )


@task
def create_query_pod(args, ctx):
    """Create a temporary Python pod in openshift-monitoring."""

    best_effort_oc("delete", "pod", ctx.pod_name, "-n", ctx.prom_namespace, "--ignore-not-found")

    overrides = json.dumps(
        {
            "spec": {
                "serviceAccountName": ctx.prom_service_account,
                "automountServiceAccountToken": True,
                "terminationGracePeriodSeconds": 1,
            },
        }
    )

    oc(
        "run",
        ctx.pod_name,
        f"--image={PYTHON_IMAGE}",
        f"--overrides={overrides}",
        "-n",
        ctx.prom_namespace,
        "--restart=Never",
        "--command",
        "--",
        "sleep",
        "600",
    )

    oc(
        "wait",
        "pod",
        ctx.pod_name,
        "-n",
        ctx.prom_namespace,
        "--for=condition=Ready",
        "--timeout=120s",
    )

    return f"Pod {ctx.pod_name} is ready"


@task
def setup_query_pod(args, ctx):
    """Install requests and inject the fetch script."""

    oc_exec(
        "sh",
        "-c",
        f"PYTHONUSERBASE={PIP_USER_BASE} PATH=$PATH:{PIP_USER_BASE}/bin"
        f" pip install --user --quiet --no-cache-dir --disable-pip-version-check requests",
        namespace=ctx.prom_namespace,
        pod=ctx.pod_name,
    )

    script_content = FETCH_SCRIPT_LOCAL.read_bytes()
    encoded = base64.b64encode(script_content).decode()
    oc_exec(
        "sh",
        "-c",
        f"echo {encoded} | base64 -d > {FETCH_SCRIPT_PATH}",
        namespace=ctx.prom_namespace,
        pod=ctx.pod_name,
    )

    return f"Installed prometheus_api_client, injected {FETCH_SCRIPT_PATH}"


def _run_fetch(*, name, query, ctx, args, raw=False):
    output_path = ctx.output_dir / f"{name}.json"

    py_args = [
        FETCH_SCRIPT_PATH,
        "--url",
        ctx.prom_url,
        "--query",
        query,
        "--start",
        ctx.start_epoch,
        "--end",
        ctx.end_epoch,
    ]
    if raw:
        py_args.append("--raw")
    else:
        py_args.extend(["--step", str(args.step_seconds)])

    cmd = [
        "sh",
        "-c",
        f"PYTHONUSERBASE={PIP_USER_BASE} python3 " + " ".join(shlex.quote(a) for a in py_args),
    ]

    result = oc_exec(
        *cmd,
        namespace=ctx.prom_namespace,
        pod=ctx.pod_name,
        stdout_dest=output_path,
        log_stdout=False,
        check=False,
    )

    if not result.success:
        return "failed"

    try:
        payload = json.loads(output_path.read_text(encoding="utf-8"))
        has_data = bool(payload.get("data", {}).get("result"))
    except (json.JSONDecodeError, OSError):
        has_data = False

    return "ok" if has_data else "no_data"


@task
def execute_queries(args, ctx):
    """Run each PromQL range query via prometheus_api_client."""

    if not ctx.queries:
        return "No PromQL queries to execute"

    ok = 0
    no_data = 0
    failed = 0

    for name, promql in ctx.queries.items():
        logger.info("  querying: %s", name)
        status = _run_fetch(name=name, query=promql, ctx=ctx, args=args)
        if status == "ok":
            ok += 1
        elif status == "no_data":
            no_data += 1
        else:
            failed += 1
            logger.warning("  query %s failed", name)

    total = ok + no_data + failed
    return f"Executed {total} range queries ({ok} with data, {no_data} empty, {failed} failed)"


@task
def execute_raw_queries(args, ctx):
    """Fetch raw metric samples via prometheus_api_client instant query."""

    if not ctx.raw_metrics:
        return "No raw metrics to execute"

    ok = 0
    no_data = 0
    failed = 0

    for name, selector in ctx.raw_metrics.items():
        logger.info("  raw metric: %s", name)
        status = _run_fetch(name=name, query=selector, ctx=ctx, args=args, raw=True)
        if status == "ok":
            ok += 1
        elif status == "no_data":
            no_data += 1
        else:
            failed += 1
            logger.warning("  raw metric %s failed", name)

    total = ok + no_data + failed
    return f"Executed {total} raw queries ({ok} with data, {no_data} empty, {failed} failed)"


@always
@task
def cleanup_pod(args, ctx):
    """Delete the temporary query pod."""

    pod_name = getattr(ctx, "pod_name", None)
    prom_namespace = getattr(ctx, "prom_namespace", "openshift-monitoring")
    if pod_name:
        best_effort_oc("delete", "pod", pod_name, "-n", prom_namespace, "--ignore-not-found")

    return "Cleaned up query pod"


if __name__ == "__main__":
    run.main()
