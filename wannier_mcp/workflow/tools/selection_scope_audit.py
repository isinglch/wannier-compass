#!/usr/bin/env python3
"""Compute decision-local review facts without choosing basis or window edges."""
import argparse
import json
from pathlib import Path

import numpy as np

from manifold_selection import band_indices
from orbital_evidence import POLICY, digest, read_cache, window_checks


def audit(cache, selection, review=None):
    meta, energy, groups, cache_hash = read_cache(cache)
    if selection.get('cache_sha256') != cache_hash:
        raise ValueError('Selection and projected-band cache differ.')
    if review is not None and selection.get('review_sha256') != digest(review):
        raise ValueError('Selection and review differ; re-propose before auditing.')
    basis = selection['basis']
    if not basis or len(set(basis)) != len(basis) or set(basis)-set(groups):
        raise ValueError('Invalid selected basis channels.')
    target_rows = band_indices(selection['target_band_ranges_1based'], energy.shape[1])
    retained = np.ones(energy.shape, dtype=bool)
    excluded = selection['band_selection']['exclude_bands_1based']
    if any(type(b) is not int or not 1 <= b <= energy.shape[1] for b in excluded):
        raise ValueError('Invalid excluded band index.')
    if excluded:
        retained[:, np.array(excluded)-1] = False
    target = np.zeros_like(retained)
    target[:, target_rows] = True
    if np.any(target & ~retained):
        raise ValueError('Excluded rows overlap the reviewed target.')
    selected = sum((groups[g] for g in basis), np.zeros(energy.shape))
    others = [w for g,w in groups.items() if g not in basis]
    strongest_omitted = np.maximum.reduce(others) if others else np.zeros(energy.shape)
    omitted_sum = sum(others, np.zeros(energy.shape))
    floor = float(POLICY['frozen_upper_review']['absolute_target_floor'])
    weak = selected < floor
    competing = strongest_omitted > selected
    negative = weak | competing
    windows = selection['windows_relative_eV']
    low, high = map(float,windows['dis_eV'])
    fl, fh = map(float,windows['frozen_eV'])
    checks = window_checks(energy[:,retained[0]],int(selection['num_wann']),windows,'band_path')
    outer = retained & (energy>=low) & (energy<=high)
    frozen = retained & (energy>=fl) & (energy<=fh)
    below_ef = target & (energy<=0)
    below_inner = below_ef & outer & (energy<fl)
    unflagged_below = below_inner & ~negative
    outside_target_inner = frozen & ~target

    def witness(index):
        k,b=map(int,index)
        return {'k_band_1based':[k+1,b+1], 'energy_eV':float(energy[k,b]),
                'selected_sum_raw':float(selected[k,b]),
                'strongest_omitted_raw':float(strongest_omitted[k,b]),
                'omitted_sum_raw':float(omitted_sum[k,b]),
                'inside_declared_target':bool(target[k,b]),
                'weak_absolute_selected':bool(weak[k,b]),
                'strongest_omitted_exceeds_selected':bool(competing[k,b]),
                'per_channel_raw':{g:float(w[k,b]) for g,w in groups.items()}}

    def samples(mask):
        ids=np.argwhere(mask)
        if not len(ids):return []
        ids=sorted(ids,key=lambda i:(float(energy[tuple(i)]),int(i[0]),int(i[1])))
        slots=sorted(set([0,len(ids)//2,len(ids)-1]))
        return [witness(ids[i]) for i in slots]

    def count(mask): return int(mask.sum())
    lower = {'target_below_EF_states':count(below_ef),
             'target_below_EF_inside_frozen':count(below_ef & frozen),
             'target_below_EF_outside_outer':count(below_ef & ~outer),
             'target_below_frozen_inside_outer':count(below_inner),
             'unflagged_target_below_frozen':count(unflagged_below),
             'flagged_target_below_frozen':count(below_inner & negative),
             'target_below_EF_minimum_eV':float(energy[below_ef].min()) if below_ef.any() else None,
             'unflagged_lower_witnesses':samples(unflagged_below),
             'flagged_lower_witnesses':samples(below_inner & negative),
             'meaning':'Below-EF, equally weighted path states; no occupation, BZ integral, or branch-continuity claim. No counterexample is neutral, not expansion evidence.'}
    scalar = {'retained_states_inside_frozen':count(frozen),
              'declared_target_states_inside_frozen':count(frozen & target),
              'outside_declared_target_states_inside_frozen':count(outside_target_inner),
              'outside_target_witnesses':samples(outside_target_inner),
              'negative_states_inside_frozen':count(frozen & negative),
              'negative_witnesses':samples(frozen & negative),
              'meaning':'Actual scalar energy mask on every retained path state; a row declaration is not an execution mask. Outside-target membership alone is not a negative projection witness.'}
    edge = selection.get('frozen_edge_candidate',{}).get('reference',{})
    anchor = edge.get('suggested_upper_eV')
    override = selection.get('reviewed_window_override')
    upper = {'adopted_upper_eV':fh,'band_edge_probe_eV':anchor,
             'candidate_source':'explicit_reviewed_scalar_override' if override else 'reviewed_row_envelope',
             'above_band_edge_probe':bool(anchor is not None and fh>anchor),
             'target_positive_energy_states_above_frozen':count(target & retained & (energy>0) & (energy>fh)),
             'meaning':'The probe is a starting option, not a physical ceiling. Review the candidate origin and target/background branches independently before using the negative screen.'}
    requests=[]
    if below_inner.any():
        requests.append({'slot':'valence_frozen_lower','action':'Review the unprotected target-valence states against the full projected bands. State specific background/mixing evidence or a physical tradeoff. If protection was omitted by choosing only upper rows, propose and check a justified deeper lower candidate.',
                         'changed_variable_if_justified':'frozen_min',
                         'basis_change_automatic':False})
    if outside_target_inner.any():
        requests.append({'slot':'scalar_window_membership','action':'Assign the actual added states a target or background role. Inspect their projections and counts; row membership alone neither authorizes freezing nor vetoes it.'})
    if upper['above_band_edge_probe']:
        requests.append({'slot':'conduction_frozen_upper','action':'Explain the independently justified low-conduction extension and where it stops. A high target-row maximum or a clean negative screen alone does not supply this evidence.',
                         'changed_variable_if_justified':'frozen_max'})
    if (frozen & negative).any():
        requests.append({'slot':'frozen_counterexamples','action':'Locate witnesses relative to the current candidate and required task states. Review lower, upper, or interior mixing; retain the criterion\'s frozen-only scope. Energy sign relative to EF does not choose the remedy.'})
    if checks['max_nfrozen']>selection['num_wann']:
        requests.append({'slot':'frozen_count_conflict','action':'Review forced states and the proposed inner interval. The count conflict does not by itself establish a basis deficiency.'})
    if not checks['gate_pass']:
        requests.append({'slot':'numerical_feasibility','action':checks['data_adequacy']['next_action'],
                         'meaning':'Keep physical task and justified candidate; identify the actual limiting input or interval.'})
    return {'format':'wannier-selection-scope-audit-v1','status':'review_facts_ready',
            'selection_proposal_id':selection['proposal_id'],'cache_sha256':cache_hash,
            'review_sha256':selection.get('review_sha256'),'basis':basis,
            'windows_relative_eV':windows,'lower_protection':lower,'scalar_membership':scalar,
            'upper_origin':upper,'numerical_checks':checks,'required_review':requests,
            'negative_rule':{'absolute_target_floor_raw':floor,'competitor':'strongest_individual_omitted_channel'},
            'sampling':'projected_band_path','interface_mesh_gate_pending':True,
            'automatically_changed_variables':[],'scientific_approval':False,
            'next_action':'Review these facts and the projected figures; make explicit scientific choices, then re-propose and answer the current conduction questions.',
            'scoring':'Report the fixed physical task and missing/unmatched states separately from the chosen frozen diagnostic. Reference rows and outer background are not automatically fit targets.'}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache',required=True)
    parser.add_argument('--selection',required=True)
    parser.add_argument('--review')
    parser.add_argument('--out')
    args=parser.parse_args()
    try:
        selection=json.loads(Path(args.selection).read_text())
        review=json.loads(Path(args.review).read_text()) if args.review else None
        result=audit(args.cache,selection,review)
        if args.out:
            path=Path(args.out)
            if path.exists():raise ValueError('Use a new audit output; do not overwrite an existing record.')
            path.parent.mkdir(parents=True,exist_ok=True)
            with path.open('x') as stream:json.dump(result,stream,indent=2,allow_nan=False)
        print(json.dumps(result,allow_nan=False))
    except (ValueError,KeyError,TypeError,OSError) as error:
        parser.exit(2,'Selection scope audit error: '+str(error)+'\n')


if __name__=='__main__':main()
