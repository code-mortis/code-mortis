"""Graph discovery uses the existing evidence and budget contracts in every adapter."""

import copy

import pytest
from sqlalchemy import select
from test_assessment import pipeline as pipeline
from test_assessment import verdict
from test_review_flow import new_run, perform_review
from test_verification import CONTEXT, RESULT
from test_verification import scenario as scenario

from code_mortis.core.db import transaction
from code_mortis.models import Connection, ReviewCandidate, ReviewRun
from code_mortis.reviews import assessment, graph_context, verification
from code_mortis.reviews import service as reviews


@pytest.fixture
def suggestions(monkeypatch):
    calls = []
    def prepare(options, path, sha, *args, **kwargs):
        calls.append((options, path, sha))
        return {'version': graph_context.VERSION, 'commit_sha': sha, 'reason': 'used',
                'status': 'partial', 'snapshot_id': 'snapshot', 'reserved_bytes': 30,
                'files': [{'path': 'related.py', 'blob_sha': 'b' * 40, 'size_bytes': 30}],
                'source_bytes': 30, 'truncated': False}, {'sources': []}
    monkeypatch.setattr(graph_context, 'prepare', prepare)
    return calls


@pytest.mark.parametrize('kind', ['api'])
def test_assessment_dispatch_and_persistence(pipeline, suggestions, monkeypatch, kind):
    with transaction() as db:
        connection = db.get(Connection, 'connection')
        connection.kind, connection.owner_id, connection.auth_status = kind, 7, 'connected'
    original = assessment.build_context
    def build(*args):
        context = original(*args)
        return {**context, 'current_path': context['finding']['path']}
    monkeypatch.setattr(assessment, 'build_context', build)
    seen = []
    def respond(context):
        seen.append(context)
        assert context['code_graph']['reason'] == 'used'
        assert 'related.py' not in context['sources']['current']
        return verdict(context)
    pipeline['respond'] = respond
    identity = new_run(assessed=True)
    with transaction() as db:
        row = db.get(ReviewRun, identity)
        row.config = {**row.config, 'code_graph': {'version': graph_context.VERSION}}
    perform_review(identity)
    with transaction() as db:
        row = db.scalar(select(ReviewCandidate).where(ReviewCandidate.run_id == identity))
        assert row.status == 'completed' and row.context['code_graph']['snapshot_id'] == 'snapshot'
        assert row.usage == {'tokens': 25}
    assert len(seen) == len(suggestions) == 1
    assert suggestions[0][2] == 'a' * 40


@pytest.mark.parametrize('kind', ['api'])
def test_verification_dispatch_preserves_original_current_evidence_and_budget(scenario, suggestions, monkeypatch, kind):
    with transaction() as db:
        connection = db.get(Connection, 'connection')
        connection.kind, connection.owner_id, connection.auth_status = kind, 7, 'connected'
        run = db.get(ReviewRun, scenario['run'])
        run.trigger, run.actor_id, run.owner_id = 'manual', 7, 7
        run.config = {**run.config, 'connection': reviews.connection_snapshot(connection),
                      'code_graph': {'version': graph_context.VERSION}}
    monkeypatch.setattr(verification, 'build_context', lambda *a, **kwargs: {
        **copy.deepcopy(CONTEXT), 'finding': {'path': 'src/retry.py'}, 'current_path': 'src/retry.py'})
    seen = []
    def respond(context, budget):
        seen.append(context)
        assert context['code_graph']['reason'] == 'used'
        assert context['sources'] == CONTEXT['sources']
        assert context['original_sha'] == 'a' * 40 and context['head_sha'] == 'c' * 40
        assert budget == 199900
        return copy.deepcopy(RESULT), {'tokens': 50}
    monkeypatch.setattr(verification, 'mortis_verify', lambda *a, **kwargs: respond(a[2], a[3]))
    perform_review(scenario['run'])
    from test_verification import saved
    row, finding, _ = saved()
    assert row.verdict == 'fixed' and row.usage == {'tokens': 50}
    assert row.context['code_graph']['snapshot_id'] == 'snapshot'
    assert finding.status == 'open' and row.resolution == 'pending'
    assert len(seen) == len(suggestions) == 1
    assert not scenario['mutations']
