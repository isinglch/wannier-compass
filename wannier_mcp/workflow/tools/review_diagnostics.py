#!/usr/bin/env python3
"""Audit a proposed selection against the same projected band path, without approving it."""
import argparse
from collections import Counter
import json
from pathlib import Path

import numpy as np

from manifold_selection import (FROZEN_COUNTEREXAMPLE_ID, frozen_counterexample_contract,
                                frozen_state_counterexample_screen, inspect, write_json)
from orbital_evidence import POLICY, band_indices, digest, read_cache


def _samples(mask, energies, weight, limit=20):
    ids = np.argwhere(mask)
    ids = sorted(ids, key=lambda ij: (-float(weight[tuple(ij)]), int(ij[0]), int(ij[1])))[:limit]
    return [dict(k_1based=int(k+1), band_1based=int(b+1), energy_eV=float(energies[k,b]),
                 diagnostic_weight=float(weight[k,b])) for k,b in ids]


def audit(cache, inspection, review, selection):
    meta, energies, groups, cache_sha = read_cache(cache)
    if meta.get('sampling') != 'band_path':
        raise ValueError('Only a projected band_path can be audited here.')
    if inspection != inspect(cache, inspection.get('seed_eV')) or inspection['inspection_id'] != selection['inspection_id']:
        raise ValueError('Inspection is stale or belongs to another cache/selection.')
    if review['inspection_id'] != inspection['inspection_id'] or digest(review) != selection['review_sha256']:
        raise ValueError('Review does not match this selection.')
    if cache_sha != selection['cache_sha256']:
        raise ValueError('Cache does not match this selection.')
    basis = selection['basis']
    if basis != sorted(row['group'] for row in review['orbital_decisions'] if row['action'] == 'include'):
        raise ValueError('Selection basis differs from the reviewed basis.')
    if selection['target_band_ranges_1based'] != review['target_band_ranges_1based']:
        raise ValueError('Selected target rows differ from review.')
    target = band_indices(review['target_band_ranges_1based'], energies.shape[1])
    reference = band_indices(review['reference_band_ranges_1based'], energies.shape[1])
    selected = sum((groups[name] for name in basis), np.zeros_like(energies))
    omitted = {name: weight for name, weight in groups.items() if name not in basis}
    largest_omitted = np.maximum.reduce(list(omitted.values())) if omitted else np.zeros_like(energies)
    target_mask = np.zeros_like(energies, dtype=bool)
    target_mask[:,target] = True
    windows = selection['windows_relative_eV']
    fl, fh = windows['frozen_eV']
    retained=np.asarray([i for i in range(energies.shape[1])
                         if i+1 not in selection['band_selection']['exclude_bands_1based']],dtype=int)
    contract=frozen_counterexample_contract()
    if ('criterion_contracts' in selection and
        selection['criterion_contracts'].get(FROZEN_COUNTEREXAMPLE_ID) != contract):
        raise ValueError('Frozen-state counterexample criterion contract changed.')
    negative_screen=frozen_state_counterexample_screen(energies,groups,basis,retained,[fl,fh],
                                                       POLICY['selection_parameters'])
    if ('adopted_frozen_negative_screen' in selection and
        selection['adopted_frozen_negative_screen'] != negative_screen):
        raise ValueError('Adopted frozen negative screen differs from current projected path.')
    frozen_mask = (energies >= fl) & (energies <= fh)
    excluded = selection['band_selection']['exclude_bands_1based']
    if excluded:
        frozen_mask[:,np.asarray(excluded, dtype=int)-1] = False
    floor = float(POLICY['frozen_upper_review']['absolute_target_floor'])
    weak = frozen_mask & (selected < floor)
    competing = frozen_mask & (largest_omitted > selected)
    channel_rows = []
    for row in review['orbital_decisions']:
        name = row['group']
        weight = groups[name]
        mean = weight[:,target].mean(axis=0)
        strongest = target[np.argsort(-mean)[:min(5,len(target))]]
        peak = np.unravel_index(np.argmax(np.where(target_mask,weight,-np.inf)),weight.shape)
        same_state = dict(k_1based=int(peak[0]+1), band_1based=int(peak[1]+1),
            energy_eV=float(energies[peak]),raw_weight=float(weight[peak]),
            selected_sum_raw=float(selected[peak]),
            strongest_other_raw=float(max((g[peak] for key,g in groups.items() if key!=name),default=0.)))
        channel_rows.append(dict(group=name, action=row['action'],
            target_raw_max=float(weight[:,target].max()),
            target_raw_mean=float(weight[:,target].mean()),
            strongest_target_bands_1based=[int(b+1) for b in strongest],
            peak_target_state=same_state,
            target_states_stronger_than_selected_sum=int(np.count_nonzero(target_mask & (weight>selected)))
                if name in omitted else None))
    chemical_omissions=[]
    for name,item in review.get('chemical_omission_evidence',{}).items():
        k,b=[v-1 for v in item['peak_reference_k_band_1based']]
        chemical_omissions.append(dict(group=name,k_band_1based=[k+1,b+1],energy_eV=float(energies[k,b]),
            raw_weight=float(groups[name][k,b]),selected_sum_raw=float(selected[k,b]),
            physical_role=item['physical_role'],reason=item['reason'],risk_if_omitted=item['risk_if_omitted']))
    row_candidate_audit=None
    if review.get('frozen_upper_decision'):
        item=review['frozen_upper_decision'];k,b=[v-1 for v in item['first_unprotected_target_k_band_1based']]
        row_candidate_audit=dict(k_band_1based=[k+1,b+1],energy_eV=float(energies[k,b]),
            selected_sum_raw=float(selected[k,b]),unselected_sum_raw=float(sum(w[k,b] for w in omitted.values())),
            disposition=item['disposition'],reason=item['reason'])
    adopted_first_unprotected=selection.get('adopted_frozen_first_unprotected_target')
    outside=np.argwhere(target_mask & (energies>fh) & (energies>0))
    expected_first=(min(outside,key=lambda kb:(float(energies[tuple(kb)]),int(kb[0]),int(kb[1])))
                    if len(outside) else None)
    if expected_first is None:
        if adopted_first_unprotected is not None:
            raise ValueError('No target lies outside adopted frozen window on sampled path.')
    else:
        k,b=map(int,expected_first)
        if (adopted_first_unprotected is None or
                adopted_first_unprotected['k_band_1based']!=[k+1,b+1] or
                abs(float(energies[k,b])-adopted_first_unprotected['energy_eV'])>1e-8):
            raise ValueError('First target outside adopted frozen window disagrees with sampled path.')
    reason_counts = Counter(row['reason'].strip() for row in review['orbital_decisions'])
    duplicate_reasons = [dict(reason=reason, count=count,
        groups=[row['group'] for row in review['orbital_decisions'] if row['reason'].strip()==reason])
        for reason,count in reason_counts.items() if count>1]
    status = review['reference_upper_status']
    text = review['reference_reason'].lower()
    status_conflict = (status == 'reviewed_decay' and 'right_censored' in text) or (
        status == 'right_censored' and 'reviewed_decay' in text)
    checks = selection['numerical_checks']
    findings = []
    if duplicate_reasons:
        findings.append('Several orbital decisions reuse identical prose; inspect each channel and its actual band block.')
    if status_conflict:
        findings.append('Reference upper status conflicts with a status term in its explanation; resolve the physical claim.')
    if weak.any():
        findings.append('The adopted frozen window contains retained states with weak absolute selected-channel projection. Review every k region, including the initial window.')
    if competing.any():
        findings.append('The adopted frozen window contains retained states where an omitted shell exceeds the selected-channel sum.')
    if not checks['gate_pass']:
        findings.append('Sampled-path numerical count/top gate fails; this selection cannot be treated as executable.')
    data_adequacy=checks.get('data_adequacy')
    if data_adequacy and data_adequacy['requires_more_data']:
        findings.append('The chosen physical outer reaches the computed projected-path top or retained-band capacity is inadequate: add non-self-consistent projected bands; preserve the physical candidate. The interface wavefunctions need a separate check.')
    final_review=selection.get('adopted_frozen_upper_review')
    if final_review and not final_review['resolved']:
        findings.append('The first target state outside the final scalar frozen window still needs an explicit expand/defer review; use the emitted coordinates, not only the earlier frozen-row envelope.')
    if reference.max() == energies.shape[1]-1:
        findings.append('Reference touches the last computed DFT band; upper completeness is right-censored.')
    return dict(format='wannier-review-diagnostics-v1', cache_sha256=cache_sha,
        inspection_id=inspection['inspection_id'], review_sha256=digest(review),
        proposal_id=selection['proposal_id'], sampling='band_path',
        scope='Diagnostic evidence on sampled k path only. No automatic orbital/window approval or interface mesh claim.',
        selected_basis=basis, target_band_ranges_1based=review['target_band_ranges_1based'],
        reference_band_ranges_1based=review['reference_band_ranges_1based'],
        frozen_eV=[float(fl),float(fh)], excluded_bands_1based=excluded,
        channel_evidence=channel_rows,
        chemical_omission_audit=chemical_omissions,
        frozen_row_candidate_audit=row_candidate_audit,
        first_unprotected_target_audit=adopted_first_unprotected,
        adopted_frozen_upper_review=final_review,
        frozen_expansion_evidence=selection.get('frozen_expansion_evidence'),
        upper_reference_audit=selection.get('upper_reference_audit'),
        dft_space_assessment=data_adequacy,
        numerical_space_checks=checks,
        frozen_state_audit=dict(retained_state_count=int(frozen_mask.sum()),
            absolute_target_floor_raw_unitless=floor, weak_target_count=int(weak.sum()),
            omitted_stronger_count=int(competing.sum()),
            weak_target_samples=_samples(weak,energies,floor-selected),
            omitted_stronger_samples=_samples(competing,energies,largest_omitted-selected)),
        adopted_frozen_negative_screen=negative_screen,
        criterion_contracts={FROZEN_COUNTEREXAMPLE_ID:contract},
        repeated_orbital_reasons=duplicate_reasons,
        reference_status_text_conflict=status_conflict,
        numerical_gate_pass=bool(checks['gate_pass']), findings=findings)


def markdown(report):
    lines=['# Selection review diagnostics','','Frozen-state counterexample screen: this criterion runs after a fixed basis and independent frozen proposal. Lower-edge counterexamples call for reviewing a higher frozen_min; upper-edge counterexamples may veto a higher frozen_max or call for lowering it. It changes neither edge automatically and cannot add/delete orbitals, decide exclude/outer, or infer enlargement from no flags.','',
           f'- Basis: `{", ".join(report["selected_basis"])}`',
           f'- Frozen: `{report["frozen_eV"]}` eV relative to SCF EF',
           f'- Path numerical gate: `{report["numerical_gate_pass"]}`',
           f'- Retained frozen states: {report["frozen_state_audit"]["retained_state_count"]}; '
           f'weak selected projection: {report["frozen_state_audit"]["weak_target_count"]}; '
           f'omitted shell stronger: {report["frozen_state_audit"]["omitted_stronger_count"]}',
           f'- Counterexample energy extent: '
           f'`{report["adopted_frozen_negative_screen"]["first_negative_energy_eV"]}` to '
           f'`{report["adopted_frozen_negative_screen"]["last_negative_energy_eV"]}` eV. '
           'Inspect the band-path samples to decide which edge, if any, can move.',
           f'- Downward-only upper reference, if one exists: '
           f'`{report["adopted_frozen_negative_screen"]["possible_upper_shrink_eV"]}` eV. '
           'This is not an automatic window change.',
           '', '## Channel evidence in declared target rows','',
           '| Channel | Action | Mean raw | Max raw | Strongest target band rows | Peak target state (k, band) |',
           '|---|---|---:|---:|---|---|']
    for row in report['channel_evidence']:
        p=row['peak_target_state']
        lines.append(f'| {row["group"]} | {row["action"]} | {row["target_raw_mean"]:.3f} | '
          f'{row["target_raw_max"]:.3f} | {row["strongest_target_bands_1based"]} | '
          f'({p["k_1based"]}, {p["band_1based"]}); raw {p["raw_weight"]:.3f}, '
          f'other {p["strongest_other_raw"]:.3f} |')
    lines += ['', '## Chemical-start omissions', '']
    if not report['chemical_omission_audit']: lines.append('- None.')
    for item in report['chemical_omission_audit']:
        lines.append(f'- {item["group"]}: reference peak at {item["k_band_1based"]}, '
            f'E−EF={item["energy_eV"]:.3f} eV; raw {item["raw_weight"]:.3f}, '
            f'selected sum {item["selected_sum_raw"]:.3f}; role {item["physical_role"]}. '
            f'Reason: {item["reason"]} Risk: {item["risk_if_omitted"]}')
    lines += ['', '## Frozen low-conduction decision', '']
    candidate=report['frozen_row_candidate_audit']
    if candidate is not None:
        lines.append(f'- Reviewed row-envelope candidate: first state above those rows at '
            f'{candidate["k_band_1based"]}, E−EF={candidate["energy_eV"]:.3f} eV; '
            f'{candidate["disposition"]}. This coordinate precedes scalar rounding/override. '
            f'Reason: {candidate["reason"]}')
    item=report['first_unprotected_target_audit']
    if item is None: lines.append('- No positive-energy reviewed target state lies above the adopted scalar frozen upper on this path.')
    else:
        lines.append(f'- First target outside the adopted scalar frozen upper at {item["k_band_1based"]}, '
            f'E−EF={item["energy_eV"]:.3f} eV; selected {item["selected_sum_raw"]:.3f}, '
            f'unselected {item["unselected_sum_raw"]:.3f}; adopted upper {item["frozen_upper_eV"]:.3f} eV.')
    final_review=report.get('adopted_frozen_upper_review')
    if final_review:
        lines.append(f'- Final-state review resolved: {final_review["resolved"]}; source: {final_review["review_source"]}.')
        if final_review['decision']:
            lines.append('- Final-state reason: '+final_review['decision']['reason'])
    rows=(report['frozen_expansion_evidence'] or {}).get('candidates',[])
    lines += ['', '## Positive frozen expansion candidates', '',
        '| Upper E−EF (eV) | Added target | Added background | Selected minimum | Unselected exceeds selected | Path candidate |',
        '|---:|---:|---:|---:|---:|---|']
    for row in rows:
        minimum=row['selected_sum_min']
        lines.append(f'| {row["frozen_upper_eV"]:.3f} | {row["added_target_states"]} | '
            f'{row["added_outside_target_states"]} | {minimum if minimum is not None else "—"} | '
            f'{row["unselected_exceeds_selected_count"]} | {row["path_positive_candidate"]} |')
    if not rows: lines.append('| — | 0 | 0 | — | 0 | no higher target-row envelope |')
    upper=report.get('upper_reference_audit')
    if upper:
        near=upper['outside_reference_near_outer']
        all_near=upper.get('all_computed_near_outer')
        lines += ['', '## Full-computed-band upper reference audit', '',
            f'- Reviewed reference ends at band {upper["reference_last_band_1based"]} of {upper["computed_nbands"]} computed bands. Outer upper is {upper["outer_high_eV"]:.3f} eV; the diagnostic probe ends at {upper["probe_high_eV"]:.3f} eV.',
            f'- Highest projected-path band: {upper["computed_path_top_eV"]["min"]:.3f} to {upper["computed_path_top_eV"]["max"]:.3f} eV across k; minimum outer-to-path-top margin {upper["outer_to_computed_path_top_min_margin_eV"]:.3f} eV. The diagnostic probe lies above the computed path top at {upper["probe_censored_kpoints"]} k points. Reference reaches the last computed band: {upper["reference_reaches_last_computed_band"]}.',
            '- If the physically chosen outer/probe is censored, obtain higher non-self-consistent projected bands rather than shrinking the justified outer to fit this DFT cutoff. Check the interface wavefunction band top independently.',
            f'- Outside the reference, this outer-to-probe interval contains {near["states"]} states, selected-shell raw weight sum {near["selected_raw_sum"]:.3f}, and {upper["crossing_outside_reference_band_count"]} energy-ordered rows crossing the adopted outer upper.',
            '- These values do not set a cutoff. Review the listed crossing rows and full projected bands before treating reference-conditioned coverage as orbital completeness; energy-ordered rows are not wavefunction-tracked branches.']
        if all_near is not None:
            lines.append(f'- All computed bands in the same outer-to-probe interval: {all_near["states"]} states; selected-shell raw sum {all_near["selected_raw_sum"]:.3f}, mean {all_near["selected_raw_mean"] if all_near["selected_raw_mean"] is not None else "—"}; omitted raw sum {all_near["omitted_raw_sum"]:.3f}. The reviewed reference rows cannot hide this signal.')
        crossing_ids={row['band_1based'] for row in upper['crossing_outside_reference_bands_top_by_selected_sum']}
        for row in upper['outside_reference_near_outer_bands_top_by_selected_sum'][:10]:
            role='crosses the outer upper' if row['band_1based'] in crossing_ids else 'starts above the outer upper'
            lines.append(f'- Row {row["band_1based"]} ({role}): E−EF {row["energy_extent_eV"]}, selected raw sum {row["selected_raw_sum"]:.3f}, max {row["selected_raw_max"]:.3f}; omitted raw sum {row["omitted_raw_sum"]:.3f} in the probe interval.')
    space=report.get('dft_space_assessment')
    if space:
        checks=report['numerical_space_checks']
        lines += ['', '## DFT space after the physical outer decision', '',
            f'- Dataset: `{space["data_scope"]}`; status `{space["status"]}`; next action `{space["next_action"]}`.',
            f'- Nouter: `{checks["nouter"]}`; Nfrozen: `{checks["nfrozen"]}`; Nouter−Nwann: `{checks["disentanglement_headroom"]}`.',
            f'- Computed-band top minus dis_max (eV): `{checks["band_top_margin_eV"]}`; retained total-band capacity shortfall: {space["retained_band_capacity_shortfall"]}.',
            '- Review thin disentanglement space and small positive margins physically; neither a failed fit nor a missing tail hint sets NBANDS automatically. No universal band-count or energy-margin default applies. Keep justified outer candidates when DFT data must be supplemented.']
    lines += ['', '## Findings', '']
    lines += ['- '+item for item in report['findings']] or ['- No automated flags. Physical review is still required.']
    lines += ['', '## Highest-priority adopted-frozen samples', '']
    for key in ('weak_target_samples','omitted_stronger_samples'):
        lines.append(f'### {key}')
        for row in report['frozen_state_audit'][key][:10]:
            lines.append(f'- k={row["k_1based"]}, band={row["band_1based"]}, '
                         f'E−EF={row["energy_eV"]:.3f} eV; excess={row["diagnostic_weight"]:.3f}')
        if not report['frozen_state_audit'][key]: lines.append('- none')
        lines.append('')
    lines += ['A low sphere-projection sum can also mean interstitial character. Inspect the plotted band and all k points before changing the model or window.']
    return '\n'.join(lines)+'\n'


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('cache','inspection','review','selection','out-dir'):
        parser.add_argument('--'+name,required=True)
    args=parser.parse_args()
    out=Path(args.out_dir)
    if out.exists(): parser.exit(2,'Use a new diagnostic directory.\n')
    try:
        report=audit(args.cache,json.loads(Path(args.inspection).read_text()),
            json.loads(Path(args.review).read_text()),json.loads(Path(args.selection).read_text()))
        out.mkdir(parents=True)
        write_json(out/'review_diagnostics.json',report)
        (out/'review_diagnostics.md').write_text(markdown(report))
    except (ValueError,KeyError,OSError) as error:
        parser.exit(2,'Diagnostic error: '+str(error)+'\n')
    print(json.dumps(dict(output=str(out),findings=len(report['findings']),
        numerical_gate_pass=report['numerical_gate_pass'])))


if __name__=='__main__': main()
