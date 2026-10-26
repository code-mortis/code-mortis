"""Explicitly authorized native evaluation, without review creation or GitHub publication.

Run inside the local review worker with a prepared requests.json. This consumes
provider quota only with --authorized. The token allowance stops later calls;
native usage arrives after inference, so one call can overshoot its allowance.
"""

import argparse
import hashlib
import json
import time
from pathlib import Path
from uuid import uuid4


def citation_accuracy(result, prompt):
    sources = {row['source_id']: row for row in json.loads(prompt)['sources']}
    evidence = result.get('evidence', [])
    valid = sum(isinstance(row, dict) and row.get('source_id') in sources
                and type(row.get('start_line')) is int and type(row.get('end_line')) is int
                and sources[row['source_id']]['start_line'] <= row['start_line'] <= row['end_line']
                <= sources[row['source_id']]['end_line'] for row in evidence)
    return {'valid': valid, 'total': len(evidence), 'source_paths': sorted({sources[row['source_id']]['path']
            for row in evidence if isinstance(row, dict) and row.get('source_id') in sources})}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--requests', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--connection-id', required=True)
    parser.add_argument('--authorized', action='store_true')
    args = parser.parse_args()
    if not args.authorized:
        parser.error('Explicit approval of the corpus, connection and quota is required before --authorized')
    if args.output.exists():
        parser.error('Choose a new output path; an evaluation must never silently repeat paid work')

    from code_mortis.code_graph import service as graph
    from code_mortis.core.db import engine, transaction
    from code_mortis.core.security import unseal
    from code_mortis.integrations import runner_client
    from code_mortis.models import Connection, Repository, User
    from code_mortis.reviews import context
    from code_mortis.reviews.service import authorize_connection, connection_snapshot

    assert engine().url.database == 'mortis_dev', 'This experiment is restricted to local development'
    requests = json.loads(args.requests.read_text())
    assert len(requests) == 18 and len({r['id'] for r in requests}) == 18
    assert {r['arm'] for r in requests} == {'baseline', 'text_symbols', 'graph'}
    # Rotate arm order across cases to avoid always warming the same arm first.
    order = {'baseline': 0, 'text_symbols': 1, 'graph': 2}
    requests.sort(key=lambda r: (r['case_id'], (order[r['arm']] - int(r['case_id'].split('-')[1])) % 3))
    report = {'calls': [], 'budget': 120000, 'spent': 0, 'model_config': None,
              'requests_sha256': hashlib.sha256(args.requests.read_bytes()).hexdigest(),
              'input_output_usage': 'Native transport reports aggregate tokens only; separate values remain unknown',
              'hard_provider_cap': False}
    args.output.write_text(json.dumps(report, indent=2) + '\n')
    for request in requests:
        if report['spent'] >= report['budget']:
            break
        with transaction() as db:
            connection = db.get(Connection, args.connection_id)
            assert connection and connection.kind == 'chatgpt' and connection.auth_status == 'connected'
            actor = db.get(User, connection.owner_id)
            repo = db.get(Repository, 1407742623)
            authorize_connection(db, connection, repo, 'manual', actor)
            config = connection_snapshot(connection)
            if report['model_config'] is None:
                report['model_config'] = config
            assert config == report['model_config'], 'Connection configuration changed during the experiment'
            credentials = {'redaction_values': [unseal(actor.access_token)]}
        graph.authorize(1407742623, config['owner_id'], initiator=False)
        prompt = context.sanitize(request['prompt'], credentials)
        call = {'id': request['id'], 'case_id': request['case_id'], 'arm': request['arm'],
                'expected': request['expected'], 'status': 'dispatched', 'tokens': None,
                'input_tokens': None, 'output_tokens': None, 'expansion_rounds': 0,
                'prompt_bytes': len(prompt.encode()), 'prompt_sha256': hashlib.sha256(prompt.encode()).hexdigest()}
        report['calls'].append(call)
        args.output.write_text(json.dumps(report, indent=2) + '\n')
        started = time.monotonic()
        try:
            raw, usage = runner_client.review(config['id'], 'graph-eval-' + str(uuid4()), {
                'kind': config['kind'], 'task': 'assessment', 'model': config['model'], 'effort': 'low',
                'prompt': prompt, 'output_schema': request['schema'], 'timeout': 180,
                'prompt_token_ceiling': config['options'].get('prompt_token_ceiling', 58888),
                'token_budget': report['budget'] - report['spent'],
            }, lambda: False)
            call.update(status='completed', result=raw, tokens=usage.get('tokens'),
                        citations=citation_accuracy(raw, prompt))
        except runner_client.RunnerBlocked as exc:
            call.update(status='failed', reason=exc.reason, tokens=exc.usage.get('tokens'))
        finally:
            call['seconds'] = round(time.monotonic() - started, 3)
            known = type(call['tokens']) is int and call['tokens'] >= 0
            if known:
                report['spent'] += call['tokens']
            args.output.write_text(json.dumps(report, indent=2) + '\n')
        if not known or call['status'] != 'completed':
            break
    print(json.dumps({'calls': len(report['calls']), 'tokens': report['spent'], 'output': str(args.output)}))


if __name__ == '__main__':
    main()
