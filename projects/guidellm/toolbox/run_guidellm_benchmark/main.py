#!/usr/bin/env python3

from __future__ import annotations

import gzip
import json
import logging
import subprocess
import time
from pathlib import Path

from projects.core.dsl import always, entrypoint, execute_tasks, retry, task
from projects.core.dsl.utils import write_json, write_text
from projects.core.dsl.utils.k8s import (
    oc,
    oc_apply,
    oc_get_json,
)
from projects.guidellm.toolbox.run_guidellm_benchmark.utils import (
    expand_guidellm_runs,
    render_guidellm_copy_pod_from_parts,
    render_guidellm_job_from_parts,
    render_guidellm_pvc_from_parts,
)

logger = logging.getLogger(__name__)
WAIT_POLL_INTERVAL_SECONDS = 10
JOB_COMPLETION_GRACE_SECONDS = 60


def trim_benchmark_json(obj):
    """Remove 'requests' field recursively from JSON data"""
    if isinstance(obj, dict):
        return {
            k: trim_benchmark_json(v)
            for k, v in obj.items()
            if k
            not in [
                "requests",
            ]
        }
    elif isinstance(obj, list):
        return [trim_benchmark_json(item) for item in obj]
    else:
        return obj


@entrypoint
def run(
    *,
    endpoint_url: str,
    name: str = "guidellm-benchmark",
    namespace: str = "",
    image: str = "ghcr.io/vllm-project/guidellm:v0.6.0",
    timeout: int = 900,
    pvc_size: str = "1Gi",
    pvc_storage_class: str | None = None,
    guidellm_args: list[str] | None = None,
    hf_token_secret: str = "",
    fs_group: int | None = None,
    keep_full_benchmark_file: bool = False,
) -> int:
    """
    Run the GuideLLM benchmark against a resolved endpoint.

    Args:
        endpoint_url: Endpoint URL for the LLM inference service to benchmark
        name: Name of the benchmark job
        namespace: Namespace to run the benchmark job in (empty string auto-detects current namespace)
        image: Full container image reference for the benchmark
        timeout: Active deadline for the Job and timeout in seconds to wait for completion
        pvc_size: Size of the PersistentVolumeClaim for storing results
        guidellm_args: List of additional guidellm arguments (e.g., ["--rate=10", "--max-seconds=30"])
        hf_token_secret: Name of the K8s secret containing HF_TOKEN. If empty, HF_TOKEN is not injected.
        fs_group: If set, adds securityContext.fsGroup to the GuideLLM job pod.
        keep_full_benchmark_file: Whether to keep the full untrimmed benchmark JSON files alongside trimmed ones (default: False)
    """

    if timeout <= 0:
        raise ValueError("timeout must be greater than zero")

    execute_tasks(locals())
    return 0


@task
def validate_parameters(args, ctx):
    """Validate and normalize parameters"""

    # Ensure guidellm_args is a list
    ctx.guidellm_args = args.guidellm_args or []
    ctx.guidellm_runs = expand_guidellm_runs(ctx.guidellm_args)

    # Auto-detect namespace if empty
    if not args.namespace:
        result = oc("project", "-q", check=False)
        if result.returncode == 0:
            ctx.target_namespace = result.stdout.strip()
        else:
            raise RuntimeError("Could not auto-detect current namespace")
    else:
        ctx.target_namespace = args.namespace

    ctx.benchmark_name = args.name
    ctx.image = args.image

    return f"Validated parameters for benchmark {ctx.benchmark_name} in namespace {ctx.target_namespace}"


@task
def cleanup_previous_guidellm_resources_task(args, ctx):
    """Delete previous GuideLLM benchmark helper resources"""

    _best_effort_delete(
        "GuideLLM benchmark copy pod",
        "delete",
        "pod",
        f"{ctx.benchmark_name}-copy",
        "-n",
        ctx.target_namespace,
        "--ignore-not-found=true",
    )
    _best_effort_delete(
        "GuideLLM benchmark job",
        "delete",
        "job",
        ctx.benchmark_name,
        "-n",
        ctx.target_namespace,
        "--ignore-not-found=true",
    )
    _best_effort_delete(
        "GuideLLM benchmark PVC",
        "delete",
        "pvc",
        ctx.benchmark_name,
        "-n",
        ctx.target_namespace,
        "--ignore-not-found=true",
    )
    return f"Deleted previous GuideLLM resources for {ctx.benchmark_name}"


def _best_effort_delete(description: str, *oc_args: str) -> None:
    try:
        oc(*oc_args, check=False, timeout_seconds=60)
    except subprocess.TimeoutExpired:
        logger.warning("Timed out deleting %s: oc %s", description, " ".join(oc_args))


@task
def create_guidellm_resources_task(args, ctx):
    """Create the GuideLLM benchmark job and PVC with job as owner"""

    # Ensure src directory exists
    (args.artifact_dir / "src").mkdir(parents=True, exist_ok=True)

    # Create the job first
    oc_apply(
        args.artifact_dir / "src" / "guidellm-job.yaml",
        render_guidellm_job_from_parts(
            namespace=ctx.target_namespace,
            name=ctx.benchmark_name,
            image=ctx.image,
            endpoint_url=args.endpoint_url,
            guidellm_args=ctx.guidellm_args,
            timeout_seconds=args.timeout,
            hf_token_secret=args.hf_token_secret,
            fs_group=args.fs_group,
        ),
    )

    # Get the job metadata for owner reference
    job_data = oc_get_json("job", name=ctx.benchmark_name, namespace=ctx.target_namespace)

    # Create owner reference from job metadata
    owner_reference = {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "name": job_data["metadata"]["name"],
        "uid": job_data["metadata"]["uid"],
        "controller": True,
        "blockOwnerDeletion": True,
    }

    # Create the PVC with job as owner
    oc_apply(
        args.artifact_dir / "src" / "guidellm-pvc.yaml",
        render_guidellm_pvc_from_parts(
            namespace=ctx.target_namespace,
            name=ctx.benchmark_name,
            pvc_size=args.pvc_size,
            pvc_storage_class=args.pvc_storage_class,
            owner_reference=owner_reference,
        ),
    )

    ctx.wait_deadline = time.monotonic() + args.timeout + JOB_COMPLETION_GRACE_SECONDS
    return f"GuideLLM benchmark {ctx.benchmark_name} created with job as PVC owner"


# An upper safety bound only; ctx.wait_deadline enforces the per-run timeout.
@retry(attempts=1080, delay=WAIT_POLL_INTERVAL_SECONDS, backoff=1.0)
@task
def wait_guidellm_benchmark_task(args, ctx):
    """Wait for the GuideLLM benchmark job to complete"""

    # Check if job is still active first
    active_result = oc(
        "get",
        "job",
        ctx.benchmark_name,
        "-n",
        ctx.target_namespace,
        "-o",
        "jsonpath={.status.active}",
        check=False,
    )

    active = active_result.stdout.strip() == "1" if active_result.returncode == 0 else False

    if active:
        if time.monotonic() >= ctx.wait_deadline:
            raise TimeoutError(
                f"GuideLLM benchmark {ctx.benchmark_name} did not complete within {args.timeout}s"
            )
        logger.info("Job %s is still active, retrying...", ctx.benchmark_name)
        return False  # Retry immediately

    # Job is not active, check final status
    succeeded_result = oc(
        "get",
        "job",
        ctx.benchmark_name,
        "-n",
        ctx.target_namespace,
        "-o",
        "jsonpath={.status.succeeded}",
        check=False,
    )
    failed_result = oc(
        "get",
        "job",
        ctx.benchmark_name,
        "-n",
        ctx.target_namespace,
        "-o",
        "jsonpath={.status.failed}",
        check=False,
    )

    succeeded = (
        succeeded_result.stdout.strip() == "1" if succeeded_result.returncode == 0 else False
    )
    failed = failed_result.stdout.strip() == "1" if failed_result.returncode == 0 else False

    logger.info(
        "Job %s final status - succeeded: %s, failed: %s", ctx.benchmark_name, succeeded, failed
    )

    if succeeded:
        return f"GuideLLM benchmark {ctx.benchmark_name} completed"
    if failed:
        # Write failure file
        failure_file = args.artifact_dir / "FAILURE.txt"
        failure_message = f"""GuideLLM benchmark job '{ctx.benchmark_name}' failed.

Check the job logs for detailed error information:
  artifacts/guidellm_benchmark_job.logs
"""
        write_text(failure_file, failure_message)
        logger.error(
            "GuideLLM job %s failed. Failure details written to %s",
            ctx.benchmark_name,
            failure_file,
        )

        raise RuntimeError(f"GuideLLM job {ctx.benchmark_name} failed")
    if time.monotonic() >= ctx.wait_deadline:
        raise TimeoutError(
            f"GuideLLM benchmark {ctx.benchmark_name} did not complete within {args.timeout}s"
        )
    return False  # Retry


@always
@task
def capture_guidellm_state_task(args, ctx):
    """Capture GuideLLM benchmark job state and logs"""

    capture_guidellm_state(
        artifact_dir=args.artifact_dir,
        namespace=ctx.target_namespace,
        benchmark_name=ctx.benchmark_name,
    )
    return f"GuideLLM benchmark {ctx.benchmark_name} state captured"


@task
def create_copy_pod(args, ctx):
    """Create copy pod for GuideLLM results"""

    pod_data = oc_get_json(
        "pods",
        namespace=ctx.target_namespace,
        selector=f"job-name={ctx.benchmark_name}",
        ignore_not_found=True,
    )
    node_name = None
    if pod_data and pod_data.get("items"):
        node_name = pod_data["items"][0].get("spec", {}).get("nodeName")

    oc_apply(
        args.artifact_dir / "src" / "guidellm-copy-pod.yaml",
        render_guidellm_copy_pod_from_parts(
            namespace=ctx.target_namespace,
            name=ctx.benchmark_name,
            pvc_size=args.pvc_size,
            node_name=node_name,
        ),
    )
    return f"Created copy pod {ctx.benchmark_name}-copy"


@retry(attempts=60, delay=5, backoff=1.0)
@task
def wait_copy_pod_ready(args, ctx):
    """Wait for copy pod to be ready"""

    payload = oc_get_json(
        "pod",
        name=f"{ctx.benchmark_name}-copy",
        namespace=ctx.target_namespace,
    )
    conditions = payload.get("status", {}).get("conditions", [])
    if any(
        condition.get("type") == "Ready" and condition.get("status") == "True"
        for condition in conditions
    ):
        return f"Copy pod {ctx.benchmark_name}-copy ready"
    return False  # Retry


@task
def extract_results(args, ctx):
    """Extract GuideLLM results from copy pod"""

    results_dir = args.artifact_dir / "artifacts" / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    extracted_files: list[dict[str, str | None]] = []
    for run in ctx.guidellm_runs:
        if run.rate is None:
            remote_path = "/results/benchmarks.json"
            local_path = results_dir / "benchmarks.json"
        else:
            remote_path = f"/results/benchmarks-{run.label}.json"
            local_path = results_dir / f"benchmarks-{run.label}.json"

        logger.info(f"Retrieving the compressed benchmark file for {run.label}...")

        # Save compressed version to temp file first
        temp_gz_path = local_path.with_suffix(".json.gz")

        result = oc(
            "exec",
            "-n",
            ctx.target_namespace,
            f"{ctx.benchmark_name}-copy",
            "--",
            "gzip",
            "-c",
            remote_path,
            check=False,
            log_stdout=False,
            stdout_dest=temp_gz_path,
            text=False,
        )
        if result.returncode != 0:
            raise RuntimeError(f"No results found for {ctx.benchmark_name} run {run.label}")

        # Extract and parse JSON from local compressed file
        logger.info(f"Extracting compressed benchmark file for {run.label}...")
        try:
            with gzip.open(temp_gz_path, "rt", encoding="utf-8") as f:
                json_content = f.read()
        except Exception as e:
            raise RuntimeError(
                f"Failed to extract compressed results for run {run.label}: {e}"
            ) from e

        # Always create trimmed version
        logger.info(f"Trimming the benchmark.json file for {run.label}...")
        try:
            raw_data = json.loads(json_content)
            cleaned_data = trim_benchmark_json(raw_data)
            with open(local_path, "w") as f:
                json.dump(cleaned_data, f, separators=(",", ":"))
        except json.JSONDecodeError:
            logger.warning(f"Failed to parse JSON for {run.label}, writing raw data")
            write_text(local_path, json_content)

        # Manage full benchmark file based on flag
        if not args.keep_full_benchmark_file:
            # Remove the compressed full file
            if temp_gz_path.exists():
                temp_gz_path.unlink()
                logger.info(f"Removed full benchmark file {temp_gz_path.name}")
        else:
            logger.info(f"Keeping full benchmark file {temp_gz_path.name}")
        extracted_files.append(
            {
                "label": run.label,
                "rate": run.rate,
                "remote_path": remote_path,
                "local_path": str(local_path.relative_to(args.artifact_dir)),
            }
        )

    write_json(
        results_dir / "index.json",
        {
            "benchmark_name": ctx.benchmark_name,
            "runs": extracted_files,
        },
    )

    return f"Extracted results for {ctx.benchmark_name}"


def _copy_result_file(
    namespace: str,
    pod: str,
    remote_path: str,
    local_path: Path,
    *,
    trim_json: bool = True,
    keep_full_file: bool = False,
) -> Path | None:
    """Copy a result file from a pod, with optional compression and trimming.

    Args:
        namespace: Kubernetes namespace
        pod: Pod name
        remote_path: Path to file inside the pod
        local_path: Local destination path
        trim_json: Whether to trim large fields from JSON files (default: True)
        keep_full_file: Whether to keep the full untrimmed file alongside trimmed one (default: False)

    Returns:
        Path to the copied file, or None if copy failed
    """
    import gzip

    # Use compression approach for better handling of large files
    logger.info(f"Retrieving compressed file from {remote_path}...")

    # Save compressed version to temp file first
    temp_gz_path = local_path.with_suffix(".json.gz")

    # Compress and stream directly from pod
    result = oc(
        "exec",
        "-n",
        namespace,
        pod,
        "--",
        "gzip",
        "-c",
        remote_path,
        check=False,
        log_stdout=False,
        stdout_dest=temp_gz_path,
        text=False,
    )
    if result.returncode != 0:
        logging.getLogger(__name__).warning("gzip extraction failed (rc=%d)", result.returncode)
        return None

    # Extract and parse JSON from local compressed file
    try:
        with gzip.open(temp_gz_path, "rt", encoding="utf-8") as f:
            json_content = f.read()
    except Exception as e:
        logging.getLogger(__name__).warning("Failed to extract compressed results: %s", e)
        temp_gz_path.unlink(missing_ok=True)
        return None

    # Create trimmed version if requested and file is JSON
    if trim_json and local_path.suffix.lower() == ".json":
        logger.info(f"Trimming JSON file {local_path.name}...")
        try:
            raw_data = json.loads(json_content)
            cleaned_data = trim_benchmark_json(raw_data)
            with open(local_path, "w") as f:
                json.dump(cleaned_data, f, separators=(",", ":"))
        except json.JSONDecodeError:
            logger.warning(f"Failed to parse JSON, writing raw data to {local_path}")
            write_text(local_path, json_content)
    else:
        # Write raw content for non-JSON files or when trimming is disabled
        write_text(local_path, json_content)

    # Manage full file based on keep_full_file flag
    if not keep_full_file:
        # Remove the compressed full file
        if temp_gz_path.exists():
            temp_gz_path.unlink()
            logger.info(f"Removed full file {temp_gz_path.name}")
    else:
        logger.info(f"Keeping full file {temp_gz_path.name}")

    if local_path.exists() and local_path.stat().st_size > 0:
        return local_path

    return None


@task
def cleanup_copy_pod(args, ctx):
    """Delete the copy pod after results extraction"""

    _best_effort_delete(
        "GuideLLM benchmark copy pod",
        "delete",
        "pod",
        f"{ctx.benchmark_name}-copy",
        "-n",
        ctx.target_namespace,
        "--ignore-not-found=true",
    )
    return f"Cleaned up copy pod {ctx.benchmark_name}-copy"


@task
def cleanup_guidellm_resources(args, ctx):
    """Delete the GuideLLM benchmark job and PVC at the end"""

    _best_effort_delete(
        "GuideLLM benchmark job",
        "delete",
        "job",
        ctx.benchmark_name,
        "-n",
        ctx.target_namespace,
        "--ignore-not-found=true",
    )
    _best_effort_delete(
        "GuideLLM benchmark PVC",
        "delete",
        "pvc",
        ctx.benchmark_name,
        "-n",
        ctx.target_namespace,
        "--ignore-not-found=true",
    )
    return f"Cleaned up GuideLLM benchmark resources for {ctx.benchmark_name}"


def capture_guidellm_state(*, artifact_dir: Path, namespace: str, benchmark_name: str) -> None:
    artifacts_dir = artifact_dir / "artifacts"

    capture_get(
        "job",
        benchmark_name,
        namespace,
        "yaml",
        artifacts_dir / "guidellm_benchmark_job.yaml",
    )
    capture_get(
        "pods",
        None,
        namespace,
        "yaml",
        artifacts_dir / "guidellm_benchmark_job.pods.yaml",
        selector=f"job-name={benchmark_name}",
    )

    # Capture job logs
    oc(
        "logs",
        f"job/{benchmark_name}",
        "-n",
        namespace,
        check=False,
        log_stdout=False,
        stdout_dest=artifacts_dir / "guidellm_benchmark_job.logs",
    )

    # Capture additional debugging info
    oc(
        "get",
        "pods",
        "-n",
        namespace,
        "-l",
        f"job-name={benchmark_name}",
        "-oyaml",
        check=False,
        log_stdout=False,
        stdout_dest=artifacts_dir / "guidellm_benchmark_pods.yaml",
    )

    oc(
        "get",
        "job",
        benchmark_name,
        "-n",
        namespace,
        "-oyaml",
        check=False,
        log_stdout=False,
        stdout_dest=artifacts_dir / "guidellm_benchmark_job_detailed.yaml",
    )

    oc(
        "logs",
        f"job/{benchmark_name}",
        "-n",
        namespace,
        check=False,
        log_stdout=False,
        stdout_dest=artifacts_dir / "guidellm_benchmark_job_logs.txt",
    )


def capture_get(
    kind: str,
    name: str | None,
    namespace: str,
    output: str,
    destination: Path,
    *,
    selector: str | None = None,
) -> None:
    args = ["get", kind]
    if name:
        args.append(name)
    args.extend(["-n", namespace])
    if selector:
        args.extend(["-l", selector])
    args.extend(["-o", output])
    oc(*args, check=False, log_stdout=False, stdout_dest=destination)


if __name__ == "__main__":
    run.main()
