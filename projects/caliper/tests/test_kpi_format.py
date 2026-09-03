from __future__ import annotations

from projects.caliper.engine.kpi.format import (
    flatten_hierarchical_kpis,
    transform_kpis_to_hierarchical_format,
)

STUB_MODEL = type("Model", (), {"plugin_module": "projects.caliper.tests.stub_plugin"})()


def test_hierarchical_format_merges_common_labels_from_all_kpis():
    kpis = [
        {
            "run_id": "run-1",
            "kpi_id": "generic",
            "value": 1,
            "labels": {"model": "llama"},
        },
        {
            "run_id": "run-1",
            "kpi_id": "dashboard",
            "value": 2,
            "labels": {"model": "llama", "tensor_parallel_size": "2"},
        },
    ]

    output = transform_kpis_to_hierarchical_format(kpis, STUB_MODEL)

    assert output["tests"][0]["labels"] == {
        "model": "llama",
        "tensor_parallel_size": "2",
    }


def test_unregistered_kpis_are_included_from_record_fields():
    kpis = [
        {
            "run_id": "run-1",
            "kpi_id": "rhaiis_output_tok_per_sec",
            "value": 42.0,
            "unit": "tokens/s",
            "higher_is_better": True,
            "is_curve": False,
            "labels": {"model": "llama"},
            "metadata": {"run_path": "/artifacts/run-1"},
        }
    ]

    output = transform_kpis_to_hierarchical_format(kpis, STUB_MODEL)

    test = output["tests"][0]
    assert len(test["kpis"]) == 1
    kpi = test["kpis"][0]
    assert kpi["id"] == "rhaiis_output_tok_per_sec"
    assert "kpi_id" not in kpi
    assert kpi["is_curve"] is False
    assert kpi["unit"] == "tokens/s"
    assert kpi["value"] == 42.0
    assert "run_path" not in test["metadata"]
    assert test["metadata"]["run_id"] == "run-1"


def test_varying_labels_stay_on_kpi_records():
    kpis = [
        {
            "run_id": "run-1",
            "kpi_id": "generic",
            "value": 1,
            "labels": {"model": "llama", "rate_index": "0"},
        },
        {
            "run_id": "run-1",
            "kpi_id": "generic",
            "value": 2,
            "labels": {"model": "llama", "rate_index": "1"},
        },
    ]

    output = transform_kpis_to_hierarchical_format(kpis, STUB_MODEL)

    test = output["tests"][0]
    assert test["labels"] == {"model": "llama"}
    assert test["kpis"][0]["labels"] == {"rate_index": "0"}
    assert test["kpis"][1]["labels"] == {"rate_index": "1"}

    flat = flatten_hierarchical_kpis(output)
    assert {rec["labels"]["rate_index"] for rec in flat} == {"0", "1"}
    assert all(rec["labels"]["model"] == "llama" for rec in flat)


def test_catalog_kpis_use_id_not_kpi_id():
    kpis = [{"run_id": "run-1", "kpi_id": "generic", "value": 1, "labels": {}}]

    output = transform_kpis_to_hierarchical_format(kpis, STUB_MODEL)

    kpi = output["tests"][0]["kpis"][0]
    assert kpi["id"] == "generic"
    assert "kpi_id" not in kpi


def test_curve_flag_comes_from_record_when_unregistered():
    kpis = [
        {
            "run_id": "run-1",
            "kpi_id": "custom_curve",
            "value": [[1, 2], [3, 4]],
            "is_curve": True,
            "labels": {},
        }
    ]

    output = transform_kpis_to_hierarchical_format(kpis, STUB_MODEL)

    kpi = output["tests"][0]["kpis"][0]
    assert kpi["id"] == "custom_curve"
    assert kpi["is_curve"] is True
    assert kpi["value"]["count"] == 2
    assert kpi["value"]["data_points"] == [{"x": 1.0, "y": 2.0}, {"x": 3.0, "y": 4.0}]


def test_missing_is_curve_is_not_inferred_from_value():
    kpis = [
        {
            "run_id": "run-1",
            "kpi_id": "unknown_pairs",
            "value": [[1, 2], [3, 4]],
            "labels": {},
        }
    ]

    output = transform_kpis_to_hierarchical_format(kpis, STUB_MODEL)

    kpi = output["tests"][0]["kpis"][0]
    assert kpi["is_curve"] is False
    assert kpi["value"] == [[1, 2], [3, 4]]
