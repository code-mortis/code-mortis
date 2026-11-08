"""Provider-free checks for the versioned security wire representation."""

import copy
import json

from code_mortis.security_scans.payload import compact_evidence


def context(quote="return secret_store[request.key]", revision="current"):
    citation = {"revision": revision, "path": "route.py", "start_line": 1, "end_line": 1, "quote": quote}
    return {"head_sha": "a" * 40, "original_sha": "a" * 40,
            "sources": {"current": {"route.py": quote}, "original": {"route.py": "return denied_by_policy()"}},
            "citation_sources": {"source_1": {"revision": "current", "path": "route.py", "line_count": 1}},
            "proposal": {"title": "Exposure", "impact": "Limited stated impact", "source_sha": "a" * 40,
                         "evidence": [citation]},
            "history": [{"id": "history_1", "evidence": {"source_sha": "a" * 40,
                                                        "evidence": [copy.deepcopy(citation)]}}]}


def test_repeated_quotes_compact_without_changing_sources_metadata_or_input():
    supplied = context("return " + "data" * 1000)
    supplied["unrelated_metadata"] = copy.deepcopy(supplied["proposal"]["evidence"][0])
    original = copy.deepcopy(supplied)
    result = compact_evidence(supplied)
    assert supplied == original
    assert result["sources"] == supplied["sources"]
    assert result["citation_sources"] == supplied["citation_sources"]
    assert result["unrelated_metadata"] == supplied["unrelated_metadata"]
    citation = result["proposal"]["evidence"][0]
    assert citation == {"revision": "current", "path": "route.py", "start_line": 1, "end_line": 1,
                        "quote_source_id": "source_1"}
    assert result["history"][0]["evidence"]["evidence"][0] == citation
    assert result["history"][0]["id"] == "history_1"
    assert {key: value for key, value in result["proposal"].items() if key != "evidence"} == {
        key: value for key, value in supplied["proposal"].items() if key != "evidence"}
    assert len(json.dumps(result).encode()) < len(json.dumps(supplied).encode())


def test_invalid_bounds_substrings_and_missing_references_keep_quotes():
    for change in ({"start_line": 0}, {"end_line": 2}, {"start_line": 2},
                   {"quote": "secret_store"}, {"path": "other.py"},
                   {"quote_source_id": "different_reference"}):
        supplied = context()
        supplied["proposal"]["evidence"][0].update(change)
        result = compact_evidence(supplied)
        assert result["proposal"]["evidence"] == supplied["proposal"]["evidence"]
    supplied = context()
    supplied["citation_sources"] = {}
    assert compact_evidence(supplied) == supplied


def test_historical_revision_is_never_reinterpreted_as_current():
    supplied = context(revision="original")
    assert compact_evidence(supplied) == supplied
    supplied["citation_sources"]["source_2"] = {"revision": "original", "path": "route.py"}
    assert compact_evidence(supplied) == supplied
    supplied["proposal"]["evidence"][0]["quote"] = "return denied_by_policy()"
    result = compact_evidence(supplied)
    assert result["proposal"]["evidence"][0]["quote_source_id"] == "source_2"
    assert result["proposal"]["source_sha"] == supplied["proposal"]["source_sha"]


def test_historical_current_citation_keeps_its_original_revision_provenance():
    supplied = context()
    supplied["history"][0]["evidence"]["source_sha"] = "b" * 40
    result = compact_evidence(supplied)
    assert "quote_source_id" in result["proposal"]["evidence"][0]
    assert result["history"] == supplied["history"]
    del supplied["head_sha"]
    assert compact_evidence(supplied)["proposal"] == supplied["proposal"]
    supplied = context()
    del supplied["history"][0]["evidence"]["source_sha"]
    assert compact_evidence(supplied)["history"] == supplied["history"]


def test_nonstring_identifiers_quotes_and_noninteger_bounds_stay_unchanged():
    for key, values in {"revision": [None, [], 1], "path": [None, [], 1], "quote": [None, [], 1],
                        "start_line": [None, "1", True], "end_line": [None, "1", True]}.items():
        for value in values:
            supplied = context()
            supplied["proposal"]["evidence"][0][key] = value
            result = compact_evidence(supplied)
            assert result["proposal"]["evidence"] == supplied["proposal"]["evidence"]


def test_distinct_candidates_with_matching_evidence_remain_distinct():
    supplied = context()
    supplied["findings"] = [copy.deepcopy(supplied["proposal"]) for _ in range(2)]
    supplied["findings"][1]["title"] = "A different allegation"
    result = compact_evidence(supplied)
    assert len(result["findings"]) == 2
    assert [finding["title"] for finding in result["findings"]] == ["Exposure", "A different allegation"]
    assert all("quote_source_id" in finding["evidence"][0] for finding in result["findings"])


def test_short_quotes_are_kept_when_references_would_increase_wire_bytes():
    supplied = context("pass")
    assert compact_evidence(supplied) == supplied


def test_multiline_quotes_require_the_exact_range_including_line_endings():
    supplied = context("first\nsecond_source_line_for_the_range\nthird")
    citation = supplied["proposal"]["evidence"][0]
    citation.update(quote="second_source_line_for_the_range\n", start_line=2, end_line=2)
    result = compact_evidence(supplied)
    assert result["proposal"]["evidence"][0]["quote_source_id"] == "source_1"
    citation["quote"] = "second_source_line_for_the_range"
    assert compact_evidence(supplied)["proposal"]["evidence"] == supplied["proposal"]["evidence"]
