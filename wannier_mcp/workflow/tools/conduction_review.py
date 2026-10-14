#!/usr/bin/env python3
"""Ask and verify conduction evidence for the exact reviewed draft.

This verifies provenance and candidate-local evidence, not the physical role
assigned to a projected state. A draft proposal can be made without answers;
real_first_try/retry must answer the questions and re-propose before selection.
"""
import argparse
import json
from pathlib import Path

import numpy as np

from orbital_evidence import POLICY, digest, read_cache


FORMAT = 'wannier-conduction-review-v1'
ROLES = {'target_continuation', 'separate_block', 'diffuse_tail', 'uncertain', 'none_in_probe'}
DECISIONS = {'defer_with_counterexample', 'exploratory_defer'}


def _rows(ranges, n):
    return {b - 1 for lo, hi in ranges for b in range(lo, hi + 1) if 1 <= b <= n}


def questions(cache, selection):
    _, energy, groups, cache_hash = read_cache(cache)
    if selection['cache_sha256'] != cache_hash:
        raise ValueError('Selection and projected-band cache differ.')
    basis = selection['basis']
    selected = sum((groups[name] for name in basis), np.zeros_like(energy))
    omitted = sum((value for name, value in groups.items() if name not in basis), np.zeros_like(energy))
    outer = float(selection['windows_relative_eV']['dis_eV'][1])
    frozen = float(selection['windows_relative_eV']['frozen_eV'][1])
    excluded = {b - 1 for b in selection['band_selection']['exclude_bands_1based']}
    probe = float(selection['upper_reference_audit']['probe_high_eV'])
    mask = (energy >= outer) & (energy < probe)
    if excluded:
        mask[:, sorted(excluded)] = False
    ids = np.argwhere(mask)
    if len(ids) and float(selected[mask].max()) > 0:
        k, b = max(ids, key=lambda kb: (float(selected[tuple(kb)]), -float(energy[tuple(kb)]),
                                      -int(kb[0]), -int(kb[1])))
        outer_state = dict(k_band_1based=[int(k + 1), int(b + 1)], energy_eV=float(energy[k, b]),
                           selected_sum_raw=float(selected[k, b]), omitted_sum_raw=float(omitted[k, b]),
                           dominant_channel=max(groups, key=lambda name: (float(groups[name][k, b]), name)),
                           per_channel_raw={name: float(weight[k, b]) for name, weight in groups.items()})
    else:
        outer_state = None
    upper_options = []
    edge = selection['frozen_edge_candidate']['reference']['suggested_upper_eV']
    if edge is not None and frozen < edge <= outer:
        upper_options.append(('band_edge', float(edge)))
    positives = [row for row in selection['frozen_expansion_evidence']['candidates']
                 if row['path_positive_candidate'] and frozen < row['frozen_upper_eV'] <= outer]
    if positives:
        upper_options.append(('first_positive_envelope', float(min(row['frozen_upper_eV'] for row in positives))))
    near = dict(states=int(mask.sum()),selected_raw_sum=float(selected[mask].sum()),
                selected_raw_mean=float(selected[mask].mean()) if mask.any() else None,
                selected_raw_max=float(selected[mask].max()) if mask.any() else None,
                omitted_raw_sum=float(omitted[mask].sum()),
                selected_greater_than_omitted_count=int((selected[mask] > omitted[mask]).sum()))
    # This is an uncertainty gate, never a command to enlarge the outer. A
    # selected-shell-dominated, appreciable near-outer signal is not established
    # as a weak tail merely because the reviewer ended reference rows there.
    continuation_concern = bool(
        near['states'] and near['selected_raw_mean'] >= POLICY['raw_drop_below']
        and near['selected_raw_sum'] > near['omitted_raw_sum']
    )
    candidates = []
    seen = set()
    target = _rows(selection['target_band_ranges_1based'], energy.shape[1])
    weak_floor = float(POLICY['frozen_upper_review']['absolute_target_floor'])
    # Target-row membership is a reviewed hypothesis. If it omits strongly
    # selected, selected-dominant states in the existing low-conduction anchor,
    # the declared "complete target" needs revision or explicit uncertainty.
    # This neither adds rows nor moves a window automatically.
    scope_mask = np.zeros_like(energy, dtype=bool)
    if edge is not None:
        scope_mask = ((energy > 0) & (energy <= float(edge))
                      & (selected >= weak_floor) & (selected > omitted))
        if excluded:
            scope_mask[:, sorted(excluded)] = False
        if target:
            scope_mask[:, sorted(target)] = False
    scope_ids = np.argwhere(scope_mask)
    first_scope = (None if not len(scope_ids) else
                   min(scope_ids, key=lambda kb: (float(energy[tuple(kb)]), int(kb[0]), int(kb[1]))))
    target_scope_gap = dict(
        reviewed_low_conduction_upper_eV=edge,
        projection_supported_outside_target_states=int(len(scope_ids)),
        first_projection_supported_outside_target=(
            None if first_scope is None else
            dict(k_band_1based=[int(first_scope[0]+1), int(first_scope[1]+1)],
                 energy_eV=float(energy[tuple(first_scope)]),
                 selected_sum_raw=float(selected[tuple(first_scope)]),
                 omitted_sum_raw=float(omitted[tuple(first_scope)]))),
        decision_role='Reconsider the claimed complete target rows for low-conduction selected character. This is a scope audit, not a frozen veto or an automatic row/window change.')
    for origin, ceiling in upper_options:
        if round(ceiling, 8) in seen:
            continue
        seen.add(round(ceiling, 8))
        mask = (energy > frozen) & (energy <= ceiling)
        if excluded:
            mask[:, sorted(excluded)] = False
        ids = np.argwhere(mask)
        if not len(ids):
            continue
        # The reviewer's target-row labels are a hypothesis, not negative
        # projection evidence. A strong selected-shell state outside those
        # rows asks for reclassification; it cannot by itself veto frozen.
        negatives = [(int(k), int(b)) for k, b in ids
                     if selected[k, b] < weak_floor or omitted[k, b] > selected[k, b]]
        outside_target_supported = [(int(k), int(b)) for k, b in ids
                                    if b not in target and selected[k, b] >= weak_floor
                                    and selected[k, b] >= omitted[k, b]]
        first_outside = (None if not outside_target_supported else
                         min(outside_target_supported,
                             key=lambda kb: (float(energy[kb]), kb[0], kb[1])))
        witness = None
        if negatives:
            k, b = min(negatives, key=lambda kb: (float(energy[kb]), kb[0], kb[1]))
            witness = dict(k_band_1based=[k + 1, b + 1], energy_eV=float(energy[k, b]),
                           selected_sum_raw=float(selected[k, b]), omitted_sum_raw=float(omitted[k, b]),
                           in_reviewed_target=bool(b in target))
        candidates.append(dict(origin=origin, upper_eV=ceiling, added_retained_path_states=int(len(ids)),
                               added_target_path_states=int(sum(int(b) in target for _, b in ids)),
                               outside_declared_target_but_projection_supported_count=len(outside_target_supported),
                               first_outside_declared_target_but_projection_supported=(
                                   None if first_outside is None else
                                   dict(k_band_1based=[first_outside[0]+1,first_outside[1]+1],
                                        energy_eV=float(energy[first_outside]),
                                        selected_sum_raw=float(selected[first_outside]),
                                        omitted_sum_raw=float(omitted[first_outside]))),
                               selected_sum_raw_min=float(selected[mask].min()),
                               selected_sum_raw_mean=float(selected[mask].mean()),
                               omitted_sum_raw_mean=float(omitted[mask].mean()),
                               recorded_counterexample_count=len(negatives),
                               first_recorded_counterexample=witness,
                               role='Positive candidate for review, not automatic window enlargement.'))
    template = dict(outer=dict(sample_k_band_1based=None if outer_state is None else outer_state['k_band_1based'],
                               dominant_channel=None if outer_state is None else outer_state['dominant_channel'],
                               role='none_in_probe' if outer_state is None else '', reason=''),
                    frozen_candidates=[dict(origin=row['origin'], upper_eV=row['upper_eV'],
                                            decision='', counterexample_k_band_1based=None, reason='')
                                       for row in candidates])
    return dict(format=FORMAT, selection_proposal_id=selection['proposal_id'],
                cache_sha256=cache_hash, current_outer_eV=outer, current_frozen_upper_eV=frozen,
                num_wann=int(selection['num_wann']),
                min_nouter=int(selection['numerical_checks']['min_nouter']),
                min_disentanglement_headroom=int(selection['numerical_checks']['disentanglement_headroom']['min']),
                headroom_role=('N_outer >= num_wann is a feasibility condition, not evidence that '
                               'the physical outer contains enough competing states for disentanglement. '
                               'Review the selected projections above outer before treating small headroom as acceptable.'),
                near_outer_probe_eV=[outer, probe], near_outer_sample=outer_state,
                probe_censored_kpoints=selection['upper_reference_audit']['probe_censored_kpoints'],
                reference_reaches_last_computed_band=selection['upper_reference_audit']['reference_reaches_last_computed_band'],
                outer_to_computed_path_top_min_margin_eV=selection['upper_reference_audit']['outer_to_computed_path_top_min_margin_eV'],
                all_computed_near_outer=near,
                outside_reference_near_outer=selection['upper_reference_audit']['outside_reference_near_outer'],
                selected_continuation_concern=continuation_concern,
                continuation_concern_role=('Advisory review flag from all computed near-outer states: '
                                           'selected raw mean reaches the existing weak-signal line and '
                                           'selected raw sum exceeds omitted raw sum. It is not proof of '
                                           'branch connectivity and never changes the outer automatically.'),
                low_conduction_target_scope=target_scope_gap,
                frozen_candidates=candidates, answer_template=template,
                instructions=('Assign the all-computed near-outer sample a physical role and review all other visible continuation. '
                              'If low_conduction_target_scope reports selected-dominant states outside declared target rows, revise those rows or declare an exploratory scope. '
                              'Evaluate each candidate against the *actual scalar energy interval* above current frozen. '
                              'A band may cross a frozen energy boundary at different k points. '
                              'A rejected candidate needs a concrete state inside that candidate interval, '
                              'or an explicitly exploratory uncertainty.'))


def verify(cache, selection, review):
    if selection.get('review_sha256') != digest(review):
        raise ValueError('Draft selection was made from a different review. Re-propose after editing review.json, then run questions/check on that new draft.')
    question = questions(cache, selection)
    answer = review.get('conduction_review')
    if not isinstance(answer, dict) or set(answer) != {'outer', 'frozen_candidates'}:
        raise ValueError('Selected trial needs conduction_review from current draft questions.')
    outer = answer['outer']
    if not isinstance(outer, dict) or set(outer) != {'sample_k_band_1based', 'dominant_channel', 'role', 'reason'}:
        raise ValueError('conduction_review.outer must give sampled state, dominant channel, role and reason.')
    expected = question['near_outer_sample']
    if outer['sample_k_band_1based'] != (None if expected is None else expected['k_band_1based']):
        raise ValueError('Outer review must address the strongest selected-projection state among all computed bands just above the outer edge.')
    if outer['dominant_channel'] != (None if expected is None else expected['dominant_channel']):
        raise ValueError('Outer review must acknowledge the actual dominant channel at the sampled state.')
    if outer['role'] not in ROLES or (expected is None) != (outer['role'] == 'none_in_probe'):
        raise ValueError('Outer review role disagrees with the sampled probe.')
    if not isinstance(outer['reason'], str) or not outer['reason'].strip():
        raise ValueError('Outer review needs a physical reason.')
    if outer['role'] in ('target_continuation', 'uncertain') and not review.get('trial_intent'):
        raise ValueError('Unresolved target continuation above outer needs revised target/reference/outer or an explicit exploratory trial.')
    if question['selected_continuation_concern'] and not review.get('trial_intent'):
        raise ValueError('Selected-shell weight remains appreciable and dominant just above outer. Re-review target/reference/outer, or mark this outer as an explicit exploratory trial; a role label alone cannot clear this uncertainty.')
    if (question['low_conduction_target_scope']['projection_supported_outside_target_states']
            and not review.get('trial_intent')):
        raise ValueError('The declared complete target rows omit projection-supported low-conduction states. Re-review target/reference rows and re-propose, or mark the unresolved scope as an explicit exploratory trial.')
    frozen_answers = answer['frozen_candidates']
    expected_candidates = question['frozen_candidates']
    if not isinstance(frozen_answers, list) or len(frozen_answers) != len(expected_candidates):
        raise ValueError('Review every band-edge and first positive frozen candidate emitted for this proposal.')
    _, energy, groups, _ = read_cache(cache)
    excluded = {b - 1 for b in selection['band_selection']['exclude_bands_1based']}
    target = _rows(selection['target_band_ranges_1based'], energy.shape[1])
    selected = sum((groups[name] for name in selection['basis']), np.zeros_like(energy))
    omitted = sum((weight for name, weight in groups.items() if name not in selection['basis']), np.zeros_like(energy))
    weak_floor = float(POLICY['frozen_upper_review']['absolute_target_floor'])
    floor = question['current_frozen_upper_eV']
    for item, candidate in zip(frozen_answers, expected_candidates):
        if (not isinstance(item, dict) or set(item) != {'origin', 'upper_eV', 'decision', 'counterexample_k_band_1based', 'reason'}
                or item['origin'] != candidate['origin'] or abs(float(item['upper_eV']) - candidate['upper_eV']) > 1e-6
                or item['decision'] not in DECISIONS or not isinstance(item['reason'], str) or not item['reason'].strip()):
            raise ValueError('Frozen candidate answer must match the current proposal and give a decision/reason.')
        witness = item['counterexample_k_band_1based']
        if item['decision'] == 'defer_with_counterexample':
            if not isinstance(witness, list) or len(witness) != 2 or not all(type(x) is int for x in witness):
                raise ValueError('Deferral needs a concrete path (k, band) counterexample.')
            k, b = (x - 1 for x in witness)
            if not (0 <= k < energy.shape[0] and 0 <= b < energy.shape[1]) or b in excluded:
                raise ValueError('Frozen counterexample coordinate is not a retained computed state.')
            if not floor < energy[k, b] <= candidate['upper_eV'] + 1e-8:
                raise ValueError('Frozen counterexample is outside the candidate interval; a higher-energy bad state cannot veto this lower candidate.')
            if not (selected[k, b] < weak_floor or omitted[k, b] > selected[k, b]):
                raise ValueError('Cited state has no projection counterexample. Being outside self-declared target rows alone cannot veto a frozen candidate; reclassify its physical role or use exploratory_defer.')
        elif witness is not None:
            raise ValueError('Only a state-specific deferral uses counterexample_k_band_1based.')
        if item['decision'] == 'exploratory_defer' and not review.get('trial_intent'):
            raise ValueError('Unresolved frozen candidate needs an explicit exploratory trial intent.')
    return dict(format=FORMAT, status='evidence_coordinates_checked_science_pending',
                selection_proposal_id=selection['proposal_id'], questions=question, answers=answer,
                scope='Coordinates, intervals and explicit uncertainty only; physical roles and fit remain to be judged.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['questions', 'check'])
    parser.add_argument('--cache', required=True)
    parser.add_argument('--selection', required=True)
    parser.add_argument('--review')
    parser.add_argument('--out')
    args = parser.parse_args()
    selection = json.loads(Path(args.selection).read_text())
    if args.command == 'check':
        if not args.review:
            parser.error('check requires --review')
        result = verify(args.cache, selection, json.loads(Path(args.review).read_text()))
    else:
        result = questions(args.cache, selection)
    if args.out:
        Path(args.out).write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
