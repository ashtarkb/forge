from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from projects.cluster.library.prom.metrics import (
    build_index,
    load_definitions,
    resolve,
    resolve_files,
    select,
    write_capture_input,
)

CLUSTER_METRICS_DIR = Path(__file__).resolve().parent.parent / "metrics"
KSERVE_METRICS_DIR = Path(__file__).resolve().parent.parent.parent / "kserve" / "metrics"

YAML_FILES = sorted(CLUSTER_METRICS_DIR.glob("*.yaml")) + sorted(KSERVE_METRICS_DIR.glob("*.yaml"))


def _write_yaml(tmp_path, name, data):
    p = tmp_path / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return p


def _check_no_duplicate_keys(path: Path) -> list[str]:
    class DuplicateKeyLoader(yaml.SafeLoader):
        pass

    duplicates = []

    def _check_mapping(loader, node):
        seen = {}
        for key_node, _value_node in node.value:
            key = loader.construct_object(key_node)
            if key in seen:
                duplicates.append(
                    f"{path.name}: duplicate key {key!r} (lines {seen[key]} and {key_node.start_mark.line + 1})"
                )
            else:
                seen[key] = key_node.start_mark.line + 1
        return loader.construct_mapping(node)

    DuplicateKeyLoader.add_constructor(
        yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _check_mapping
    )

    with path.open("r", encoding="utf-8") as f:
        yaml.load(f, Loader=DuplicateKeyLoader)

    return duplicates


class TestLoadDefinitions:
    @pytest.mark.parametrize("yaml_file", YAML_FILES, ids=lambda p: p.name)
    def test_no_duplicate_keys_in_yaml(self, yaml_file):
        duplicates = _check_no_duplicate_keys(yaml_file)
        assert duplicates == [], "\n".join(duplicates)

    def test_load_all_bundled_files(self):
        defs = load_definitions(*YAML_FILES)
        assert len(defs) > 0
        keys = [d.key for d in defs]
        assert len(keys) == len(set(keys))

    def test_all_definitions_have_required_fields(self):
        for defn in load_definitions(*YAML_FILES):
            assert defn.description
            assert defn.unit
            assert defn.promql or defn.metric
            assert defn.on_error in ("ignore", "fail")

    def test_duplicate_key_across_files_raises(self, tmp_path):
        f1 = _write_yaml(
            tmp_path,
            "a.yaml",
            {
                "my_metric": {"description": "d", "unit": "u", "promql": "count(up)"},
            },
        )
        f2 = _write_yaml(
            tmp_path,
            "b.yaml",
            {
                "my_metric": {"description": "d", "unit": "u", "promql": "count(up)"},
            },
        )
        with pytest.raises(ValueError, match="duplicate metric key"):
            load_definitions(f1, f2)

    def test_unknown_field_raises(self, tmp_path):
        f = _write_yaml(
            tmp_path,
            "bad.yaml",
            {
                "m": {"description": "d", "unit": "u", "promql": "count(up)", "bogus": 1},
            },
        )
        with pytest.raises(ValueError, match="unknown fields"):
            load_definitions(f)

    def test_defaults_applied(self, tmp_path):
        f = _write_yaml(
            tmp_path,
            "minimal.yaml",
            {
                "m": {"description": "d", "unit": "u", "promql": "count(up)"},
            },
        )
        defs = load_definitions(f)
        assert defs[0].on_error == "ignore"
        assert defs[0].params == ()

    def test_metric_field_loaded(self, tmp_path):
        f = _write_yaml(
            tmp_path,
            "raw.yaml",
            {
                "m": {"description": "d", "unit": "u", "metric": 'my_gauge{ns="foo"}'},
            },
        )
        defs = load_definitions(f)
        assert defs[0].metric == 'my_gauge{ns="foo"}'
        assert defs[0].promql is None
        assert defs[0].is_raw is True
        assert defs[0].capture_expr == 'my_gauge{ns="foo"}'

    def test_both_promql_and_metric_raises(self, tmp_path):
        f = _write_yaml(
            tmp_path,
            "both.yaml",
            {
                "m": {"description": "d", "unit": "u", "promql": "up", "metric": "up"},
            },
        )
        with pytest.raises(ValueError, match="not both"):
            load_definitions(f)

    def test_neither_promql_nor_metric_raises(self, tmp_path):
        f = _write_yaml(
            tmp_path,
            "neither.yaml",
            {
                "m": {"description": "d", "unit": "u"},
            },
        )
        with pytest.raises(ValueError, match="must have either"):
            load_definitions(f)

    def test_bare_selector_in_promql_raises(self, tmp_path):
        f = _write_yaml(
            tmp_path,
            "bare.yaml",
            {
                "m": {"description": "d", "unit": "u", "promql": "my_metric"},
            },
        )
        with pytest.raises(ValueError, match="bare metric selector"):
            load_definitions(f)

    def test_bare_selector_with_labels_in_promql_raises(self, tmp_path):
        f = _write_yaml(
            tmp_path,
            "bare_labels.yaml",
            {
                "m": {"description": "d", "unit": "u", "promql": 'my_metric{ns="foo"}'},
            },
        )
        with pytest.raises(ValueError, match="bare metric selector"):
            load_definitions(f)

    def test_promql_expression_in_metric_raises(self, tmp_path):
        f = _write_yaml(
            tmp_path,
            "expr.yaml",
            {
                "m": {"description": "d", "unit": "u", "metric": "rate(my_metric[5m])"},
            },
        )
        with pytest.raises(ValueError, match="looks like a PromQL expression"):
            load_definitions(f)


class TestSelect:
    @pytest.fixture()
    def defs(self):
        return load_definitions(*YAML_FILES)

    def test_filter_by_keys(self, defs):
        result = select(defs, keys=["avg_cpu_usage_percent", "avg_memory_working_set_bytes"])
        assert {d.key for d in result} == {"avg_cpu_usage_percent", "avg_memory_working_set_bytes"}


class TestResolve:
    def test_substitute_params(self, tmp_path):
        f = _write_yaml(
            tmp_path,
            "t.yaml",
            {
                "test_metric": {
                    "description": "d",
                    "unit": "u",
                    "params": {"ns": {"description": "namespace regex"}},
                    "promql": 'avg(up{namespace=~"{ns}"})',
                },
            },
        )
        defs = load_definitions(f)
        resolved = resolve(defs, {"ns": "foo|bar"})
        assert resolved.queries == {"test_metric": 'avg(up{namespace=~"foo|bar"})'}
        assert resolved.raw_metrics == {}

    def test_default_param_used(self, tmp_path):
        f = _write_yaml(
            tmp_path,
            "t.yaml",
            {
                "test_metric": {
                    "description": "d",
                    "unit": "u",
                    "params": {"ns": {"description": "ns", "default": "default-ns"}},
                    "promql": 'avg(up{namespace=~"{ns}"})',
                },
            },
        )
        defs = load_definitions(f)
        resolved = resolve(defs, {})
        assert resolved.queries == {"test_metric": 'avg(up{namespace=~"default-ns"})'}

    def test_missing_mandatory_param_skips(self, tmp_path, caplog):
        f = _write_yaml(
            tmp_path,
            "t.yaml",
            {
                "m1": {
                    "description": "d",
                    "unit": "u",
                    "params": {"ns": {"description": "ns"}},
                    "promql": "count(up)",
                },
                "m2": {
                    "description": "d",
                    "unit": "u",
                    "params": {"ns": {"description": "ns"}, "foo": {"description": "f"}},
                    "promql": "count(up)",
                },
            },
        )
        defs = load_definitions(f)
        resolved = resolve(defs, {})
        assert resolved.queries == {}
        assert resolved.raw_metrics == {}
        assert "m1.ns" in caplog.text
        assert "m2.foo" in caplog.text

    def test_no_params_metric(self, tmp_path):
        f = _write_yaml(
            tmp_path,
            "t.yaml",
            {
                "simple": {
                    "description": "d",
                    "unit": "u",
                    "promql": "count(up)",
                },
            },
        )
        defs = load_definitions(f)
        resolved = resolve(defs, {})
        assert resolved.queries == {"simple": "count(up)"}

    def test_resolve_real_cpu_metrics(self):
        cpu_file = CLUSTER_METRICS_DIR / "resource_cpu.yaml"
        defs = load_definitions(cpu_file)
        resolved = resolve(
            defs,
            {"namespace": "test-ns", "pod_name": "my-pod"},
        )
        assert len(resolved.queries) == len(defs)
        assert resolved.raw_metrics == {}
        for promql in resolved.queries.values():
            assert "{namespace}" not in promql
            assert "test-ns" in promql

    def test_resolve_mixed_promql_and_metric(self, tmp_path):
        f = _write_yaml(
            tmp_path,
            "mixed.yaml",
            {
                "query_m": {"description": "d", "unit": "u", "promql": "rate(up[5m])"},
                "raw_m": {"description": "d", "unit": "u", "metric": "my_gauge"},
            },
        )
        defs = load_definitions(f)
        resolved = resolve(defs, {})
        assert resolved.queries == {"query_m": "rate(up[5m])"}
        assert resolved.raw_metrics == {"raw_m": "my_gauge"}

    def test_resolve_raw_gpu_metrics(self):
        gpu_file = CLUSTER_METRICS_DIR / "gpu.yaml"
        defs = load_definitions(gpu_file)
        resolved = resolve(
            defs,
            {"namespace": "test-ns", "pod_name": "my-pod-.*"},
        )
        assert resolved.queries == {}
        assert len(resolved.raw_metrics) == len(defs)
        for selector in resolved.raw_metrics.values():
            assert "{namespace}" not in selector
            assert "test-ns" in selector


class TestWriteCaptureInput:
    def test_writes_flat_yaml(self, tmp_path):
        queries = {"cpu_usage": "sum(rate(...))", "memory": "sum(...)"}
        path = write_capture_input(queries, tmp_path / "input.yaml")
        assert path.exists()

        with path.open("r") as f:
            loaded = yaml.safe_load(f)
        assert loaded == queries

    def test_creates_parent_dirs(self, tmp_path):
        path = write_capture_input({"m": "up"}, tmp_path / "a" / "b" / "input.yaml")
        assert path.exists()


class TestBuildIndex:
    @pytest.fixture()
    def defs_file(self, tmp_path):
        return _write_yaml(
            tmp_path / "defs",
            "t.yaml",
            {
                "ok_metric": {
                    "description": "d",
                    "unit": "cores",
                    "params": {"ns": {"description": "ns"}},
                    "promql": 'sum(up{ns="{ns}"})',
                },
                "empty_metric": {
                    "description": "d2",
                    "unit": "cores",
                    "promql": "count(up)",
                },
                "error_metric": {
                    "description": "d3",
                    "unit": "bytes",
                    "promql": "count(up)",
                },
                "missing_metric": {
                    "description": "d4",
                    "unit": "bytes",
                    "promql": "count(up)",
                },
            },
        )

    def test_builds_index_with_results(self, tmp_path, defs_file):
        defs = load_definitions(defs_file)

        results_dir = tmp_path / "results"
        results_dir.mkdir()
        (results_dir / "ok_metric.json").write_text(
            json.dumps(
                {
                    "status": "success",
                    "data": {
                        "resultType": "matrix",
                        "result": [{"metric": {}, "values": [[1, "1"]]}],
                    },
                }
            )
        )
        (results_dir / "empty_metric.json").write_text(
            json.dumps({"status": "success", "data": {"resultType": "matrix", "result": []}})
        )
        (results_dir / "error_metric.json").write_text(
            json.dumps({"status": "error", "errorType": "bad_data", "error": "parse error"})
        )

        index_path = build_index(
            defs, {"ns": "test"}, results_dir, timestamp="2026-01-01T00:00:00Z"
        )
        assert index_path.exists()

        with index_path.open("r") as fh:
            index = yaml.safe_load(fh)

        assert index["timestamp"] == "2026-01-01T00:00:00Z"
        assert index["results"]["ok_metric"]["status"] == "ok"
        assert index["results"]["ok_metric"]["params"] == {"ns": "test"}
        assert "promql" in index["results"]["ok_metric"]
        assert index["results"]["empty_metric"]["status"] == "no_data"
        assert index["results"]["error_metric"]["status"] == "error"
        assert index["results"]["missing_metric"]["status"] == "no_data"

    def test_build_index_with_raw_metric(self, tmp_path):
        f = _write_yaml(
            tmp_path / "defs",
            "raw.yaml",
            {
                "raw_m": {
                    "description": "d",
                    "unit": "bytes",
                    "metric": "my_gauge",
                },
            },
        )
        defs = load_definitions(f)
        results_dir = tmp_path / "results"
        results_dir.mkdir()
        (results_dir / "raw_m.json").write_text(
            json.dumps(
                {
                    "status": "success",
                    "data": {
                        "resultType": "matrix",
                        "result": [{"metric": {}, "values": [[1, "1"]]}],
                    },
                }
            )
        )
        index_path = build_index(defs, {}, results_dir, timestamp="2026-01-01T00:00:00Z")
        with index_path.open("r") as fh:
            index = yaml.safe_load(fh)
        assert index["results"]["raw_m"]["status"] == "ok"
        assert "metric" in index["results"]["raw_m"]
        assert "promql" not in index["results"]["raw_m"]


class TestResolveFiles:
    def test_resolve_by_name(self):
        dirs = [CLUSTER_METRICS_DIR, KSERVE_METRICS_DIR]
        result = resolve_files(["resource_cpu", "vllm_latency"], dirs)
        assert len(result) == 2
        assert result[0] == CLUSTER_METRICS_DIR / "resource_cpu.yaml"
        assert result[1] == KSERVE_METRICS_DIR / "vllm_latency.yaml"

    def test_resolve_absolute_path(self):
        absolute = str(CLUSTER_METRICS_DIR / "workload.yaml")
        result = resolve_files([absolute], [])
        assert len(result) == 1
        assert result[0] == Path(absolute)

    def test_resolve_relative_path_with_slash(self):
        relative = str(CLUSTER_METRICS_DIR / "workload.yaml")
        result = resolve_files([relative], [])
        assert len(result) == 1

    def test_missing_file_raises(self):
        with pytest.raises(FileNotFoundError, match="no_such_file"):
            resolve_files(["no_such_file"], [CLUSTER_METRICS_DIR])

    def test_first_match_wins(self, tmp_path):
        d1 = tmp_path / "d1"
        d2 = tmp_path / "d2"
        d1.mkdir()
        d2.mkdir()
        (d1 / "test.yaml").write_text(
            yaml.safe_dump({"m1": {"description": "d", "unit": "u", "promql": "count(up)"}})
        )
        (d2 / "test.yaml").write_text(
            yaml.safe_dump({"m2": {"description": "d", "unit": "u", "promql": "count(up)"}})
        )
        result = resolve_files(["test"], [d1, d2])
        assert result == [d1 / "test.yaml"]
