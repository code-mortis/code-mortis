import copy
import json

import pytest
from sqlalchemy import select
from test_code_graph_workflow import fixture_git, run
from test_code_graph_workflow import graph as graph

from code_mortis import jobs
from code_mortis.code_graph import retrieval, service, store
from code_mortis.core.db import transaction
from code_mortis.integrations import ocr, runner_client
from code_mortis.models import (
    CodeGraphRepository,
    CodeGraphSnapshot,
    Connection,
    Job,
    Repository,
    User,
)
from code_mortis.reviews import delegated, graph_context, verification
from code_mortis.schemas.common import RepoSettings, native_prompt_size


@pytest.fixture
def related(graph, monkeypatch):
    root = graph['source']
    (root / 'a.py').write_text('from .one import value\nfrom .two import value\nfrom .three import value\nfrom .four import value\n')
    for name in ('one', 'two', 'three', 'four'):
        (root / f'{name}.py').write_text(f'def {name}():\n    return "private-fixture-value"\n')
    fixture_git(root, 'add', '.')
    fixture_git(root, 'commit', '-qm', 'related source')
    graph['head'] = fixture_git(root, 'rev-parse', 'HEAD')
    row, _ = run(service.enqueue_snapshot(22))
    graph['snapshot'] = row.id
    with transaction() as db:
        connection = db.get(Connection, 'connection')
        connection.kind, connection.owner_id = 'chatgpt', 7
        graph['options'] = graph_context.configuration(db, db.get(Repository, 22), connection, db.get(User, 7))
    monkeypatch.setattr(verification, 'filter_context', lambda repo, env, sources, options, stopped: set(sources))
    return graph


def prepare(related, **kwargs):
    return graph_context.prepare(related['options'], 'a.py', kwargs.pop('sha', related['head']), related['source'], {},
                                 RepoSettings().model_dump(), {'api_key': 'private-fixture-value'}, lambda: False,
                                 remaining=kwargs.pop('remaining', graph_context.MAX_RUN_BYTES), **kwargs)


def test_metadata_navigation_does_not_need_git_volume_and_source_still_does(related, monkeypatch):
    def locked():
        pytest.fail('Metadata lookup attempted to acquire the source volume lock')
    monkeypatch.setattr(store, 'locked', locked)
    result = retrieval.query(retrieval.GraphQuery(repository_id=22, commit_sha=related['head'],
                             operation='dependencies', paths=['a.py'], depth=1), 7)
    assert result['snapshot_id'] == related['snapshot'] and len(result['items']) == 4
    assert result['file_manifest']['one.py']['size_bytes'] > 0
    selection, context = prepare(related)
    assert selection['reason'] == 'used' and len(context['sources']) == 3
    assert selection['truncated']
    assert [row['path'] for row in context['sources']] == ['four.py', 'one.py', 'three.py']
    assert 'private-fixture-value' not in json.dumps(context)
    assert 'text' not in json.dumps(selection)
    assert all(source['commit_sha'] == related['head'] for source in [context])


def test_missing_head_queues_once_without_using_default_or_waiting(related):
    first, context = prepare(related, sha=related['first'])
    second, _ = prepare(related, sha=related['first'])
    assert first['reason'] == second['reason'] == 'unavailable' and context is None
    with transaction() as db:
        queued = db.scalars(select(Job).where(Job.kind == 'code_graph_refresh')).all()
        assert len(queued) == 1 and queued[0].payload['commit_sha'] == related['first']
        default_job = service.schedule_refresh(db, 22)
        assert default_job != queued[0].id
        job_id = queued[0].id
    jobs.execute_job(job_id)
    with transaction() as db:
        snapshot = db.scalar(select(CodeGraphSnapshot).where(CodeGraphSnapshot.commit_sha == related['first']))
        assert snapshot.status == 'queued'
        assert db.get(CodeGraphRepository, 22).observed_sha != related['first']
    run(snapshot.id)
    frozen, context = prepare(related, sha=related['first'], saved=first)
    assert frozen == first and context is None
    fresh, context = prepare(related, sha=related['first'])
    assert fresh['snapshot_id'] == snapshot.id


def test_retry_uses_saved_selection_after_retention_without_requery(related, monkeypatch):
    selection, first = prepare(related)
    with transaction() as db:
        db.delete(db.get(CodeGraphSnapshot, related['snapshot']))
    monkeypatch.setattr(retrieval, 'query', lambda *a: pytest.fail('Retry changed its graph selection'))
    saved, second = prepare(related, saved=selection, remaining=0)
    assert saved == selection and second == first


@pytest.mark.parametrize('change,reason', [('disabled', 'access_unavailable'), ('revoked', 'access_unavailable'),
                                         ('policy', 'policy_changed'), ('pat', 'access_unavailable'), ('mcp', 'access_unavailable')])
def test_permission_policy_and_origin_changes_drop_context(related, monkeypatch, change, reason):
    selection, _ = prepare(related)
    with transaction() as db:
        if change == 'disabled':
            db.get(CodeGraphRepository, 22).enabled = False
        elif change == 'revoked':
            db.get(User, 7).enabled = False
        elif change == 'policy':
            db.get(Repository, 22).settings = {'exclusions': 'one.py'}
    if change in {'pat', 'mcp'}:
        monkeypatch.setattr(getattr(service, change), 'current_provenance', lambda: {'id': 'no-code-scope'})
    saved, context = prepare(related, saved=selection)
    assert saved['reason'] == reason and context is None and not saved['files']


def test_byte_caps_and_review_scope_drop_optional_sources(related, monkeypatch):
    selection, context = prepare(related, remaining=1)
    assert selection['truncated'] and context is None
    monkeypatch.setattr(verification, 'filter_context', lambda *args: set())
    selection, context = prepare(related)
    assert not selection['files'] and context is None


@pytest.mark.parametrize('problem', ['blob', 'size', 'symlink', 'binary'])
def test_source_must_match_regular_text_blob(related, monkeypatch, problem):
    original = ocr.git
    def git(repo, env, *args, **kwargs):
        result = original(repo, env, *args, **kwargs)
        if args[:3] == ('ls-tree', '-z', '-l'):
            if problem == 'blob':
                return result.replace(result.split()[2], 'f' * 40)
            if problem == 'size':
                return result.replace(result.split()[3], '999')
            if problem == 'symlink':
                return result.replace('100644', '120000')
        if args[0] == 'show' and problem == 'binary':
            return '\x00binary'
        return result
    monkeypatch.setattr(ocr, 'git', git)
    selection, context = prepare(related)
    assert not selection['files'] and context is None


def test_native_eligibility_is_frozen_and_old_grants_do_not_opt_in(related, monkeypatch):
    from code_mortis.models import PullRequest
    from code_mortis.reviews.service import create_run
    with transaction() as db:
        connection, repo, actor = db.get(Connection, 'connection'), db.get(Repository, 22), db.get(User, 7)
        connection.auth_status = 'connected'
        native = create_run(db, db.get(PullRequest, 'pr'), repo, 'manual', actor)
        assert native.config['code_graph'] == related['options']
        original = copy.deepcopy(native.config)
        db.get(CodeGraphRepository, 22).enabled = False
        assert graph_context.configuration(db, repo, connection, actor) is None
        assert native.config == original
        db.get(CodeGraphRepository, 22).enabled = True
        connection.kind = 'api'
        assert graph_context.configuration(db, repo, connection, actor) == related['options']
        connection.kind = 'chatgpt'
        monkeypatch.setattr(service.mcp, 'current_provenance', lambda: {'resource': 'reviews'})
        assert graph_context.configuration(db, repo, connection, actor) is None


def native(related, monkeypatch, *, checkpoint=None, ceiling=58888, blocked=False, deleted=False, use_graph=True, background=""):
    monkeypatch.setattr(ocr, 'check_version', lambda *a: None)
    def command(args, root, env, **kwargs):
        if 'preview' in args:
            data = {'schema_version': '1', 'reviewable_count': 1,
                    'reviewable_files': [{'path': 'a.py', 'status': 'deleted' if deleted else 'modified'}]}
        else:
            data = {'schema_version': '1', 'groups': [{'files': ['a.py'], 'rule': 'Find defects'}]}
        return 0, json.dumps(data), ''
    # Delegate commands are stubbed; Git-object reads remain real.
    original = ocr.command
    monkeypatch.setattr(ocr, 'command', lambda args, *a, **kw: original(args, *a, **kw) if args[0]=='git' else command(args, *a, **kw))
    prompts, checkpoints = [], []
    def runner(connection, identity, payload, stopped):
        prompts.append(payload)
        if blocked:
            raise runner_client.RunnerBlocked('authentication', {'tokens': 0})
        return {'comments': []}, {'tokens': 123}
    monkeypatch.setattr(runner_client, 'review', runner)
    args = (related['source'], {}, related['first'], related['head'], related['first'],
            {'id': 'connection', 'kind': 'chatgpt', 'model': 'test', 'options': {'prompt_token_ceiling': ceiling}},
            RepoSettings(token_budget=20000, background=background).model_dump(), 'run', lambda: False, lambda v: checkpoints.append(copy.deepcopy(v)))
    try:
        result = delegated.review(*args, checkpoint or {}, graph_options=related['options'] if use_graph else None,
                                  credentials={'api_key': 'private-fixture-value'})
    except runner_client.RunnerBlocked:
        assert blocked
        result = None
    return result, prompts, checkpoints


def test_native_prompt_usage_checkpoint_and_scope(related, monkeypatch):
    result, prompts, checkpoints = native(related, monkeypatch)
    status, report, comments = result
    payload = json.loads(prompts[0]['prompt'])
    assert payload['assigned_path'] == 'a.py' and len(payload['code_graph']['sources']) == 3
    assert status == 'completed' and not comments
    assert report['summary']['total_tokens'] == 123
    assert report['limits']['context_bytes'] == len(prompts[0]['prompt'].encode()) + 4096
    assert len(report['manifest']['coverage']['selected']) == len(report['manifest']['coverage']['completed']) == 1
    assert checkpoints[-1]['code_graph']['a.py']['reason'] == 'used'
    assert 'private-fixture-value' not in json.dumps(checkpoints)
    assert 'def four' not in json.dumps(report)
    with pytest.raises(ValueError, match='assigned file'):
        delegated.validate_result({'comments': [{'path':'one.py','start_line':1,'end_line':1,'severity':'high',
                                    'category':'bug','content':'Other file defect'}]}, 'a.py', 4, '')


def test_optional_context_cannot_make_baseline_prompt_fail(related, monkeypatch):
    result, prompts, _ = native(related, monkeypatch, ceiling=4096, background='x' * 256)
    assert result[0] == 'completed'
    assert native_prompt_size(prompts[0]['prompt'], delegated.output_schema()) <= 4096 * 4 // 5
    assert 'code_graph' not in json.loads(prompts[0]['prompt'])
    assert result[1]['code_graph']['files']['a.py']['reason'] == 'prompt_limit'


def test_deleted_file_does_not_use_head_graph(related, monkeypatch):
    result, prompts, _ = native(related, monkeypatch, deleted=True)
    assert 'code_graph' not in json.loads(prompts[0]['prompt'])
    assert result[1]['code_graph']['files']['a.py']['reason'] == 'deleted_file'


def test_resume_keeps_attempted_prompt_and_usage(related, monkeypatch):
    result, first, checkpoints = native(related, monkeypatch, blocked=True)
    assert result is None and checkpoints[-1]['total_tokens'] == 0
    monkeypatch.setattr(retrieval, 'query', lambda *a: pytest.fail('Resume selected new graph context'))
    result, resumed, _ = native(related, monkeypatch, checkpoint=checkpoints[-1])
    assert first[0]['prompt'] == resumed[0]['prompt']
    assert result[1]['limits']['context_bytes'] == 2 * (len(first[0]['prompt'].encode()) + 4096)
    assert result[1]['summary']['total_tokens'] == 123


def test_legacy_native_review_never_queries_graph(related, monkeypatch):
    monkeypatch.setattr(graph_context, 'prepare', lambda *a, **kw: pytest.fail('Legacy review used graph'))
    result, prompts, _ = native(related, monkeypatch, use_graph=False)
    assert 'code_graph' not in json.loads(prompts[0]['prompt']) and 'code_graph' not in result[1]


def test_run_source_reservations_are_not_reset(related, monkeypatch):
    checkpoint = {'code_graph': {'earlier.py': {'source_bytes': 0, 'reserved_bytes': graph_context.MAX_RUN_BYTES}}}
    result, prompts, _ = native(related, monkeypatch, checkpoint=checkpoint)
    assert 'code_graph' not in json.loads(prompts[0]['prompt'])
    assert result[1]['code_graph']['files']['a.py']['reason'] == 'limited_or_excluded'


def test_metadata_snapshot_lock_prevents_concurrent_retention(related, monkeypatch):
    from sqlalchemy import text
    from sqlalchemy.exc import OperationalError

    from code_mortis.core.db import engine
    original = retrieval.dependencies
    def dependencies(db, row, *args):
        with engine().connect() as other:
            with pytest.raises(OperationalError):
                other.execute(text('select id from code_graph_snapshots where id=:id for update nowait'), {'id': row.id})
        return original(db, row, *args)
    monkeypatch.setattr(retrieval, 'dependencies', dependencies)
    assert prepare(related)[1]


def test_policy_changed_during_source_read_prevents_delivery(related, monkeypatch):
    def filter_context(repo, env, sources, options, stopped):
        with transaction() as db:
            db.get(Repository, 22).settings = {'exclusions': 'one.py'}
        return set(sources)
    monkeypatch.setattr(verification, 'filter_context', filter_context)
    selection, context = prepare(related)
    assert selection['reason'] == 'policy_changed' and context is None


def test_cancellation_is_not_hidden_as_optional_fallback(related):
    with pytest.raises(ocr.ReviewStopped):
        graph_context.prepare(related['options'], 'a.py', related['head'], related['source'], {}, {}, {},
                              lambda: True, remaining=graph_context.MAX_BYTES)


def test_secret_in_optional_path_is_not_persisted_or_sent(related):
    selection, context = graph_context.prepare(related['options'], 'a.py', related['head'], related['source'], {},
        RepoSettings().model_dump(), {'api_key': 'one.py'}, lambda: False, remaining=graph_context.MAX_BYTES)
    assert context is not None
    assert 'one.py' not in json.dumps(selection) and 'one.py' not in json.dumps(context)


def discovery(related, *, kind='api', ceiling=58888, evidence=None):
    from code_mortis.reviews import assessment
    evidence = evidence or {
        'finding': {'path': 'a.py'}, 'current_path': 'a.py', 'head_sha': related['head'],
        'original_sha': related['first'], 'sources': {'current': {'a.py': 'from .one import value\n'}},
        'history': [], 'prior_candidates': [], 'diff': '',
    }
    return graph_context.discovery(evidence, related['options'], related['source'], {},
        RepoSettings().model_dump(), {'api_key': 'private-fixture-value'}, lambda: False,
        {'kind': kind, 'options': {'prompt_token_ceiling': ceiling}}, assessment.INSTRUCTIONS,
        assessment.output_schema(evidence))


@pytest.mark.parametrize('kind', ['api', 'chatgpt', 'claude_code'])
def test_discovery_suggests_paths_without_promoting_them_to_citation_evidence(related, kind):
    from code_mortis.reviews import assessment, context
    evidence = discovery(related, kind=kind)
    graph = evidence['code_graph']
    assert graph['reason'] == 'used' and graph['commit_sha'] == related['head']
    assert graph['snapshot_id'] == related['snapshot'] and len(graph['files']) == 3
    assert 'private-fixture-value' not in json.dumps(evidence)
    assert 'def four' not in json.dumps(evidence)
    supplied, _, _ = context.selection_context(evidence, assessment.output_schema(evidence))
    assert {source['path'] for source in supplied['citation_sources'].values()} == {'a.py'}
    assert graph['files'][0]['path'] not in evidence['sources']['current']
    verification.expand_context(related['source'], {}, evidence,
        [{'revision': 'current', 'path': graph['files'][0]['path']}], RepoSettings().model_dump(), lambda: False)
    supplied, _, _ = context.selection_context(evidence, assessment.output_schema(evidence))
    assert {source['path'] for source in supplied['citation_sources'].values()} == {'a.py', graph['files'][0]['path']}
    assert evidence['original_sha'] == related['first']


@pytest.mark.parametrize('change', ['disabled', 'policy', 'missing', 'limit'])
def test_discovery_fallback_and_revocation_preserve_required_evidence(related, change):
    from code_mortis.reviews import assessment, context
    evidence = discovery(related)
    original = copy.deepcopy(evidence['sources'])
    if change in {'disabled', 'policy'}:
        with transaction() as db:
            if change == 'disabled':
                db.get(CodeGraphRepository, 22).enabled = False
            else:
                repo = db.get(Repository, 22)
                repo.settings = {**repo.settings, 'exclusions': '*.py'}
    if change == 'missing':
        evidence.pop('code_graph')
        evidence['head_sha'] = related['first']
    evidence = discovery(related, evidence=evidence, ceiling=4096 if change == 'limit' else 58888)
    assert evidence['code_graph']['reason'] != 'used'
    assert not evidence['code_graph']['files'] and evidence['sources'] == original
    supplied, _, _ = context.selection_context(evidence, assessment.output_schema(evidence))
    assert 'code_graph' not in supplied


def test_discovery_does_not_opt_in_legacy_runs_or_rewrite_history(related):
    evidence = {'sources': {'original': {'old.py': 'historical evidence'}}, 'history': [{'id': 'finding'}]}
    assert graph_context.discovery(evidence, None, None, None, None, None, None, None, None, None) is evidence
    current = discovery(related)
    current['history'] = [{'id': 'finding'}]
    current['sources']['original'] = evidence['sources']['original']
    result = discovery(related, evidence=current)
    assert result['history'] == current['history'] and result['sources'] == current['sources']
