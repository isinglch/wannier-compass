#!/usr/bin/env python3
"""Draft skeletons, incremental edits and atomic review/seal orchestration.

All physical choices remain explicit. The original proposal and stage gates
remain authoritative; computed coordinates are not physical decisions.
"""
import argparse
import copy
import json
from pathlib import Path
import uuid

import numpy as np

import manifold_selection as selection
from orbital_evidence import POLICY, read_cache
from workflow_runtime import atomic_json, execute, sha, runtime_directory, file_lock


def read(path):
    return json.loads(Path(path).read_text())


def unsealed(path):
    path = Path(path).absolute()
    if path.is_symlink():
        raise ValueError('Draft must not be a symlink.')
    if any((parent / 'STAGE.json').exists() for parent in (path.parent, *path.parents)):
        raise ValueError('Cannot modify a sealed stage or its descendants.')
    if any(parent.name == 'records' for parent in path.parents):
        raise ValueError('Use a separate draft directory, outside immutable records/.')
    return path


def merge_patch(original, patch, field_path=()):
    if not isinstance(patch, dict):
        raise ValueError('An incremental patch must be a JSON object.')
    result = copy.deepcopy(original)
    for key, value in patch.items():
        path = (*field_path, key)
        if value is None and path == ('task_scope_review', 'requested_scope_evidence'):
            # The task-scope schema requires this key and uses literal null
            # for the ordinary primary-manifold task. It is not a delete.
            result[key] = None
        elif value is None:
            result.pop(key, None)
        elif isinstance(value, dict):
            result[key] = merge_patch(result.get(key, {}) if isinstance(result.get(key), dict) else {}, value, path)
        else:
            result[key] = copy.deepcopy(value)
    return result


def patch_review(path, patch, expected_sha256):
    path = unsealed(path)
    with file_lock(path.with_name(path.name + '.edit.lock')):
        return patch_locked(path, patch, expected_sha256)


def patch_locked(path, patch, expected_sha256):
    if sha(path) != expected_sha256:
        raise ValueError('Draft changed; supply its current expected_sha256.')
    previous = read(path)
    updated = merge_patch(previous, patch)
    history = path.parent / '.draft_history' / (uuid.uuid4().hex + '.json')
    atomic_json(history, {'previous_sha256': expected_sha256, 'previous_review': previous,
                          'patch': patch, 'scientific_choice_automatically_assigned': False})
    atomic_json(path, updated)
    return {'status': 'draft_updated_not_sealed', 'path': str(path), 'sha256': sha(path),
            'history': str(history), 'history_sha256': sha(history)}


def template(inspection, out):
    inspection = read(inspection)
    path = unsealed(out)
    if path.exists():
        raise ValueError('Use a new review draft.')
    data = {'format': selection.FORMAT, 'inspection_id': inspection['inspection_id'],
            'basis_revision': None,
            'task_scope_review': {'format': 'task-scope-review-v1',
                'objective_kind': 'primary_task_manifold', 'objective': None,
                'requested_scope_evidence': None, 'task_anchor_band_ranges_1based': None,
                'deep_blocks': None},
            'additional_basis_evidence': {},
            'orbital_decisions': [{'group': row['group'], 'action': None, 'reason': None,
                                  'task_role': None, 'risk_if_included': None, 'risk_if_omitted': None}
                                  for row in inspection['orbital_evidence']],
            'target_band_ranges_1based': None, 'reference_band_ranges_1based': None,
            'frozen_band_ranges_1based': None, 'reference_upper_status': None,
            'target_reason': None, 'reference_reason': None, 'frozen_reason': None}
    atomic_json(path, data)
    return {'status': 'needs_scientific_input', 'path': str(path), 'sha256': sha(path),
            'required_fields': [k for k, v in data.items() if v is None] +
                ['task_scope_review.objective', 'task_scope_review.task_anchor_band_ranges_1based',
                 'task_scope_review.deep_blocks', 'orbital_decisions.*.task_role',
                 'orbital_decisions.*.risk_if_included', 'orbital_decisions.*.risk_if_omitted'],
            'orbital_actions': ['include', 'omit_first_trial'],
            'note': 'Declare the task objective and independent task-anchor rows first. Review concentrated deep blocks, not the whole occupied manifold by default; [] explicitly means none identified. Each included non-main channel needs additional_basis_evidence with a task witness and representation reason. Optional chemical hints create no inclusion/omission obligation. Null placeholders are not a decision.'}


def requirements(cache, inspection, review):
    inspection = read(inspection) if not isinstance(inspection, dict) else inspection
    review = read(review) if not isinstance(review, dict) else review
    _, e, groups, cache_hash = read_cache(cache)
    if inspection['cache_sha256'] != cache_hash:
        raise ValueError('Inspection does not bind this cache.')
    evidence = []
    calculated = {}
    rows = review.get('orbital_decisions') or []
    included = {r['group'] for r in rows if r.get('action') == 'include'}
    reference = review.get('reference_band_ranges_1based')
    frozen = review.get('frozen_band_ranges_1based')
    target = review.get('target_band_ranges_1based')
    for group in sorted(included - set(inspection['chemical_starting_basis'])):
        evidence.append({'field': 'additional_basis_evidence.' + group,
                         'human_fields': ['witness_k_band_1based', 'representation_reason', 'independent_of_deep_block'],
                         'scope': 'Cite an actual channel state in the independently declared task anchor. A representation concern or planned exploratory comparison is valid; nonzero raw weight is traceability only, never sufficient inclusion evidence.'})
    if reference:
        ref = selection.band_indices(reference, e.shape[1])
        for group in sorted(set(inspection['chemical_starting_basis']) - included):
            if any(r.get('group') == group and r.get('action') == 'omit_first_trial' for r in rows):
                peak = np.unravel_index(int(np.argmax(groups[group][:, ref])), groups[group][:, ref].shape)
                coordinate = [int(peak[0] + 1), int(ref[peak[1]] + 1)]
                calculated.setdefault('chemical_omission_evidence', {})[group] = {
                    'peak_reference_k_band_1based': coordinate}
                evidence.append({'field': 'chemical_omission_evidence.' + group,
                                 'k_band_1based': coordinate,
                                 'raw_weight': float(groups[group][peak[0], ref[peak[1]]]),
                                 'human_fields': ['physical_role', 'reason', 'risk_if_omitted']})
    if frozen and target:
        f = selection.band_indices(frozen, e.shape[1])
        t = selection.band_indices(target, e.shape[1])
        upper = float(e[:, f].max()) + POLICY['selection_parameters']['frozen_buffer_eV']
        ids = np.argwhere((e > upper) & (e > 0) & np.isin(np.arange(e.shape[1])[None, :], t))
        if len(ids):
            k, b = min(ids, key=lambda kb: (float(e[tuple(kb)]), int(kb[0]), int(kb[1])))
            coordinate = [int(k + 1), int(b + 1)]
            calculated['frozen_upper_decision'] = {'first_unprotected_target_k_band_1based': coordinate}
            evidence.append({'field': 'frozen_upper_decision', 'k_band_1based': coordinate,
                             'energy_eV': float(e[k, b]),
                             'per_channel_raw': {name: float(weight[k, b]) for name, weight in groups.items()},
                             'human_fields': ['reason', 'disposition'],
                             'allowed_dispositions': ['review_for_expansion', 'defer_with_evidence'],
                             'scope': 'Reviewed frozen rows before scalar override; not a frozen expansion recommendation.'})
        override = review.get('window_override')
        if override and isinstance(override.get('frozen_eV'), list) and len(override['frozen_eV']) == 2:
            final_upper = float(override['frozen_eV'][1])
            final_ids = np.argwhere((e > final_upper) & (e > 0) & np.isin(np.arange(e.shape[1])[None, :], t))
            if len(final_ids):
                k, b = min(final_ids, key=lambda kb: (float(e[tuple(kb)]), int(kb[0]), int(kb[1])))
                coordinate = [int(k + 1), int(b + 1)]
                calculated['adopted_frozen_upper_decision'] = {'first_unprotected_target_k_band_1based': coordinate}
                evidence.append({'field': 'adopted_frozen_upper_decision', 'k_band_1based': coordinate,
                                 'energy_eV': float(e[k, b]), 'human_fields': ['reason', 'disposition'],
                                 'allowed_dispositions': ['review_for_expansion', 'defer_with_evidence'],
                                 'scope': 'Final scalar override; records an unprotected state, not advice to enlarge frozen.'})
    return {'status': 'requirements_only', 'cache_sha256': cache_hash,
            'computed_fields': calculated, 'evidence_requests': evidence,
            'scientific_choice_automatically_assigned': False,
            'note': 'Copy numeric coordinates with the supplied patch command; provide physical roles and reasons yourself. Official proposal validation remains mandatory.'}


def propose(cache, inspection, review, out_dir, workspace):
    # Rejections remain nonzero, but their required coordinates are exposed
    # without asking a smaller model to browse source code.
    requests = requirements(cache, inspection, review)
    output, parsed = execute('manifold_selection.py', ['propose', '--cache', cache,
                              '--inspection', inspection, '--review', review, '--out-dir', out_dir], workspace)
    output['evidence_requests'] = requests['evidence_requests']
    output['computed_fields'] = requests['computed_fields']
    if output['ok']:
        apath = Path(out_dir) / 'scope_audit.json'
        scope_audit, _ = execute('selection_scope_audit.py', ['--cache', cache,
                                  '--selection', str(Path(out_dir) / 'selection.json'),
                                  '--review', review, '--out', str(apath)], workspace)
        if not scope_audit['ok']:
            return dict(scope_audit, next_action='repair the bound audit inputs before reviewing or sealing')
        output['scope_audit'] = scope_audit
        qpath = Path(out_dir) / 'conduction_questions.json'
        questions, _ = execute('conduction_review.py', ['questions', '--cache', cache,
                                '--selection', str(Path(out_dir) / 'selection.json'), '--out', str(qpath)], workspace)
        output['questions'] = questions
        output['next_action'] = 'review scope_audit lower protection, scalar membership and upper origin with the figures; revise scientific choices first, then answer current conduction questions and re-propose before sealing'
    return output


def seal(cache, inspection, review, context, case, role, workspace, parent='', trial_id='',
         reason='', parent_result=''):
    review = str(unsealed(review))
    # One transport call orchestrates all deterministic recomputation. No
    # question answer is invented and no failed gate is waived.
    scratch = runtime_directory(workspace, 'drafts') / uuid.uuid4().hex
    proposal = propose(cache, inspection, review, str(scratch), workspace)
    if not proposal['ok']:
        return proposal
    if role != 'initial':
        check, _ = execute('conduction_review.py', ['check', '--cache', cache,
                              '--selection', str(scratch / 'selection.json'), '--review', review], workspace)
        if not check['ok']:
            return dict(check, next_action='patch scientific answers against current questions', questions=proposal['questions'])
    arguments = ['initial' if role == 'initial' else 'select', '--case-dir', case,
                 '--review', review, '--context', context]
    if role != 'initial':
        if not all((parent, trial_id, reason)):
            raise ValueError('Selected stage requires parent, trial_id and reason.')
        arguments += ['--parent', parent, '--trial-id', trial_id, '--reason', reason]
        if parent_result:
            arguments += ['--parent-result', parent_result]
    output, _ = execute('decision_stages.py', arguments, workspace)
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['template', 'patch', 'requirements', 'propose', 'seal'])
    parser.add_argument('--workspace', required=True)
    for name in ('cache', 'inspection', 'review', 'out', 'changes', 'expected-sha256',
                 'context', 'case-dir', 'parent', 'trial-id', 'reason', 'parent-result'):
        parser.add_argument('--' + name)
    parser.add_argument('--role', choices=['initial', 'real_first_try', 'retry'])
    args = parser.parse_args()
    try:
        if args.command == 'template':
            result = template(args.inspection, args.out)
        elif args.command == 'patch':
            result = patch_review(args.review, read(args.changes), args.expected_sha256)
        elif args.command == 'requirements':
            result = requirements(args.cache, args.inspection, args.review)
        elif args.command == 'propose':
            result = propose(args.cache, args.inspection, args.review, args.out, args.workspace)
        else:
            if args.role is None:
                raise ValueError('seal requires --role')
            result = seal(args.cache, args.inspection, args.review, args.context, args.case_dir,
                          args.role, args.workspace, args.parent, args.trial_id, args.reason, args.parent_result)
        if args.out and args.command == 'requirements':
            atomic_json(args.out, result)
        print(json.dumps(result, ensure_ascii=False))
        if result.get('ok') is False:
            raise SystemExit(result.get('exit_code') or 2)
    except (ValueError, TypeError, KeyError, OSError) as error:
        parser.exit(2, 'Review workflow error: ' + str(error) + '\n')


if __name__ == '__main__':
    main()
