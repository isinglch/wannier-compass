#!/usr/bin/env python3
"""Prepare native postw90 interpolation at the exact current DFT path; convert output.

This helper never launches a program, evaluates an hr.dat Fourier sum,
assigns DFT/Wannier bands, or accepts a fit. NumPy and Python's standard library only.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil

import numpy as np


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def record(path):
    path = Path(path).resolve()
    return dict(path=str(path), sha256=sha(path), bytes=path.stat().st_size)


def read_json(path):
    return json.loads(Path(path).read_text())


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + '\n')


def number(text):
    value = float(text.replace('D', 'E').replace('d', 'e'))
    if not np.isfinite(value):
        raise ValueError('Nonfinite number.')
    return value


def win_info(text):
    assignments, blocks, block = {}, {}, None
    for line in text.splitlines():
        clean = re.split(r'[!#]', line, maxsplit=1)[0].strip()
        if not clean:
            continue
        match = re.fullmatch(r'begin\s+(\w+)', clean, flags=re.I)
        if match:
            block = match[1].lower()
            if block in blocks:
                raise ValueError('Repeated win block: ' + block)
            blocks[block] = []
            continue
        match = re.fullmatch(r'end\s+(\w+)', clean, flags=re.I)
        if match:
            if block != match[1].lower():
                raise ValueError('Unbalanced win block.')
            block = None
            continue
        if block:
            blocks[block].append(clean)
            continue
        match = re.fullmatch(r'(\w+)\s*[=:]\s*(.+)', clean)
        if match:
            key = match[1].lower()
            if key in assignments:
                raise ValueError('Repeated win parameter: ' + key)
            assignments[key] = match[2].strip()
    if block:
        raise ValueError('Unclosed win block.')
    if assignments.get('num_iter') != '0':
        raise ValueError('Current workflow requires explicit num_iter = 0; a localized or unspecified source cannot become a disentanglement-only validation input.')
    nw = int(assignments['num_wann'])
    if nw <= 0:
        raise ValueError('num_wann must be positive.')
    if assignments.get('effective_model', 'false').strip('.').lower() in ('t', 'true'):
        raise ValueError('Only checkpoint/eigenvalue interpolation is supported, not effective_model.')
    if abs(number(assignments.get('scissors_shift', '0'))) > 1e-14:
        raise ValueError('Scissors shifts would change the compared Hamiltonian; unsupported.')
    lines = list(blocks.get('unit_cell_cart', []))
    factor = 1.0
    if lines and lines[0].lower() in ('ang', 'angstrom', 'bohr'):
        if lines.pop(0).lower() == 'bohr':
            factor = 0.529177210903
    lattice = factor * np.array([[number(v) for v in line.split()] for line in lines])
    if lattice.shape != (3, 3) or abs(np.linalg.det(lattice)) < 1e-12:
        raise ValueError('Explicit nonsingular unit_cell_cart with three row vectors is required.')
    return assignments, nw, lattice


def geninterp_win(text):
    win_info(text)  # Never inherit a nonzero or implicit localization default.
    # Preserve the Wannier basis, windows, geometry, and interpolation convention.
    # Native postw90 task flags alone are replaced so no unrelated transport runs.
    flags = dict(dos=False, kpath=False, kslice=False, spin_moment=False,
                 berry=False, boltzwann=False, gyrotropic=False,
                 geninterp=True, geninterp_alsofirstder=False, geninterp_single_file=True)
    result, block = [], None
    for line in text.splitlines():
        clean = re.split(r'[!#]', line, maxsplit=1)[0].strip()
        match = re.fullmatch(r'begin\s+(\w+)', clean, flags=re.I)
        if match:
            block = match[1].lower()
        match = re.fullmatch(r'end\s+(\w+)', clean, flags=re.I)
        if match:
            block = None
        match = re.fullmatch(r'(\w+)\s*[=:]\s*(.+)', clean)
        if block is None and match and match[1].lower() in flags:
            continue
        result.append(line)
    result += ['', '! Exact-path validation only; native interpolation convention unchanged.']
    result += [f'{key} = {str(value).lower()}' for key, value in flags.items()]
    return '\n'.join(result) + '\n'


def path_data(cache, path, tolerance=1e-6):
    cache = Path(cache)
    fit = read_json(path)
    with np.load(cache, allow_pickle=False) as data:
        meta = json.loads(str(data['metadata']))
        geometry = json.loads(str(data['path_geometry']))
        k = np.asarray(data['kcoords'], dtype=float)
    if meta.get('sampling') != 'band_path' or k.ndim != 3 or k.shape[0] != 1 or k.shape[2] != 3 or len(k[0]) < 2:
        raise ValueError('One-channel current band_path cache is required.')
    k = k[0]
    if not np.isfinite(k).all():
        raise ValueError('Nonfinite DFT k points.')
    if fit.get('cache_sha256') != sha(cache):
        raise ValueError('fit_path.json must authenticate this exact cache_sha256.')
    if fit.get('procar_sha256') != meta.get('procar_sha256'):
        raise ValueError('fit_path.json PROCAR provenance differs from cache.')
    if fit.get('source_kpoints_sha256') != geometry.get('source_kpoints_sha256'):
        raise ValueError('Path/cache KPOINTS provenance differs.')
    if fit.get('coordinate_system') != 'fractional_reciprocal' or geometry.get('kcoords_type') != 'fractional':
        raise ValueError('Explicit fractional reciprocal path is required.')
    reciprocal = np.asarray(geometry['reciprocal_lattice_2pi_per_angstrom'], dtype=float)
    if reciprocal.shape != (3, 3) or not np.isfinite(reciprocal).all() or abs(np.linalg.det(reciprocal)) < 1e-12:
        raise ValueError('Invalid reciprocal lattice in cache.')
    segments = fit.get('segments', [])
    if not segments:
        raise ValueError('Explicit path segments are required.')
    x, previous_stop = np.full(len(k), np.nan), 0
    for i, segment in enumerate(segments):
        start, stop = segment['start'], segment['stop']
        if type(start) is not int or type(stop) is not int or not 0 <= start < stop <= len(k) or stop-start < 2:
            raise ValueError('Invalid zero-based path slice.')
        if start not in ([0] if i == 0 else [previous_stop-1, previous_stop]) or stop <= previous_stop:
            raise ValueError('Segments must cover the stored sequence; at most one shared endpoint.')
        expected = np.linspace(segment['start_fractional'], segment['end_fractional'], stop-start)
        if expected.shape != (stop-start, 3) or not np.allclose(k[start:stop], expected, atol=tolerance, rtol=0):
            raise ValueError('DFT point sequence differs from declared straight segments.')
        steps = np.linalg.norm(np.diff(k[start:stop], axis=0) @ reciprocal, axis=1)
        if (steps <= 0).any():
            raise ValueError('Path distance must increase inside every segment.')
        origin = 0.0 if i == 0 else x[previous_stop-1]
        x[start:stop] = origin + np.r_[0., np.cumsum(steps)]
        previous_stop = stop
    if previous_stop != len(k) or not np.isfinite(x).all():
        raise ValueError('Path segments do not cover all DFT samples.')
    if geometry.get('segments_1based') != [[s['start']+1, s['stop']] for s in segments]:
        raise ValueError('Cache and fit_path segment slices differ.')
    return meta, fit, k, reciprocal, x


def prepare(trial, cache, path, output, executable, seed='wannier90'):
    if not re.fullmatch(r'[A-Za-z0-9_-]+', seed):
        raise ValueError('Use a simple seed basename.')
    trial, out = Path(trial).resolve(), Path(output).resolve()
    if out.exists():
        raise ValueError('Use a new directory; interpolation outputs must not overwrite trials.')
    originals = {ext: trial/f'{seed}.{ext}' for ext in ('win', 'chk', 'eig')}
    sources = {key: record(value) for key, value in originals.items()}
    if any(value['bytes'] == 0 for value in sources.values()):
        raise ValueError('win, chk and eig must be nonempty current outputs.')
    sources.update(cache=record(cache), fit_path=record(path), postw90=record(executable))
    text = originals['win'].read_text()
    assignments, nw, lattice = win_info(text)
    meta, fit, k, reciprocal, x = path_data(cache, path)
    if not np.allclose(2*np.pi*np.linalg.inv(lattice).T, reciprocal, atol=1e-7, rtol=0):
        raise ValueError('Wannier win cell differs from current DFT path cell.')
    out.mkdir(parents=True)
    for ext in ('chk', 'eig'):
        shutil.copyfile(originals[ext], out/f'{seed}.{ext}')
    (out/f'{seed}.win').write_text(geninterp_win(text))
    lines = ['Exact current DFT path; preserve sequence and repeated endpoints.', 'crystal', str(len(k))]
    lines += [f'{i} ' + ' '.join(f'{v:.17g}' for v in row) for i, row in enumerate(k, 1)]
    (out/f'{seed}_geninterp.kpt').write_text('\n'.join(lines)+'\n')
    inputs = {p.name: record(p) for p in out.iterdir() if p.is_file()}
    manifest = dict(format='native-geninterp-path-1', seed=seed, num_wann=nw, nkpoints=len(k),
                    sources=sources, prepared_inputs=inputs, helper_sha256=sha(__file__),
                    kcoords_fractional=k.tolist(), reciprocal_lattice_2pi_per_angstrom=reciprocal.tolist(),
                    distance_inv_angstrom=x.tolist(), segments=fit['segments'], ef_eV=meta['ef'],
                    use_ws_distance_input=assignments.get('use_ws_distance', 'native version default (unchanged)'),
                    command=[str(Path(executable).resolve()), seed],
                    status='prepared_not_executed', energy_reference='absolute_eV_no_shift',
                    scope='Native checkpoint/eigenvalue interpolation only; checkpoint quality is not certified.')
    write_json(out/'geninterp_manifest.json', manifest)
    return dict(status=manifest['status'], directory=str(out), num_wann=nw, nkpoints=len(k), command=manifest['command'])


def convert(directory):
    root = Path(directory).resolve()
    manifest_path = root/'geninterp_manifest.json'
    manifest = read_json(manifest_path)
    if manifest.get('format') != 'native-geninterp-path-1':
        raise ValueError('Unrecognized interpolation manifest.')
    for name, value in manifest['prepared_inputs'].items():
        if Path(name).name != name or sha(root/name) != value['sha256']:
            raise ValueError('Prepared interpolation input changed: ' + name)
    for name, value in manifest['sources'].items():
        if sha(value['path']) != value['sha256']:
            raise ValueError('Current interpolation source changed: ' + name)
    meta, fit, k, reciprocal, x = path_data(manifest['sources']['cache']['path'], manifest['sources']['fit_path']['path'])
    seed, nw, nk = manifest['seed'], manifest['num_wann'], manifest['nkpoints']
    if k.shape != (nk, 3) or not np.array_equal(k, np.asarray(manifest['kcoords_fractional'])) or not np.allclose(x, manifest['distance_inv_angstrom'], atol=1e-12, rtol=0):
        raise ValueError('Manifest path differs from authenticated DFT data.')
    native = root/f'{seed}_geninterp.dat'
    rows, ids = [], []
    for line in native.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        fields = line.split()
        if len(fields) != 5:
            raise ValueError('Expected native energy-only geninterp output with five columns.')
        ids.append(int(fields[0])); rows.append([number(v) for v in fields[1:]])
    if len(rows) != nk*nw or ids != np.repeat(np.arange(1, nk+1), nw).tolist():
        raise ValueError('Geninterp point IDs/order/multiplicities differ; no sorting or inferred remapping allowed.')
    values = np.asarray(rows).reshape(nk, nw, 4)
    cart_error = float(np.max(np.abs(values[:, :, :3] - (k @ reciprocal)[:, None, :])))
    if cart_error > 1e-7:
        raise ValueError('Native Cartesian output coordinates do not match the requested DFT sequence.')
    energies = values[:, :, 3]
    if (np.diff(energies, axis=1) < -1e-9).any():
        raise ValueError('Expected the native ascending eigensolver sequence at each k.')
    wpout = root/f'{seed}.wpout'
    if not wpout.exists() or 'All done: postw90 exiting' not in wpout.read_text():
        raise ValueError('Native postw90 completion footer is missing.')
    paths = dict(dat=root/f'{seed}_band.dat', kpt=root/f'{seed}_band.kpt', report=root/'geninterp_conversion.json')
    if any(p.exists() for p in paths.values()):
        raise ValueError('Conversion outputs exist; do not overwrite earlier review evidence.')
    with paths['dat'].open('x') as stream:
        for band in energies.T:
            for distance, energy in zip(x, band):
                stream.write(f'{distance:.17g} {energy:.17g}\n')
            stream.write('\n')
    with paths['kpt'].open('x') as stream:
        stream.write(str(nk)+'\n')
        for row in k:
            stream.write(' '.join(f'{value:.17g}' for value in row)+'\n')
    result = dict(status='native_output_converted_not_fit_accepted', fit_pass=None,
                  sources=dict(manifest=record(manifest_path), native_output=record(native), native_log=record(wpout), **manifest['sources']),
                  outputs={key:record(value) for key,value in paths.items() if key != 'report'},
                  helper_sha256=sha(__file__), nkpoints=nk, num_wann=nw,
                  max_cartesian_k_difference_inv_angstrom=cart_error,
                  energy_reference='absolute_eV_no_shift', ef_eV=meta['ef'],
                  segments=fit['segments'], distance_units='inverse_angstrom',
                  path_rule='Euclidean reciprocal length within each segment; zero intersegment jump; endpoints and break slices preserved.',
                  limitations=['No process return code inspection; caller must retain and check the execution ledger.',
                               'Native completion and same-point conversion do not certify localization or fit quality.',
                               'No fitted energy shift, band assignment, missing-branch or off-path validation.'])
    write_json(paths['report'], result)
    return dict(status=result['status'], wannier_dat=str(paths['dat']), wannier_kpt=str(paths['kpt']), report=str(paths['report']))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('prepare')
    for option in ('trial', 'cache', 'path', 'out', 'postw90'):
        p.add_argument('--'+option, required=True)
    p.add_argument('--seed', default='wannier90')
    p = sub.add_parser('convert'); p.add_argument('--directory', required=True)
    args = parser.parse_args()
    result = prepare(args.trial, args.cache, args.path, args.out, args.postw90, args.seed) if args.command == 'prepare' else convert(args.directory)
    print(json.dumps(result, allow_nan=False, indent=2))


if __name__ == '__main__':
    main()
