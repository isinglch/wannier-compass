#!/usr/bin/env python3
"""Plot one current DFT/Wannier trial on a verified identical k path.

No material lookup, path rescaling, automatic band assignment, or fit approval.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

import numpy as np

from orbital_evidence import read_cache


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_wannier(dat_path, kpt_path):
    lines = [v.strip() for v in Path(kpt_path).read_text().splitlines() if v.strip()]
    try:
        nk = int(lines[0])
    except (ValueError, IndexError) as exc:
        raise ValueError('Wannier kpt must start with the integer number of k points.') from exc
    rows = [list(map(float, line.split())) for line in lines[1:]]
    if nk < 2 or len(rows) != nk or any(len(row) not in (3, 4) for row in rows):
        raise ValueError('Wannier kpt must contain nk rows of fractional x y z [weight].')
    kcoords = np.asarray([row[:3] for row in rows])
    blocks, current = [], []
    for line in Path(dat_path).read_text().splitlines() + ['']:
        if not line.strip():
            if current:
                blocks.append(np.asarray(current, dtype=float))
                current = []
            continue
        if line.lstrip().startswith('#'):
            continue
        fields = line.split()
        if len(fields) != 2:
            raise ValueError('Wannier dat requires two columns and blank-separated band blocks.')
        current.append([float(v) for v in fields])
    if not blocks or any(b.shape != (nk, 2) for b in blocks):
        raise ValueError('Each Wannier band block must have exactly the kpt point count.')
    x = blocks[0][:, 0]
    energies = np.column_stack([b[:, 1] for b in blocks])
    if not all(np.isfinite(v).all() for v in (kcoords, x, energies)):
        raise ValueError('Nonfinite Wannier k coordinates, distances, or energies.')
    if any(not np.allclose(b[:, 0], x, rtol=0, atol=1e-10) for b in blocks):
        raise ValueError('Wannier band blocks use different distance sequences.')
    if (np.diff(x) < -1e-10).any():
        raise ValueError('Wannier path distance must be nondecreasing across all points.')
    return kcoords, x, energies


def verify_path(cache, kcoords, x, path, tolerance):
    with np.load(cache, allow_pickle=False) as z:
        if 'kcoords' not in z:
            raise ValueError('DFT cache needs kcoords; energy arrays alone cannot align paths.')
        dft_k = np.asarray(z['kcoords'], dtype=float)
    if dft_k.shape != (1, len(kcoords), 3) or not np.isfinite(dft_k).all():
        raise ValueError('DFT kcoords must have shape (1, nk, 3), matching Wannier exactly.')
    delta = float(np.max(np.abs(dft_k[0] - kcoords)))
    if delta > tolerance:
        raise ValueError(f'DFT/Wannier k sequences differ by {delta:g}; no rescaling or remapping allowed.')
    if path.get('coordinate_system') != 'fractional_reciprocal':
        raise ValueError('Path must explicitly use fractional_reciprocal coordinates.')
    segments = path.get('segments', [])
    if not segments:
        raise ValueError('Explicit path segments are required; gaps cannot be inferred from energy curves.')
    previous_stop = 0
    for i, segment in enumerate(segments):
        start, stop = segment['start'], segment['stop']
        if type(start) is not int or type(stop) is not int:
            raise ValueError('Segment start/stop must be zero-based integer slices.')
        if not 0 <= start < stop <= len(kcoords) or stop-start < 2:
            raise ValueError('Each segment needs at least two in-range points.')
        allowed_starts = [0] if i == 0 else [previous_stop-1, previous_stop]
        if start not in allowed_starts or stop <= previous_stop:
            raise ValueError('Segments must cover the sequence in order, with at most one shared endpoint.')
        endpoints = np.asarray([segment['start_fractional'], segment['end_fractional']], dtype=float)
        if endpoints.shape != (2, 3) or not np.isfinite(endpoints).all():
            raise ValueError('Segment endpoints must be finite fractional three-vectors.')
        expected = np.linspace(endpoints[0], endpoints[1], stop-start)
        if not np.allclose(kcoords[start:stop], expected, atol=tolerance, rtol=0):
            raise ValueError(f'Segment {i+1} does not match the explicitly declared straight path.')
        if (np.diff(x[start:stop]) <= 0).any():
            raise ValueError('Distance must strictly increase within each declared segment.')
        previous_stop = stop
    if previous_stop != len(kcoords):
        raise ValueError('Declared segments do not cover every sampled point.')
    return delta


def interval(values, name):
    if len(values) != 2 or not np.isfinite(values).all() or values[0] >= values[1]:
        raise ValueError(f'{name} must be two finite increasing energies relative to EF.')
    return list(map(float, values))


def window_display_ranges(windows, frozen_padding=1., outer_padding=3.):
    """Context around physical windows, never a fixed interval around EF."""
    if not np.isfinite([frozen_padding,outer_padding]).all() or min(frozen_padding,outer_padding)<0:
        raise ValueError('Display padding must be finite and nonnegative.')
    dis=interval(windows['dis_eV'],'Outer window');frozen=interval(windows['frozen_eV'],'Frozen window')
    if not dis[0]<=frozen[0]<frozen[1]<=dis[1]:
        raise ValueError('Current decision windows must be nested.')
    return dict(frozen_context=[frozen[0]-frozen_padding,frozen[1]+frozen_padding],
                outer_context=[dis[0]-outer_padding,dis[1]+outer_padding])


def mapping_metrics(mapping, dft, wannier, regions=None):
    refs = mapping.get('evidence_refs')
    if (not isinstance(mapping.get('reason'), str) or not mapping['reason'].strip()
            or not isinstance(refs, list) or not refs
            or any(not isinstance(v, str) or not v.strip() for v in refs)):
        raise ValueError('A band mapping requires its review reason and evidence_refs.')
    pairs = mapping.get('pairs_1based', [])
    if not pairs or any(len(p) != 2 or any(type(v) is not int for v in p) for p in pairs):
        raise ValueError('Mapping requires integer [dft_band, wannier_band] pairs.')
    di, wi = zip(*pairs)
    if len(set(di)) != len(di) or len(set(wi)) != len(wi):
        raise ValueError('Mapping must be one-to-one; reusing a DFT or Wannier band is forbidden.')
    if min(di) < 1 or max(di) > dft.shape[1] or min(wi) < 1 or max(wi) > wannier.shape[1]:
        raise ValueError('Mapped band number outside the supplied arrays.')
    reference = dft[:, np.asarray(di)-1]
    error = wannier[:, np.asarray(wi)-1] - reference
    # A legacy explicit interval remains a caller request, never a default.
    if isinstance(regions,(list,tuple)):
        regions={'requested':regions}
    def stats(values):
        return dict(sampled_states=int(values.size), mae_eV=float(np.mean(np.abs(values))),
                    rmse_eV=float(np.sqrt(np.mean(values**2))), max_abs_eV=float(np.max(np.abs(values)))) if values.size else None
    return dict(pairs_1based=pairs, full_mapped_path=stats(error),
                regions_by_dft_energy={name:dict(interval_eV=interval(pair,name),
                    statistics=stats(error[(reference>=pair[0])&(reference<=pair[1])]))
                    for name,pair in (regions or {}).items()},
                weighting='equal sampled states; no BZ integration or path-length weighting',
                mapping_scope='caller-reviewed fixed band-index pairs; connectivity across crossings is not verified',
                reference_scope='only mapped DFT bands; omitted bands are not assessed')


def run(args):
    meta, dft, _, cache_hash = read_cache(args.cache)
    wk, x, wannier = read_wannier(args.wannier_dat, args.wannier_kpt)
    if len(dft) != len(wk):
        raise ValueError('DFT and Wannier k-point counts differ.')
    if not np.isfinite(args.k_tolerance) or not 0 < args.k_tolerance <= 1e-4:
        raise ValueError('k tolerance must be positive and at most 1e-4 fractional coordinate units.')
    path = json.loads(Path(args.path).read_text())
    mismatch = verify_path(args.cache, wk, x, path, args.k_tolerance)
    focus = interval(args.seed, 'Explicit focus') if args.seed is not None else None
    if args.wannier_energy_reference == 'absolute':
        wannier = wannier - meta['ef']
    mapping = json.loads(Path(args.mapping).read_text()) if args.mapping else None
    decision = json.loads(Path(args.decision).read_text()) if args.decision else None
    windows = decision.get('windows') if decision else None
    regions={};views=[];display_ranges={}
    if windows is not None:
        dis = interval(windows['dis_eV'], 'Outer window')
        frozen = interval(windows['frozen_eV'], 'Frozen window')
        if not dis[0] <= frozen[0] < frozen[1] <= dis[1]:
            raise ValueError('Current decision windows must be nested.')
        display_ranges=window_display_ranges(windows,getattr(args,'frozen_padding',1.),getattr(args,'outer_padding',3.))
        views=[('Frozen window with context',display_ranges['frozen_context']),
               ('Outer window with context',display_ranges['outer_context'])]
        regions=dict(frozen=frozen,outer=dis)
    full = interval(args.full_range, 'Full plot range') if args.full_range else [float(min(dft.min(), wannier.min()))-.2, float(max(dft.max(), wannier.max()))+.2]
    if not views or args.full_range:
        views.append(('Declared display range' if args.full_range else 'All supplied states',full))
        display_ranges['declared' if args.full_range else 'all_supplied']=full
    if focus is not None:
        views.append(('Explicit caller-selected detail',focus));display_ranges['requested_detail']=focus
        regions['requested_detail']=focus
    metrics = mapping_metrics(mapping,dft,wannier,regions) if mapping else None
    inputs = dict(dft_cache=args.cache, wannier_dat=args.wannier_dat,
                  wannier_kpt=args.wannier_kpt, path=args.path)
    if args.mapping:
        inputs['mapping'] = args.mapping
    if args.decision:
        inputs['decision'] = args.decision
    prefix = Path(args.out_prefix)
    outputs = {ext:Path(str(prefix)+'.'+ext) for ext in ['png', 'pdf', 'json']}
    if any(p.exists() for p in outputs.values()):
        raise ValueError('Output already exists; use a new out-prefix to preserve prior review evidence.')
    if any(p.resolve() in [Path(v).resolve() for v in inputs.values()] for p in outputs.values()):
        raise ValueError('Output must not overwrite an input.')
    prefix.parent.mkdir(parents=True, exist_ok=True)
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D
    figsize=[6.5*len(views),5.4]
    fig, axes = plt.subplots(1,len(views),figsize=figsize,constrained_layout=True,squeeze=False)
    axes=axes.ravel()
    for ax, (title,limits) in zip(axes,views):
        for seg in path['segments']:
            sl = slice(seg['start'], seg['stop'])
            ax.plot(x[sl], dft[sl], color='0.55', lw=.7, alpha=.8)
            ax.plot(x[sl], wannier[sl], color='#c22d3b', lw=.95, ls='--')
        if windows is not None:
            for values, color in [(dis, '#249667'), (frozen, '#df9b24')]:
                ax.axhspan(*values, color=color, alpha=.10)
                for value in values:
                    ax.axhline(value, color=color, lw=.8, ls=':')
        ticks = {}
        for seg in path['segments']:
            for index, label in [(seg['start'], seg.get('start_label', '')), (seg['stop']-1, seg.get('end_label', ''))]:
                loc = float(x[index])
                if label:
                    values = ticks.setdefault(loc, [])
                    if label not in values:
                        values.append(label)
        if ticks:
            ax.set_xticks(list(ticks), [' | '.join(v) for v in ticks.values()])
            for value in ticks:
                ax.axvline(value, color='0.85', lw=.6)
        ax.axhline(0, color='#22447a', lw=.9, ls=':')
        ax.set(xlim=(x[0], x[-1]), ylim=limits, ylabel=r'$E-E_F$ (eV)',
               xlabel='Verified shared path (Wannier distance coordinate)', title=title)
    handles = [Line2D([0], [0], color='0.55', lw=1, label='DFT'), Line2D([0], [0], color='#c22d3b', ls='--', label='Current Wannier')]
    if windows is not None:
        handles += [Line2D([0], [0], color='#249667', ls=':', label='Outer window'), Line2D([0], [0], color='#df9b24', ls=':', label='Frozen window')]
    axes[0].legend(handles=handles, loc='best', fontsize=9)
    fig.suptitle(args.title or 'Current trial: DFT and Wannier band review', fontsize=13)
    fig.savefig(outputs['png'], dpi=180)
    fig.savefig(outputs['pdf'])
    plt.close(fig)
    result = dict(status='mapped_error_review' if mapping else 'overlay_only', fit_pass=None,
        tool=dict(script_sha256=sha256(__file__),
                  cache_adapter_sha256=sha256(Path(__file__).with_name('orbital_evidence.py')),
                  numpy_version=np.__version__, matplotlib_version=matplotlib.__version__),
        sources={key:dict(path=str(Path(value).resolve()), sha256=sha256(value)) for key, value in inputs.items()},
        dft_cache_sha256=cache_hash, dft_procar_sha256=meta['procar_sha256'],
        ef_eV=meta['ef'], ef_source=meta['ef_source'],
        wannier_energy_reference=args.wannier_energy_reference,
        alignment=dict(nkpoints=len(wk), coordinate_system='fractional_reciprocal', tolerance=args.k_tolerance,
                       max_coordinate_difference=mismatch, segments=path['segments'],
                       path_rescaling=False, k_reordering=False, energy_shift_fitted=False),
        bands=dict(dft=dft.shape[1], wannier=wannier.shape[1]),
        requested_detail_eV=focus,display_ranges_eV=display_ranges,decision_windows=windows,metrics=metrics,
        display_range_role='Visualization context only; padding is not an acceptance range or error tolerance.',
        plotting=dict(title=args.title or 'Current trial: DFT and Wannier band review',
                      png_dpi=180, size_inches=figsize),
        limitations=['Same sampled path only; no off-path validation.',
                     'Decision windows are display annotations, not validated execution settings.',
                     'No wavefunction, symmetry, response-function, or subspace-rank validation.',
                     'No automatic tolerance or fit approval.'],
        artifacts={key:dict(path=str(value.resolve()), sha256=sha256(value)) for key, value in outputs.items() if key != 'json'})
    outputs['json'].write_text(json.dumps(result, indent=2, allow_nan=False)+'\n')
    return dict(status=result['status'], fit_pass=None, output=str(outputs['json']))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--cache', required=True)
    p.add_argument('--wannier-dat', required=True)
    p.add_argument('--wannier-kpt', required=True)
    p.add_argument('--wannier-energy-reference', required=True, choices=['absolute', 'relative_to_ef'])
    p.add_argument('--path', required=True, help='Explicit fractional path segments JSON')
    p.add_argument('--focus-range','--seed',dest='seed',type=float,nargs=2,
                   help='Optional explicit extra view; no default EF-centered range.')
    p.add_argument('--frozen-padding',type=float,default=1.,help='Display margin outside each frozen edge, eV.')
    p.add_argument('--outer-padding',type=float,default=3.,help='Display margin outside each outer edge, eV.')
    p.add_argument('--full-range', type=float, nargs=2)
    p.add_argument('--k-tolerance', type=float, default=1e-6)
    p.add_argument('--mapping', help='Reviewed one-to-one fixed band-index mapping JSON')
    p.add_argument('--decision', help='Current decision JSON; windows shown as annotations only')
    p.add_argument('--title')
    p.add_argument('--out-prefix', required=True)
    try:
        print(json.dumps(run(p.parse_args())))
        return 0
    except (ValueError, KeyError, TypeError, OSError, IndexError) as exc:
        print(f'Cannot produce aligned fit review: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
