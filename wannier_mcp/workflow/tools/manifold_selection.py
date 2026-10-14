#!/usr/bin/env python3
"""Read-only projected-band inspection and reviewed-manifold window proposal.

inspect -> AI/human review.json -> propose -> plot. No external executable,
scheduler, historical-material database, automatic basis approval or fit claim.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import re

import numpy as np

import basis_scope
from orbital_channels import basis_dimension
from orbital_evidence import (POLICY, analyze, band_indices, check_record, digest,
                              manifold_checks, read_cache, window_checks)
from valence_selection import (boundary_evidence, build_selection, occupations,
                               prefix_candidates, retained_indices)

FORMAT = 'wannier-manifold-review-v3'
chemical_hint = basis_scope.chemical_hint


def write_json(path, value):
    Path(path).write_text(json.dumps(value,ensure_ascii=False,allow_nan=False,indent=2)+'\n')


def inspect(cache, seed=None):
    meta,e,groups,sha=read_cache(cache)
    if meta.get('sampling')!='band_path':
        raise ValueError('This selection entry requires projected band_path data, not a mesh prerequisite.')
    if meta.get('spin_mode') not in ('scalar','spinor'):
        raise ValueError('Require explicit source spin mode.')
    evidence=analyze(cache,seed,[],'discovery','projected_bands_review','',[],meta['spin_mode'],'band_path',allow_empty_seed=True)
    keys=list(groups); weights=np.stack(list(groups.values()),axis=-1)
    means=weights.mean(axis=0)
    rows=[]
    for b in range(e.shape[1]):
        # Equal-weight channels retain cache order across NumPy implementations.
        # Their display order participates in the inspection provenance digest.
        order=np.argsort(-means[b],kind='stable')
        rows.append(dict(band_1based=b+1,energy_extent_eV=[float(e[:,b].min()),float(e[:,b].max())],
            raw_mean={a:float(means[b,j]) for j,a in enumerate(keys)},
            raw_max={a:float(weights[:,b,j].max()) for j,a in enumerate(keys)},
            strongest_mean_channels=[keys[j] for j in order[:3]],
            gap_above_global_eV=float(e[:,b+1].min()-e[:,b].max()) if b+1<e.shape[1] else None,
            boundary_above=boundary_evidence(e,b+1) if b+1<e.shape[1] else None))
    for row in evidence['orbital_evidence']:
        element,shell=row['group'].split(':')
        row['chemical_inspection_hint']=shell in chemical_hint(element)
        row['optional_chemical_inspection_hint']=shell in basis_scope.OPTIONAL_HINT_SHELLS.get(element,())
    occ=occupations(cache)
    gaps=[dict(after_band_1based=r['band_1based'],gap_eV=r['gap_above_global_eV'],
               interval_eV=[r['energy_extent_eV'][1],rows[r['band_1based']]['energy_extent_eV'][0]],
               clear_for_default_exclusion=r['gap_above_global_eV']>=POLICY['valence_selection']['minimum_gap_eV'])
          for r in rows[:-1] if r['gap_above_global_eV']>0]
    for b,row in enumerate(rows):
        row['occupation_extent']=None if occ is None else [float(occ[:,b].min()),float(occ[:,b].max())]
    # analyze identity changed when adding hints; inspection has its own digest.
    result=dict(format='wannier-band-inspection-v1',cache_sha256=sha,
        effective_parameters=evidence['effective_parameters'],
        source=evidence['source'],seed_eV=list(seed) if seed is not None else None,
        inspection_domain=evidence['inspection_domain'],orbital_evidence=evidence['orbital_evidence'],
        chemical_starting_basis=basis_scope.starting_basis(groups),
        optional_chemical_hints=sorted(a for a in groups if a.split(':')[1] in basis_scope.OPTIONAL_HINT_SHELLS.get(a.split(':')[0],())),
        sampled_global_gaps=gaps,
        occupied_prefix_candidates=prefix_candidates(e,occ,meta['spin_mode'],
            POLICY['valence_selection']['minimum_gap_eV'],POLICY['valence_selection']['occupation_tolerance'],e.shape[1]),
        band_table=rows,sampling_measure='equal weight per stored path point; duplicated endpoints retained; not a DOS or BZ integral',
        warnings=['Band IDs are energy order, not wavefunction-tracked branches.',
                  'Declare the task objective before target and basis. Main chemical inspection shells are p for p block, d for d block, s for s block and available f for the f series; the f-series hint does not establish f occupation or a necessary task role. Optional hints never create inclusion or omission obligations.',
                  'Complete means complete the chosen task manifold, not every bonding valence shell. Extra channels need task-related evidence independent of adding their deep block to target.',
                  'Review both inclusion and omission risks. A deep-only role can be omitted without a global gap; residual mixing alone does not add a basis channel.',
                  'A gap and a filled shell alone do not identify a non-target block; inspect its projections and role.',
                  'A distant deep peak sharing angular l need not belong to the near-EF target shell.'])
    unavailable=sorted(f'{element}:{shell}' for element in set(meta['species'])
        for shell in chemical_hint(element) if f'{element}:{shell}' not in groups)
    if unavailable:
        result['unavailable_chemical_shell_evidence']=unavailable
        result['warnings'].append('Chemical inspection channels absent from this source layout: '+
            ', '.join(unavailable)+'. Missing columns mean unavailable evidence, not zero weight; obtain supported projections if these channels matter to the task. No channel is fabricated or automatically selected.')
    result['inspection_id']=digest(result)
    return result


def validate_review(review, inspection, e, groups):
    if review.get('format') != FORMAT:
        raise ValueError('New proposals require '+FORMAT+'; explicitly migrate the old review with a task_scope_review and additional_basis_evidence. Frozen archives are not rewritten and no orbitals are added automatically.')
    allowed={'format','inspection_id','basis_revision','orbital_decisions','target_band_ranges_1based',
             'reference_band_ranges_1based','frozen_band_ranges_1based','reference_upper_status',
             'target_reason','reference_reason','frozen_reason','task_scope_review','additional_basis_evidence'}
    optional={'valence_exclusions','conduction_groups','conduction_reason','window_override','electronic_character','trial_intent',
              'chemical_omission_evidence','frozen_upper_decision','adopted_frozen_upper_decision',
              'conduction_review'}
    if not allowed<=set(review) or set(review)-allowed-optional:
        raise ValueError('Review keys differ from the documented contract: '+str(sorted(set(review)^allowed)))
    if review.get('format')!=FORMAT or review.get('inspection_id')!=inspection['inspection_id']:
        raise ValueError('Review format/inspection identity mismatch; inspect the exact current cache first.')
    # Task-role review precedes target construction and basis-dependent windows.
    basis_scope.validate(review['task_scope_review'],review['additional_basis_evidence'],
                         review['orbital_decisions'],e,groups,review.get('trial_intent'))
    for field in ('basis_revision','target_reason','reference_reason','frozen_reason'):
        if not isinstance(review.get(field),str) or not review[field].strip():
            raise ValueError('Missing review explanation: '+field)
    rows=review.get('orbital_decisions',[]); keys=[r['group'] for r in rows]
    if len(keys)!=len(set(keys)) or set(keys)!=set(groups):
        raise ValueError('Review every element-shell channel exactly once.')
    for row in rows:
        if row['action'] not in ('include','omit_first_trial') or not row.get('reason','').strip():
            raise ValueError('Resolve each orbital action and explain it before proposing windows.')
    basis=sorted(r['group'] for r in rows if r['action']=='include')
    if not basis:
        raise ValueError('Empty reviewed basis.')
    if 'conduction_groups' in review:
        cg=review['conduction_groups']
        if (not isinstance(cg,list) or len(set(cg))!=len(cg) or not set(cg)<=set(basis) or
                not review.get('conduction_reason','').strip()):
            raise ValueError('Conduction groups must be a reviewed subset of the basis with a physical reason.')
    target=band_indices(review['target_band_ranges_1based'],e.shape[1])
    reference=band_indices(review['reference_band_ranges_1based'],e.shape[1])
    frozen=band_indices(review['frozen_band_ranges_1based'],e.shape[1])
    if not set(frozen)<=set(target)<=set(reference):
        raise ValueError('Require frozen rows within complete target rows within reference rows.')
    basis_scope.validate(review['task_scope_review'],review['additional_basis_evidence'],
                         rows,e,groups,review.get('trial_intent'),target)
    omitted_chemical=set(inspection['chemical_starting_basis'])-set(basis)
    omissions=review.get('chemical_omission_evidence',{})
    if not isinstance(omissions,dict) or set(omissions)!=omitted_chemical:
        raise ValueError('Every omitted chemical starting shell needs chemical_omission_evidence on the reviewed reference rows.')
    for name,item in omissions.items():
        if (not isinstance(item,dict) or set(item)!={'peak_reference_k_band_1based','physical_role','reason','risk_if_omitted'}
                or item['physical_role'] not in ('separate_non_target_block','no_target_role','secondary_hybridization')
                or any(not isinstance(item[k],str) or not item[k].strip() for k in ('reason','risk_if_omitted'))):
            raise ValueError('Chemical omission needs a physical role, same-state reference peak, reason and omission risk: '+name)
        peak=np.unravel_index(int(np.argmax(groups[name][:,reference])),groups[name][:,reference].shape)
        expected=[int(peak[0]+1),int(reference[peak[1]]+1)]
        if item['peak_reference_k_band_1based']!=expected:
            raise ValueError('Chemical omission must cite its strongest raw projection in the reviewed reference rows: '+name+' expected '+str(expected))
    upper=float(e[:,frozen].max())+POLICY['selection_parameters']['frozen_buffer_eV']
    unprotected=np.argwhere((e>upper)&(e>0)&np.isin(np.arange(e.shape[1])[None,:],target))
    upper_decision=review.get('frozen_upper_decision')
    if len(unprotected):
        first=min(unprotected,key=lambda kb:(float(e[tuple(kb)]),int(kb[0]),int(kb[1])))
        expected=[int(first[0]+1),int(first[1]+1)]
        if (not isinstance(upper_decision,dict) or set(upper_decision)!={'first_unprotected_target_k_band_1based','reason','disposition'}
                or upper_decision['first_unprotected_target_k_band_1based']!=expected
                or upper_decision['disposition'] not in ('review_for_expansion','defer_with_evidence')
                or not isinstance(upper_decision['reason'],str) or not upper_decision['reason'].strip()):
            raise ValueError('Review the first target low-conduction state above the frozen subset: '+str(expected)+'; occupancy alone is not an explanation.')
    elif upper_decision is not None:
        raise ValueError('frozen_upper_decision is only used when target low-conduction states lie above the reviewed frozen subset.')
    if review.get('reference_upper_status') not in ('reviewed_decay','right_censored','ambiguous'):
        raise ValueError('Declare reference upper status: reviewed_decay/right_censored/ambiguous.')
    intent=review.get('trial_intent')
    if intent is not None:
        if (not isinstance(intent,dict) or set(intent)!={'mode','reason'} or
                intent['mode']!='exploratory' or not isinstance(intent['reason'],str) or not intent['reason'].strip()):
            raise ValueError('trial_intent requires mode=exploratory and a nonempty physical reason.')
        if 'window_override' not in review:
            raise ValueError('An exploratory trial needs explicit reviewed candidate windows.')
    return basis,target,reference,frozen


def quantile(e,w,fraction):
    order=np.argsort(e.ravel(),kind='stable'); ww=w.ravel()[order]
    if ww.sum()<=0:
        return None
    index=min(int(np.searchsorted(np.cumsum(ww),fraction*ww.sum())),len(order)-1)
    return float(e.ravel()[order[index]])


def upper_evidence(e, groups, basis, reference, seed=None):
    """Conduction-only 70% and tail: occupied valence cannot dominate the CDF."""
    er=e[:,reference]; width=POLICY['tail_reference']['bin_eV']
    floor=POLICY['conduction_selection']['energy_floor_relative_ef_eV']
    conduction=er>=floor
    edges=np.arange(floor,max(floor+3*width,math.ceil(er.max()/width)*width)+width*.5,width)
    if len(edges)<3:
        edges=np.linspace(float(er.min())-.01,float(er.max())+.01,4)
    output={}
    for a in basis:
        w=np.where(conduction,groups[a][:,reference],0.);q=quantile(er,w,POLICY['coverage_target'])
        profile=np.histogram(er,bins=edges,weights=w)[0]/(len(e)*np.diff(edges))
        peak=int(np.argmax(profile)); tail=None
        if q is not None and profile[peak]>0:
            for i in range(peak+1,len(profile)-1):
                # Both bins must be fully below the computed top at every path point.
                # Partial top bins cannot manufacture an apparent decay.
                if (edges[i]>=q and edges[i+2]<=e[:,-1].min() and
                        np.all(profile[i:i+2]<=POLICY['tail_reference']['to_peak_ratio']*profile[peak])):
                    tail=float(edges[i]);break
        output[a]=dict(q70_eV=q,tail_start_eV=tail,bin_edges_eV=edges.tolist(),
            path_projected_spectral_weight_per_eV=profile.tolist(),
            reference_total_raw_weight=float(w.sum()),
            denominator='raw weight on reviewed reference rows at E-EF >= '+str(floor)+' eV only',
            interpretation='conduction diagnostic on finite path rows; not radial-shell identification or a DOS')
    return output


def upper_reference_audit(e, groups, basis, reference, outer_high):
    """Expose selected-shell continuation beyond the AI-reviewed reference rows.

    This is an independent, full-computed-band diagnostic. Energy-ordered rows
    and atomic-sphere weights do not establish wavefunction connectivity or a
    mandatory outer cutoff, so no threshold here changes a scientific choice.
    """
    ref_mask=np.zeros(e.shape[1],dtype=bool);ref_mask[reference]=True
    selected=sum((groups[a] for a in basis),np.zeros_like(e))
    omitted=sum((w for a,w in groups.items() if a not in basis),np.zeros_like(e))
    floor=float(POLICY['conduction_selection']['energy_floor_relative_ef_eV'])
    probe_high=float(outer_high+POLICY['tail_reference']['second_upper_increment_eV'])
    outside=np.broadcast_to(~ref_mask[None,:],e.shape)

    def summarize(mask):
        values=selected[mask]
        return dict(states=int(mask.sum()),selected_raw_sum=float(values.sum()),
            selected_raw_mean=float(values.mean()) if values.size else None,
            selected_raw_max=float(values.max()) if values.size else None,
            omitted_raw_sum=float(omitted[mask].sum()),
            selected_greater_than_omitted_count=int((selected[mask]>omitted[mask]).sum()))

    near_all=(e>=max(floor,outer_high))&(e<probe_high)
    near=outside&near_all
    beyond=outside&(e>=max(floor,probe_high))
    crossings=[];near_rows=[]
    for b in np.flatnonzero(~ref_mask):
        mask=(e[:,b]>=outer_high)&(e[:,b]<probe_high)
        if not mask.any():
            continue
        row=dict(band_1based=int(b+1),energy_extent_eV=[float(e[:,b].min()),float(e[:,b].max())],
            states_in_probe=int(mask.sum()),selected_raw_sum=float(selected[mask,b].sum()),
            selected_raw_max=float(selected[mask,b].max()),omitted_raw_sum=float(omitted[mask,b].sum()))
        near_rows.append(row)
        if e[:,b].min()<outer_high and e[:,b].max()>outer_high:
            crossings.append(row)
    crossings.sort(key=lambda row:row['selected_raw_sum'],reverse=True)
    near_rows.sort(key=lambda row:row['selected_raw_sum'],reverse=True)
    all_conduction=e>=floor
    ref_conduction=all_conduction&np.broadcast_to(ref_mask[None,:],e.shape)
    groups_total={a:dict(reviewed_reference=float(groups[a][ref_conduction].sum()),
        outside_reference=float(groups[a][all_conduction&outside].sum())) for a in basis}
    computed_top=e[:,-1]
    return dict(format='upper-reference-audit-v1',reference_last_band_1based=int(reference.max()+1),
        computed_nbands=int(e.shape[1]),outer_high_eV=float(outer_high),probe_high_eV=probe_high,
        computed_path_top_eV=dict(min=float(computed_top.min()),median=float(np.median(computed_top)),
            max=float(computed_top.max())),
        outer_to_computed_path_top_min_margin_eV=float((computed_top-outer_high).min()),
        probe_censored_kpoints=int((computed_top<probe_high).sum()),
        reference_reaches_last_computed_band=bool(reference.max()==e.shape[1]-1),
        all_computed_near_outer=summarize(near_all),
        outside_reference_near_outer=summarize(near),outside_reference_above_probe=summarize(beyond),
        outside_reference_near_outer_bands_top_by_selected_sum=near_rows[:20],
        crossing_outside_reference_band_count=len(crossings),
        crossing_outside_reference_bands_top_by_selected_sum=crossings[:20],
        selected_group_conduction_raw_weight_scope=groups_total,
        decision_role='Check whether the reviewed reference rows hide selected-shell conduction continuation before interpreting reference-conditioned coverage. Re-review target/reference/outer if physical branches continue. If the projected path top censors a physically chosen outer or probe, add non-self-consistent projected bands; do not shrink the justified outer to fit the DFT cutoff. This diagnostic never enlarges outer or changes orbitals automatically.',
        scope='All computed projected path bands only. A path probe beyond its highest computed band is right-censored even if the interface mesh has more bands; report those scopes separately. Energy-ordered rows are not wavefunction-tracked branches. Atomic-sphere raw weights are not whole-space probabilities; a diffuse high-energy tail is not a fit requirement.')


def down(value,step): return float(round(math.floor((value+1e-10)/step)*step,10))
def up(value,step): return float(round(math.ceil((value-1e-10)/step)*step,10))


def frozen_edge_reference(e, reviewed=None):
    """Anchor a 0.5 eV starting hint; never classify the full BZ from a path."""
    if reviewed is not None:
        if (not isinstance(reviewed,dict) or reviewed.get('kind') not in ('metal','semimetal','insulator')
                or not isinstance(reviewed.get('reason'),str) or not reviewed['reason'].strip()):
            raise ValueError('electronic_character needs metal/semimetal/insulator and a physical reason.')
        kind=reviewed['kind']; source='explicit_review'
        cut=reviewed.get('valence_band_count')
        if kind=='insulator' and (type(cut) is not int or not 0<cut<e.shape[1]):
            raise ValueError('Insulator review requires the original valence_band_count to locate CBM.')
    else:
        # A path may miss a pocket. This is only a provisional anchor hint;
        # an explicit metal/semimetal review takes precedence over a path gap.
        counts=(e<=0).sum(axis=1)
        if np.all(counts==counts[0]) and 0<counts[0]<e.shape[1]:
            cut=int(counts[0]);kind='insulator';source='sampled_gap_hint_requires_review'
        elif np.any((e.min(axis=0)<0)&(e.max(axis=0)>0)):
            cut=None;kind='metal_or_semimetal';source='sampled_fermi_crossing_hint'
        else:
            return dict(kind='undetermined',anchor=None,anchor_eV=None,
                suggested_upper_eV=None,source='insufficient_path_evidence',
                scope='Supply electronic_character review; no guessed CBM or automatic EF fallback.')
    if kind=='insulator':
        vbm=float(e[:,cut-1].max());cbm=float(e[:,cut].min())
        if cbm<=vbm:
            raise ValueError('Reviewed insulating split lacks a sampled global gap; review electronic_character.')
        result=dict(kind=kind,anchor='CBM',anchor_eV=cbm,valence_band_count=cut,
                    vbm_eV=vbm,cbm_eV=cbm,sampled_gap_eV=cbm-vbm)
    else:
        result=dict(kind=kind,anchor='EF',anchor_eV=0.)
    result.update(source=source,review=reviewed,offset_eV=.5,
        suggested_upper_eV=result['anchor_eV']+.5,
        scope='Starting candidate only, relative to SCF EF. Path gaps do not establish a full-BZ insulator; explicit physical review takes precedence. No change to orbital selection or final windows.')
    return result


FROZEN_COUNTEREXAMPLE_ID = 'frozen_state_counterexample_v1'


def frozen_counterexample_contract():
    """Declare the negative-only decision slot occupied by this criterion."""
    return dict(criterion_id=FROZEN_COUNTEREXAMPLE_ID,
        name='Frozen-state counterexample screen',
        name_zh='frozen 逐态反证筛查',
        stage='After an independently reviewed basis, target and frozen candidate are fixed.',
        examined_object='Retained DFT states within the supplied frozen interval at every sampled band-path k point.',
        allowed_effects=['Veto an independently proposed higher frozen upper edge.',
                         'Flag the adopted frozen interval for review: lower-edge counterexamples may call for raising frozen_min; upper-edge counterexamples may call for lowering frozen_max. Neither edge is changed automatically.'],
        forbidden_effects=['Add or delete orbitals from the basis.',
                           'Define or revise target/reference bands or exclude_bands.',
                           'Enlarge either frozen edge or change the dis window.',
                           'Approve an orbital model, a Wannier fit or whole-BZ isolation.'],
        clean_result='No counterexample on the sampled path is neutral, never positive evidence for enlargement.',
        unresolved='Inspect the band and projection of every counterexample. Mixed states inside the target interval may have no clean scalar frozen boundary; keep them in outer and review which target states to freeze.')


def frozen_state_counterexample_screen(e, groups, basis, retained, frozen_pair, params,
                                       shrink_cap_eV=None):
    """Find counterexamples inside an already selected frozen interval."""
    if POLICY['frozen_upper_review']['criterion_id'] != FROZEN_COUNTEREXAMPLE_ID:
        raise ValueError('Frozen-state counterexample policy and tool criterion IDs differ.')
    fl, fh = map(float, frozen_pair)
    retained_mask = np.zeros_like(e, dtype=bool)
    retained_mask[:,retained] = True
    mask = retained_mask & (e>=fl) & (e<=fh)
    selected = sum((groups[a] for a in basis), np.zeros_like(e))
    omitted = [w for a,w in groups.items() if a not in basis]
    strongest_omitted = np.maximum.reduce(omitted) if omitted else np.zeros_like(e)
    floor = float(POLICY['frozen_upper_review']['absolute_target_floor'])
    weak = mask & (selected<floor)
    competitor = mask & (strongest_omitted>selected)
    negative = weak | competitor
    ids = np.argwhere(negative)
    ids = sorted(ids, key=lambda pair:(float(e[tuple(pair)]),int(pair[0]),int(pair[1])))
    samples=[]
    for k,b in ids[:int(POLICY['frozen_upper_review']['max_weak_samples'])]:
        samples.append(dict(k_band_1based=[int(k+1),int(b+1)],energy_eV=float(e[k,b]),
            selected_sum_raw=float(selected[k,b]),strongest_omitted_raw=float(strongest_omitted[k,b]),
            weak_absolute_target=bool(weak[k,b]),omitted_stronger=bool(competitor[k,b]),
            raw_weights={a:float(w[k,b]) for a,w in groups.items()}))
    first=float(e[tuple(ids[0])]) if ids else None
    last=float(e[tuple(ids[-1])]) if ids else None
    backoff=float(POLICY['frozen_upper_review']['review_backoff_eV'])
    # An upper-edge backoff is meaningful only when some clean retained target
    # states lie below the first counterexample. If the problem starts at the
    # lower edge, a downward upper probe would remove the protected target.
    clean_below=bool(first is not None and np.any(mask & ~negative & (e<first)))
    reduced=down(first-backoff,params['frozen_rounding_eV']) if clean_below else None
    cap=fh if shrink_cap_eV is None else min(fh,float(shrink_cap_eV))
    if reduced is not None and not fl<reduced<cap:
        reduced=None
    return dict(format='frozen-negative-screen-v1',criterion_id=FROZEN_COUNTEREXAMPLE_ID,
        screened_frozen_eV=[fl,fh],
        retained_state_count=int(mask.sum()),absolute_target_floor_raw=floor,
        weak_target_states_count=int(weak.sum()),
        omitted_stronger_states_count=int(competitor.sum()),
        negative_state_count=int(negative.sum()),negative_samples=samples,
        first_negative_energy_eV=first,last_negative_energy_eV=last,
        possible_upper_shrink_eV=reduced,
        status='negative_evidence_requires_review' if len(ids) else 'no_negative_evidence_on_sampled_path',
        decision_role='Negative-only review of an already chosen frozen interval. A lower-edge counterexample can motivate raising frozen_min; an upper-edge counterexample can motivate lowering frozen_max. Inspect the actual states first. Never selects or deletes basis orbitals, changes exclude/outer, or justifies enlargement; no window is changed automatically.',
        scope='Retained states on the supplied projected k path. Atomic-sphere weights are not normalized whole-space probabilities.')


# Compatibility for external callers of the earlier function name. Its scope is
# still defined exclusively by frozen_counterexample_contract().
frozen_negative_screen = frozen_state_counterexample_screen


def frozen_upper_review(e, groups, basis, target, retained, nw, windows, params):
    """Screen pre-existing upper-envelope options only for negative evidence.

    The envelope supplies options independently of this screen. Passing the
    screen cannot create an option or justify adopting a larger frozen window.
    """
    low, high = windows['dis_eV']; fl, fh = windows['frozen_eV']
    target_mask = np.zeros_like(e,dtype=bool); target_mask[:,target] = True
    retained_mask = np.zeros_like(e,dtype=bool); retained_mask[:,retained] = True
    selected = sum((groups[a] for a in basis),np.zeros_like(e))
    unselected = sum((w for a,w in groups.items() if a not in basis),np.zeros_like(e))
    tops = e[:,target].max(axis=0)
    levels = {}
    for band,top in zip(target,tops):
        if fh < top <= high:
            ceiling = min(high,up(float(top)+params['frozen_buffer_eV'],params['frozen_rounding_eV']))
            levels.setdefault(ceiling,[]).append(int(band+1))

    def statistics(mask):
        ids = np.argwhere(mask)
        if not len(ids):
            return dict(states=0,selected_sum_mean=None,selected_sum_min=None,
                        selected_sum_max=None,
                        unselected_sum_mean=None,unselected_sum_exceeds_selected_count=0,
                        both_projection_sums_zero_count=0,largest_competition_samples=[])
        order = np.argsort(-(unselected-selected)[mask],kind='stable')[:5]
        samples = []
        for k,b in ids[order]:
            samples.append(dict(k_band_1based=[int(k+1),int(b+1)],energy_eV=float(e[k,b]),
                in_reviewed_target=bool(target_mask[k,b]),selected_sum=float(selected[k,b]),
                unselected_sum=float(unselected[k,b]),
                raw_weights={a:float(w[k,b]) for a,w in groups.items()}))
        target_values=selected[mask]
        return dict(states=int(mask.sum()),selected_sum_mean=float(target_values.mean()),
            selected_sum_min=float(target_values.min()),selected_sum_max=float(target_values.max()),
            unselected_sum_mean=float(unselected[mask].mean()),
            unselected_sum_exceeds_selected_count=int((mask & (unselected>selected)).sum()),
            both_projection_sums_zero_count=int((mask & (selected==0) & (unselected==0)).sum()),
            largest_competition_samples=samples)

    candidates = []
    for ceiling,origins in sorted(levels.items()):
        added = retained_mask & (e>fh) & (e<=ceiling)
        candidate = dict(dis_eV=[low,high],frozen_eV=[fl,ceiling])
        check = window_checks(e[:,retained],nw,candidate,'band_path')
        stats=statistics(added)
        screen=frozen_state_counterexample_screen(e,groups,basis,retained,[fl,ceiling],params,fh)
        added_screen=frozen_state_counterexample_screen(e,groups,basis,retained,[max(fl,fh+1e-8),ceiling],params,fh)
        candidates.append(dict(windows=candidate,origin_target_rows_1based=origins,
            origin='Pre-existing target-row envelope option, independent of the negative screen; not a recommendation to expand.',
            added_target_states=int((added & target_mask).sum()),
            added_outside_target_states=int((added & ~target_mask).sum()),
            added_outside_target_rows_1based=(np.flatnonzero((added & ~target_mask).any(axis=0))+1).tolist(),
            added_state_projection=stats,numerical_checks=check,
            negative_screen=screen,added_state_negative_screen=added_screen,
            negative_veto=bool(screen['negative_state_count'] or not check['gate_pass']),
            automatically_applied=False))
    return dict(format='frozen-upper-review-v1',current_windows=dict(dis_eV=[low,high],frozen_eV=[fl,fh]),
        target_states_above_current_upper=int((target_mask & retained_mask & (e>fh)).sum()),
        criterion_id=FROZEN_COUNTEREXAMPLE_ID,
        status='negative_screen_of_preexisting_options',
        candidates=candidates,automatically_applied=False,
        adoption='This screen can veto or suggest shrinking an independently proposed option. A no-veto result never recommends enlargement. Review any enlargement using the original band-branch physics before this screen.',
        scope='Existing sampled path only. No automatic orbital purity approval, band-connection inference, actual interface mesh approval or fit claim.')


def frozen_expansion_evidence(upper_review):
    """Positive candidate facts, independent of the negative-only veto's decision role."""
    rows=[]
    for item in upper_review['candidates']:
        stats=item['added_state_projection']
        positive=bool(item['added_target_states'] and not item['added_outside_target_states']
                      and stats['selected_sum_min'] is not None and stats['selected_sum_min']>0
                      and not stats['unselected_sum_exceeds_selected_count']
                      and item['numerical_checks']['gate_pass'])
        rows.append(dict(frozen_upper_eV=item['windows']['frozen_eV'][1],
            origin_target_rows_1based=item['origin_target_rows_1based'],
            added_target_states=item['added_target_states'],
            added_outside_target_states=item['added_outside_target_states'],
            selected_sum_min=stats['selected_sum_min'],selected_sum_mean=stats['selected_sum_mean'],
            unselected_sum_mean=stats['unselected_sum_mean'],
            unselected_exceeds_selected_count=stats['unselected_sum_exceeds_selected_count'],
            count_gate_pass=item['numerical_checks']['gate_pass'],
            path_positive_candidate=positive))
    return dict(format='frozen-expansion-evidence-v1',candidates=rows,
        role='Positive target-row membership and selected-projection evidence for physical expansion review. A true flag is not automatic adoption: inspect branch continuity, localized weak states and then apply the separate counterexample screen.',
        origin='Reviewed target rows, raw element-shell projections and path count gate; no fit or whole-BZ claim.')


def propose(cache, inspection, review, evidence_ref):
    fresh=inspect(cache,inspection['seed_eV'])
    if fresh!=inspection:
        raise ValueError('Stale or modified inspection; regenerate it from this cache and policy.')
    meta,e,groups,sha=read_cache(cache)
    basis,target,reference,frozen=validate_review(review,inspection,e,groups)
    vp=POLICY['valence_selection']
    selection,valence_check=build_selection(e,occupations(cache),meta,groups,basis,
        review.get('valence_exclusions',[]),vp['minimum_gap_eV'],vp['occupation_tolerance'])
    retained=retained_indices(selection,e.shape[1]); active_e=e[:,retained]
    if not set(reference)<=set(retained):
        raise ValueError('Excluded bands overlap reviewed target/reference/frozen membership.')
    nw=basis_dimension(basis,meta['species'],meta['counts'],meta['spin_mode'])
    params=POLICY['selection_parameters']; buffer=params['outer_buffer_eV'];step=params['outer_rounding_eV']
    low=down(float(e[:,target].min())-buffer,step)
    conduction_groups=review.get('conduction_groups',basis)
    upper=upper_evidence(e,groups,conduction_groups,reference,inspection['seed_eV'])
    protective_gap_candidate=None
    cut=len(selection['exclude_bands_1based'])
    if cut and cut==int(target.min()):
        boundary=valence_check['blocks'][-1]
        protective_gap_candidate=dict(
            midpoint_eV=float(round((boundary['excluded_top_eV']+boundary['retained_next_bottom_eV'])/2,10)),
            excluded_top_eV=boundary['excluded_top_eV'],
            retained_next_bottom_eV=boundary['retained_next_bottom_eV'],
            automatically_applied=False,
            role='Optional shared lower boundary after separate physical review; an exclusion gap does not require dis_min=frozen_min.')
    # State-count floor is a lower bound on the upper cutoff, NOT a target definition.
    eligible=[np.sort(row[row>=low]) for row in active_e]
    count_floor=max(float(row[nw-1]) for row in eligible) if all(len(row)>=nw for row in eligible) else None
    core_top=float(e[:,target].max())
    thresholds=[core_top+buffer]
    if count_floor is not None: thresholds.append(count_floor+buffer)
    for row in upper.values():
        if row['q70_eV'] is not None: thresholds.append(row['q70_eV'])
        if row['tail_start_eV'] is not None:
            thresholds.append(row['tail_start_eV']+POLICY['tail_reference']['upper_buffer_from_tail_start_eV'])
    high=up(max(thresholds),step)
    for a,row in upper.items():
        er=e[:,reference];wr=groups[a][:,reference];den=float(wr.sum())
        row['full_reference_outer_coverage']=float(wr[(er>=low)&(er<=high)].sum()/den) if den else None
        wr=np.where(er>=POLICY['conduction_selection']['energy_floor_relative_ef_eV'],wr,0.);den=float(wr.sum())
        row['actual_outer_coverage']=float(wr[(er>=low)&(er<=high)].sum()/den) if den else None
        row['below_outer_fraction']=float(wr[er<low].sum()/den) if den else None
        row['above_outer_fraction']=float(wr[er>high].sum()/den) if den else None
    fs=params['frozen_rounding_eV'];fb=params['frozen_buffer_eV']
    fl=max(low,down(float(e[:,frozen].min())-fb,fs))
    fh=min(high,up(float(e[:,frozen].max())+fb,fs))
    # Do not exploit a millielectronvolt gap to force a superficially clean
    # frozen window. Keep the declared buffer above retained non-target rows even
    # if this leaves the very bottom of the target in disentanglement.
    initial_fl=fl
    below=retained[retained<int(target.min())]
    background=None
    if len(below):
        previous=float(e[:,below].max())
        background=dict(original_bands_1based=(below+1).tolist(),maximum_eV=previous,
            target_minimum_eV=float(e[:,target].min()),suggested_frozen_floor_eV=up(previous+fb,fs),
            role='Retained lower background; outer protects all target rows while the frozen floor avoids forcing this block. Projections and mixing still require review.')
        fl=max(fl,background['suggested_frozen_floor_eV'])
    # Exclusion, outer coverage and frozen protection are three independent
    # decisions. Even a valid excluded prefix never forces both lower edges
    # into the same gap. An explicitly reasoned window_override can do so.
    heuristic_windows=dict(dis_eV=[low,high],frozen_eV=[fl,fh])
    override=review.get('window_override')
    if override is not None:
        if (not isinstance(override,dict) or set(override)!={'dis_eV','frozen_eV','reason'} or
                not isinstance(override['reason'],str) or not override['reason'].strip()):
            raise ValueError('A reviewed window override needs dis_eV, frozen_eV and a physical reason.')
        for key in ('dis_eV','frozen_eV'):
            pair=override[key]
            if not isinstance(pair,list) or len(pair)!=2 or not np.isfinite(pair).all():
                raise ValueError('Reviewed windows must have finite energy pairs.')
        low,high=map(float,override['dis_eV']);fl,fh=map(float,override['frozen_eV'])
        window_checks(active_e,nw,dict(dis_eV=[low,high],frozen_eV=[fl,fh]),'band_path')
        for a,row in upper.items():
            er=e[:,reference];wr=groups[a][:,reference];den=float(wr.sum())
            row['full_reference_outer_coverage']=float(wr[(er>=low)&(er<=high)].sum()/den) if den else None
            wr=np.where(er>=POLICY['conduction_selection']['energy_floor_relative_ef_eV'],wr,0.);den=float(wr.sum())
            row['actual_outer_coverage']=float(wr[(er>=low)&(er<=high)].sum()/den) if den else None
            row['below_outer_fraction']=float(wr[er<low].sum()/den) if den else None
            row['above_outer_fraction']=float(wr[er>high].sum()/den) if den else None
    if fl>=fh:
        raise ValueError('Retained lower background overlaps the requested frozen subset; review frozen membership.')
    windows=dict(dis_eV=[low,high],frozen_eV=[fl,fh],coverage_mode='per_group',
        reference_domain=dict(kind='reviewed_target_manifold',description=review['target_reason'],
            evidence_refs=[evidence_ref],complete_band_ranges_1based=review['target_band_ranges_1based']),
        review_reason=review['reference_reason']+' '+review['frozen_reason']+(' AI window override: '+override['reason'] if override else ''))
    analysis=analyze(cache,inspection['seed_eV'],basis,review['basis_revision'],'projected_bands_review',
        review['target_reason'],[evidence_ref],meta['spin_mode'],'band_path',allow_empty_seed=True)
    checks=window_checks(active_e,nw,windows,'band_path')
    data_adequacy=checks['data_adequacy']
    complete=manifold_checks(e,windows,windows['reference_domain'])
    adopted_frozen_screen=frozen_state_counterexample_screen(e,groups,basis,retained,[fl,fh],params)
    # Review the final scalar window independently of the earlier row envelope.
    # Missing reasons keep the draft and its figures available for review.
    outside=np.argwhere((e>fh)&(e>0)&np.isin(np.arange(e.shape[1])[None,:],target))
    adopted_first=None
    if len(outside):
        k,b=map(int,min(outside,key=lambda kb:(float(e[tuple(kb)]),int(kb[0]),int(kb[1]))))
        adopted_first=dict(k_band_1based=[k+1,b+1],energy_eV=float(e[k,b]),
            selected_sum_raw=float(sum(groups[name][k,b] for name in basis)),
            unselected_sum_raw=float(sum(weight[k,b] for name,weight in groups.items() if name not in basis)),
            frozen_upper_eV=float(fh),scope='first positive-energy reviewed target state above adopted scalar frozen upper on sampled path')
    adopted_review=review.get('adopted_frozen_upper_decision')
    reused_review=review.get('frozen_upper_decision')
    review_source=None
    if adopted_review is not None:
        if (adopted_first is None or not isinstance(adopted_review,dict) or
                set(adopted_review)!={'first_unprotected_target_k_band_1based','reason','disposition'} or
                adopted_review['first_unprotected_target_k_band_1based']!=adopted_first['k_band_1based'] or
                adopted_review['disposition'] not in ('review_for_expansion','defer_with_evidence') or
                not isinstance(adopted_review['reason'],str) or not adopted_review['reason'].strip()):
            raise ValueError('adopted_frozen_upper_decision must review the first target state outside the final scalar frozen window: '+str(None if adopted_first is None else adopted_first['k_band_1based']))
        review_source='adopted_frozen_upper_decision'
    elif (adopted_first is not None and reused_review is not None and
          reused_review['first_unprotected_target_k_band_1based']==adopted_first['k_band_1based']):
        adopted_review=reused_review
        review_source='matching_row_envelope_checkpoint'
    first_state_reviewed=adopted_first is None or adopted_review is not None
    warnings=[]
    if not first_state_reviewed:
        warnings.append('Review the first target state outside the adopted scalar frozen window '+str(adopted_first['k_band_1based'])+' using adopted_frozen_upper_decision. A row-subset review before rounding/override is not a review of this different state; the draft retains its windows and figures until resolved.')
    if data_adequacy['requires_more_data']:
        warnings.append('The physically chosen outer reaches the computed projected-path top or the total retained-band capacity is too small. Keep this basis and outer candidate; add non-self-consistent projected bands, then inspect the actual interface wavefunctions separately. Do not shrink dis to hide the DFT cutoff.')
    elif data_adequacy['zero_disentanglement_headroom_kpoints_1based']:
        warnings.append('N_outer equals num_wann at some path points: no extra disentanglement choice remains there. Review physical target/tails and outer space; this alone does not prove that DFT NBANDS is insufficient.')
    if adopted_frozen_screen['negative_state_count']:
        warnings.append('Adopted frozen window contains weak selected or stronger omitted projections. This negative-only screen blocks proposed_trial until the frozen window is revised and rechecked; it never enlarges a window, deletes an orbital or changes outer.')
    if len(target)>nw:
        warnings.append('Required outer-input rows exceed num_wann: they include entangled target-character candidates, not a promise to reproduce every row independently with this model dimension.')
    if fl!=initial_fl and override is None:
        warnings.append('Frozen lower boundary raised above retained non-target lower rows with the declared buffer; outer still protects the full target.')
    if max(float(w.max()) for w in groups.values())>1.0:
        warnings.append('Some raw element-shell projection sums exceed one. Preserve source values; do not interpret them as normalized probabilities or rescale them.')
    if review['reference_upper_status']!='reviewed_decay':
        warnings.append('The upper target reference is '+review['reference_upper_status']+'; a feasible count is not a reviewed tail.')
    missing=[a for a,row in upper.items() if row['tail_start_eV'] is None]
    if missing: warnings.append('No 70%-plus-tail hint for: '+','.join(missing)+'. Do not equate this alone with NBANDS insufficiency.')
    if reference.max()==e.shape[1]-1:
        warnings.append('Reference reaches the last computed band; upper coverage remains right-censored.')
    # Final bands touching the reference top make a completeness approval invalid.
    reviewed=(review['reference_upper_status']=='reviewed_decay' and reference.max()<e.shape[1]-1)
    intent=review.get('trial_intent')
    # Upper target/reference completeness is a scientific uncertainty that an
    # explicit trial can investigate. Never discard the missing-state evidence.
    # Protect the established lower manifold and all executable numeric checks.
    exploratory_ready=(intent is not None and not complete['below_outer_states'])
    if intent is not None:
        warnings.append('Exploratory trial: uncertain upper reference and omitted upper target-character states remain unresolved; execution is not completeness or fit approval. '+intent['reason'])
        if complete['above_outer_states']:
            warnings.append(str(len(complete['above_outer_states']))+' reviewed upper target-character samples lie outside this trial; inspect raw projections and trial results before changing the window.')
    status=('needs_more_data' if data_adequacy['requires_more_data'] else
            'proposed_trial' if checks['gate_pass'] and not adopted_frozen_screen['negative_state_count'] and first_state_reviewed and
            ((reviewed and complete['complete_on_sampled_path']) or exploratory_ready)
            else 'needs_window_review')
    record=dict(policy_version=analysis['policy_version'],
        scope=dict(model_kind='selected_manifold',priority_window_eV=None,
            description=review['task_scope_review']['objective']),
        task_scope_review=review['task_scope_review'],
        additional_basis_evidence=review['additional_basis_evidence'],
        evidence={k:analysis[k] for k in ('analysis_id','comparison_basis','basis_revision')},
        orbital_decisions=[dict(**row,evidence_refs=[evidence_ref]) for row in review['orbital_decisions']],
        trial_basis=basis,status=status,windows=windows,band_selection=selection,
        numerical_checks=dict(analysis_id=analysis['analysis_id'],basis_revision=review['basis_revision'],
            trial_basis=basis,dis_eV=[low,high],frozen_eV=[fl,fh],gate_pass=checks['gate_pass'],
            sampling='band_path',mesh_gate_pending=True,scope='Existing projected path only; no interface calculation.',
            details=['Target-row envelope checked separately from Nouter/Nfrozen.',*warnings]),
        next_action=('add_bands_nscf' if status=='needs_more_data' else
                     'prepare_interface' if status=='proposed_trial' else 'review_windows'))
    record['evidence']['source_sha256']=analysis['source']['source_sha256']
    if intent is not None:
        record['trial_intent']=intent
    validation=check_record(record,analysis,cache)
    total=sum((groups[a] for a in basis),np.zeros_like(e))
    others=[w for a,w in groups.items() if a not in basis]
    unwanted=np.maximum.reduce(others) if others else np.zeros_like(e)
    def frozen_diagnostic(pair):
        mask=(e>=pair[0])&(e<=pair[1])
        win=dict(dis_eV=[low,high],frozen_eV=pair)
        check=window_checks(active_e,nw,win,'band_path')
        mask[:,:cut]=False
        ids=np.argwhere(mask & (unwanted>total))
        return dict(frozen_eV=pair,numerical_checks=check,
            negative_screen=frozen_state_counterexample_screen(e,groups,basis,retained,pair,params,fh),
            unselected_stronger_than_selected_sum_states=(ids+1).tolist(),
            max_unselected_raw=float(unwanted[mask].max()) if mask.any() else None,
            role='Negative-only diagnostic; absence of a flag does not support enlargement or fit acceptance.')
    alternatives=[frozen_diagnostic([fl,fh])]
    # Local shrink/lower-bound probes remain. The upper starting hint is
    # referenced to EF or CBM, never to the previous frozen upper boundary.
    for pair in ([fl,max(fl+fs,fh-.5)],
                 [max(low,fl-.5),fh],[min(fh-fs,fl+.5),fh]):
        pair=[float(round(v,10)) for v in pair]
        if pair not in [a['frozen_eV'] for a in alternatives]: alternatives.append(frozen_diagnostic(pair))
    edge=frozen_edge_reference(e,review.get('electronic_character'))
    requested=edge['suggested_upper_eV']
    edge_candidate=dict(reference=edge,requested_frozen_eV=[fl,requested],automatically_applied=False,
        status='needs_reference_review' if requested is None else 'outside_current_window_bounds',diagnostic=None)
    if requested is not None and fl<requested<=high:
        edge_candidate.update(status='preexisting_edge_option_no_negative_flag',diagnostic=frozen_diagnostic([fl,requested]),
            relation_to_current_upper='higher' if requested>fh else 'lower' if requested<fh else 'equal')
        if edge_candidate['diagnostic']['negative_screen']['negative_state_count']:
            edge_candidate['status']='negative_evidence_requires_review_or_shrink'
        if [fl,requested] not in [a['frozen_eV'] for a in alternatives]:
            alternatives.append(edge_candidate['diagnostic'])
    second=dict(dis_eV=[low,high+POLICY['tail_reference']['second_upper_increment_eV']],frozen_eV=[fl,fh])
    output=dict(format='wannier-selection-proposal-v1',inspection_id=inspection['inspection_id'],
        tool_sha256={name:hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
                     for name in ('manifold_selection.py','basis_scope.py','orbital_evidence.py','valence_selection.py','orbital_channels.py')},
        review_sha256=digest(review),cache_sha256=sha,status=status,basis=basis,num_wann=nw,
        task_scope_review=review['task_scope_review'],
        additional_basis_evidence=review['additional_basis_evidence'],
        task_scope_checks=basis_scope.validate(review['task_scope_review'],review['additional_basis_evidence'],
            review['orbital_decisions'],e,groups,intent,target),
        parameters=params,source_measure=inspection['sampling_measure'],
        heuristic_windows_relative_eV=heuristic_windows,reviewed_window_override=override,
        windows_relative_eV=windows,windows_absolute_eV=dict(dis_eV=[low+meta['ef'],high+meta['ef']],frozen_eV=[fl+meta['ef'],fh+meta['ef']]),
        target_band_ranges_1based=review['target_band_ranges_1based'],
        reference_band_ranges_1based=review['reference_band_ranges_1based'],
        frozen_band_ranges_1based=review['frozen_band_ranges_1based'],
        band_selection=selection,valence_exclusion_checks=valence_check,
        valence_lower_strategy='explicit_AI_review' if override else 'independent_outer_target_and_frozen_clean_subset',
        protective_gap_candidate=protective_gap_candidate,
        conduction_groups=conduction_groups,
        upper_evidence=upper,upper_reference_audit=upper_reference_audit(e,groups,basis,reference,high),
        upper_count_floor_eV=count_floor,manifold_checks=complete,
        frozen_lower_adjustment_eV=dict(before=initial_fl,after=fl),
        retained_lower_background=background,
        adopted_frozen_negative_screen=adopted_frozen_screen,
        criterion_contracts={FROZEN_COUNTEREXAMPLE_ID:frozen_counterexample_contract()},
        numerical_checks=checks,dft_space_assessment=data_adequacy,frozen_candidates=alternatives,
        frozen_edge_candidate=edge_candidate,
        frozen_upper_review=frozen_upper_review(e,groups,basis,target,retained,nw,windows,params),
        second_upper_candidate=dict(windows=second,checks=window_checks(active_e,nw,second,'band_path')),
        warnings=warnings,num_iter=0,executed_wannier=False,fit_accepted=False,
        qualification='AI/human reviewed band membership and basis; program-computed window candidates. No fit was executed.')
    upper_review=output['frozen_upper_review']
    output['frozen_expansion_evidence']=frozen_expansion_evidence(upper_review)
    output['chemical_omission_evidence']=review.get('chemical_omission_evidence',{})
    output['frozen_upper_decision']=review.get('frozen_upper_decision')
    output['frozen_upper_decision_scope']='above_reviewed_frozen_band_subset_before_scalar_window_rounding_or_override'
    # The review coordinate refers to the positive row-envelope candidate.  A
    # rounded/overridden scalar window can already include that state, so report
    # the first state outside the *adopted* window independently.
    output['adopted_frozen_first_unprotected_target']=adopted_first
    output['adopted_frozen_upper_review']=dict(required=adopted_first is not None,
        resolved=first_state_reviewed,review_source=review_source,decision=adopted_review,
        role='Review why the final frozen leaves this target low-conduction state outside. The explanation may support an independently proposed expansion or a reasoned deferral; it never automatically moves a boundary.')
    if intent is not None:
        output['trial_intent']=intent
    output['proposal_id']=digest(output)
    return output,analysis,record,validation


def plot(cache,path_file,proposal,out):
    from plot_dft_bands import verified_path,tick_locations
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    meta,e,groups,sha=read_cache(cache)
    if sha!=proposal['cache_sha256']:
        raise ValueError('Plot cache differs from the selection proposal.')
    p,k,x,_=verified_path(cache,path_file,sha,meta,len(e))
    ticks,labels=tick_locations(p,x)
    target=band_indices(proposal['target_band_ranges_1based'],e.shape[1])
    low,high=proposal['windows_relative_eV']['dis_eV'];fl,fh=proposal['windows_relative_eV']['frozen_eV']
    target_weight=sum((groups[a] for a in proposal['basis']),np.zeros_like(e))
    fig,ax=plt.subplots(1,2,figsize=(13,5.5),gridspec_kw={'width_ratios':[1.65,1]})
    excluded=proposal.get('band_selection',{}).get('exclude_bands_1based',[])
    lower_display=low-2
    if excluded:
        cut=len(excluded);top=float(e[:,:cut].max());bottom=float(e[:,cut].min())
        lower_display=min(lower_display,top-.5)
        ax[0].axhspan(top,bottom,color='#f4c558',alpha=.16)
    for seg in p['segments']:
        sl=slice(seg['start'],seg['stop'])
        ax[0].plot(x[sl],e[sl],color='.8',lw=.55)
        if excluded:
            ax[0].plot(x[sl],e[sl,:len(excluded)],color='#c48129',lw=.65)
        ax[0].plot(x[sl],e[sl][:,target],color='#176b8e',lw=.8)
    xx=np.broadcast_to(x[:,None],e.shape)
    mask=(e>=lower_display)&(e<=high+3)
    ax[0].scatter(xx[mask],e[mask],s=8*target_weight[mask],color='#176b8e',alpha=.5,lw=0)
    for v in (low,high): ax[0].axhline(v,color='#d44a31',ls='--',lw=1.3)
    ax[0].axhspan(fl,fh,color='#54aa6a',alpha=.13)
    ax[0].axhline(0,color='.4',ls=':',lw=.8)
    ax[0].set(ylim=(lower_display,high+3),xlim=(x.min(),x.max()),ylabel='Energy - SCF EF (eV)',title='Blue lines: declared target; dots: selected-shell weight on all rows\nOuter: red dashed; frozen: green; excluded-prefix gap: yellow')
    ax[0].set_xticks(ticks,labels)
    # Keep the reviewer-defined reference visible, but never let its chosen
    # endpoint hide selected-shell continuation in the other computed bands.
    width=float(POLICY['tail_reference']['bin_eV'])
    top=math.ceil(max(float(e.max()),high)/width)*width
    edges=np.arange(0.,top+width*.5,width)
    centers=(edges[:-1]+edges[1:])/2
    reference=band_indices(proposal['reference_band_ranges_1based'],e.shape[1])
    reference_mask=np.zeros(e.shape[1],dtype=bool);reference_mask[reference]=True
    for a in proposal['basis']:
        weights=groups[a]
        all_mask=e>=0
        reviewed_mask=all_mask&reference_mask[None,:]
        whole=np.histogram(e[all_mask],bins=edges,weights=weights[all_mask])[0]/(len(e)*width)
        reviewed=np.histogram(e[reviewed_mask],bins=edges,weights=weights[reviewed_mask])[0]/(len(e)*width)
        line,=ax[1].plot(centers,whole,lw=1.8,label=a+' all computed')
        ax[1].plot(centers,reviewed,color=line.get_color(),ls='--',lw=1.0,alpha=.7,label=a+' reviewed')
    ax[1].axvline(high,color='#d44a31',ls='--',lw=1.3)
    ax[1].axvspan(high,top,color='#d44a31',alpha=.045)
    ax[1].legend(fontsize=7,ncol=2)
    ax[1].set(xlim=(0,top),xlabel='Energy - SCF EF (eV)',ylabel='Raw path spectral weight / eV / k point',title='Solid: all computed bands; dashed: reviewed rows\nPath histogram, not BZ DOS')
    excluded_label=f' | exclude 1-{len(excluded)}' if excluded else ' | no exclusion'
    negative_count=proposal.get('adopted_frozen_negative_screen',{}).get('negative_state_count',0)
    negative_label=f' | frozen counterexamples {negative_count}' if negative_count else ''
    fig.suptitle('Selection only | '+', '.join(proposal['basis'])+
                 f" | {proposal['num_wann']} WFs | min outer {proposal['numerical_checks']['min_nouter']}"+
                 excluded_label+negative_label+' | no Wannier run',fontsize=10)
    fig.tight_layout();fig.savefig(out/'selection.png',dpi=170);fig.savefig(out/'selection.pdf');plt.close(fig)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    subs=parser.add_subparsers(dest='command',required=True)
    a=subs.add_parser('inspect');a.add_argument('--cache',required=True);a.add_argument('--inspect-range','--seed',dest='seed',nargs=2,type=float,help='Optional explicit diagnostic interval; no default EF range.');a.add_argument('--out',required=True)
    a=subs.add_parser('propose');a.add_argument('--cache',required=True);a.add_argument('--inspection',required=True);a.add_argument('--review',required=True);a.add_argument('--out-dir',required=True);a.add_argument('--path');a.add_argument('--no-gallery',action='store_true',help='Skip the default selection-only gallery for initial/real_first_try/retry_NNN case layouts.')
    a=subs.add_parser('render');a.add_argument('--cache',required=True);a.add_argument('--selection',required=True);a.add_argument('--out-dir',required=True);a.add_argument('--path')
    args=parser.parse_args()
    try:
        if args.command=='inspect':
            if Path(args.out).exists(): raise ValueError('Inspection output already exists.')
            result=inspect(args.cache,args.seed);write_json(args.out,result)
            print(json.dumps(dict(inspection_id=result['inspection_id'],output=args.out,
                chemical_starting_basis=result['chemical_starting_basis'],
                optional_chemical_hints=result['optional_chemical_hints'])));return
        if args.command=='render':
            out=Path(args.out_dir)
            if out.exists(): raise ValueError('Use a new render output directory.')
            result=json.loads(Path(args.selection).read_text())
            if result.get('format')!='wannier-selection-proposal-v1':
                raise ValueError('Require an existing formal selection proposal.')
            from plot_dft_bands import verified_path
            meta,e,_,sha=read_cache(args.cache)
            if sha!=result.get('cache_sha256'):
                raise ValueError('Plot cache differs from the selection proposal.')
            verified_path(args.cache,args.path,sha,meta,len(e))
            out.mkdir(parents=True)
            plot(args.cache,args.path,result,out)
            manifest=dict(format='wannier-selection-render-v1',status='selection_visual_review_only',
                source_selection=dict(path=str(Path(args.selection).resolve()),sha256=hashlib.sha256(Path(args.selection).read_bytes()).hexdigest()),
                source_cache=dict(path=str(Path(args.cache).resolve()),sha256=sha),
                source_fit_path=dict(path=str(Path(args.path).resolve()),sha256=hashlib.sha256(Path(args.path).read_bytes()).hexdigest()) if args.path else None,
                renderer_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                outputs={name:dict(sha256=hashlib.sha256((out/name).read_bytes()).hexdigest(),bytes=(out/name).stat().st_size)
                         for name in ('selection.png','selection.pdf')},fit_accepted=False)
            write_json(out/'render_manifest.json',manifest)
            print(json.dumps(dict(output=str(out),selection_png=str(out/'selection.png'),selection_pdf=str(out/'selection.pdf'))));return
        out=Path(args.out_dir)
        if out.exists(): raise ValueError('Use a new selection output directory.')
        inspection=json.loads(Path(args.inspection).read_text());review=json.loads(Path(args.review).read_text())
        result,analysis,record,validation=propose(args.cache,inspection,review,str((out/'review.json').resolve()))
        from plot_dft_bands import verified_path
        meta,e,_,sha=read_cache(args.cache)
        verified_path(args.cache,args.path,sha,meta,len(e))
        out.mkdir(parents=True)
        for name,data in [('inspection',inspection),('review',review),('selection',result),
                          ('analysis',analysis),('decision',record),('validation',validation)]:
            write_json(out/(name+'.json'),data)
        plot(args.cache,args.path,result,out)
        write_json(out/'selection_plot_manifest.json',dict(
            format='wannier-selection-figure-v1',proposal_id=result['proposal_id'],
            selection_sha256=hashlib.sha256((out/'selection.json').read_bytes()).hexdigest(),
            renderer_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            figures={name:hashlib.sha256((out/name).read_bytes()).hexdigest()
                     for name in ('selection.png','selection.pdf')},
            scope='Official figures for this exact selection proposal; not scientific or fit acceptance.'))
        gallery_result=None;gallery_warning=None
        if (not args.no_gallery and
                (out.name in ('initial','real_first_try') or
                 re.fullmatch(r'retry_[0-9]{3,}',out.name))):
            try:
                import selection_gallery
                gallery_result=selection_gallery.render(out.parent.parent,mode='selection-only')
            except (ValueError,KeyError,TypeError,OSError,json.JSONDecodeError) as error:
                gallery_warning=str(error)
        upper=result['frozen_upper_review']
        print(json.dumps(dict(status=result['status'],basis=result['basis'],num_wann=result['num_wann'],
            task_scope_review=result['task_scope_review'],
            additional_basis_evidence=result['additional_basis_evidence'],
            task_scope_checks=result['task_scope_checks'],
            windows=result['windows_relative_eV'],output=str(out),gallery=gallery_result,
            gallery_warning=gallery_warning,
            frozen_upper_review=dict(criterion_id=FROZEN_COUNTEREXAMPLE_ID,status=upper['status'],
                candidate_count=len(upper['candidates']),
                candidate_negative_veto_count=sum(r['negative_veto'] for r in upper['candidates']),
                adopted_negative_state_count=result['adopted_frozen_negative_screen']['negative_state_count'],
                automatically_applied=False,negative_screen_only=True,
                evidence=str(out/'selection.json')+'#/frozen_upper_review'),
            frozen_edge_candidate=result['frozen_edge_candidate']),ensure_ascii=False))
    except (ValueError,KeyError,TypeError,FileNotFoundError) as exc:
        parser.exit(2,'Selection error: '+str(exc)+'\n')


if __name__=='__main__': main()
