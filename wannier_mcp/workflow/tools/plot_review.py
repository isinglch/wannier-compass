#!/usr/bin/env python3
"""Portable, read-only raw-projection review plots. No reference-fit inputs."""
import argparse
import csv
import hashlib
import json
from pathlib import Path

import numpy as np

from orbital_evidence import read_cache


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def path_axis(cache, nk, geometry_file=None):
    """Use physical distances only with explicit coordinates/units/segments."""
    with np.load(cache, allow_pickle=False) as z:
        coords = np.asarray(z['kcoords'], dtype=float) if 'kcoords' in z else None
        embedded = json.loads(str(z['path_geometry'])) if 'path_geometry' in z else None
    if coords is not None and coords.shape == (1, nk, 3):
        coords = coords[0]
    geometry = json.loads(Path(geometry_file).read_text()) if geometry_file else embedded
    source = {'kcoords_present': coords is not None,
              'geometry_source': str(Path(geometry_file).resolve()) if geometry_file else
              ('cache:path_geometry' if embedded is not None else None)}
    if geometry_file:
        source['geometry_sha256'] = sha256(geometry_file)
    if geometry is None:
        return np.arange(1, nk + 1, dtype=float), np.ones(nk), {
            **source, 'kind': 'sample_index', 'label': 'Stored k-point index (geometry unavailable)',
            'segments_1based': None, 'ticks': [],
            'note': 'Coordinates alone do not define metric and path breaks; no band lines are drawn.'}
    if coords is None or coords.shape != (nk, 3) or not np.isfinite(coords).all():
        raise ValueError('Physical geometry requires finite kcoords with shape (nk,3) or (1,nk,3).')
    kind = geometry.get('kcoords_type')
    if kind == 'fractional':
        reciprocal = np.asarray(geometry.get('reciprocal_lattice_2pi_per_angstrom'), dtype=float)
        if reciprocal.shape != (3, 3) or not np.isfinite(reciprocal).all() or abs(np.linalg.det(reciprocal)) < 1e-12:
            raise ValueError('Fractional coordinates require three reciprocal basis ROW vectors including 2*pi.')
        coords = coords @ reciprocal
    elif kind != 'cartesian_inv_angstrom':
        raise ValueError('Specify kcoords_type: fractional or cartesian_inv_angstrom.')
    segments = geometry.get('segments_1based')
    if not isinstance(segments, list) or not segments:
        raise ValueError('Explicit inclusive segments_1based are required for physical path distances.')
    x = np.full(nk, np.nan)
    weights = np.zeros(nk)
    offset = 0.0
    last = 0
    normalized = []
    for pair in segments:
        if not isinstance(pair, list) or len(pair) != 2 or any(type(v) is not int for v in pair):
            raise ValueError('Each segment is an inclusive pair of integer, one-based indices.')
        first, stop = pair
        if first != last + 1 or stop < first + 1 or stop > nk:
            raise ValueError('Segments must partition all stored points in order, each with at least two points.')
        steps = np.linalg.norm(np.diff(coords[first-1:stop], axis=0), axis=1)
        if steps.sum() <= 0:
            raise ValueError('Each physical segment must have positive length.')
        x[first-1:stop] = offset + np.r_[0.0, np.cumsum(steps)]
        weights[first-1:stop-1] += steps / 2
        weights[first:stop] += steps / 2
        offset = x[stop-1]
        last = stop
        normalized.append([first, stop])
    if last != nk:
        raise ValueError('segments_1based must cover every stored k point.')
    ticks = geometry.get('ticks', [])
    for tick in ticks:
        if not isinstance(tick, dict) or type(tick.get('index_1based')) is not int or not 1 <= tick['index_1based'] <= nk or not isinstance(tick.get('label'), str):
            raise ValueError('ticks require index_1based and a string label.')
    return x, weights, {**source, 'kind': 'segmented_distance',
        'label': 'Cumulative segment length (1/Angstrom; jumps excluded)',
        'segments_1based': normalized, 'ticks': ticks, 'geometry': geometry,
        'note': 'Each explicit segment contributes its own length; gaps/jumps are not connected.'}


def projected_spectrum(energies, groups, kweight, sigma, grid):
    """Gaussian path/sample average, normalized by sum of input k weights."""
    if sigma <= 0 or not np.isfinite(sigma):
        raise ValueError('Gaussian sigma must be finite and positive.')
    q = kweight / kweight.sum()
    flat_e = energies.ravel()
    density = {a: np.zeros_like(grid) for a in groups}
    # Bound memory independently of the number of bands and energy samples.
    for start in range(0, len(flat_e), 512):
        end = start + 512
        kernel = np.exp(-.5 * ((grid[:, None] - flat_e[None, start:end]) / sigma)**2)
        kernel /= sigma * np.sqrt(2 * np.pi)
        for a, raw in groups.items():
            density[a] += kernel @ (raw * q[:, None]).ravel()[start:end]
    return density


def current_windows(path, procar_sha):
    if path is None:
        return None
    record = json.loads(Path(path).read_text())
    if record.get('evidence', {}).get('source_sha256') != procar_sha:
        raise ValueError('Decision overlay must identify the same PROCAR source_sha256.')
    windows = record.get('windows')
    if windows is None:
        return None
    low, high = windows['dis_eV']
    frozen_low, frozen_high = windows['frozen_eV']
    if not np.isfinite([low, high, frozen_low, frozen_high]).all() or not low <= frozen_low < frozen_high <= high:
        raise ValueError('Current candidate windows must be finite, nested, and increasing.')
    return {'dis_eV': [low, high], 'frozen_eV': [frozen_low, frozen_high]}


def save_csv(path, columns, rows):
    with Path(path).open('w', newline='') as stream:
        writer = csv.writer(stream)
        writer.writerow(columns)
        writer.writerows(rows)


def make_review(args):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    out = Path(args.out_dir)
    if out.exists() and (not out.is_dir() or any(out.iterdir())):
        raise ValueError('Output directory must be new or empty; use a separate directory for each review revision.')
    meta, energies, all_groups, cache_hash = read_cache(args.cache)
    groups = args.groups.split(',') if args.groups else list(all_groups)
    basis = args.comparison_basis.split(',') if args.comparison_basis else []
    groups = [g.strip() for g in groups]
    basis = [g.strip() for g in basis]
    if len(set(groups)) != len(groups) or len(set(basis)) != len(basis) or set(groups + basis) - set(all_groups):
        raise ValueError('Unknown or duplicate element:s/p/d/f channel.')
    if args.seed is not None and (not np.isfinite(args.seed).all() or args.seed[0] >= args.seed[1]):
        raise ValueError('Explicit inspection range must be finite and increasing relative to EF.')
    if not np.isfinite(args.marker_area) or args.marker_area <= 0:
        raise ValueError('Marker area per unit raw weight must be positive.')
    if args.points < 100:
        raise ValueError('Use at least 100 energy samples for the diagnostic spectrum.')
    seed_mask = (energies >= args.seed[0]) & (energies <= args.seed[1]) if args.seed is not None else np.ones_like(energies,dtype=bool)
    extent = args.energy_range or [float(energies.min()), float(energies.max())]
    if not np.isfinite(extent).all() or extent[0] >= extent[1]:
        raise ValueError('Energy plot range must be finite and increasing.')
    x, arc_weights, geometry = path_axis(args.cache, energies.shape[0], args.geometry)
    if args.spectrum_weight == 'path_arclength' and geometry['kind'] != 'segmented_distance':
        raise ValueError('Path arclength weighting needs verified physical geometry and explicit segments.')
    weights = arc_weights if args.spectrum_weight == 'path_arclength' else np.ones(len(x))
    grid = np.linspace(energies.min() - 5*args.sigma, energies.max() + 5*args.sigma, args.points)
    selected = {a: all_groups[a] for a in groups}
    density = projected_spectrum(energies, selected, weights, args.sigma, grid)
    windows = current_windows(args.decision, meta['procar_sha256'])
    detail=args.seed or extent
    if windows:
        from plot_fit_review import window_display_ranges
        contexts=window_display_ranges(windows)
        extent=args.energy_range or contexts['outer_context']
        detail=args.seed or contexts['frozen_context']
    out.mkdir(parents=True, exist_ok=True)
    produced = []
    xx = np.repeat(x[:, None], energies.shape[1], axis=1)
    palette = plt.get_cmap('tab10')

    def overlays(ax):
        ax.axhline(0, color='black', lw=.65, alpha=.55)
        if args.seed is not None: ax.axhspan(*args.seed, color='#e8bc5b', alpha=.13)
        if windows:
            for key, color, style in [('dis_eV', '#008f82', '--'), ('frozen_eV', '#96549c', ':')]:
                for i, edge in enumerate(windows[key]):
                    ax.axhline(edge, color=color, linestyle=style, lw=1.1,
                               label=key.replace('_eV', '') + ' candidate' if i == 0 else None)

    def decorate_path(ax):
        ax.set_xlabel(geometry['label'], fontsize=8)
        ticks = geometry['ticks']
        if ticks:
            # Multiple names at a segment junction share a single honest tick.
            merged = {}
            for tick in ticks:
                position = float(x[tick['index_1based']-1])
                merged.setdefault(position, []).append(tick['label'])
            ax.set_xticks(list(merged), ['|'.join(dict.fromkeys(v)) for v in merged.values()])
        if geometry['segments_1based']:
            for first, _ in geometry['segments_1based'][1:]:
                ax.axvline(x[first-1], color='.75', lw=.6, linestyle=':')

    for i, a in enumerate(groups):
        color = palette(i % 10)
        fig, axs = plt.subplots(1, 3, figsize=(13.2, 4.65), gridspec_kw={'width_ratios': [1.35, 1.35, 1]})
        for ax, limits, title in [(axs[0], extent, 'Computed projection'), (axs[1], detail, 'Explicit detail' if args.seed else 'Frozen context' if windows else 'All supplied states')]:
            visible = (energies >= limits[0]) & (energies <= limits[1])
            ax.scatter(xx[visible], energies[visible], s=1.0, color='.7', alpha=.5, linewidths=0, rasterized=True)
            ax.scatter(xx[visible], energies[visible], s=args.marker_area * all_groups[a][visible],
                       color=color, alpha=.55, linewidths=0, rasterized=True)
            ax.set_ylim(*limits)
            ax.set_ylabel('E - EF (eV)')
            ax.set_title(title, fontsize=10)
            overlays(ax)
            decorate_path(ax)
        handles = [axs[1].scatter([], [], s=args.marker_area*w, color=color, alpha=.55,
                                 label=f'{w:g}') for w in [.1, .5, 1.0]]
        fig.legend(handles=handles, title='Raw weight (same area scale)', fontsize=7, title_fontsize=7,
                   loc='lower center', bbox_to_anchor=(.51, .065), ncol=3, frameon=False)
        axs[2].plot(density[a], grid, color=color, lw=1.3)
        axs[2].set_ylim(*extent)
        axs[2].set_xlim(left=0)
        axs[2].set_xlabel('Raw projection / eV / weighted k sample', fontsize=8)
        axs[2].set_ylabel('E - EF (eV)')
        axs[2].set_title('Path/sample sum; NOT BZ DOS', fontsize=10)
        overlays(axs[2])
        if windows:
            axs[2].legend(fontsize=7, loc='best', frameon=False)
        fig.suptitle(f'{a} | unnormalized charge projection | Gaussian sigma = {args.sigma:g} eV', fontsize=12)
        fig.text(.5, .02, f'{args.spectrum_weight}; no per-channel normalization; inspection domain: '+(str(args.seed)+' eV' if args.seed is not None else 'all computed states')
                 + ('; current window overlay is not a validated fit' if windows else ''), ha='center', fontsize=8)
        fig.subplots_adjust(left=.06, right=.98, bottom=.23, top=.83, wspace=.32)
        stem = 'projection_' + a.replace(':', '_')
        for suffix in ['png', 'pdf']:
            path = out / f'{stem}.{suffix}'
            fig.savefig(path, dpi=180)
            produced.append(path)
        plt.close(fig)

    state_csv = out / 'raw_states.csv'
    save_csv(state_csv, ['k_1based', 'band_1based', 'path_x', 'energy_minus_ef_eV'] + list(all_groups),
             ([k+1, b+1, x[k], energies[k, b]] + [all_groups[a][k, b] for a in all_groups]
              for k in range(energies.shape[0]) for b in range(energies.shape[1])))
    produced.append(state_csv)
    spectrum_csv = out / 'path_projected_spectrum.csv'
    save_csv(spectrum_csv, ['energy_minus_ef_eV'] + groups,
             ([energy] + [density[a][i] for a in groups] for i, energy in enumerate(grid)))
    produced.append(spectrum_csv)
    weight_csv = out / 'path_sampling.csv'
    save_csv(weight_csv, ['k_1based', 'path_x', 'spectrum_weight_unnormalized', 'spectrum_weight_normalized'],
             ([i+1, x[i], weights[i], weights[i]/weights.sum()] for i in range(len(x))))
    produced.append(weight_csv)

    if basis:
        largest = np.maximum.reduce([all_groups[a] for a in basis])
        combined = sum(all_groups[a] for a in basis)
        competitors = [a for a in groups if a not in basis]
        competition_csv = out / 'same_state_competition.csv'
        save_csv(competition_csv, ['candidate', 'k_1based', 'band_1based', 'energy_minus_ef_eV',
                 'candidate_raw', 'strongest_B_raw', 'sum_B_raw', 'candidate_over_strongest_B'],
                 ([a, k+1, b+1, energies[k,b], all_groups[a][k,b], largest[k,b], combined[k,b],
                   all_groups[a][k,b]/largest[k,b] if largest[k,b] > 0 else '']
                  for a in competitors for k,b in np.argwhere(seed_mask)))
        produced.append(competition_csv)
        for a in competitors:
            fig, axs = plt.subplots(1, 2, figsize=(9, 4.6))
            candidate = all_groups[a][seed_mask]
            for ax, other, label in [(axs[0], largest, 'Strongest single B channel'), (axs[1], combined, 'Sum of B channels')]:
                other_seed = other[seed_mask]
                top = max(float(candidate.max()) if candidate.size else 0., float(other_seed.max()) if other_seed.size else 0., .01)*1.05
                ax.scatter(other_seed, candidate, color='#3e78aa', s=9, alpha=.5, rasterized=True)
                ax.plot([0,top], [0,top], color='.55', linestyle='--', lw=.8)
                ax.set(xlim=(0,top), ylim=(0,top), xlabel=label + ' raw weight', ylabel=a + ' raw weight')
                ax.set_aspect('equal')
            fig.suptitle(f'{a}: same (k, band) comparison on the declared inspection domain', fontsize=11)
            fig.text(.5, .03, 'B = ' + ', '.join(basis) + '\nDiagonal is equality, not a selection threshold; no band-connectivity claim.',
                     ha='center', fontsize=8)
            fig.subplots_adjust(bottom=.24, top=.86, wspace=.32)
            for suffix in ['png', 'pdf']:
                path = out / ('competition_' + a.replace(':', '_') + '.' + suffix)
                fig.savefig(path, dpi=180)
                produced.append(path)
            plt.close(fig)

    provenance = {
        'format_version': 'wannier-review-plots-1',
        'source': {'cache_sha256': cache_hash, 'procar_sha256': meta['procar_sha256'],
                   'ef_eV': meta['ef'], 'ef_source': meta['ef_source'],
                   'nk': energies.shape[0], 'nb': energies.shape[1]},
        'parameters': {'inspection_energy_range_eV': args.seed, 'inspection_domain': 'explicit_range' if args.seed is not None else 'all_computed_states', 'display_energy_range_eV': extent,
                       'groups_plotted': groups, 'comparison_basis': basis,
                       'basis_revision': args.basis_revision, 'basis_reason': args.basis_reason,
                       'marker_area_pt2_per_raw_weight': args.marker_area,
                       'self_normalization': False, 'spectrum_weight': args.spectrum_weight,
                       'gaussian_sigma_eV': args.sigma, 'energy_grid_points': args.points},
        'geometry': geometry,
        'spectrum_definition': 'sum_k q_k sum_n w_alpha(k,n) Gaussian_sigma(E-E_kn) / sum_k q_k; NOT Brillouin-zone DOS',
        'current_decision_overlay': {'sha256': sha256(args.decision), 'windows': windows,
                                     'scientifically_validated': False} if args.decision else None,
        'dependencies': {'numpy': np.__version__, 'matplotlib': matplotlib.__version__},
        'tool_sha256': sha256(__file__), 'cache_reader_sha256': sha256(Path(__file__).with_name('orbital_evidence.py')),
        'outputs': {p.name: sha256(p) for p in produced},
        'scope': 'Projection evidence only. No historical fitted basis/windows/results are loaded. No automatic orbital selection, fit acceptance, DOS, or branch matching.'}
    (out / 'provenance.json').write_text(json.dumps(provenance, ensure_ascii=False, allow_nan=False, indent=2) + '\n')
    return {'output_directory': str(out.resolve()), 'plots': len([p for p in produced if p.suffix == '.png']),
            'geometry': geometry['kind'], 'fit_validation': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache', required=True)
    parser.add_argument('--inspect-range','--seed',dest='seed',nargs=2,type=float,help='Optional diagnostic interval; otherwise compare all computed states.')
    parser.add_argument('--out-dir', required=True)
    parser.add_argument('--groups', default='', help='Comma-separated element:s/p/d/f; default all raw groups.')
    parser.add_argument('--comparison-basis', default='')
    parser.add_argument('--basis-revision', default='unassigned')
    parser.add_argument('--basis-reason', default='')
    parser.add_argument('--decision', help='Explicit CURRENT decision record; only source-matched candidate window overlay.')
    parser.add_argument('--geometry', help='Explicit geometry JSON; otherwise cache:path_geometry or honest sample index.')
    parser.add_argument('--spectrum-weight', choices=['sample_equal', 'path_arclength'], default='sample_equal')
    parser.add_argument('--sigma', type=float, default=.10, help='Gaussian standard deviation in eV (not FWHM).')
    parser.add_argument('--points', type=int, default=1600)
    parser.add_argument('--marker-area', type=float, default=100., help='Marker area in points squared per raw weight.')
    parser.add_argument('--energy-range', nargs=2, type=float, help='Display range only; all computed states enter the spectrum.')
    args = parser.parse_args()
    if args.comparison_basis and (args.basis_revision == 'unassigned' or not args.basis_reason.strip()):
        parser.error('Comparison B needs --basis-revision and --basis-reason from this review.')
    try:
        print(json.dumps(make_review(args), ensure_ascii=False))
    except (ValueError, KeyError, TypeError, OSError) as exc:
        parser.exit(2, str(exc) + '\n')


if __name__ == '__main__':
    main()
