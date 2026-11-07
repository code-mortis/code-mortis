"""Frozen M0 scope expectations, reusable when Mortis replaces the OCR adapter."""

import json
from pathlib import Path

import pytest
from test_graph_admission_policy import manifest_request

from code_mortis.code_graph import errors
from code_mortis.code_graph.client import validate_result
from code_mortis.code_graph.extract import extract
from code_mortis.core import file_policy
from code_mortis.integrations import ocr
from code_mortis.schemas.code_graph import CodeGraphLimits

SCOPE = json.loads((Path(__file__).parent / "fixtures" / "engine_scope_v1.json").read_text())


@pytest.mark.parametrize("case", SCOPE["paths"], ids=lambda case: case["id"])
def test_frozen_path_policy(case):
    rules = file_policy.policy({"exclusions": case.get("exclusions", "")})
    assert rules["version"] == SCOPE["policy_version"]
    assert (not file_policy.excluded(case["path"], rules)) is case["eligible"]


def test_frozen_graph_defaults_and_upper_bounds():
    assert CodeGraphLimits().model_dump() == SCOPE["graph_defaults"]
    assert CodeGraphLimits(**SCOPE["graph_maxima"]).model_dump() == SCOPE["graph_maxima"]
    for field, maximum in SCOPE["graph_maxima"].items():
        with pytest.raises(ValueError):
            CodeGraphLimits(**{**SCOPE["graph_maxima"], field: maximum + 1})


@pytest.mark.parametrize("case", SCOPE["graph_cases"], ids=lambda case: case["id"])
def test_frozen_graph_admission(case, monkeypatch):
    entries = {path: text.encode() for path, text in case["files"].items()}
    if "failure" in case:
        loaded = []
        with pytest.raises(errors.GraphError) as raised:
            manifest_request(monkeypatch, entries, case["limits"], loaded=loaded)
        assert raised.value.failure.code == case["failure"]
        assert raised.value.failure.measurements == case["measurements"]
        assert loaded == []
        return
    payload, loaded = manifest_request(
        monkeypatch, entries, case["limits"], custom=case.get("additional_exclusions", "")
    )
    assert sorted(loaded) == sorted(case["sources"])
    assert {item.path: item.exclusion for item in payload.files if item.exclusion} == case["exclusions"]
    result = validate_result(payload, extract(payload))
    assert sorted(item.path for item in result.files if item.status == "parsed") == sorted(case["sources"])
    assert sorted(item.path for item in result.files if item.status == "unsupported") == sorted(case["unsupported"])
    assert all(item.content_base64 is None for item in payload.files if item.path not in case["sources"])


@pytest.mark.parametrize("case", SCOPE["review_gates"], ids=lambda case: case["id"])
def test_frozen_review_gate(case):
    paths = [f"src/file_{number}.py" for number in range(case["selected_count"])]
    result = ocr.file_limit_result(paths, case["maximum"])
    assert (result is not None) is case["skipped"]
    if result:
        status, report, comments = result
        assert status == "skipped" and comments == []
        coverage = report["manifest"]["coverage"]
        assert [item["path"] for item in coverage["selected"]] == paths
        assert coverage["completed"] == [] and coverage["reused"] == []
        assert len(coverage["waived"]) == len(paths)
        assert report["summary"]["total_tokens"] == 0
