import copy
import json

import pytest
from sqlalchemy import select
from test_code_graph_workflow import fixture_git, run
from test_code_graph_workflow import graph as graph

from code_mortis import jobs
from code_mortis.code_graph import retrieval, service, store
from code_mortis.core import source as git_source
from code_mortis.core.db import transaction
from code_mortis.core.errors import ReviewStopped
from code_mortis.models import (
    CodeGraphRepository,
    CodeGraphSnapshot,
    Connection,
    Job,
    Repository,
    User,
)
from code_mortis.reviews import engine, graph_context, planning, verification
from code_mortis.schemas.common import RepoSettings


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
    monkeypatch.setattr(verification, 'filter_context', lambda repo, env, sources, options, stopped, **kwargs: set(sources))
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
    assert [row['path'] for row in context['sources']] == ['one.py', 'two.py', 'three.py']
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
    # A saved order from an older selector must survive a later ordering change.
    selection['files'].reverse()
    first['sources'].reverse()
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
    original = git_source.git
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
    monkeypatch.setattr(git_source, 'git', git)
    selection, context = prepare(related)
    assert not selection['files'] and context is None


def test_shared_eligibility_is_frozen_and_old_native_grants_cannot_start_work(related, monkeypatch):
    from code_mortis.models import PullRequest
    from code_mortis.reviews.service import create_run
    with transaction() as db:
        connection, repo, actor = db.get(Connection, 'connection'), db.get(Repository, 22), db.get(User, 7)
        connection.kind, connection.auth_status = 'chatgpt', 'connected'
        with pytest.raises(ValueError, match='Subscription execution is retired'):
            create_run(db, db.get(PullRequest, 'pr'), repo, 'manual', actor)
        connection.kind = 'api'
        native = create_run(db, db.get(PullRequest, 'pr'), repo, 'manual', actor)
        assert native.config['code_graph'] == graph_context.configuration(db, repo, connection, actor, engine={'analysis_engine': 'mortis'})
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


@pytest.mark.parametrize('case', ['python_many_importers', 'typescript_byte_pressure', 'same_file_tail'])
def test_required_context_reaches_complete_mortis_request(graph, monkeypatch, case):
    if case == 'python_many_importers':
        path, dependency = 'z_review.py', 'z_contract.py'
        files = {
            path: 'from .z_contract import limit\n\ndef allocate(raw):\n    return [0] * limit(raw)\n',
            dependency: 'def limit(raw):\n    return int(raw) if raw.isdecimal() else None\n',
            **{f'a_consumer_{i:02}.py': 'from .z_review import allocate\n' for i in range(16)},
        }
    elif case == 'typescript_byte_pressure':
        path, dependency = 'review.ts', 'z_contract.ts'
        files = {
            path: 'import { limit } from "./z_contract";\nexport const render = (raw: string) => limit(raw).toFixed();\n',
            dependency: '// ' + 'contract documentation ' * 400 + '\nexport const limit = (raw: string): number | null => raw ? Number(raw) : null;\n',
            'a_consumer.ts': 'import { render } from "./review";\n// ' + 'consumer documentation ' * 350 + '\n',
        }
        assert len((files[dependency] + files['a_consumer.ts']).encode()) > graph_context.MAX_BYTES
        assert len(files[dependency].encode()) < graph_context.MAX_BYTES
    else:
        path, dependency = 'a.py', None
        files = {path: 'def allocate(raw):\n    return [0] * limit(raw)\n' + '# padding\n' * 900
                 + '\ndef limit(raw):\n    return int(raw) if raw.isdecimal() else None\n'}
        assert files[path].index('def limit') > 8192
    for name, source in files.items():
        (graph['source'] / name).write_text(source)
    fixture_git(graph['source'], 'add', '.')
    fixture_git(graph['source'], 'commit', '-qm', case)
    graph['head'] = fixture_git(graph['source'], 'rev-parse', 'HEAD')
    row, _ = run(service.enqueue_snapshot(22))
    options = RepoSettings(token_budget=200000).model_dump()
    with transaction() as db:
        connection = db.get(Connection, 'connection')
        connection.kind, connection.owner_id = 'api', None
        graph_options = graph_context.configuration(db, db.get(Repository, 22), connection, db.get(User, 7),
                                                     engine={'analysis_engine': 'mortis'})
    plan = planning.plan_review(graph['source'], {}, graph['first'], graph['head'], graph['first'], options, lambda: False)
    selection, supporting = graph_context.prepare(
        graph_options, path, graph['head'], graph['source'], {}, options, {}, lambda: False,
        remaining=graph_context.MAX_RUN_BYTES, base_sha=graph['first'], trusted_rules=plan.rules)
    connection = {'kind': 'api', 'protocol': 'openai', 'model': 'fixture', 'options': {'prompt_token_ceiling': 200000}}
    plain = engine.prepare_batches(plan, connection, options, {})
    enriched = engine.prepare_batches(plan, connection, options, {}, graph_by_path={path: supporting} if supporting else {})
    assert plain and len(plain) == len(enriched)
    plain_payloads = [json.loads(batch.request.messages[0].content) for batch in plain]
    enriched_payloads = [json.loads(batch.request.messages[0].content) for batch in enriched]
    assert [payload['assigned_units'] for payload in plain_payloads] == [payload['assigned_units'] for payload in enriched_payloads]
    assigned = [unit for payload in enriched_payloads for unit in payload['assigned_units'] if unit['path'] == path]
    assert ''.join(unit['source'] for unit in assigned) == files[path]
    assert all(payload['head_sha'] == graph['head'] for payload in enriched_payloads)
    if dependency:
        assert selection['reason'] == 'used' and supporting['snapshot_id'] == row.id
        assert supporting['commit_sha'] == graph['head']
        sources = {source['path']: source['text'] for source in supporting['sources']}
        assert dependency in sources, f'{case}: missing required contract; selected {list(sources)}'
        assert sources[dependency] == files[dependency]
        assert len(sources) <= graph_context.MAX_FILES
        assert sum(len(source.encode()) for source in sources.values()) <= graph_context.MAX_BYTES
        supplied = next(payload['code_graph'][path] for payload in enriched_payloads if path in payload.get('code_graph', {}))
        assert supplied == supporting
    else:
        assert supporting is None and selection['reason'] == 'no_related_source'
        assert not any('code_graph' in payload for payload in enriched_payloads)


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
    def filter_context(repo, env, sources, options, stopped, **kwargs):
        with transaction() as db:
            db.get(Repository, 22).settings = {'exclusions': 'one.py'}
        return set(sources)
    monkeypatch.setattr(verification, 'filter_context', filter_context)
    selection, context = prepare(related)
    assert selection['reason'] == 'policy_changed' and context is None


def test_cancellation_is_not_hidden_as_optional_fallback(related):
    with pytest.raises(ReviewStopped):
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
