#!/usr/bin/env python3
"""Read-only v0.2 evidence and decision consistency CLI, not a VASP executor.

Input adapter: the existing charge-only PROCAR NPZ cache. No historical basis
table, material-dependent thresholds, orbital auto-selection, or job submission.
"""
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
from orbital_channels import basis_dimension, resolved_shells

PACKAGE=Path(__file__).resolve().parents[1]
POLICY_FILE=PACKAGE/'policies/reference-v0.2.json'
VERSION='wannier-reference-0.2'
POLICY=json.loads(POLICY_FILE.read_text())
RAW_WEAK=POLICY['raw_drop_below']
RAW_CLEAR=POLICY['raw_nominate_at']


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,allow_nan=False,
                                    separators=(',',':')).encode()).hexdigest()


def read_cache(path):
    path=Path(path)
    with np.load(path,allow_pickle=False) as z:
        meta=json.loads(str(z['metadata']))
        e=np.array(z['energies'],dtype=float)
        raw=np.array(z['raw'],dtype=float)
    if e.ndim!=3 or e.shape[0]!=1 or raw.ndim!=5 or raw.shape[:3]!=e.shape:
        raise ValueError('Adapter requires one charge channel with matching k/band axes.')
    if not np.isfinite(e).all() or not np.isfinite(raw).all() or (raw<0).any():
        raise ValueError('Nonfinite energies or negative/nonfinite charge projections.')
    if not np.isfinite(meta['ef']):
        raise ValueError('Missing finite independent energy reference.')
    species,counts=meta['species'],meta['counts']
    if len(species)!=len(counts) or sum(counts)!=raw.shape[3] or any(n<=0 for n in counts):
        raise ValueError('POSCAR species/counts do not match the charge table.')
    cols=meta['columns']
    shells=resolved_shells(cols)
    if len(cols)!=raw.shape[4]:
        raise ValueError('Resolved charge columns disagree with the stored orbital axis.')
    groups={};start=0
    for element,n in zip(species,counts):
        for shell,labels in shells.items():
            key=f'{element}:{shell}'
            w=raw[0,:,:,start:start+n,:][...,[cols.index(v) for v in labels]].sum(axis=(-1,-2))
            groups[key]=groups.get(key,0)+w
        start+=n
    assert np.allclose(sum(groups.values()),raw[0].sum(axis=(-1,-2)))
    return meta,e[0]-meta['ef'],groups,hashlib.sha256(path.read_bytes()).hexdigest()


def analyze(cache,seed,basis,revision,origin,reason,refs,spin_mode,sampling,allow_empty_seed=False):
    meta,e,groups,cache_sha=read_cache(cache)
    if meta.get('spin_mode') is not None and meta['spin_mode'] != spin_mode:
        raise ValueError('Analysis spin mode disagrees with the parsed source cache.')
    declared_sampling={'band_path':'band_path','wannier_mesh':'mesh_fallback'}.get(meta.get('sampling'))
    if declared_sampling is not None and sampling != declared_sampling:
        raise ValueError('Analysis sampling disagrees with the parsed source cache.')
    if seed is not None and (len(seed)!=2 or not np.isfinite(seed).all() or seed[0]>=seed[1]):
        raise ValueError('Optional inspection range must be finite and increasing relative to EF.')
    basis=sorted(basis)
    if len(set(basis))!=len(basis) or set(basis)-set(groups):
        raise ValueError('Duplicate or unknown comparison-basis channel.')
    if basis and (not revision or not reason.strip() or not refs):
        raise ValueError('A comparison basis needs its revision, reason and evidence references.')
    mask=(e>=seed[0])&(e<=seed[1]) if seed is not None else np.ones(e.shape,dtype=bool)
    if not mask.any() and not allow_empty_seed:
        raise ValueError('No sampled states inside seed; review the energy reference/seed.')
    def state(index):
        return dict(k_band_1based=[int(v+1) for v in index],energy_eV=float(e[index]),
            raw_weights={a:float(w[index]) for a,w in groups.items()})
    rows=[]
    for a,w in groups.items():
        peak=np.unravel_index(np.where(mask,w,-np.inf).argmax(),e.shape) if mask.any() else None
        score=float(round(float(w[peak]),6)) if peak is not None else 0.
        grade=('weak_in_seed' if score<RAW_WEAK else 'gray' if score<RAW_CLEAR else 'visible_candidate') if peak is not None else 'no_states_in_priority'
        full_peak=np.unravel_index(w.argmax(),e.shape)
        row=dict(group=a,raw_seed_max=float(w[peak]) if seed is not None and peak is not None else None,
            absolute_grade=grade if seed is not None else 'unclassified_full_spectrum',
            seed_peak=state(peak) if seed is not None and peak is not None else None,in_comparison_basis=a in basis,
            raw_domain_max=float(w[peak]) if peak is not None else None,
            domain_peak=state(peak) if peak is not None else None,
            full_spectrum_peak=state(full_peak),raw_band_mean=w.mean(axis=0).tolist(),
            seed_role='optional caller-selected diagnostic range only; no default EF interval or basis gate',
            decision=None,branch_review_status='unreviewed',competition=None)
        if basis:
            others=[b for b in basis if b!=a]
            largest=np.maximum.reduce([groups[b] for b in others]) if others else np.zeros_like(e)
            combined=sum((groups[b] for b in others),np.zeros_like(e))
            ratio=np.divide(w,largest,out=np.full_like(w,np.nan),where=largest>0)
            summaries=[]
            for cut in [RAW_WEAK,RAW_CLEAR]:
                supported=mask&(np.round(w,6)>=cut)
                finite=supported&np.isfinite(ratio)
                winners=supported&(w>largest+1e-6)
                strongest_state=None
                if finite.any():
                    i=np.unravel_index(np.where(finite,ratio,-np.inf).argmax(),e.shape)
                    strongest_state=state(i)
                summaries.append(dict(raw_support_cutoff=cut,sampled_states=int(supported.sum()),
                    denominator_zero_states=int((supported&(largest==0)).sum()),
                    max_ratio_to_strongest=float(np.max(ratio[finite])) if finite.any() else None,
                    max_ratio_state=strongest_state,
                    exceeds_strongest_states=int(winners.sum()),
                    exceeds_strongest_state_ids=(np.argwhere(winners)+1).tolist()))
            row['competition']=dict(comparison_basis_revision=revision,comparison_channels=others,
                tested_channel_excluded=True,
                at_raw_peak=dict(strongest_selected=float(largest[peak]),selected_sum=float(combined[peak]),
                    candidate_over_strongest=float(ratio[peak]) if np.isfinite(ratio[peak]) else None,
                    denominator_zero=bool(largest[peak]==0)) if peak is not None else None,support_summaries=summaries,
                interpretation='continuous same-state evidence; no automatic ratio veto or branch-connectivity claim')
        rows.append(row)
    result=dict(policy_version=VERSION,
        source=dict(dataset=meta.get('name',Path(cache).stem),source_sha256=meta['procar_sha256'],
                    cache_sha256=cache_sha,ef_eV=meta['ef'],ef_source=meta['ef_source'],
                    nbands=e.shape[1],nkpoints=e.shape[0],sampling=sampling,spin_mode=spin_mode),
        seed_eV=list(seed) if seed is not None else None,
        inspection_domain=dict(kind='explicit_energy_range' if seed is not None else 'all_computed_states',
            energy_range_eV=list(seed) if seed is not None else None,automatic_orbital_decision=False),
        comparison_basis=basis,basis_revision=revision,
        comparison_basis_provenance=dict(origin=origin,reason=reason,evidence_refs=refs),
        effective_parameters=dict(raw_weak_below=RAW_WEAK,raw_visible_at=RAW_CLEAR,
            self_normalization=False,competition_hard_cutoff=None,
            policy_file_sha256=hashlib.sha256(POLICY_FILE.read_bytes()).hexdigest()),
        orbital_evidence=rows,status='needs_basis_review',trial_basis=None,
        scope_note='No automatic final basis, window proposal or new fit validation. Stored endpoints are retained.')
    result['analysis_id']=digest(result)
    return result


def window_checks(energies_relative, num_wann, windows, sampling):
    """Count states only on the supplied samples; a band path is provisional."""
    e = np.asarray(energies_relative, dtype=float)
    if e.ndim != 2 or not e.size or not np.isfinite(e).all():
        raise ValueError('Window checks require finite sampled k/band energies.')
    if type(num_wann) is not int or num_wann <= 0:
        raise ValueError('Window checks require a positive integer num_wann.')
    if sampling not in ('band_path', 'wannier_mesh'):
        raise ValueError('Window checks must label band_path or wannier_mesh sampling.')
    low, high = windows['dis_eV']; fl, fh = windows['frozen_eV']
    if not all(np.isfinite([low, high, fl, fh])) or not low <= fl < fh <= high:
        raise ValueError('Require dis_min <= frozen_min < frozen_max <= dis_max.')
    outer = ((e >= low) & (e <= high)).sum(axis=1)
    frozen = ((e >= fl) & (e <= fh)).sum(axis=1)
    margins = e.max(axis=1) - high
    passed = bool(np.all(outer >= num_wann) and np.all(frozen <= num_wann) and np.all(margins > 0))
    # Diagnose only the available DFT space after the physical windows/basis
    # have been chosen.  A failed fit, missing tail hint, or small positive
    # margin is not proof that more bands are needed.  No window is changed.
    top_clipped = np.where(margins <= 0)[0] + 1
    too_few = np.where(outer < num_wann)[0] + 1
    too_many = np.where(frozen > num_wann)[0] + 1
    capacity_shortfall = max(0, num_wann - e.shape[1])
    needs_data = bool(len(top_clipped) or capacity_shortfall)
    data_scope = 'projected_band_path' if sampling == 'band_path' else 'interface_wavefunctions'
    adequacy = dict(format='wannier-dft-space-audit-v1',
        data_scope=data_scope, computed_retained_bands=int(e.shape[1]),
        retained_band_capacity_shortfall=int(capacity_shortfall),
        requires_more_data=needs_data,
        affected_data=[data_scope] if needs_data else [],
        status=('needs_more_data' if needs_data else 'needs_outer_review' if len(too_few)
                else 'needs_frozen_review' if len(too_many) else 'numeric_gate_pass_not_completeness'),
        next_action=('add_bands_nscf' if needs_data else 'review_physical_outer' if len(too_few)
                     else 'review_frozen' if len(too_many) else 'inspect_projection_and_headroom'),
        zero_disentanglement_headroom_kpoints_1based=(np.where(outer == num_wann)[0]+1).tolist(),
        preserve_physical_candidate=True, automatically_changed_variables=[],
        interpretation='Audit the fixed physical outer against this dataset only. A reached computed top or inadequate total retained-band capacity requires more non-self-consistent data. Nouter too small with an unreached top first calls for physical outer review; a feasible count or positive band-top margin is not completeness. Zero headroom flags restricted disentanglement choices, not an automatic NBANDS rule. No universal margin or band-count default is imposed.')
    return dict(num_wann=num_wann, min_nouter=int(outer.min()), max_nfrozen=int(frozen.max()),
                top_margin_eV=float(margins.min()), gate_pass=passed, sampling=sampling,
                data_adequacy=adequacy,
                nouter=dict(min=int(outer.min()),median=float(np.median(outer)),max=int(outer.max())),
                nfrozen=dict(min=int(frozen.min()),median=float(np.median(frozen)),max=int(frozen.max())),
                disentanglement_headroom=dict(min=int((outer-num_wann).min()),
                    median=float(np.median(outer-num_wann)),max=int((outer-num_wann).max())),
                band_top_margin_eV=dict(min=float(margins.min()),median=float(np.median(margins)),
                    max=float(margins.max())),
                mesh_gate_pending=sampling == 'band_path',
                violating_kpoints_1based=dict(outer_too_few=too_few.tolist(),
                    frozen_too_many=too_many.tolist(), top_clipped=top_clipped.tolist()),
                scope='Available sampled k points only. First choose the physically justified outer, then inspect these counts and DFT band-top margins. Insufficient DFT coverage calls for higher non-self-consistent NBANDS, not shrinking a justified outer. A band path is provisional, not an actual interface mesh gate or fit certification.')


def band_indices(ranges, nbands):
    """Explicit energy-ordered path rows, NOT wavefunction-tracked branches."""
    if not isinstance(ranges,list) or not ranges:
        raise ValueError('Specify nonempty reviewed band ranges.')
    indices=[]
    for pair in ranges:
        if (not isinstance(pair,list) or len(pair)!=2 or
                any(type(v) is not int for v in pair) or not 1<=pair[0]<=pair[1]<=nbands):
            raise ValueError('Invalid 1-based inclusive reviewed band range.')
        indices.extend(range(pair[0]-1,pair[1]))
    if len(set(indices))!=len(indices):
        raise ValueError('Reviewed band ranges overlap.')
    return np.array(sorted(indices),dtype=int)


def manifold_checks(e, windows, domain):
    """Recompute the envelope of the explicitly reviewed target rows."""
    ids=band_indices(domain['complete_band_ranges_1based'],e.shape[1])
    low,high=windows['dis_eV']; target=e[:,ids]
    below=target<low;above=target>high
    def states(mask):
        k,b=np.where(mask)
        return [[int(x+1),int(ids[y]+1)] for x,y in zip(k,b)]
    return dict(target_band_ranges_1based=domain['complete_band_ranges_1based'],
        target_energy_extent_eV=[float(target.min()),float(target.max())],
        below_outer_states=states(below),above_outer_states=states(above),
        complete_on_sampled_path=not bool(below.any() or above.any()),
        identity_scope='reviewed energy-ordered band rows on supplied samples, not eigenvector connectivity or BZ certification')


def check_record(record,analysis,cache):
    import jsonschema
    schema=json.loads((PACKAGE/'policies/decision-v0.2.schema.json').read_text())
    jsonschema.Draft202012Validator(schema).validate(record)
    payload={k:v for k,v in analysis.items() if k!='analysis_id'}
    if digest(payload)!=analysis['analysis_id']:
        raise ValueError('Analysis fingerprint does not match its contents.')
    if record['policy_version']!=VERSION:
        raise ValueError('Policy version mismatch.')
    if analysis['effective_parameters']['policy_file_sha256']!=hashlib.sha256(POLICY_FILE.read_bytes()).hexdigest():
        raise ValueError('Policy changed; recompute evidence before validating a decision.')
    priority=record['scope']['priority_window_eV']
    if priority is not None and (not np.isfinite(priority).all() or priority[0]>=priority[1]):
        raise ValueError('Priority window must be finite and ordered.')
    ev=record['evidence']
    for field in ['analysis_id','comparison_basis','basis_revision']:
        if ev[field]!=analysis[field]:raise ValueError('Stale decision: '+field)
    if ev['source_sha256']!=analysis['source']['source_sha256']:
        raise ValueError('Source identity mismatch.')
    meta,e,groups,sha=read_cache(cache)
    if sha!=analysis['source']['cache_sha256']:
        raise ValueError('Cache changed; recompute evidence and review.')
    decisions=record['orbital_decisions']
    keys=[d['group'] for d in decisions]
    if len(keys)!=len(set(keys)) or set(keys)!=set(groups):
        raise ValueError('Record each available group exactly once; weak evidence must remain traceable.')
    included={d['group'] for d in decisions if d['action']=='include'}
    if included!=set(record['trial_basis']):
        raise ValueError('trial_basis must equal the groups whose decision is include.')
    import basis_scope
    windows=record['windows']
    domain=windows['reference_domain'] if windows is not None else {}
    target=(band_indices(domain['complete_band_ranges_1based'],e.shape[1])
            if 'complete_band_ranges_1based' in domain else None)
    task_scope_checks=basis_scope.validate(record.get('task_scope_review'),
        record.get('additional_basis_evidence'),decisions,e,groups,record.get('trial_intent'),target)
    if record['scope']['description']!=record['task_scope_review']['objective']:
        raise ValueError('Record scope must carry the reviewed task objective, not replace it with generic completeness prose.')
    unresolved=any(d['action']=='defer' for d in decisions)
    if unresolved and record['status'] in ['basis_trial_candidate','proposed_trial']:
        raise ValueError('Unresolved alternatives cannot silently become a reviewed trial basis.')
    sampling = 'band_path' if analysis['source']['sampling'] == 'band_path' else 'wannier_mesh'
    mesh_gate_pending = sampling == 'band_path'
    if record['status']=='proposed_trial' and mesh_gate_pending:
        if record['next_action'] != 'prepare_interface':
            raise ValueError('A band-based proposal must next prepare_interface; the actual mesh gate is pending until that interface completes.')
        checks = record['numerical_checks']
        if checks.get('sampling') != 'band_path' or checks.get('mesh_gate_pending') is not True:
            raise ValueError('Band-based proposals must explicitly record sampling=band_path and mesh_gate_pending=true.')
    from valence_selection import build_selection, occupations, retained_indices
    selection=record.get('band_selection')
    if selection is not None:
        expected_selection,_=build_selection(e,occupations(cache),meta,groups,record['trial_basis'],
            selection['reviewed_blocks'],POLICY['valence_selection']['minimum_gap_eV'],POLICY['valence_selection']['occupation_tolerance'])
        if selection != expected_selection:
            raise ValueError('Exclusion selection differs from recomputed occupied-block contract.')
    retained=retained_indices(selection,e.shape[1])
    windows=record['windows'];actual=None;manifold=None
    if windows is not None:
        low,high=windows['dis_eV'];fl,fh=windows['frozen_eV']
        if not all(np.isfinite([low,high,fl,fh])) or not low<=fl<fh<=high:
            raise ValueError('Require dis_min <= frozen_min < frozen_max <= dis_max.')
        nw=basis_dimension(included,meta['species'],meta['counts'],analysis['source']['spin_mode'])
        actual=window_checks(e[:,retained],nw,windows,sampling)
        domain=windows['reference_domain']
        if 'complete_band_ranges_1based' in domain:
            if not set(band_indices(domain['complete_band_ranges_1based'],e.shape[1])) <= set(retained):
                raise ValueError('Excluded bands overlap the reviewed target.')
            manifold=manifold_checks(e,windows,domain)
        elif record['status']=='proposed_trial' and sampling=='band_path':
            raise ValueError('A band proposal needs explicit complete_band_ranges_1based; narrative-only target claims are insufficient.')
        passed=actual['gate_pass']
        checks=record['numerical_checks']
        if checks is not None:
            expected=dict(analysis_id=analysis['analysis_id'],basis_revision=analysis['basis_revision'],
                trial_basis=record['trial_basis'],dis_eV=windows['dis_eV'],frozen_eV=windows['frozen_eV'])
            if any(checks[k]!=v for k,v in expected.items()):
                raise ValueError('Numerical checks describe an earlier basis/window revision.')
            if checks['gate_pass']!=passed:
                raise ValueError('Reported numerical gate contradicts recomputed state counts/top margin.')
            if 'sampling' in checks and checks['sampling'] != sampling:
                raise ValueError('Numerical check sampling disagrees with the projection evidence.')
            if 'mesh_gate_pending' in checks and checks['mesh_gate_pending'] != mesh_gate_pending:
                raise ValueError('A path-only count cannot be reported as a completed actual mesh gate.')
        if record['status']=='proposed_trial' and not passed:
            violations=actual['violating_kpoints_1based']
            if violations['top_clipped']:
                raise ValueError('The physically reviewed dis upper reaches or exceeds a computed DFT band top. Keep the justified outer candidate and increase NBANDS for the required non-self-consistent projected band path and/or interface wavefunctions; do not shrink dis to pass this gate.')
            if violations['outer_too_few']:
                raise ValueError('N_outer < num_wann at sampled k points. Recheck the physical outer and basis; if the needed outer exceeds the DFT band top, add non-self-consistent NBANDS. Do not shrink dis to pass this gate.')
            raise ValueError('N_frozen > num_wann at sampled k points; review the frozen window independently of the physical outer.')
        if record['status']=='proposed_trial' and manifold is not None and not manifold['complete_on_sampled_path']:
            exploratory=record.get('trial_intent',{}).get('mode')=='exploratory'
            if not exploratory or manifold['below_outer_states']:
                raise ValueError('Outer window cuts the explicitly reviewed complete target band group.')
    elif record['numerical_checks'] is not None:
        raise ValueError('Window checks without a current window are stale.')
    return dict(record_consistent=True,status=record['status'],recomputed_checks=actual,
                task_scope_checks=task_scope_checks,
                recomputed_manifold_checks=manifold,
                check_scope=sampling,mesh_gate_pending=mesh_gate_pending,
                interface_preparation_allowed=record['status']=='proposed_trial',
                ready_for_wannier=False,
                scope='proposal validation only; generate interface matrices and pass its actual mesh gate before running Wannier90')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    a=sub.add_parser('analyze')
    a.add_argument('--cache',required=True)
    a.add_argument('--inspect-range','--seed',dest='seed',nargs=2,type=float,
                   help='Optional caller-selected diagnostic interval; default: all computed states. No EF cutoff.')
    a.add_argument('--spin-mode',choices=['spinor','scalar'],required=True)
    a.add_argument('--sampling',choices=['band_path','mesh_fallback'],required=True)
    a.add_argument('--comparison-basis',default='')
    a.add_argument('--basis-revision',default='unassigned')
    a.add_argument('--basis-origin',choices=['projected_bands_review','user_supplied','historical_control'],default='projected_bands_review')
    a.add_argument('--basis-reason',default='')
    a.add_argument('--basis-evidence-ref',action='append',default=[])
    a.add_argument('--out',required=True)
    v=sub.add_parser('check-record')
    v.add_argument('--cache',required=True);v.add_argument('--record',required=True);v.add_argument('--analysis',required=True)
    args=parser.parse_args()
    try:
        if args.command=='analyze':
            basis=[s.strip() for s in args.comparison_basis.split(',') if s.strip()]
            result=analyze(args.cache,args.seed,basis,args.basis_revision,args.basis_origin,
                args.basis_reason,args.basis_evidence_ref,args.spin_mode,args.sampling)
            Path(args.out).write_text(json.dumps(result,ensure_ascii=False,allow_nan=False,indent=2)+'\n')
            print(json.dumps(dict(status=result['status'],analysis_id=result['analysis_id'],output=args.out)))
        else:
            result=check_record(json.loads(Path(args.record).read_text()),
                                json.loads(Path(args.analysis).read_text()),args.cache)
            print(json.dumps(result,ensure_ascii=False,allow_nan=False,indent=2))
    except (ValueError,KeyError) as exc:
        parser.exit(2,str(exc)+'\n')
    except Exception as exc:
        # Keep schema failures machine-readable as unsuccessful validation, not a success proposal.
        if exc.__class__.__module__.startswith('jsonschema'):
            parser.exit(2,'Decision schema rejected record: '+str(exc.message)+'\n')
        raise


if __name__=='__main__':main()
