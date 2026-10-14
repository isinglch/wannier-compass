#!/usr/bin/env python3
"""Make a compact, answer-free review packet from one verified projected band cache."""
import argparse
import csv
import hashlib
import json
from pathlib import Path

from manifold_selection import inspect, write_json
from orbital_evidence import read_cache
from plot_dft_bands import run as plot_dft_bands


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def make_packet(cache, out_dir, title=''):
    cache, out = Path(cache), Path(out_dir)
    if out.exists():
        raise ValueError('Use a new review-packet directory; prior evidence is immutable.')
    meta, energies, groups, cache_hash = read_cache(cache)
    if meta.get('sampling') != 'band_path':
        raise ValueError('The orbital review packet requires a projected band_path cache.')
    # Authenticate geometry and its original KPOINTS identity before creating output.
    from plot_dft_bands import verified_path
    verified_path(cache, None, cache_hash, meta, len(energies))
    evidence = inspect(cache)
    out.mkdir(parents=True)
    write_json(out/'inspection.json', evidence)
    group_names = list(groups)
    with (out/'band_table.csv').open('x', newline='') as stream:
        fields = ['band_1based', 'energy_min_eV', 'energy_max_eV',
                  'occupation_min', 'occupation_max', 'global_gap_above_eV',
                  'minimum_direct_gap_above_eV']
        fields += ['raw_mean_'+g for g in group_names]
        fields += ['raw_max_'+g for g in group_names]
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for row in evidence['band_table']:
            occupied = row['occupation_extent'] or [None, None]
            values = dict(band_1based=row['band_1based'],
                energy_min_eV=row['energy_extent_eV'][0],
                energy_max_eV=row['energy_extent_eV'][1],
                occupation_min=occupied[0], occupation_max=occupied[1],
                global_gap_above_eV=row['gap_above_global_eV'],
                minimum_direct_gap_above_eV=(row['boundary_above'] or {}).get('minimum_direct_gap_eV'))
            values.update({'raw_mean_'+g:row['raw_mean'][g] for g in group_names})
            values.update({'raw_max_'+g:row['raw_max'][g] for g in group_names})
            writer.writerow(values)
    plot_dft_bands(argparse.Namespace(cache=str(cache), path=None, out_dir=str(out/'plots'),
        energy_range=None, seed=None, decision=None, marker_area=48., title=title))
    # The ordinary plotting manifest records absolute runtime paths. A review
    # packet may be shipped to another evaluator, so retain only hashes and
    # output basenames; no source-directory provenance belongs in this artifact.
    plot_manifest_path=out/'plots/manifest.json'
    plot_manifest=json.loads(plot_manifest_path.read_text())
    plot_manifest['sources']['cache'].pop('path',None)
    for item in plot_manifest['outputs'].values():
        item['path']=Path(item['path']).name
    write_json(plot_manifest_path,plot_manifest)
    lines = [
        '# Projected-band review packet', '',
        'This packet contains current DFT evidence only. It makes no basis, target-block, exclusion, or window decision.', '',
        f'- Sampling: `{meta["sampling"]}`; {len(energies)} stored path k points, {energies.shape[1]} DFT bands.',
        f'- Species and counts: `{list(zip(meta["species"], meta["counts"]))}`.',
        f'- Energy zero: supplied SCF EF = {meta["ef"]:.6f} eV; all tabulated energies below are E−EF.',
        f'- Chemically expected starting channels: `{", ".join(evidence["chemical_starting_basis"])}`. This is a starting point, not a selected basis.',
        '- Raw projections are atomic-sphere weights on this path. No per-channel normalization or BZ-DOS claim.',
        '', '## Channels to review', '',
        '| Channel | Chemical start? | Largest raw projection | Peak path state (k, band; E−EF) | Three largest band-mean rows |',
        '|---|---:|---:|---|---|']
    for row in evidence['orbital_evidence']:
        group = row['group']
        peak = row['full_spectrum_peak']
        k, band = peak['k_band_1based']
        means = row['raw_band_mean']
        top = sorted(range(len(means)), key=lambda i: means[i], reverse=True)[:3]
        top_text = ', '.join(f'{i+1}: {means[i]:.3f}' for i in top)
        lines.append(f'| {group} | {"yes" if row["chemical_inspection_hint"] else "no"} | '
            f'{peak["raw_weights"][group]:.3f} | ({k}, {band}; {peak["energy_eV"]:.3f} eV) | {top_text} |')
    lines += ['', '## Positive sampled global gaps', '',
        'A gap is a boundary candidate, not proof that its lower block is non-target or safe to exclude.', '',
        '| After DFT band | Global gap (eV) | Energy interval E−EF (eV) |',
        '|---:|---:|---|']
    for gap in evidence['sampled_global_gaps']:
        a, b = gap['interval_eV']
        lines.append(f'| {gap["after_band_1based"]} | {gap["gap_eV"]:.4f} | [{a:.3f}, {b:.3f}] |')
    if not evidence['sampled_global_gaps']:
        lines.append('| none | — | — |')
    lines += ['', '## Required review before proposing windows', '',
        '1. Start from chemically plausible valence channels, then identify each channel’s role in the intended complete valence and related conduction manifold. If omitting a chemical starting channel, cite its strongest state on the reviewed reference rows, compare raw selected weights at that SAME state, state its physical role and the omission risk.',
        '2. Name the lowest target-valence weight and retained unwanted or weakly projected lower states. Place dis_min below the complete target, allowing some background into outer; independently raise frozen_min beyond lower background so it remains available for disentanglement rather than frozen. No gap or shared lower boundary is required.',
        '3. Separately review optional occupied non-target deep-band exclusion. A sampled global gap and physical non-target role support a prefix candidate; a direct-only gap does not. If unsafe, use a shorter prefix or none and still proceed. Choose conduction dis_max for the full target and enough states. After proposing, inspect upper_reference_audit: reference-conditioned 70% or coverage can hide selected-shell branches in higher computed bands. Re-review their physical role before accepting the outer edge; a diffuse tail alone does not require a larger outer. Then choose frozen_max from positively identified clean low-conduction target states. Cite the first deferred target state.',
        '4. After a draft `propose`, run `conduction_review.py questions` on that selection and this same cache. Answer the outside-reference state and each band-edge/first-positive frozen candidate. A deferred candidate needs a bad state inside that candidate interval, or an explicitly exploratory uncertainty. Revise the review and propose again; then run `conduction_review.py check` and `review_diagnostics.py` before selecting real_first_try or retry.',
        '', '## Files', '',
        '- `inspection.json`: full machine-readable evidence on this cache.',
        '- `band_table.csv`: every DFT band with energy/occupation extents, adjacent gap evidence and every raw element×shell weight.',
        '- `plots/dft_bands.png/pdf` and `plots/projected_bands.png/pdf`: official full-path figures.',
        '- `packet_manifest.json`: source and output hashes. No historical model or Wannier fit was read.']
    (out/'REVIEW_PACKET.md').write_text('\n'.join(lines)+'\n')
    outputs = {str(p.relative_to(out)):dict(sha256=sha(p),bytes=p.stat().st_size)
               for p in sorted(out.rglob('*')) if p.is_file()}
    manifest = dict(format='wannier-review-packet-v1', complete=True,
        cache=dict(sha256=cache_hash),
        inspection_id=evidence['inspection_id'],
        tools={name:sha(Path(__file__).with_name(name)) for name in
               ('review_packet.py','manifold_selection.py','plot_dft_bands.py')},
        outputs=outputs, scope='Current projected-band evidence only; no historical answers or scientific selection.')
    write_json(out/'packet_manifest.json', manifest)
    return manifest


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache',required=True)
    parser.add_argument('--out-dir',required=True)
    parser.add_argument('--title',default='')
    args=parser.parse_args()
    try:
        manifest=make_packet(args.cache,args.out_dir,args.title)
    except (ValueError,KeyError,OSError) as exc:
        parser.exit(2,'Review packet error: '+str(exc)+'\n')
    print(json.dumps(dict(output=args.out_dir,inspection_id=manifest['inspection_id'],complete=True)))


if __name__=='__main__':
    main()
