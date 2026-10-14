#!/usr/bin/env python3
"""Prepare a reviewed VASP/Wannier90 interface trial; never execute a program.

Only static, same-spin-mode PAW semilocal inputs are supported. The default
band-evidence route defers actual mesh checks until the single interface NSCF.
"""
import argparse
import hashlib
import json
import math
import re
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np

import orbital_evidence as evidence

NATIVE_OUTPUT_FLAGS = ('write_hr', 'write_rmn', 'write_tb', 'bands_plot')
DIS_POLICY = evidence.POLICY['wannier_execution']
DIS_DEFAULT = DIS_POLICY['dis_num_iter_default']
DIS_MAX = DIS_POLICY['dis_num_iter_max']
DIS_TOL = DIS_POLICY['dis_conv_tol']
DIS_WINDOW = DIS_POLICY['dis_conv_window']
_SCALAR_FASTPATH_ERRORS = dict(divide='warn', over='warn', under='ignore', invalid='warn')


def validate_dis_budget(value):
    if type(value) is not int or not 1 <= value <= DIS_MAX:
        raise ValueError(f'dis_num_iter must be a positive integer <= {DIS_MAX}; num_iter remains fixed at zero.')
    return value


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for part in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(part)
    return h.hexdigest()


def require_file(path):
    path = Path(path)
    if not path.is_file() or not path.stat().st_size:
        raise ValueError(f'Required nonempty file missing: {path}')
    return path


def incar(path):
    result = {}
    text = Path(path).read_text()
    payloads = []
    def extract(match):
        payloads.append(match.group(2))
        return '\n'
    text = re.sub(r'^\s*WANNIER90_WIN\s*=\s*("""|")(.*?)\1[ \t]*(?:[!#][^\n]*)?$',
                  extract, text, flags=re.I | re.M | re.S)
    if len(payloads) > 1:
        raise ValueError('Duplicate WANNIER90_WIN payloads are forbidden.')
    for line in text.splitlines():
        line = re.split(r'[!#]', line, maxsplit=1)[0]
        for item in line.split(';'):
            if not item.strip():
                continue
            if '=' not in item:
                raise ValueError(f'Unsupported INCAR line in {path}: {item}')
            key, value = [x.strip() for x in item.split('=', 1)]
            key = key.upper()
            if key in result or not re.fullmatch(r'[A-Z][A-Z0-9_]*', key) or not value:
                raise ValueError(f'Duplicate or invalid INCAR setting: {key}')
            result[key] = value
    if payloads:
        if 'WANNIER90_WIN' in result:
            raise ValueError('Duplicate WANNIER90_WIN setting.')
        result['WANNIER90_WIN'] = payloads[0]
    return result


def boolean(value):
    value = str(value).upper().strip('.')
    if value not in ('TRUE', 'FALSE', 'T', 'F'):
        raise ValueError('Invalid VASP boolean: ' + str(value))
    return value in ('TRUE', 'T')


def integer(value):
    result = int(value)
    if str(result) != str(value).strip() and float(value) != result:
        raise ValueError('Expected integer: ' + str(value))
    return result


def spin_mode(settings):
    noncollinear = boolean(settings.get('LSORBIT', 'F')) or boolean(settings.get('LNONCOLLINEAR', 'F'))
    if integer(settings.get('ISPIN', '1')) != 1:
        raise ValueError('This interface supports scalar ISPIN=1 or noncollinear ISPIN=1 only.')
    return 'spinor' if noncollinear else 'scalar'


def physical_contract(mesh, scf, require_full_mesh=True):
    for settings in (mesh, scf):
        for flag in ('LHFCALC', 'LDAU', 'LHFONE', 'LUSE_VDW'):
            if boolean(settings.get(flag, 'F')):
                raise ValueError(f'{flag} is outside this semilocal interface contract.')
        if settings.get('METAGGA', '').upper() not in ('', 'NONE'):
            raise ValueError('METAGGA is outside this semilocal interface contract.')
        if settings.get('ALGO', 'Normal').upper() not in ('NORMAL', 'FAST', 'VERYFAST', 'ALL', 'DAMPED', 'EXACT'):
            raise ValueError('Unsupported electronic algorithm for static semilocal DFT.')
        if any(k in settings for k in ('LEXCH', 'ALGO_GW', 'NELMALL')):
            raise ValueError('Unsupported alternate electronic method.')
        if integer(settings.get('NSW', '0')) != 0:
            raise ValueError('Static NSW=0 source calculations are required.')
    if integer(mesh.get('ICHARG', '-1')) != 11 or (require_full_mesh and integer(mesh.get('ISYM', '2')) != -1):
        raise ValueError('Mesh must use ICHARG=11 and ISYM=-1.')
    if integer(scf.get('ICHARG', '2')) >= 10:
        raise ValueError('SCF source cannot itself be a fixed-density calculation.')
    if spin_mode(mesh) != spin_mode(scf):
        raise ValueError('This version requires the same spin mode; scalar-to-SOC needs a separate recorded protocol.')
    defaults = {'ISPIN': '1', 'LSORBIT': 'F', 'LNONCOLLINEAR': 'F',
                'SAXIS': '0 0 1', 'LASPH': 'F', 'GGA_COMPAT': 'T',
                'LMAXMIX': '2', 'LREAL': 'F', 'PREC': 'Normal', 'ADDGRID': 'F'}
    keys = ('ENCUT', 'ENAUG', 'GGA', 'NELECT', 'ISPIN', 'LSORBIT', 'LNONCOLLINEAR',
            'SAXIS', 'MAGMOM', 'LASPH', 'GGA_COMPAT', 'LMAXMIX', 'LREAL', 'PREC',
            'ADDGRID', 'LDIPOL', 'IDIPOL', 'DIPOL', 'EFIELD', 'VCA')
    bool_keys = {'LSORBIT', 'LNONCOLLINEAR', 'LASPH', 'GGA_COMPAT', 'ADDGRID', 'LDIPOL'}
    def canonical(key, settings):
        value = settings.get(key, defaults.get(key, ''))
        if key in bool_keys:
            return boolean(value or 'F')
        return ' '.join(value.upper().split())
    differences = [key for key in keys if canonical(key, mesh) != canonical(key, scf)]
    if differences:
        raise ValueError('SCF/mesh physical settings differ: ' + ', '.join(differences))
    return {key: canonical(key, mesh) for key in keys}


def completed_source(directory, fixed_density, expected_bands=None):
    text = require_file(directory / 'OUTCAR').read_text(errors='replace')
    if 'General timing and accounting' not in text or not re.search(r'aborting loop because EDIFF is reached', text, re.I):
        raise ValueError(f'Source lacks normal termination/electronic EDIFF convergence evidence: {directory}')
    bands = re.findall(r'NBANDS\s*=\s*(\d+)', text)
    charge = re.findall(r'ICHARG\s*=\s*(\d+)', text)
    if not bands or not charge or (int(charge[-1]) != 11 if fixed_density else int(charge[-1]) >= 10):
        raise ValueError('Source OUTCAR has missing/inconsistent NBANDS/ICHARG echo.')
    if expected_bands is not None and int(bands[-1]) != expected_bands:
        raise ValueError('Mesh OUTCAR NBANDS differs from cache.')
    record_path = directory / 'run.json'
    level = 'outcar_convergence_and_identity'
    if record_path.exists():
        record = json.loads(record_path.read_text())
        hashes = record.get('output_hashes', {})
        if record.get('returncode') != 0 or 'OUTCAR' not in hashes or not hashes:
            raise ValueError('Source runner record does not report successful identified outputs.')
        for filename, expected in hashes.items():
            if Path(filename).name != filename or sha(require_file(directory / filename)) != expected:
                raise ValueError('Source outputs changed since runner record: ' + filename)
        level = 'runner_record_and_outcar_convergence_and_identity'
    return dict(actual_nbands=int(bands[-1]), evidence_level=level,
                note='Electronic convergence markers do not certify cutoff or k-mesh convergence.')


def read_poscar(path):
    lines = Path(path).read_text().splitlines()
    scale_values = lines[1].split()
    if len(scale_values) != 1 or float(scale_values[0]) <= 0:
        raise ValueError('This adapter requires a positive single POSCAR scale.')
    scale = float(scale_values[0])
    lattice = np.array([[float(v) for v in line.split()[:3]] for line in lines[2:5]]) * scale
    if lattice.shape != (3, 3) or not np.isfinite(lattice).all() or abs(np.linalg.det(lattice)) < 1e-12:
        raise ValueError('Invalid POSCAR lattice.')
    species = lines[5].split()
    if not all(re.fullmatch('[A-Z][a-z]?', s) for s in species):
        raise ValueError('VASP5 POSCAR species labels are required.')
    counts = [int(v) for v in lines[6].split()]
    if len(counts) != len(species) or min(counts) < 1:
        raise ValueError('Invalid POSCAR counts.')
    offset = 7
    if lines[offset].strip().lower().startswith('s'):
        offset += 1
    mode = lines[offset].strip().lower()[0]
    if mode not in 'dck':
        raise ValueError('Unsupported POSCAR coordinate mode.')
    coords = np.array([[float(v) for v in line.split()[:3]] for line in lines[offset + 1:offset + 1 + sum(counts)]])
    if coords.shape != (sum(counts), 3) or not np.isfinite(coords).all():
        raise ValueError('Invalid POSCAR coordinates.')
    cart = coords @ lattice if mode == 'd' else coords * scale
    return species, counts, lattice, cart


def wrapped(points):
    return np.round(np.mod(np.asarray(points, dtype=float), 1.0), 7) % 1.0


def automatic_mesh_spec(kpoints_path):
    lines = [line.split('!')[0].strip() for line in Path(kpoints_path).read_text().splitlines() if line.split('!')[0].strip()]
    if len(lines) < 4 or int(lines[1]) != 0 or not lines[2].lower().startswith(('g', 'm')):
        raise ValueError('VASP Wannier/PEAD requires automatic Gamma/Monkhorst KPOINTS; '
                         'redo the mesh NSCF with the same physical sampling and automatic KPOINTS. '
                         'Do not rewrite KPOINTS and reuse an explicit-mesh WAVECAR with potentially different k order.')
    dims = [int(v) for v in lines[3].split()]
    if len(dims) != 3 or min(dims) <= 0 or len(lines) > 5:
        raise ValueError('Invalid automatic mesh dimensions or extra KPOINTS records.')
    shift = np.array([float(v) for v in lines[4].split()]) if len(lines) > 4 else np.zeros(3)
    if shift.shape != (3,) or not np.isfinite(shift).all():
        raise ValueError('Invalid automatic mesh shift.')
    return lines[2][0].lower(), dims, shift


def mesh_geometry(kpoints_path, points, weights):
    """Verify actual sampled coordinates against KPOINTS and a full uniform grid."""
    points = np.asarray(points, dtype=float)
    weights = np.asarray(weights, dtype=float)
    if points.ndim != 2 or points.shape[1] != 3 or not np.isfinite(points).all():
        raise ValueError('Missing finite cached k coordinates.')
    nk = len(points)
    if weights.shape != (nk,) or not np.isfinite(weights).all() or np.any(weights <= 0) or not np.allclose(weights, weights[0]):
        raise ValueError('Wannier mesh requires equal positive k weights.')
    actual = wrapped(points)
    axes = [np.unique(actual[:, i]) for i in range(3)]
    grid = [len(axis) for axis in axes]
    if np.prod(grid) != nk or len({tuple(row) for row in actual}) != nk:
        raise ValueError('Actual k points are not a complete unique product mesh.')
    for axis in axes:
        if not np.allclose(np.diff(np.r_[axis, axis[0] + 1]), 1 / len(axis), atol=2e-7, rtol=0):
            raise ValueError('Actual k points are not uniformly spaced.')
    mode, dims, shift = automatic_mesh_spec(kpoints_path)
    coordinates = []
    for i, n in enumerate(dims):
        axis = np.arange(n) / n if mode == 'g' else (2 * np.arange(n) + 1 - n) / (2 * n)
        coordinates.append(axis + shift[i] / n)
    expected = np.array(np.meshgrid(*coordinates, indexing='ij')).reshape(3, -1).T
    if dims != grid or {tuple(row) for row in wrapped(expected)} != {tuple(row) for row in actual}:
        raise ValueError('Cached full mesh differs from automatic KPOINTS.')
    return grid


def mesh_input(mesh_kpoints=None, mp_grid=None):
    """Normalize input sampling only; no existing mesh calculation is needed."""
    if (mesh_kpoints is None) == (mp_grid is None):
        raise ValueError('Specify exactly one of mesh_kpoints or mp_grid.')
    source = {}
    if mp_grid is not None:
        dims = list(mp_grid)
        if len(dims) != 3 or any(type(v) is not int or v <= 0 for v in dims):
            raise ValueError('mp_grid requires three positive integers.')
        mode, shift = 'g', np.zeros(3)
        source = dict(kind='declared_mp_grid', dimensions=dims)
    else:
        path = require_file(mesh_kpoints)
        source = dict(kind='kpoints_input', sha256=sha(path), path=str(path.resolve()))
        lines = [line.split('!')[0].strip() for line in path.read_text().splitlines() if line.split('!')[0].strip()]
        if len(lines) < 4:
            raise ValueError('Incomplete mesh KPOINTS input.')
        count = int(lines[1])
        if count == 0:
            mode, dims, shift = automatic_mesh_spec(path)
            source['normalization'] = 'automatic_input_preserved'
        else:
            if count < 1 or not lines[2].lower().startswith('r') or len(lines) != count + 3:
                raise ValueError('Explicit mesh input requires a complete reciprocal point list with equal positive weights.')
            rows = [finite_numbers(line, 4) for line in lines[3:]]
            table = np.array(rows); points = wrapped(table[:, :3]); weights = table[:, 3]
            axes = [np.unique(points[:, i]) for i in range(3)]
            dims = [len(axis) for axis in axes]
            if (np.prod(dims) != count or len({tuple(row) for row in points}) != count or
                    np.any(weights <= 0) or not np.allclose(weights, weights[0])):
                raise ValueError('Explicit input is not a complete unique uniform equal-weight mesh.')
            for axis in axes:
                if not np.allclose(np.diff(np.r_[axis, axis[0] + 1]), 1 / len(axis), atol=2e-7, rtol=0):
                    raise ValueError('Explicit input is not a uniformly spaced product mesh.')
            mode = 'g'; shift = np.array([axis[0] * n for axis, n in zip(axes, dims)])
            expected = np.array(np.meshgrid(*[(np.arange(n) + shift[i]) / n for i, n in enumerate(dims)], indexing='ij')).reshape(3, -1).T
            if {tuple(row) for row in wrapped(expected)} != {tuple(row) for row in points}:
                raise ValueError('Explicit-to-automatic mesh point sets are not equivalent.')
            source.update(normalization='explicit_product_to_equivalent_automatic_gamma',
                          point_set_verified=True, source_order_reused=False)
    text = ('Wannier interface automatic mesh\n0\n' + ('Gamma' if mode == 'g' else 'Monkhorst-Pack') + '\n' +
            ' '.join(map(str, dims)) + '\n' + ' '.join(f'{v:.12g}' for v in shift) + '\n')
    source.update(dimensions=dims, shift=shift.tolist(), actual_vasp_order='pending')
    return text, dims, source


def native_band_plot(band_dir, expected_source_hashes=None):
    """Use the actual DFT line path for native plotting, not exact-point fitting."""
    from vasp_to_cache import read_eigenval, line_geometry
    band_dir = Path(band_dir)
    hashes = {name:sha(require_file(band_dir/name)) for name in ('POSCAR','KPOINTS','EIGENVAL')}
    for name,digest in hashes.items():
        expected = (expected_source_hashes or {}).get('band/'+name)
        if expected is not None and digest != expected:
            raise ValueError('Native plot source differs from reviewed band '+name)
    _, _, lattice, _ = read_poscar(band_dir/'POSCAR')
    eigen = read_eigenval(band_dir/'EIGENVAL')
    _, fit = line_geometry(band_dir/'KPOINTS', lattice, eigen['kcoords'], 2e-7)
    fit_path = band_dir/'fit_path.json'
    if fit_path.exists():
        supplied = json.loads(fit_path.read_text())
        if (supplied.get('coordinate_system') != fit['coordinate_system'] or
                supplied.get('segments') != fit['segments'] or
                supplied.get('source_kpoints_sha256') != hashes['KPOINTS']):
            raise ValueError('fit_path.json differs from the actual DFT KPOINTS/EIGENVAL path.')
        hashes['fit_path.json'] = sha(fit_path)
    labels = {}; label_details = []; rows = []
    def label_for(raw, coords):
        identity = (str(raw), tuple(coords))
        if identity not in labels:
            candidate = str(raw).replace('Γ','G').replace('Gamma','GAMMA')
            candidate = re.sub(r'[^A-Za-z0-9_]', '_', candidate).strip('_')[:10]
            if not candidate or candidate in labels.values():
                candidate = 'K'+str(len(labels)+1).zfill(3)
            labels[identity] = candidate
            label_details.append(dict(source_label=raw, label=candidate, fractional=coords))
        return labels[identity]
    for segment in fit['segments']:
        a, b = segment['start_fractional'], segment['end_fractional']
        rows.append(label_for(segment['start_label'],a)+' '+' '.join(f'{v:.12g}' for v in a)+' '+
                    label_for(segment['end_label'],b)+' '+' '.join(f'{v:.12g}' for v in b))
    points = fit['segments'][0]['stop']-fit['segments'][0]['start']
    reciprocal = 2*np.pi*np.linalg.inv(lattice).T
    lengths = [np.linalg.norm((np.asarray(s['end_fractional'])-s['start_fractional']) @ reciprocal)
               for s in fit['segments']]
    # W90 3.1 scales later segment intervals by length and does not clamp a
    # rounded zero. Ensure every existing segment receives at least one interval.
    points = max(points, int(np.ceil(lengths[0]/min(lengths))))
    return dict(scalars={**{key:'true' for key in NATIVE_OUTPUT_FLAGS}, 'bands_num_points':str(points)},
                kpoint_path=rows, source_directory=str(band_dir.resolve()), source_hashes=hashes,
                labels=label_details, dft_segments=fit['segments'],
                sampling_scope='Native W90 uses the first-section point budget and length-scaled later sections; not guaranteed identical to the DFT sample sequence.')


def prepare_band(band_dir, scf_dir, cache, analysis_path, decision_path, out_dir,
                 mesh_kpoints=None, mp_grid=None, dis_num_iter=DIS_DEFAULT, shared_source=None):
    """Band evidence -> one interface NSCF, with the actual mesh gate deferred."""
    band_dir, scf_dir, out_dir = map(Path, (band_dir, scf_dir, out_dir))
    if out_dir.exists():
        raise ValueError('Output must be a new directory; an existing trial is never overwritten.')
    validate_dis_budget(dis_num_iter)
    shared = None
    if shared_source is not None:
        if mesh_kpoints is not None or mp_grid is not None:
            raise ValueError('A sealed shared source supplies the complete KPOINTS; do not override its grid.')
        from shared_nscf import verify
        shared = verify(shared_source, scf_dir, band_dir)
        kpoints_text = (Path(shared_source)/'KPOINTS').read_text()
        grid = shared['grid']
        sampling_input = dict(kind='sealed_shared_nscf', source_id=shared['source_id'],
                              directory=shared['directory'])
    else:
        kpoints_text, grid, sampling_input = mesh_input(mesh_kpoints, mp_grid)
    analysis = json.loads(Path(analysis_path).read_text())
    decision = json.loads(Path(decision_path).read_text())
    checks = evidence.check_record(decision, analysis, cache)
    from decision_stages import require_executable_decision
    stage_binding = require_executable_decision(decision_path, cache)
    if (decision['status'] != 'proposed_trial' or checks.get('check_scope') != 'band_path' or
            checks.get('mesh_gate_pending') is not True or not checks.get('interface_preparation_allowed')):
        raise ValueError('Band interface preparation requires a reviewed band_path proposed_trial with mesh_gate_pending=true.')
    meta, energies, groups, cache_sha = evidence.read_cache(cache)
    if meta.get('sampling') != 'band_path':
        raise ValueError('The default interface route requires a projected band_path cache.')
    tags = incar(require_file(band_dir/'INCAR')); scf_tags = incar(require_file(scf_dir/'INCAR'))
    disabled = {key for key in ('LWANNIER90', 'LWANNIER90_RUN') if key in tags and not boolean(tags[key])}
    if any(key not in disabled and ('WANN' in key or key in ('LOCPROJ', 'LSCDM', 'LWRITE_UNK', 'LWRITE_MMN_AMN', 'LWRITE_SPN')) for key in tags):
        raise ValueError('Band INCAR already contains Wannier/projection-construction settings.')
    physical = physical_contract(tags, scf_tags, require_full_mesh=False)
    mode = spin_mode(tags)
    if mode != meta.get('spin_mode') or mode != analysis['source']['spin_mode']:
        raise ValueError('Cache, analysis and band spin modes differ.')
    for name in ('POSCAR', 'POTCAR'):
        if sha(require_file(band_dir/name)) != sha(require_file(scf_dir/name)):
            raise ValueError('Band/SCF source identity differs: ' + name)
    density_sha = sha(require_file(scf_dir/'CHGCAR'))
    if (band_dir/'CHGCAR').exists() and sha(band_dir/'CHGCAR') != density_sha:
        raise ValueError('Band density differs from the current SCF CHGCAR.')
    for key, name in (('procar', 'PROCAR'), ('eigenval', 'EIGENVAL'), ('poscar', 'POSCAR'), ('kpoints', 'KPOINTS')):
        actual = sha(require_file(band_dir/name))
        if meta.get('source_files', {}).get(key, {}).get('sha256') != actual:
            raise ValueError('Cache does not identify current band ' + name)
        if key == 'procar' and meta.get('procar_sha256') != actual:
            raise ValueError('PROCAR source hash mismatch.')
    species, counts, lattice, cart = read_poscar(band_dir/'POSCAR')
    if species != meta['species'] or counts != meta['counts']:
        raise ValueError('Cache species/counts differ from current POSCAR.')
    band_nbands = energies.shape[1]
    nb = shared['actual_nbands'] if shared else band_nbands
    if nb < band_nbands:
        raise ValueError('Shared WAVECAR has fewer bands than the reviewed projected-band source.')
    completion = dict(scf=completed_source(scf_dir, False), band=completed_source(band_dir, True, band_nbands))
    if shared:
        completion['shared_nscf'] = shared['completion']
    if (band_dir/'run.json').exists():
        run_record = json.loads((band_dir/'run.json').read_text())
        if run_record.get('density_sha256') not in (None, density_sha):
            raise ValueError('Band runner density source differs from current SCF CHGCAR.')
    from valence_selection import retained_indices
    selection = decision.get('band_selection')
    retained_indices(selection, band_nbands)
    if shared and selection is not None:
        selection = dict(selection, original_nbands=nb,
                         retained_original_bands_1based=list(range(len(selection['exclude_bands_1based'])+1, nb+1)),
                         num_bands_retained=nb-len(selection['exclude_bands_1based']))
    retained = retained_indices(selection, nb)
    nw = checks['recomputed_checks']['num_wann']; windows = decision['windows']
    scalars = dict(num_wann=str(nw), num_bands=str(len(retained)), spinors='.true.' if mode == 'spinor' else '.false.',
                   mp_grid=' '.join(map(str, grid)), num_iter='0', dis_num_iter=str(dis_num_iter),
                   dis_conv_tol=str(DIS_TOL), dis_conv_window=str(DIS_WINDOW))
    if selection and selection['exclude_bands_1based']:
        scalars['exclude_bands'] = '1-'+str(len(selection['exclude_bands_1based']))
    native_plot = native_band_plot(band_dir)
    scalars.update(native_plot['scalars'])
    for names, values in ((('dis_win_min', 'dis_win_max'), windows['dis_eV']),
                         (('dis_froz_min', 'dis_froz_max'), windows['frozen_eV'])):
        scalars.update({name: f'{value + meta["ef"]:.12g}' for name, value in zip(names, values)})
    # VASP owns dimensions/spin/grid and the actual output geometry. Do not
    # duplicate auto-generated keys in WANNIER90_WIN.
    payload_scalars = {key: value for key, value in scalars.items()
                       if key not in ('num_wann', 'num_bands', 'spinors', 'mp_grid')}
    payload = '\n'.join(f'{key} = {value}' for key, value in payload_scalars.items())
    payload += '\n\nbegin projections\n' + '\n'.join(decision['trial_basis']) + '\nend projections\n'
    payload += '\nbegin kpoint_path\n' + '\n'.join(native_plot['kpoint_path']) + '\nend kpoint_path\n'
    parallelism = dict(policy='interface-ncore1-kpar1-v1', source={key: tags.get(key) for key in ('NCORE', 'NPAR', 'KPAR')},
        interface=dict(NCORE='1', NPAR=None, KPAR='1'), rank_policy='largest_divisor_of_actual_nbands_not_above_site_ntasks',
        reason='VASP Wannier/PEAD NCORE=1; preserve the sealed shared WAVECAR actual NBANDS.' if shared else
               'VASP Wannier/PEAD NCORE=1; preserve the completed projected-band actual NBANDS.')
    new_tags = dict(tags); new_tags.pop('NPAR', None)
    new_tags.update(ICHARG='11', ISTART='0', ISYM='-1', NSW='0', IBRION='-1', NBANDS=str(nb), NUM_WANN=str(nw),
        LWANNIER90='.TRUE.', LWANNIER90_RUN='.FALSE.', LWRITE_MMN_AMN='.TRUE.', LWRITE_UNK='.FALSE.',
        LCHARG='.FALSE.', LWAVE='.FALSE.', NCORE='1', KPAR='1')
    if shared:
        new_tags.pop('LORBIT', None)
        new_tags.update(ISTART='1', ALGO='None', NELM='1', LDIAG='.FALSE.')
    incar_text = '\n'.join(f'{key} = {value}' for key, value in new_tags.items()) + '\nWANNIER90_WIN = "\n' + payload + '"\n'
    copies = dict(POSCAR=band_dir/'POSCAR', POTCAR=band_dir/'POTCAR', CHGCAR=scf_dir/'CHGCAR')
    sources = {f'band/{name}': sha(band_dir/name) for name in ('INCAR', 'POSCAR', 'POTCAR', 'KPOINTS', 'PROCAR', 'EIGENVAL', 'OUTCAR')}
    sources.update({f'scf/{name}': sha(scf_dir/name) for name in ('INCAR', 'POSCAR', 'POTCAR', 'CHGCAR', 'OUTCAR')})
    sources.update(cache=cache_sha, analysis=sha(analysis_path), decision=sha(decision_path))
    if shared:
        sources['shared/SOURCE_SEAL.json'] = sha(Path(shared_source)/'SOURCE_SEAL.json')
        sources['shared/WAVECAR'] = shared['file_sha256']['WAVECAR']
    for label, directory in (('band', band_dir), ('scf', scf_dir)):
        if (directory/'run.json').exists(): sources[label+'/run.json'] = sha(directory/'run.json')
    out_dir.mkdir(parents=True)
    for name, source in copies.items(): shutil.copyfile(source, out_dir/name)
    if shared:
        wavecar_source = Path(shared_source)/'WAVECAR'
        wavecar_copy = out_dir/'WAVECAR'
        # Never give VASP a symlink to the immutable shared source. A reflink
        # is cheap where supported; otherwise this is an ordinary isolated copy.
        if sys.platform.startswith('linux'):
            try:
                subprocess.run(['cp', '--reflink=auto', '--', str(wavecar_source), str(wavecar_copy)],
                               check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except (OSError, subprocess.CalledProcessError):
                shutil.copyfile(wavecar_source, wavecar_copy)
        else:
            shutil.copyfile(wavecar_source, wavecar_copy)
        if wavecar_copy.is_symlink() or sha(wavecar_copy) != shared['file_sha256']['WAVECAR']:
            raise ValueError('Isolated WAVECAR copy differs from its sealed source.')
    (out_dir/'KPOINTS').write_text(kpoints_text)
    (out_dir/'INCAR').write_text(incar_text)
    (out_dir/'wannier90.win.in').write_text(payload)
    atoms = [dict(species=s, position=r.tolist()) for s, r in zip(sum(([s]*n for s, n in zip(species, counts)), []), cart)]
    manifest = dict(decision_stage=stage_binding, format='wannier-interface-trial-v3' if shared else 'wannier-interface-trial-v2',
        mode='band_to_shared_wavecar_interface' if shared else 'band_to_interface', status='prepared_not_executed',
        analysis_id=analysis['analysis_id'], trial_basis=decision['trial_basis'], mesh_gate_pending=True, ready_for_wannier=False,
        expected=dict(nbands=nb, num_bands_retained=len(retained), nkpoints=int(np.prod(grid)), num_wann=nw, spin_mode=mode, mp_grid=grid),
        band_selection=selection, reviewed_band_nbands=band_nbands,
        ef_eV=meta['ef'], ef_source=meta['ef_source'], windows_relative_eV=windows, win_scalars=scalars,
        payload_scalars=payload_scalars, native_plot=native_plot,
        physical_settings=physical, source_completion=completion, parallelism=parallelism, sampling_input=sampling_input,
        kpoints=None, lattice_angstrom=lattice.tolist(), atoms_cart_angstrom=atoms, source_hashes=sources,
        input_hashes={name:sha(out_dir/name) for name in [*copies, 'KPOINTS', 'INCAR', 'wannier90.win.in',
                                                          *(['WAVECAR'] if shared else [])]},
        shared_source=dict(source_id=shared['source_id'], directory=shared['directory'],
                           wavecar_sha256=shared['file_sha256']['WAVECAR'],
                           eigenval_sha256=shared['file_sha256']['EIGENVAL']) if shared else None,
        source_directories=dict(band=str(band_dir.resolve()), scf=str(scf_dir.resolve())),
        validation_scope='Projected-band basis/window proposal; actual interface k order, energies and mesh gate remain pending.')
    manifest['manifest_id'] = evidence.digest(manifest)
    (out_dir/'trial_manifest.json').write_text(json.dumps(manifest, indent=2, ensure_ascii=False, allow_nan=False)+'\n')
    return manifest


def prepare(mesh_dir, scf_dir, cache, analysis_path, decision_path, out_dir, reuse_wavecar=False, dis_num_iter=DIS_DEFAULT, band_dir=None):
    mesh_dir, scf_dir, out_dir = map(Path, (mesh_dir, scf_dir, out_dir))
    if out_dir.exists():
        raise ValueError('Output must be a new directory; an existing trial is never overwritten.')
    validate_dis_budget(dis_num_iter)
    if band_dir is None:
        raise ValueError('New legacy mesh interface preparation also requires --band-dir for mandatory native band outputs.')
    band_dir = Path(band_dir)
    automatic_mesh_spec(require_file(mesh_dir / 'KPOINTS'))
    analysis = json.loads(Path(analysis_path).read_text())
    decision = json.loads(Path(decision_path).read_text())
    if decision.get('band_selection',{}).get('exclude_bands_1based'):
        raise ValueError('Occupied-block exclusion uses the default band-to-interface route; do not use the legacy mesh preparation path.')
    checks = evidence.check_record(decision, analysis, cache)
    from decision_stages import require_executable_decision
    stage_binding = require_executable_decision(decision_path, cache)
    if decision['status'] != 'proposed_trial':
        raise ValueError('A reviewed proposed_trial decision is required.')
    meta, energies, groups, cache_sha = evidence.read_cache(cache)
    if meta.get('sampling') != 'wannier_mesh':
        raise ValueError('Cache must explicitly identify sampling=wannier_mesh.')
    mesh_incar = incar(require_file(mesh_dir / 'INCAR'))
    scf_incar = incar(require_file(scf_dir / 'INCAR'))
    disabled_flags = {key for key in ('LWANNIER90', 'LWANNIER90_RUN')
                      if key in mesh_incar and not boolean(mesh_incar[key])}
    forbidden = [key for key in mesh_incar if key not in disabled_flags and
                 ('WANN' in key or key in ('LOCPROJ', 'LSCDM', 'LWRITE_UNK', 'LWRITE_MMN_AMN', 'LWRITE_SPN'))]
    if forbidden:
        raise ValueError('Mesh INCAR already contains Wannier/projection-construction settings: ' + ', '.join(forbidden))
    physical = physical_contract(mesh_incar, scf_incar)
    mode = spin_mode(mesh_incar)
    if mode != meta.get('spin_mode') or mode != analysis['source']['spin_mode']:
        raise ValueError('Cache, analysis, and mesh spin modes differ.')
    for filename in ('POSCAR', 'POTCAR', 'CHGCAR'):
        if sha(require_file(mesh_dir / filename)) != sha(require_file(scf_dir / filename)):
            raise ValueError(f'Mesh/SCF {filename} provenance differs.')
    for key, filename in (('procar', 'PROCAR'), ('eigenval', 'EIGENVAL'), ('poscar', 'POSCAR')):
        actual_hash = sha(require_file(mesh_dir / filename))
        if meta.get('source_files', {}).get(key, {}).get('sha256') != actual_hash:
            raise ValueError(f'Cache does not identify current mesh {filename}.')
        if key == 'procar' and meta['procar_sha256'] != actual_hash:
            raise ValueError('PROCAR source hash mismatch.')
    if 'kpoints' in meta.get('source_files', {}) and meta['source_files']['kpoints']['sha256'] != sha(require_file(mesh_dir / 'KPOINTS')):
        raise ValueError('Cache KPOINTS provenance differs.')
    species, counts, lattice, cart = read_poscar(mesh_dir / 'POSCAR')
    if species != meta['species'] or counts != meta['counts']:
        raise ValueError('Cache species/counts differ from current POSCAR.')
    native_plot = native_band_plot(band_dir, {'band/POSCAR':sha(mesh_dir/'POSCAR')})
    with np.load(cache, allow_pickle=False) as data:
        coords = np.asarray(data['kcoords'], dtype=float)
        weights = np.asarray(data['kweights'], dtype=float)
    nk, nb = energies.shape
    source_completion = dict(scf=completed_source(scf_dir, False), mesh=completed_source(mesh_dir, True, nb))
    if coords.shape != (1, nk, 3) or weights.shape != (1, nk):
        raise ValueError('Cache coordinate/weight dimensions differ from energies.')
    grid = mesh_geometry(require_file(mesh_dir / 'KPOINTS'), coords[0], weights[0])
    nw = checks['recomputed_checks']['num_wann']
    windows = decision['windows']
    scalar_settings = dict(num_wann=str(nw), num_bands=str(nb),
                           spinors='.true.' if mode == 'spinor' else '.false.',
                           mp_grid=' '.join(map(str, grid)),
                           num_iter='0', dis_num_iter=str(dis_num_iter),
                           dis_conv_tol=str(DIS_TOL), dis_conv_window=str(DIS_WINDOW))
    scalar_settings.update(native_plot['scalars'])
    for names, values in ((('dis_win_min', 'dis_win_max'), windows['dis_eV']),
                          (('dis_froz_min', 'dis_froz_max'), windows['frozen_eV'])):
        scalar_settings.update({name: f'{value + meta["ef"]:.12g}' for name, value in zip(names, values)})
    win = '\n'.join(f'{key} = {value}' for key, value in scalar_settings.items())
    win += '\n\nbegin projections\n' + '\n'.join(decision['trial_basis']) + '\nend projections\n'
    win += '\nbegin kpoint_path\n' + '\n'.join(native_plot['kpoint_path']) + '\nend kpoint_path\n'
    atoms = [dict(species=s, position=r.tolist()) for s, r in
             zip(sum(([s] * n for s, n in zip(species, counts)), []), cart)]
    def vector_text(row):
        return ' '.join(f'{value:.17g}' for value in row)
    win += '\nbegin unit_cell_cart\nAng\n' + '\n'.join(vector_text(row) for row in lattice) + '\nend unit_cell_cart\n'
    win += '\nbegin atoms_cart\nAng\n' + '\n'.join(atom['species'] + ' ' + vector_text(atom['position']) for atom in atoms) + '\nend atoms_cart\n'
    win += '\nbegin kpoints\n' + '\n'.join(vector_text(row) for row in coords[0]) + '\nend kpoints\n'
    new_incar = dict(mesh_incar)
    # The VASP Wannier/PEAD path needs NCORE=1. NPAR takes precedence over
    # NCORE, so copying a source NPAR would silently undo this requirement.
    parallelism = dict(policy='interface-ncore1-kpar1-v1',
        source={key: mesh_incar.get(key) for key in ('NCORE', 'NPAR', 'KPAR')},
        interface=dict(NCORE='1', NPAR=None, KPAR='1'),
        rank_policy='largest_divisor_of_actual_nbands_not_above_site_ntasks',
        reason='VASP Wannier/PEAD requires NCORE=1; preserve the reviewed actual band dimension.')
    new_incar.pop('NPAR', None)
    new_incar.update(ICHARG='11', ISTART='1' if reuse_wavecar else '0', ISYM='-1', NSW='0',
                     IBRION='-1', NBANDS=str(nb), NUM_WANN=str(nw), LWANNIER90='.TRUE.',
                     LWANNIER90_RUN='.FALSE.', LWRITE_MMN_AMN='.TRUE.',
                     LWRITE_UNK='.FALSE.', LCHARG='.FALSE.', LWAVE='.TRUE.',
                     NCORE='1', KPAR='1')
    copy_sources = {name: mesh_dir / name for name in ('POSCAR', 'POTCAR', 'KPOINTS')}
    copy_sources['CHGCAR'] = scf_dir / 'CHGCAR'
    if reuse_wavecar:
        copy_sources['WAVECAR'] = require_file(mesh_dir / 'WAVECAR')
    sources = {f'mesh/{name}': sha(mesh_dir / name) for name in ('INCAR', 'POSCAR', 'POTCAR', 'KPOINTS', 'CHGCAR', 'PROCAR', 'EIGENVAL', 'OUTCAR')}
    sources.update({f'scf/{name}': sha(scf_dir / name) for name in ('INCAR', 'POSCAR', 'POTCAR', 'CHGCAR', 'OUTCAR')})
    for label, directory in (('mesh', mesh_dir), ('scf', scf_dir)):
        if (directory / 'run.json').exists(): sources[label + '/run.json'] = sha(directory / 'run.json')
    sources.update(cache=cache_sha, analysis=sha(analysis_path), decision=sha(decision_path))
    sources.update({'band/'+name:value for name,value in native_plot['source_hashes'].items()})
    if reuse_wavecar:
        sources['mesh/WAVECAR'] = sha(copy_sources['WAVECAR'])
    out_dir.mkdir(parents=True)
    for filename, source in copy_sources.items():
        shutil.copyfile(source, out_dir / filename)
    (out_dir / 'INCAR').write_text('\n'.join(f'{key} = {value}' for key, value in new_incar.items()) + '\n')
    (out_dir / 'wannier90.win').write_text(win)
    manifest = dict(decision_stage=stage_binding, format='wannier-interface-trial-v1', status='prepared_not_executed',
        analysis_id=analysis['analysis_id'], trial_basis=decision['trial_basis'],
        expected=dict(nbands=nb, nkpoints=nk, num_wann=nw, spin_mode=mode, mp_grid=grid),
        ef_eV=meta['ef'], ef_source=meta['ef_source'], windows_relative_eV=windows,
        win_scalars=scalar_settings, physical_settings=physical, native_plot=native_plot,
        source_completion=source_completion, parallelism=parallelism,
        kpoints=coords[0].tolist(), lattice_angstrom=lattice.tolist(),
        atoms_cart_angstrom=atoms,
        energies_absolute_eV=(energies + meta['ef']).tolist(), source_hashes=sources,
        input_hashes={name: sha(out_dir / name) for name in [*copy_sources, 'INCAR', 'wannier90.win']},
        source_directories=dict(mesh=str(mesh_dir.resolve()), scf=str(scf_dir.resolve()), band=str(band_dir.resolve())),
        validation_scope='Input identity, source electronic convergence evidence, declared same-spin semilocal protocol, full mesh and reviewed count gate. No new VASP interface or fit success claim.')
    manifest['manifest_id'] = evidence.digest(manifest)
    (out_dir / 'trial_manifest.json').write_text(json.dumps(manifest, indent=2, ensure_ascii=False, allow_nan=False) + '\n')
    return manifest


def read_win(path):
    return parse_win(Path(path).read_text())


def parse_win(text):
    settings, blocks, active = {}, {}, None
    for line in text.splitlines():
        line = re.split(r'[!#]', line, maxsplit=1)[0].strip()
        if not line:
            continue
        words = line.lower().split()
        if words[0] == 'begin':
            if active is not None or len(words) != 2 or words[1] in blocks:
                raise ValueError('Invalid or duplicate Wannier block.')
            active = words[1]; blocks[active] = []
        elif words[0] == 'end':
            if len(words) != 2 or words[1] != active:
                raise ValueError('Mismatched Wannier block end.')
            active = None
        elif active is not None:
            blocks[active].append(line)
        else:
            pair = re.split(r'\s*[=:]\s*', line, maxsplit=1)
            if len(pair) != 2 or pair[0].lower() in settings:
                raise ValueError('Invalid or duplicate Wannier setting.')
            settings[pair[0].lower()] = pair[1]
    if active is not None:
        raise ValueError('Unclosed Wannier block.')
    return settings, blocks


def finite_numbers(line, count):
    values = [float(v.replace('D', 'E').replace('d', 'e')) for v in line.split()]
    if len(values) != count or not all(math.isfinite(v) for v in values):
        raise ValueError('Malformed/nonfinite numerical interface row.')
    return values


def finite_mmn_numbers(line):
    """Accept VASP's optional |M| diagnostic after the complex overlap."""
    values = [float(v.replace('D', 'E').replace('d', 'e')) for v in line.split()]
    if len(values) not in (2, 3) or not all(math.isfinite(v) for v in values):
        raise ValueError('Malformed/nonfinite MMN matrix row.')
    if len(values) == 3:
        # Preserve NumPy's hypot and asymmetric isclose formula/tolerances.
        # Avoid constructing tiny arrays millions of times for scalar rows.
        magnitude = np.hypot(*values[:2])
        # A bounded default-policy fast path avoids exceptional arithmetic.
        # This is NOT a magnitude acceptance limit: every other row uses the
        # original NumPy predicate, including custom warning/error traps.
        if (np.geterr() == _SCALAR_FASTPATH_ERRORS and
                math.isfinite(magnitude) and magnitude <= 1 and abs(values[2]) <= 1):
            close = abs(values[2] - magnitude) <= 1e-9 + 1e-10 * abs(magnitude)
        else:
            close = np.isclose(values[2], magnitude, atol=1e-9, rtol=1e-10)
        if not close:
            raise ValueError('MMN magnitude column disagrees with complex overlap.')
    return values[:2]


def validate_win_geometry(blocks, manifest):
    def strip_units(lines):
        factor = 1.0
        if lines and lines[0].lower() in ('ang', 'angstrom', 'bohr'):
            factor = 0.529177210903 if lines[0].lower() == 'bohr' else 1.0
            lines = lines[1:]
        return lines, factor
    lines, factor = strip_units(blocks.get('unit_cell_cart', []))
    if len(lines) != 3 or not np.allclose(np.array([finite_numbers(row, 3) for row in lines]) * factor, manifest['lattice_angstrom'], atol=1e-6, rtol=0):
        raise ValueError('Output win lattice differs from trial structure.')
    krows = blocks.get('kpoints', [])
    if len(krows) != manifest['expected']['nkpoints'] or not np.array_equal(wrapped([finite_numbers(row, 3) for row in krows]), wrapped(manifest['kpoints'])):
        raise ValueError('Output win kpoints differ from actual mesh order.')
    if 'atoms_cart' in blocks:
        lines, factor = strip_units(blocks['atoms_cart'])
        coords = np.array([finite_numbers(' '.join(row.split()[1:]), 3) for row in lines]) * factor
    elif 'atoms_frac' in blocks:
        lines = blocks['atoms_frac']
        coords = np.array([finite_numbers(' '.join(row.split()[1:]), 3) for row in lines]) @ np.array(manifest['lattice_angstrom'])
    else:
        raise ValueError('Output win is missing its atom geometry.')
    atoms = manifest['atoms_cart_angstrom']
    reference = np.asarray([atom['position'] for atom in atoms], dtype=float)
    if [row.split()[0] for row in lines] != [atom['species'] for atom in atoms] or coords.shape != reference.shape:
        raise ValueError('Output win atoms differ from trial structure/order.')
    # VASP may wrap each atom into a different periodic image. Compare each
    # original atom with its own image in the reviewed lattice, without using
    # output coordinates to replace the manifest or matching atoms by sorting.
    lattice = np.asarray(manifest['lattice_angstrom'], dtype=float)
    delta_frac = np.linalg.solve(lattice.T, (coords-reference).T).T
    residual_cart = (delta_frac-np.rint(delta_frac)) @ lattice
    if not np.allclose(residual_cart, 0, atol=1e-6, rtol=0):
        raise ValueError('Output win atoms differ from trial structure/order.')


def mark_seen(bits, index):
    byte, bit = divmod(index, 8)
    mask = 1 << bit
    if bits[byte] & mask:
        raise ValueError('Duplicate interface matrix index.')
    bits[byte] |= mask


def validate_outputs(directory, manifest, eig_tolerance):
    expected = manifest['expected']
    nb, nk, nw = (expected[key] for key in ('nbands', 'nkpoints', 'num_wann'))
    from valence_selection import retained_indices
    retained = retained_indices(manifest.get('band_selection'), nb)
    nb = len(retained)
    if expected.get('num_bands_retained', nb) != nb:
        raise ValueError('Retained interface dimensions disagree with the exclusion mapping.')
    with require_file(directory / 'wannier90.amn').open() as stream:
        next(stream)
        if [int(x) for x in next(stream).split()] != [nb, nk, nw]:
            raise ValueError('AMN dimensions differ from trial.')
        seen = bytearray((nb * nk * nw + 7) // 8); count = 0
        for line in stream:
            if not line.strip(): continue
            row = line.split()
            if len(row) != 5: raise ValueError('Malformed AMN row.')
            band, projection, k = map(int, row[:3])
            if not (1 <= band <= nb and 1 <= projection <= nw and 1 <= k <= nk):
                raise ValueError('AMN index outside trial dimensions.')
            finite_numbers(' '.join(row[3:]), 2)
            mark_seen(seen, ((k - 1) * nw + projection - 1) * nb + band - 1); count += 1
        if count != nb * nk * nw: raise ValueError('Incomplete AMN matrix.')
    with require_file(directory / 'wannier90.mmn').open() as stream:
        next(stream)
        header = [int(x) for x in next(stream).split()]
        if len(header) != 3 or header[:2] != [nb, nk] or header[2] < 1:
            raise ValueError('MMN dimensions differ from trial.')
        neighbors = header[2]; seen = set(); per_k = np.zeros(nk, dtype=int)
        for line in stream:
            if not line.strip(): continue
            key = tuple(map(int, line.split()))
            if len(key) != 5 or not 1 <= key[0] <= nk or not 1 <= key[1] <= nk or key in seen:
                raise ValueError('Invalid/duplicate MMN neighbor block.')
            seen.add(key); per_k[key[0] - 1] += 1
            for _ in range(nb * nb): finite_mmn_numbers(next(stream))
        if not np.all(per_k == neighbors): raise ValueError('Incomplete MMN neighbor blocks.')
    reference = np.array(manifest['energies_absolute_eV'])[:,retained]
    seen = bytearray((nb * nk + 7) // 8); count = 0; max_error = 0.0
    with require_file(directory / 'wannier90.eig').open() as stream:
        for line in stream:
            if not line.strip(): continue
            row = line.split()
            if len(row) != 3: raise ValueError('Malformed EIG row.')
            band, k = map(int, row[:2]); energy = finite_numbers(row[2], 1)[0]
            if not (1 <= band <= nb and 1 <= k <= nk): raise ValueError('EIG index outside trial dimensions.')
            mark_seen(seen, (k - 1) * nb + band - 1); count += 1
            max_error = max(max_error, abs(energy - reference[k - 1, band - 1]))
    if count != nb * nk: raise ValueError('Incomplete EIG matrix.')
    if max_error > eig_tolerance:
        raise ValueError(f'Interface EIG differs from reviewed mesh by {max_error:.6g} eV; recompute evidence/review or explicitly investigate the tolerance.')
    return dict(amn_entries=nb * nk * nw, mmn_neighbor_blocks=nk * neighbors,
                eig_entries=count, eig_max_difference_eV=max_error, eig_tolerance_eV=eig_tolerance)


def audit_no_orbital_update(directory):
    """Check VASP's actual one-step NONE trace, not just the requested INCAR."""
    oszicar = require_file(Path(directory)/'OSZICAR').read_text(errors='replace')
    labels = re.findall(r'^\s*(DAV|RMM|SDA|CGA|DMP|DIA|EIG|NONE):?\s+\d+\b',
                        oszicar, flags=re.M | re.I)
    labels = [label.upper() for label in labels]
    if labels != ['NONE']:
        raise ValueError('Shared Wannier interface did not show exactly one NONE step without orbital optimization: '+str(labels))
    outcar = require_file(Path(directory)/'OUTCAR').read_text(errors='replace')
    for tag, expected in (('ISTART', 1), ('IALGO', 2)):
        values = re.findall(r'\b'+tag+r'\s*=\s*([+\-]?\d+)\b', outcar)
        if values and int(values[-1]) != expected:
            raise ValueError('Shared interface effective '+tag+' differs from frozen-orbital postprocessing.')
    return dict(electronic_trace=labels, orbital_update=False, electronic_diagonalization=False,
                evidence='Requested ALGO=None/LDIAG=false plus VASP OSZICAR one-step NONE trace; no claim of zero VASP internal work.')


def validate(trial_dir, outputs=False, eig_tolerance=1e-4, preflight_wavecar_sha=None):
    directory = Path(trial_dir)
    manifest = json.loads(require_file(directory / 'trial_manifest.json').read_text())
    if evidence.digest({key: value for key, value in manifest.items() if key != 'manifest_id'}) != manifest['manifest_id']:
        raise ValueError('Trial manifest fingerprint mismatch.')
    automatic_mesh_spec(require_file(directory / 'KPOINTS'))
    shared_route = manifest.get('mode') == 'band_to_shared_wavecar_interface'
    band_route = manifest.get('mode') in ('band_to_interface', 'band_to_shared_wavecar_interface')
    shared_output_placeholder = False
    if shared_route and outputs:
        wavecar = directory/'WAVECAR'
        shared_output_placeholder = (wavecar.is_file() and not wavecar.is_symlink() and
                                     wavecar.stat().st_size == 0)
        if shared_output_placeholder:
            if preflight_wavecar_sha is None and (directory/'interface_run.json').is_file():
                run_record = json.loads((directory/'interface_run.json').read_text())
                if (run_record.get('returncode') == 0 and
                        run_record.get('manifest_id') == manifest['manifest_id']):
                    preflight_wavecar_sha = run_record.get('preflight_wavecar_sha256')
            if preflight_wavecar_sha != manifest['input_hashes'].get('WAVECAR'):
                raise ValueError('VASP left an empty WAVECAR placeholder without verified pre-execution shared input.')
    if shared_route:
        from shared_nscf import verify
        source = manifest.get('shared_source') or {}
        sealed = verify(source.get('directory'), manifest['source_directories']['scf'],
                        manifest['source_directories']['band'])
        if (sealed['source_id'] != source.get('source_id') or
                sha(Path(source['directory'])/'SOURCE_SEAL.json') != manifest['source_hashes'].get('shared/SOURCE_SEAL.json')):
            raise ValueError('Shared wavefunction source identity changed.')
    if sha(require_file(directory/'INCAR')) != manifest['input_hashes'].get('INCAR'):
        raise ValueError('Trial input changed: INCAR')
    tags = incar(directory / 'INCAR')
    if band_route:
        payload = require_file(directory/'wannier90.win.in').read_text()
        if tags.get('WANNIER90_WIN', '').strip() != payload.strip():
            raise ValueError('INCAR WANNIER90_WIN differs from the reviewed input payload.')
        wavecar = directory/'WAVECAR'
        wavecar_present = wavecar.exists() or wavecar.is_symlink()
        # A fresh input must have no WAVECAR. Some VASP builds create an empty
        # regular output placeholder even with LWAVE=false; it contains no
        # wavefunction and is safe only in the post-execution validation path.
        empty_output_placeholder = (outputs and not wavecar.is_symlink() and
                                    wavecar.is_file() and wavecar.stat().st_size == 0)
        if shared_route:
            if (tags.get('ISTART') != '1' or tags.get('ALGO', '').upper() != 'NONE' or
                    integer(tags.get('NELM', '0')) != 1 or boolean(tags.get('LDIAG', 'T')) or
                    boolean(tags.get('LWAVE', 'T')) or boolean(tags.get('LCHARG', 'T')) or
                    boolean(tags.get('LWANNIER90_RUN', 'T')) or not wavecar.is_file() or
                    wavecar.is_symlink() or (not wavecar.stat().st_size and not shared_output_placeholder)):
                raise ValueError('Shared interface needs an isolated WAVECAR, ISTART=1, ALGO=None, NELM=1, LDIAG=false and no output WAVECAR/charge writes.')
        elif (tags.get('ISTART') != '0' or boolean(tags.get('LWAVE', 'T')) or
              boolean(tags.get('LWANNIER90_RUN', 'T')) or (wavecar_present and not empty_output_placeholder)):
            raise ValueError('Band interface must use ISTART=0, LWAVE=false, LWANNIER90_RUN=false and no inherited WAVECAR.')
        if not outputs and (directory/'wannier90.win').exists():
            raise ValueError('Band interface preparation must not precreate wannier90.win; VASP supplies actual mesh order.')
        scalars, blocks = read_win(require_file(directory/'wannier90.win')) if outputs else parse_win(payload)
    else:
        scalars, blocks = read_win(require_file(directory / 'wannier90.win'))
    if scalars.get('num_iter') != '0':
        raise ValueError('Disentanglement-only contract requires explicit num_iter = 0; missing or nonzero localization iterations are forbidden.')
    if not re.fullmatch(r'[1-9][0-9]*', scalars.get('dis_num_iter', '')):
        raise ValueError('Disentanglement-only contract requires an explicit positive integer dis_num_iter.')
    if not np.isfinite(eig_tolerance) or eig_tolerance < 0:
        raise ValueError('EIG tolerance must be finite and nonnegative.')
    for filename, digest in manifest['input_hashes'].items():
        if outputs and (filename == 'wannier90.win' or
                        (filename == 'WAVECAR' and (not shared_route or shared_output_placeholder))):
            continue
        if sha(require_file(directory / filename)) != digest:
            raise ValueError('Trial input changed: ' + filename)
    parallelism = manifest.get('parallelism', {})
    if (tags.get('NCORE') != '1' or tags.get('KPAR') != '1' or 'NPAR' in tags or
            parallelism.get('policy') != 'interface-ncore1-kpar1-v1' or
            parallelism.get('interface') != dict(NCORE='1', NPAR=None, KPAR='1') or
            parallelism.get('rank_policy') != 'largest_divisor_of_actual_nbands_not_above_site_ntasks'):
        raise ValueError('Interface requires recorded NCORE=1, KPAR=1 and no NPAR; prepare a fresh trial.')
    def canonical(key, value):
        if key == 'exclude_bands':
            from valence_selection import parse_exclude
            return parse_exclude(value)
        if key == 'spinors' or key in NATIVE_OUTPUT_FLAGS: return boolean(value)
        if key in ('num_wann', 'num_bands', 'num_iter', 'dis_num_iter', 'bands_num_points'): return integer(value)
        if key == 'mp_grid': return tuple(map(int, value.split()))
        return float(value)
    expected_scalars = manifest['payload_scalars'] if band_route and not outputs else manifest['win_scalars']
    if band_route and outputs and manifest['expected']['spin_mode'] == 'scalar' and 'spinors' not in scalars:
        # VASP 6.4.2 only emits this auto key for NRSPINORS=2. Its absent
        # scalar form is equivalent to false, never to a missing spinor proof.
        scalars = dict(scalars, spinors='.false.')
    if (set(scalars) != set(expected_scalars) or
            any(canonical(key, scalars[key]) != canonical(key, value) for key, value in expected_scalars.items()) or
            blocks.get('projections') != manifest['trial_basis']):
        raise ValueError('Trial Wannier settings/projections changed.')
    native_plot = manifest.get('native_plot')
    if native_plot is not None:
        if (blocks.get('kpoint_path') != native_plot['kpoint_path'] or
                any(not boolean(scalars.get(key,'false')) for key in NATIVE_OUTPUT_FLAGS) or
                integer(scalars.get('bands_num_points','0')) < 2):
            raise ValueError('Native plotting path or required output controls changed.')
    elif 'kpoint_path' in blocks:
        raise ValueError('Native plotting path is not identified in this trial manifest.')
    expected_blocks = {'projections', 'unit_cell_cart', 'atoms_cart', 'atoms_frac', 'kpoints', 'kpoint_path'}
    if set(blocks) - expected_blocks or ('atoms_cart' in blocks and 'atoms_frac' in blocks):
        raise ValueError('Unexpected or duplicate Wannier geometry blocks.')
    actual_manifest = manifest
    if band_route and outputs:
        from vasp_to_cache import read_eigenval
        current = read_eigenval(require_file(directory/'EIGENVAL'))
        if current['energies'].shape != (manifest['expected']['nkpoints'], manifest['expected']['nbands']):
            raise ValueError('Current interface EIGENVAL dimensions differ from the reviewed band count/declared mesh.')
        if current['nions'] != len(manifest['atoms_cart_angstrom']):
            raise ValueError('Current interface EIGENVAL ion count differs from structure.')
        grid = mesh_geometry(directory/'KPOINTS', current['kcoords'], current['kweights'])
        if grid != manifest['expected']['mp_grid']:
            raise ValueError('Current interface grid differs from input dimensions.')
        if shared_route:
            source = manifest.get('shared_source') or {}
            if source.get('wavecar_sha256') != manifest['input_hashes'].get('WAVECAR'):
                raise ValueError('Shared source identity and isolated WAVECAR hash differ.')
            from vasp_to_cache import read_eigenval as read_shared_eigenval
            source_eigenval = Path(source['directory'])/'EIGENVAL'
            if sha(require_file(source_eigenval)) != source['eigenval_sha256']:
                raise ValueError('Sealed shared EIGENVAL has changed.')
            prior = read_shared_eigenval(source_eigenval)
            if (prior['energies'].shape != current['energies'].shape or
                    not np.array_equal(wrapped(prior['kcoords']), wrapped(current['kcoords'])) or
                    np.max(np.abs(prior['energies']-current['energies'])) > eig_tolerance):
                raise ValueError('ALGO=None interface eigenvalues/order differ from the sealed WAVECAR source.')
        actual_manifest = dict(manifest, kpoints=current['kcoords'].tolist(), energies_absolute_eV=current['energies'].tolist())
    if not band_route or outputs:
        validate_win_geometry(blocks, actual_manifest)
    elif set(blocks) != ({'projections','kpoint_path'} if native_plot is not None else {'projections'}):
        raise ValueError('Band interface payload must not guess geometry or kpoints; VASP supplies actual order.')
    result = dict(status='prepared_inputs_consistent', manifest_id=manifest['manifest_id'],
                  wannier_policy=dict(mode='disentanglement_only', num_iter=0, dis_num_iter=int(scalars['dis_num_iter'])),
                  mesh_gate_pending=band_route, ready_for_wannier=False,
                  scope='Input verification only; VASP has not been certified or run by this command.')
    if outputs:
        if shared_route:
            result['postprocess_audit'] = audit_no_orbital_update(directory)
        try:
            result['output_checks'] = validate_outputs(directory, actual_manifest, eig_tolerance)
        except StopIteration as exc:
            raise ValueError('Truncated interface matrix file.') from exc
        result.update(status='interface_files_consistent', interface_files_consistent=True, mesh_gate_pending=False,
                      scope='Matrix dimensions/index completeness/finiteness, geometry and eigenvalue identity only; no localization or fit quality claim.')
        if band_route:
            from valence_selection import prefix_candidates, retained_indices, sampled_gap_checks
            retained = retained_indices(manifest.get('band_selection'), current['energies'].shape[1])
            exclusion_gate = sampled_gap_checks(current['energies']-manifest['ef_eV'],current['occupations'],
                manifest.get('band_selection'),manifest['expected']['spin_mode'])
            result['output_checks']['eig_reference'] = 'current_interface_EIGENVAL_in_actual_k_order'
            gate = evidence.window_checks(current['energies'][:,retained] - manifest['ef_eV'], manifest['expected']['num_wann'],
                                          manifest['windows_relative_eV'], 'wannier_mesh')
            gate['scope'] = ('Actual current interface EIGENVAL mesh sampled-state counts and top-band margin; '
                             'no off-grid completeness, interpolation accuracy or fit certification.')
            result.update(mesh_gate=gate, exclusion_gate=exclusion_gate,
                          ready_for_wannier=gate['gate_pass'] and exclusion_gate['gate_pass'],
                          dft_space_assessment=gate['data_adequacy'],
                          actual_kpoints=current['kcoords'].tolist(),
                          actual_eigenval_sha256=sha(directory/'EIGENVAL'))
            if not gate['gate_pass']:
                result['status'] = ('needs_more_data' if gate['data_adequacy']['requires_more_data']
                                    else 'window_gate_failed')
                result['next_action'] = ('add_bands_nscf' if gate['data_adequacy']['requires_more_data']
                                        else 'review_windows')
            if not exclusion_gate['gate_pass']:
                result['status'] = 'exclusion_gate_failed'
                selection = manifest['band_selection']
                result['exclusion_recovery'] = dict(
                    current_exclude_bands_1based=selection['exclude_bands_1based'],
                    actual_interface_candidates=prefix_candidates(current['energies']-manifest['ef_eV'],
                        current['occupations'],manifest['expected']['spin_mode'],selection['minimum_gap_eV'],
                        selection['occupation_tolerance'],len(selection['exclude_bands_1based'])),
                    next_action='review_shorter_prefix_or_no_exclusion',
                    requires_new_matching_matrices=True,
                    instructions='Compare with band-path projections and gaps; review a shorter non-target prefix or retain all bands. Recheck outer target coverage and frozen contamination. Record a revised proposal within the trial budget, then generate matching matrices. Do not edit only the win or run these rejected matrices.')
        else:
            result['ready_for_wannier'] = True
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    prep = commands.add_parser('prepare')
    prep.add_argument('--band-dir', required=True, help='Completed projected-band source; default evidence route, also required for legacy native plotting.')
    prep.add_argument('--mesh-dir', help='Optional compatibility route for an already completed mesh cache; --band-dir supplies its plot path.')
    mesh = prep.add_mutually_exclusive_group()
    mesh.add_argument('--mesh-kpoints', help='Input sampling only: automatic grid or complete uniform reciprocal point list.')
    mesh.add_argument('--mp-grid', type=int, nargs=3, metavar=('NX', 'NY', 'NZ'))
    for name in ('scf-dir', 'cache', 'analysis', 'decision', 'out-dir'):
        prep.add_argument('--' + name, required=True)
    prep.add_argument('--reuse-wavecar', action='store_true')
    prep.add_argument('--shared-source', help='Sealed automatic-grid WAVECAR; ALGO=None makes basis-dependent matrices without re-diagonalization.')
    prep.add_argument('--dis-num-iter', type=int, default=DIS_DEFAULT,
                      help=f'Disentanglement iteration budget (default/maximum: {DIS_MAX}); num_iter is always explicitly zero.')
    check = commands.add_parser('validate')
    check.add_argument('--trial-dir', required=True)
    check.add_argument('--outputs', action='store_true')
    check.add_argument('--eig-tolerance', type=float, default=1e-4)
    args = parser.parse_args()
    try:
        if args.command == 'prepare':
            if not args.mesh_dir:
                if args.reuse_wavecar:
                    raise ValueError('Use --shared-source with a sealed wavefunction bank; --reuse-wavecar is legacy-only.')
                result = prepare_band(args.band_dir, args.scf_dir, args.cache, args.analysis, args.decision,
                                      args.out_dir, args.mesh_kpoints, args.mp_grid, args.dis_num_iter, args.shared_source)
            else:
                if args.mesh_kpoints is not None or args.mp_grid is not None or args.shared_source is not None:
                    raise ValueError('Legacy --mesh-dir uses that completed source mesh; new mesh inputs belong to --band-dir.')
                result = prepare(args.mesh_dir, args.scf_dir, args.cache, args.analysis,
                                 args.decision, args.out_dir, args.reuse_wavecar, args.dis_num_iter,args.band_dir)
            result = dict(status=result['status'], manifest_id=result['manifest_id'],
                          expected=result['expected'], trial_dir=str(Path(args.out_dir).resolve()),
                          mesh_gate_pending=result.get('mesh_gate_pending', False), ready_for_wannier=False)
        else:
            result = validate(args.trial_dir, args.outputs, args.eig_tolerance)
        print(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False))
        if args.command == 'validate' and args.outputs and not result.get('ready_for_wannier'):
            raise SystemExit(2)
    except (ValueError, KeyError, OSError, IndexError) as exc:
        parser.exit(2, str(exc) + '\n')
    except Exception as exc:
        if exc.__class__.__module__.startswith('jsonschema'):
            parser.exit(2, 'Decision schema rejected record: ' + str(exc.message) + '\n')
        raise


if __name__ == '__main__':
    main()
