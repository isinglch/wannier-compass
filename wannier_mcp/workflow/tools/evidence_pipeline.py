#!/usr/bin/env python3
"""Create one portable evidence packet and unselected draft from a band cache."""
import argparse
import json
from pathlib import Path

from workflow_runtime import atomic_json, execute, sha


def prepare(cache, out_dir, workspace):
    directory = Path(out_dir).resolve()
    if directory.exists():
        raise ValueError('Use a new evidence output directory; existing evidence is retained.')
    directory.mkdir(parents=True)
    records = []
    operations = (
        ('manifold_selection.py', ['inspect', '--cache', str(cache), '--out', str(directory / 'inspection.json')]),
        ('review_packet.py', ['--cache', str(cache), '--out-dir', str(directory / 'packet')]),
        ('review_workflow.py', ['template', '--workspace', str(workspace),
                                '--inspection', str(directory / 'inspection.json'), '--out', str(directory / 'review.json')]),
    )
    for tool, arguments in operations:
        result, _ = execute(tool, arguments, workspace)
        records.append({'tool': tool, 'log': result['log'], 'log_sha256': result['log_sha256'], 'ok': result['ok']})
        if not result['ok']:
            atomic_json(directory / 'pipeline.json', {'status': 'evidence_failed', 'operations': records})
            return dict(result, next_action='inspect the failing operation; retain its evidence and logs')
    artifacts = {name: {'path': str(directory / name), 'sha256': sha(directory / name)}
                 for name in ('inspection.json', 'review.json')}
    value = {'status': 'scientific_decision_required', 'cache': str(Path(cache).resolve()),
             'cache_sha256': sha(cache), 'artifacts': artifacts, 'packet': str(directory / 'packet'),
             'operations': records, 'scientific_choice_automatically_assigned': False,
             'next_action': 'read current projected-band evidence; fill explicit choices in the review draft'}
    atomic_json(directory / 'pipeline.json', value)
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache', required=True)
    parser.add_argument('--out-dir', required=True)
    parser.add_argument('--workspace', required=True)
    args = parser.parse_args()
    try:
        result = prepare(args.cache, args.out_dir, args.workspace)
        print(json.dumps(result, ensure_ascii=False))
        if result.get('ok') is False:
            raise SystemExit(result.get('exit_code') or 2)
    except (ValueError, OSError) as error:
        parser.exit(2, 'Evidence pipeline error: ' + str(error) + '\n')


if __name__ == '__main__':
    main()
