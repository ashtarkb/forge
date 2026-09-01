"""
Shared helpers for MCP Gateway platform install/cleanup toolbox modules.

Avoids duplicating step-lookup and namespace-wait logic across
install_platform and cleanup_platform.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

from projects.core.dsl.utils.k8s import oc, oc_resource_exists

logger = logging.getLogger(__name__)

_PLATFORM_REPO_DEFAULT = "https://github.com/Kuadrant/mcp-gateway.git"
_PLATFORM_SUBDIR_DEFAULT = "config/openshift"
_PLATFORM_CLONE_DIR = Path(os.environ.get("FORGE_BASE_DIR", "/tmp")) / "mcp-gw-platform-manifests"

# Upstream mcp-gateway still pins EOL Istio (v1.26-latest). Current
# servicemeshoperator3 / Sail rejects that for new installs.
_DEFAULT_ISTIO_VERSION = "v1.30-latest"
_ISTIO_VERSION_RE = re.compile(r"^(\s*version:\s*)\S+\s*$", re.MULTILINE)


def clone_platform_repo(
    *,
    version: str,
    repo_url: str = _PLATFORM_REPO_DEFAULT,
    subdir: str = _PLATFORM_SUBDIR_DEFAULT,
) -> Path:
    """Sparse-checkout the platform manifests from the upstream mcp-gateway repo.

    Uses ``version`` as the git ref (tag or branch). Falls back to ``main``
    if the ref doesn't exist.  The checkout is shallow and only fetches the
    ``subdir`` subtree to keep it fast.

    The clone is placed under ``$FORGE_BASE_DIR/mcp-gw-platform-manifests/``
    (defaults to ``/tmp/mcp-gw-platform-manifests/``) so that subsequent
    phases (e.g. cleanup) can reuse it without cloning again.  Call
    :func:`cleanup_platform_clone` at the end of the last phase to remove it.

    Returns the absolute path to the checked-out subdirectory
    (e.g. ``/tmp/mcp-gw-platform-manifests/mcp-gateway/config/openshift``).
    """
    repo_dir = _PLATFORM_CLONE_DIR / "mcp-gateway"
    result_path = repo_dir / subdir

    if result_path.is_dir():
        cached_ref = _get_cached_ref(repo_dir)
        requested_ref = _resolve_git_ref(repo_url, version)
        if cached_ref and cached_ref != requested_ref:
            logger.info(
                "Cached clone ref (%s) differs from requested (%s), re-cloning",
                cached_ref,
                requested_ref,
            )
            shutil.rmtree(str(_PLATFORM_CLONE_DIR), ignore_errors=True)
        else:
            logger.info("Platform manifests already cloned at %s, reusing", result_path)
            return result_path

    _PLATFORM_CLONE_DIR.mkdir(parents=True, exist_ok=True)

    ref = _resolve_git_ref(repo_url, version)

    logger.info(
        "Cloning platform manifests from %s (ref=%s, subdir=%s)",
        repo_url,
        ref,
        subdir,
    )
    subprocess.run(
        [
            "git",
            "clone",
            "--depth",
            "1",
            "--filter=blob:none",
            "--sparse",
            "--branch",
            ref,
            repo_url,
            str(repo_dir),
        ],
        check=True,
        timeout=120,
    )
    subprocess.run(
        ["git", "sparse-checkout", "set", subdir],
        cwd=str(repo_dir),
        check=True,
        timeout=30,
    )

    if not result_path.is_dir():
        raise FileNotFoundError(f"Expected directory {result_path} not found after sparse checkout")

    logger.info("Platform manifests available at %s", result_path)
    return result_path


def get_platform_clone_path(subdir: str = _PLATFORM_SUBDIR_DEFAULT) -> Path | None:
    """Return the path to a previously cloned platform checkout, or None."""
    candidate = _PLATFORM_CLONE_DIR / "mcp-gateway" / subdir
    return candidate if candidate.is_dir() else None


def cleanup_platform_clone() -> None:
    """Remove the cloned platform manifests directory."""
    if _PLATFORM_CLONE_DIR.exists():
        shutil.rmtree(str(_PLATFORM_CLONE_DIR), ignore_errors=True)
        logger.info("Cleaned up platform clone at %s", _PLATFORM_CLONE_DIR)


def _get_cached_ref(repo_dir: Path) -> str | None:
    """Return the current HEAD ref of a cached clone, or None if unreadable."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=str(repo_dir),
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
        return result.stdout.strip() or None
    except (subprocess.SubprocessError, OSError):
        return None


def _resolve_git_ref(repo_url: str, version: str) -> str:
    """Check if ``version`` exists as a remote ref; fall back to ``main``."""
    result = subprocess.run(
        ["git", "ls-remote", "--exit-code", repo_url, f"refs/tags/{version}"],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    if result.returncode == 0:
        return version

    result = subprocess.run(
        ["git", "ls-remote", "--exit-code", repo_url, f"refs/heads/{version}"],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    if result.returncode == 0:
        return version

    logger.warning(
        "Git ref '%s' not found in %s — falling back to 'main'",
        version,
        repo_url,
    )
    return "main"


def find_step(steps: list[dict], name: str) -> dict | None:
    """Find a step by name in the platform steps list."""
    for step in steps:
        if step["name"] == name:
            return step
    return None


def has_step(steps: list[dict], name: str) -> bool:
    """Check whether a named step exists."""
    return find_step(steps, name) is not None


def wait_for_namespace_termination(
    namespaces: list[str],
    timeout: int = 300,
    force_remove_finalizers: bool = False,
) -> None:
    """Wait until the given namespaces are fully gone.

    Args:
        namespaces: Namespace names to wait for.
        timeout: Maximum seconds to wait.
        force_remove_finalizers: After 60s, strip finalizers from stuck namespaces.
    """
    still_present = [
        ns for ns in namespaces if oc_resource_exists("namespace", ns) and _is_terminating(ns)
    ]
    if not still_present:
        return

    logger.info("Waiting for terminating namespaces: %s", still_present)
    deadline = time.time() + timeout
    start = time.time()

    while still_present and time.time() < deadline:
        time.sleep(10)

        if force_remove_finalizers and (time.time() - start) > 60:
            for ns in still_present:
                _force_remove_namespace_finalizers(ns)

        still_present = [ns for ns in still_present if oc_resource_exists("namespace", ns)]
        if still_present:
            logger.info("Still waiting for: %s", still_present)

    if still_present:
        raise RuntimeError(
            f"Namespaces still terminating after {timeout}s: {still_present}. "
            "Manual intervention may be required."
        )


def wait_for_crd_deletion(
    crd_name: str,
    timeout: int = 120,
) -> bool:
    """Wait until a CRD is fully removed from the cluster.

    Returns True if the CRD was confirmed gone, False if timed out.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not oc_resource_exists("crd", crd_name):
            return True
        time.sleep(5)

    logger.warning("CRD %s still present after %ds", crd_name, timeout)
    return False


def best_effort_cmd(*cmd_args: str) -> None:
    """Run an arbitrary command, swallowing timeout and other errors."""
    try:
        subprocess.run(list(cmd_args), check=False, timeout=120)
    except subprocess.TimeoutExpired:
        logger.warning("Timed out: %s", " ".join(cmd_args))
    except Exception as exc:
        logger.warning("Error: %s: %s", " ".join(cmd_args), exc)


def _is_terminating(namespace: str) -> bool:
    result = oc(
        "get",
        "namespace",
        namespace,
        "-o",
        "jsonpath={.status.phase}",
        check=False,
    )
    return result.returncode == 0 and "Terminating" in result.stdout


def _force_remove_namespace_finalizers(namespace: str) -> None:
    """Strip finalizers from a stuck namespace so it can terminate."""
    import json as json_mod

    result = oc("get", "namespace", namespace, "-o", "json", check=False)
    if result.returncode != 0 or not result.stdout:
        return

    try:
        ns_obj = json_mod.loads(result.stdout)
        if not ns_obj.get("spec", {}).get("finalizers"):
            return
        ns_obj["spec"]["finalizers"] = []
        payload = json_mod.dumps(ns_obj)

        import tempfile

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write(payload)
            tmp_path = f.name

        oc(
            "replace",
            "--raw",
            f"/api/v1/namespaces/{namespace}/finalize",
            "-f",
            tmp_path,
            check=False,
        )
        logger.info("Removed finalizers from namespace %s", namespace)
    except Exception as exc:
        logger.warning("Failed to remove finalizers from %s: %s", namespace, exc)


def patch_service_mesh_istio_version(
    kustomize_path: Path,
    version: str = _DEFAULT_ISTIO_VERSION,
) -> list[str]:
    """Rewrite ``spec.version`` in Istio / IstioCNI manifests under *kustomize_path*.

    Upstream mcp-gateway pins an EOL Istio tag that current Sail / OSSM 3
    rejects. Forge patches the cloned manifests before ``oc apply -k``.

    Returns the list of files that were modified.
    """
    if not version:
        return []

    patched: list[str] = []
    for path in sorted(kustomize_path.rglob("*.yaml")):
        text = path.read_text(encoding="utf-8")
        if "sailoperator.io" not in text and "kind: Istio" not in text:
            continue
        if "version:" not in text:
            continue

        new_text, n = _ISTIO_VERSION_RE.subn(rf"\g<1>{version}", text)
        if n == 0 or new_text == text:
            continue

        path.write_text(new_text, encoding="utf-8")
        patched.append(str(path))
        logger.info("Patched Istio version -> %s in %s", version, path)

    if not patched:
        logger.warning(
            "No Istio/IstioCNI version fields patched under %s (wanted %s)",
            kustomize_path,
            version,
        )
    return patched
