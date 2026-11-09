"""Validated engine progress reaches the run API before the final report is saved."""

import pytest
from test_review_flow import new_run, perform_review, remote

from code_mortis.integrations import github
from code_mortis.reviews import mortis


@pytest.mark.parametrize("parallel", [False, True])
def test_live_counts_reach_run_api_before_report_is_saved(seed, github_stub, client, monkeypatch, parallel):
    monkeypatch.setattr(github, "request", lambda *a, **k: remote())
    identity = new_run(parallel=parallel)

    def review(*args, progress, **kwargs):
        for completed in (0, 1, 2):
            value = {"selected": 3, "completed": completed, "reused": 0, "failed": 0}
            progress(value)
            response = client.get(f"/api/v1/runs/{identity}")
            assert response.status_code == 200
            run = response.json()["run"]
            assert run["status"] == "running" and run["report"] is None
            assert {key: run["progress"][key] for key in value} == value
        return "completed", {"message": "final report"}, []

    monkeypatch.setattr(mortis, "review", review)
    perform_review(identity)
    assert client.get(f"/api/v1/runs/{identity}").json()["run"]["report"] == {"message": "final report"}

