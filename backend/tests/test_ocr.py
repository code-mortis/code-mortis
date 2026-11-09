"""Historical report fixtures and retained provider trust-boundary checks."""

import pytest

from code_mortis.core.review_results import fingerprint, report_tokens
from code_mortis.core.security import safe_endpoint


def report(state="complete"):
    item = {"item_id": "file-1", "path": "main.py"}
    return {
        "manifest": {
            "schema_version": "ocr.run-manifest/v1",
            "terminal_state": state,
            "coverage": {"selected": [item], "completed": [item], "reused": [], "failed": [], "waived": []},
        },
        "comments": [
            {
                "path": "main.py",
                "content": "Fix this with secret-key",
                "start_line": 1,
                "end_line": 1,
                "thinking": "private reasoning",
            }
        ],
    }



def test_endpoints_reject_credentials_and_metadata(monkeypatch):
    with pytest.raises(ValueError, match="credentials"):
        safe_endpoint("https://token@example.com/v1")
    monkeypatch.setattr("socket.getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("169.254.169.254", 443))])
    with pytest.raises(ValueError, match="protected system"):
        safe_endpoint("https://metadata.example")



def test_historical_finding_identity_and_usage_remain_readable():
    comment = report()["comments"][0]
    assert fingerprint(comment) == fingerprint({**comment, "content": "  Fix this with secret-key  "})
    assert fingerprint(comment) != fingerprint({**comment, "path": "different.py"})
    assert report_tokens({"summary": {"total_tokens": 123}}) == 123
    assert report_tokens(report()) is None
