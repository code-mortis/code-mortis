"""Historical diagnostics remain readable after retiring their runtime collectors."""

import pytest

from code_mortis.reviews import diagnostics as diagnostic


@pytest.mark.parametrize("origin,expected", [
    ("gateway_incomplete", "gateway returned incomplete"),
    ("ocr_mismatch", "OCR's parsed completion differs"),
    ("arguments_preserved", "recorded identical completion arguments"),
    ("unavailable", "origin could not be determined"),
])
def test_saved_diagnostics_explain_historical_origin_without_including_payload(origin, expected):
    assert expected in diagnostic.explanation({"origin": origin, "arguments": "private-source-and-credential"})
    assert "private" not in diagnostic.explanation({"origin": origin, "arguments": "private-source-and-credential"})


@pytest.mark.parametrize("value", [None, [], {}, {"origin": None}, {"origin": []}, {"origin": "unknown"}])
def test_unrecognized_historical_diagnostics_do_not_invent_an_explanation(value):
    assert diagnostic.explanation(value) == ""
