#!/usr/bin/env python3
"""Prepare, submit, inspect and collect static DFT inputs for Wannier evidence.

No material-specific orbital/window rules. Preparation never launches VASP.
"""
import argparse
import hashlib
import itertools
import json
import math
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone

import numpy as np

STAGES = ('scf', 'band', 'mesh')
DEFAULT_STAGE_DIRECTORIES = {'scf': '01_scf', 'band': '02_band', 'mesh': '_optional_mesh'}
COMMON_TAGS = {'ENCUT', 'PREC', 'EDIFF', 'NELM', 'NELMIN', 'ALGO', 'LREAL',
               'ISMEAR', 'SIGMA', 'GGA', 'LASPH', 'ADDGRID', 'LMAXMIX',
               'NCORE', 'KPAR', 'NELECT', 'SAXIS', 'GGA_COMPAT', 'AMIX', 'BMIX'}
REQUIRED_TAGS = {'ENCUT', 'EDIFF', 'ISMEAR', 'SIGMA', 'LMAXMIX'}


def sha(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024*1024), b''):
            value.update(block)
    return value.hexdigest()


def write_json(path, data):
    path = Path(path)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, allow_nan=False, indent=2) + '\n')
    temporary.replace(path)


def read_json(path):
    return json.loads(Path(path).read_text())


def structure(path):
    lines = Path(path).read_text().splitlines()
    scale = float(lines[1]); lattice = np.array([list(map(float, line.split()[:3])) for line in lines[2:5]])
    if lattice.shape != (3, 3) or not np.isfinite(lattice).all() or abs(np.linalg.det(lattice)) < 1e-12 or not math.isfinite(scale) or scale == 0:
        raise ValueError('POSCAR needs a finite nonsingular cell and single nonzero scale.')
    species = lines[5].split(); counts = list(map(int, lines[6].split()))
    if not species or any(not re.fullmatch(r'[A-Z][a-z]?', x) for x in species) or len(species) != len(counts) or any(n <= 0 for n in counts):
        raise ValueError('Use VASP5 POSCAR with explicit species and positive counts.')
    cursor = 7 + int(lines[7].strip().lower().startswith('s'))
    if not lines[cursor].strip().lower().startswith(('d', 'c', 'k')):
        raise ValueError('Missing POSCAR coordinate convention.')
    coords = np.array([list(map(float, line.split()[:3])) for line in lines[cursor+1:cursor+1+sum(counts)]])
    if coords.shape != (sum(counts), 3) or not np.isfinite(coords).all():
        raise ValueError('Incomplete POSCAR positions.')
    return species, counts


def potentials(path, species, counts):
    text = Path(path).read_text(errors='strict')
    chunks = [chunk for chunk in text.split('End of Dataset') if chunk.strip()]
    if len(chunks) != len(species):
        raise ValueError('POTCAR dataset count differs from POSCAR species count.')
    result = []
    for chunk, expected in zip(chunks, species):
        element = re.search(r'VRHFIN\s*=\s*([A-Z][a-z]?)\s*:', chunk)
        zval = re.search(r'ZVAL\s*=\s*([0-9.Ee+\-]+)', chunk)
        enmax = re.search(r'ENMAX\s*=\s*([0-9.Ee+\-]+)', chunk)
        if not element or element[1] != expected or not zval or not enmax:
            raise ValueError('POTCAR species/order or ZVAL/ENMAX metadata mismatch.')
        z, cutoff = float(zval[1]), float(enmax[1])
        if not all(math.isfinite(v) and v > 0 for v in (z, cutoff)):
            raise ValueError('Invalid POTCAR numeric metadata.')
        result.append({'element': expected, 'zval': z, 'enmax_eV': cutoff})
    return result, sum(item['zval'] * n for item, n in zip(result, counts))


def nbands_budget(policy, nelect, nions, spin_mode, species, counts):
    # Estimate the documented nonmagnetic default before MPI padding. It is a budget,
    # not a promise about a particular VASP build's effective default.
    scalar_default = math.ceil(max(.5*math.floor(nelect+2+.5) + max(.5*nions, 3), math.floor(.6*nelect)))
    estimate = scalar_default * (2 if spin_mode == 'spinor' else 1)
    mode = policy.get('mode', 'double_default_estimate')
    if mode == 'explicit':
        requested = policy['value']
    elif mode == 'double_default_estimate':
        requested = 2*estimate
    elif mode == 'double_reference':
        if type(policy.get('reference_bands')) is not int or policy['reference_bands'] <= 0 or not str(policy.get('reason', '')).strip():
            raise ValueError('double_reference needs positive reference_bands and its provenance reason.')
        requested = 2*policy['reference_bands']
    elif mode == 'shell_reserve':
        slots = policy['empty_electron_slots_per_atom']
        if set(slots) != set(species) or any(type(v) is not int or v < 0 for v in slots.values()):
            raise ValueError('Explicit empty electron slots must cover each species; they are not orbital selections.')
        extra = sum(slots[element]*n for element, n in zip(species, counts))
        requested = estimate + math.ceil(extra/(1 if spin_mode == 'spinor' else 2))
    else:
        raise ValueError('Unsupported NBANDS budget mode.')
    minimum = math.ceil(nelect/(1 if spin_mode == 'spinor' else 2)) + 1
    if type(requested) is not int or requested < minimum:
        raise ValueError('NBANDS must include an empty-state reserve above the occupied lower bound.')
    return {'requested': requested, 'mode': mode, 'default_estimate_before_parallel_padding': estimate,
            'occupied_plus_one_lower_bound': minimum, 'policy': policy,
            'scope': 'initial budget; actual NBANDS and relevant empty-state coverage must be checked after execution'}


def vector3(value, integer=False):
    if not isinstance(value, list) or len(value) != 3 or not all(type(v) in (int, float) and math.isfinite(v) for v in value):
        raise ValueError('Expected a finite three-vector.')
    if integer and any(type(v) is not int or v <= 0 for v in value):
        raise ValueError('Mesh dimensions must be three positive integers.')
    return value


def settings(config, species, counts, nelect):
    permitted = {'case_id', 'spin_mode', 'common_incar', 'nbands', 'scf_mesh', 'wannier_mesh', 'band_path', 'settings_reason'}
    if set(config)-permitted or not re.fullmatch(r'[A-Za-z0-9_-]+', config.get('case_id', '')):
        raise ValueError('Unknown settings field or invalid neutral case_id.')
    mode = config.get('spin_mode')
    if mode not in ('scalar', 'spinor'):
        raise ValueError('Current automatic route supports scalar nonmagnetic or SOC spinor calculations.')
    common = dict(config['common_incar'])
    if set(common)-COMMON_TAGS or not REQUIRED_TAGS <= set(common):
        raise ValueError('Supply required common INCAR tags only; no Wannier, restart, hybrid/meta-GGA/+U or magnetic overrides.')
    for key in ('ENCUT', 'EDIFF', 'SIGMA'):
        if type(common[key]) not in (int, float) or not math.isfinite(common[key]) or common[key] <= 0:
            raise ValueError('ENCUT, EDIFF and SIGMA must be positive finite values.')
    if common['LMAXMIX'] not in (2, 4, 6):
        raise ValueError('Choose LMAXMIX 2/4/6 for the actual PAW angular content before producing CHGCAR.')
    if type(common['ISMEAR']) is not int:
        raise ValueError('ISMEAR must be an integer.')
    common.setdefault('PREC', 'Accurate'); common.setdefault('ALGO', 'Normal')
    common.setdefault('NELM', 160); common.setdefault('LREAL', False)
    if str(common['ALGO']).upper() not in ('NORMAL', 'FAST', 'VERYFAST', 'ALL', 'DAMPED', 'EXACT'):
        raise ValueError('ALGO must describe a supported static semilocal electronic solver.')
    if type(common['NELM']) is not int or common['NELM'] < 2:
        raise ValueError('NSCF empty states need electronic iterations; NELM must be at least 2.')
    if 'NELECT' in common:
        nelect = float(common['NELECT'])
    if not math.isfinite(nelect) or nelect <= 0:
        raise ValueError('NELECT must be positive.')
    if not str(config.get('settings_reason', '')).strip():
        raise ValueError('Record the basis for cutoff, mesh, occupations and physical settings.')
    vector3(config['scf_mesh'], True)
    if 'wannier_mesh' in config:
        vector3(config['wannier_mesh'], True)
    path = config['band_path']
    if type(path.get('points_per_segment')) is not int or path['points_per_segment'] < 2 or not path.get('segments') or not str(path.get('reason', '')).strip():
        raise ValueError('Band path needs explicit segments, >=2 points/segment and its structure-specific reason.')
    for segment in path['segments']:
        a, b = vector3(segment['start']), vector3(segment['end'])
        if np.allclose(a, b, rtol=0, atol=1e-12):
            raise ValueError('Zero-length band segment.')
        for label in (segment.get('start_label', ''), segment.get('end_label', '')):
            if not isinstance(label, str) or any(c in label for c in '\r\n'):
                raise ValueError('Path labels must be single-line strings.')
    config = dict(config, common_incar=common)
    return config, nbands_budget(config.get('nbands', {}), nelect, sum(counts), mode, species, counts)


def incar_text(values):
    def encode(value):
        if isinstance(value, bool):
            return '.TRUE.' if value else '.FALSE.'
        if isinstance(value, list):
            return ' '.join(encode(v) for v in value)
        result = str(value)
        if any(c in result for c in '\r\n;!#='):
            raise ValueError('INCAR values must not contain inline commands, comments or assignments.')
        return result
    return '\n'.join(f'{key} = {encode(value)}' for key, value in values.items()) + '\n'


def kpoints_text(config, stage):
    if stage == 'scf':
        return 'SCF integration mesh\n0\nGamma\n' + ' '.join(map(str, config['scf_mesh'])) + '\n0 0 0\n'
    if stage == 'band':
        path = config['band_path']
        lines = ['Current structure band path', str(path['points_per_segment']), 'Line-mode', 'Reciprocal']
        for segment in path['segments']:
            lines += [' '.join(map(str, segment['start'])) + ' ! ' + segment.get('start_label', ''),
                      ' '.join(map(str, segment['end'])) + ' ! ' + segment.get('end_label', ''), '']
        return '\n'.join(lines) + '\n'
    return ('Wannier automatic Gamma mesh; ISYM=-1 requires full output\n0\nGamma\n' +
            ' '.join(map(str, config['wannier_mesh'])) + '\n0 0 0\n')


def stage_directories(manifest):
    """Resolve declared physical directories; old manifests retain their layout."""
    mapping = manifest.get('stage_directories')
    if mapping is None:
        return {stage: stage for stage in STAGES}
    if (not isinstance(mapping, dict) or set(mapping) != set(STAGES) or
            any(not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]*', name)
                or name in ('.', '..') for name in mapping.values()) or
            len({name.casefold() for name in mapping.values()}) != len(STAGES)):
        raise ValueError('stage_directories must map scf/band/mesh to distinct safe directory basenames.')
    return dict(mapping)


def stage_directory(root, stage, manifest=None):
    if stage not in STAGES:
        raise ValueError('Unknown DFT stage.')
    root = Path(root).resolve()
    manifest = read_json(root/'case.json') if manifest is None else manifest
    return root/stage_directories(manifest)[stage]


def load_case(root):
    root = Path(root).resolve(); manifest = read_json(root/'case.json')
    if manifest.get('format') != 'wannier-dft-1':
        raise ValueError('Unsupported case manifest.')
    mapping = stage_directories(manifest)
    for stage in manifest.get('prepared_stages', STAGES):
        if stage not in STAGES or any(f'{mapping[stage]}/{name}' not in manifest['input_hashes']
                for name in ('POSCAR', 'POTCAR', 'INCAR', 'KPOINTS')):
            raise ValueError('Prepared stage inputs are missing from the declared directory bindings.')
    for name, digest in manifest['input_hashes'].items():
        if Path(name).is_absolute() or '..' in Path(name).parts or sha(root/name) != digest:
            raise ValueError('Prepared input changed: ' + name)
    return root, manifest


def prepare(poscar, potcar, config_path, output, reuse_case=None, stages=None):
    species, counts = structure(poscar)
    potmeta, nelect = potentials(potcar, species, counts)
    config, budget = settings(read_json(config_path), species, counts, nelect)
    stages = ['scf', 'band'] if stages is None else list(stages)
    if ('scf' not in stages or len(set(stages)) != len(stages) or
            any(stage not in STAGES for stage in stages) or stages != sorted(stages, key=STAGES.index)):
        raise ValueError('Prepared stages must include scf and use unique scf, band, mesh order.')
    if 'mesh' in stages and 'wannier_mesh' not in config:
        raise ValueError('Optional standalone mesh preparation requires settings.wannier_mesh.')
    root = Path(output).resolve()
    if root.exists():
        raise ValueError('Use a new case directory; existing trials are never overwritten.')
    reuse = None
    if reuse_case:
        old, previous = load_case(reuse_case)
        report = inspect(old, 'scf')
        if not report['usable_outputs']:
            raise ValueError('Cannot reuse SCF: ' + '; '.join(report['problems']))
        expected = {'poscar': sha(poscar), 'potcar': sha(potcar), 'common_incar': config['common_incar'],
                    'spin_mode': config['spin_mode'], 'scf_mesh': config['scf_mesh']}
        if previous['density_identity'] != expected:
            raise ValueError('SCF reuse requires identical structure, potentials, physical settings, spin mode and density mesh.')
        reuse = {'case': str(old), 'scf_report': report}
    # Validate every text before creating any files.
    texts = {}
    for stage in stages:
        tags = dict(config['common_incar'], ISTART=0, ICHARG=2 if stage=='scf' else 11,
                    NSW=0, IBRION=-1, NBANDS=budget['requested'], ISYM=2 if stage=='scf' else -1,
                    LORBIT=11, LWAVE=stage=='mesh', LCHARG=stage=='scf',
                    LWANNIER90=False, LWANNIER90_RUN=False, ISPIN=1)
        if config['spin_mode'] == 'spinor':
            tags.update(LSORBIT=True, LNONCOLLINEAR=True, MAGMOM=f'{3*sum(counts)}*0.0')
        else:
            tags.update(LSORBIT=False, LNONCOLLINEAR=False)
        if stage == 'band' and tags['ISMEAR'] in (-4, -5, -14, -15):
            # The fixed density is unchanged; line points are not a tetrahedron mesh.
            tags['ISMEAR'] = 0
        texts[stage] = (incar_text(tags), kpoints_text(config, stage))
    root.mkdir(parents=True)
    for stage in ('scf', 'band'):
        (root/DEFAULT_STAGE_DIRECTORIES[stage]).mkdir()
    hashes = {}
    for stage in stages:
        directory = root/DEFAULT_STAGE_DIRECTORIES[stage]; directory.mkdir(exist_ok=True)
        for source, name in [(poscar, 'POSCAR'), (potcar, 'POTCAR')]:
            shutil.copyfile(source, directory/name)
        (directory/'INCAR').write_text(texts[stage][0]); (directory/'KPOINTS').write_text(texts[stage][1])
        for name in ('POSCAR', 'POTCAR', 'INCAR', 'KPOINTS'):
            hashes[f'{DEFAULT_STAGE_DIRECTORIES[stage]}/{name}'] = sha(directory/name)
    identity = {'poscar': sha(poscar), 'potcar': sha(potcar), 'common_incar': config['common_incar'],
                'spin_mode': config['spin_mode'], 'scf_mesh': config['scf_mesh']}
    manifest = {'format': 'wannier-dft-1', 'settings': config, 'nbands_budget': budget,
                'density_identity': identity, 'potcar_metadata': potmeta, 'input_hashes': hashes,
                'scope': 'static nonmagnetic semilocal PAW, same scalar or SOC Hamiltonian across stages',
                'created_utc': datetime.now(timezone.utc).isoformat(), 'reuse_scf': reuse,
                'prepared_stages': stages, 'stage_directories': dict(DEFAULT_STAGE_DIRECTORIES)}
    if reuse:
        previous_dir = stage_directory(reuse['case'], 'scf', previous)
        current_dir = stage_directory(root, 'scf', manifest)
        for name in ('INCAR', 'KPOINTS', 'OUTCAR', 'EIGENVAL', 'CHGCAR'):
            shutil.copyfile(previous_dir/name, current_dir/name)
        shutil.copyfile(previous_dir/'run.json', current_dir/'run.reused-source.json')
        for name in ('INCAR', 'KPOINTS'):
            manifest['input_hashes'][f'{DEFAULT_STAGE_DIRECTORIES["scf"]}/{name}'] = sha(current_dir/name)
        # A reused execution keeps its original record intact. Bind a separate
        # local reuse record to current physical paths, including old->new layouts.
        record = read_json(previous_dir/'run.json')
        prefix = DEFAULT_STAGE_DIRECTORIES['scf']+'/'
        record['prepared_input_hashes'] = {k:v for k,v in hashes.items() if k.startswith(prefix)}
        record['record_type'] = 'reused_verified_scf_outputs'
        record['reuse_provenance'] = dict(source_case=str(Path(reuse['case']).resolve()),
            source_stage_directory=str(previous_dir), source_record_sha256=sha(previous_dir/'run.json'),
            source_record_copy='run.reused-source.json', new_execution=False)
        write_json(current_dir/'run.json', record)
    write_json(root/'case.json', manifest)
    from material_layout import register_fresh
    layout = register_fresh(root)
    return {'case': str(root), 'prepared_stages': stages, 'nbands_budget': budget,
            'scf_reused': bool(reuse), 'submitted': False,
            'stage_directories': dict(DEFAULT_STAGE_DIRECTORIES), 'material_layout': layout}


def eigenval_summary(path):
    with Path(path).open() as stream:
        header = [next(stream) for _ in range(6)]
        fields = header[5].split(); nk, nb = int(fields[1]), int(fields[2]); coords = []; weights = []
        if nk <= 0 or nb <= 0:
            raise ValueError('Invalid EIGENVAL dimensions.')
        for _ in range(nk):
            line = next(stream)
            while not line.strip():
                line = next(stream)
            point = list(map(float, line.split()))
            if len(point) != 4 or not np.isfinite(point).all():
                raise ValueError('Invalid EIGENVAL k point.')
            coords.append(point[:3])
            weights.append(point[3])
            for ib in range(nb):
                row = next(stream).split()
                if len(row) != 3 or int(row[0]) != ib+1 or not np.isfinite(list(map(float, row[1:]))).all():
                    raise ValueError('EIGENVAL needs complete single-spectrum band records.')
        if any(line.strip() for line in stream):
            raise ValueError('Unexpected extra EIGENVAL records.')
    return nk, nb, np.asarray(coords), np.asarray(weights)


def inspect(root, stage, require_record=True):
    if stage not in STAGES:
        raise ValueError('Unknown DFT stage.')
    root, manifest = load_case(root); directory = stage_directory(root, stage, manifest)
    problems = []; data = {}; required = ['OUTCAR', 'EIGENVAL', 'CHGCAR'] if stage == 'scf' else ['OUTCAR', 'EIGENVAL', 'PROCAR']
    if stage == 'mesh':
        required.append('WAVECAR')
    for name in required:
        if not (directory/name).is_file() or (directory/name).stat().st_size == 0:
            problems.append('Missing/nonempty output required: ' + name)
    if not problems:
        text = (directory/'OUTCAR').read_text(errors='replace')
        if 'General timing and accounting' not in text:
            problems.append('OUTCAR has no normal termination marker.')
        if not re.search(r'aborting loop because EDIFF is reached', text, re.I):
            problems.append('No electronic EDIFF convergence evidence; ionic accuracy wording is insufficient.')
        ef = re.findall(r'E-fermi\s*:\s*([+\-0-9.Ee]+)', text)
        actual_bands = re.findall(r'NBANDS\s*=\s*(\d+)', text)
        icharg = re.findall(r'ICHARG\s*=\s*(\d+)', text)
        nsw = re.findall(r'NSW\s*=\s*(\d+)', text)
        if not nsw or int(nsw[-1]) != 0:
            problems.append('Static-stage contract requires actual NSW=0.')
        if not ef or not math.isfinite(float(ef[-1])) or not actual_bands or not icharg or int(icharg[-1]) != (2 if stage=='scf' else 11):
            problems.append('Missing or inconsistent EF/NBANDS/ICHARG echo.')
        else:
            data.update(ef_eV=float(ef[-1]), actual_nbands=int(actual_bands[-1]))
        try:
            nk, nb, coords, weights = eigenval_summary(directory/'EIGENVAL')
            data['nkpoints'] = nk
            if nb != data.get('actual_nbands'):
                problems.append('OUTCAR/EIGENVAL NBANDS mismatch.')
            minimum = manifest['nbands_budget']['requested'] if stage != 'scf' or not manifest['reuse_scf'] else manifest['reuse_scf']['scf_report']['actual_nbands']
            if nb < minimum:
                problems.append('Actual NBANDS smaller than requested.')
            if stage == 'band':
                path = manifest['settings']['band_path']
                expected = np.concatenate([np.linspace(s['start'], s['end'], path['points_per_segment']) for s in path['segments']])
                if coords.shape != expected.shape or not np.allclose(coords, expected, atol=1e-6, rtol=0):
                    problems.append('Band path count/order differs from the requested structure path.')
            if stage == 'mesh':
                mesh = manifest['settings']['wannier_mesh']
                expected = np.array([np.array(index)/mesh for index in itertools.product(*(range(n) for n in mesh))])
                def point_set(points):
                    return {tuple(row) for row in np.round(np.mod(points, 1.), 7) % 1.}
                if coords.shape != expected.shape or point_set(coords) != point_set(expected):
                    problems.append('Wannier mesh is reduced, duplicated or differs from the requested full grid.')
                if np.any(weights <= 0) or not np.allclose(weights, weights[0]):
                    problems.append('Wannier mesh requires equal positive actual k weights.')
        except (ValueError, StopIteration, IndexError) as error:
            problems.append('Incomplete EIGENVAL: ' + str(error))
    hashes = {name: sha(directory/name) for name in required if (directory/name).is_file()}
    if require_record:
        record_file = directory/'run.json'
        if not record_file.is_file():
            problems.append('No completed runner record for these outputs.')
        else:
            record = read_json(record_file)
            if record.get('returncode') != 0 or record.get('output_hashes') != hashes:
                problems.append('Failed process or outputs changed since the recorded execution.')
            prepared = record.get('prepared_input_hashes', {})
            prefix = stage_directories(manifest)[stage]+'/'
            if any(prepared.get(key) != value for key, value in manifest['input_hashes'].items() if key.startswith(prefix)):
                problems.append('Execution record does not bind the current stage inputs.')
            if stage != 'scf':
                try:
                    current_density = sha(directory/'CHGCAR')
                    if record.get('density_sha256') != current_density or current_density != sha(stage_directory(root, 'scf', manifest)/'CHGCAR'):
                        problems.append('NSCF density differs from the recorded SCF source.')
                except OSError:
                    problems.append('NSCF source density is missing.')
    return {'stage': stage, 'directory': str(directory), 'usable_outputs': not problems, 'problems': problems,
            'output_hashes': hashes, **data,
            'scope': 'termination, electronic convergence marker and file/count consistency; not mesh/cutoff or Wannier-fit convergence'}


def validate_slurm_exclude(value):
    """Accept host names and numeric bracket hostlists, never directive text."""
    numeric_list = r'[0-9]+(?:-[0-9]+)?(?:,[0-9]+(?:-[0-9]+)?)*'
    host = r'[A-Za-z0-9][A-Za-z0-9_.-]*(?:\[' + numeric_list + r'\][A-Za-z0-9_.-]*)*'
    if not isinstance(value, str) or not re.fullmatch(host + r'(?:,' + host + r')*', value):
        raise ValueError('Invalid scheduler field: exclude (expected a Slurm hostlist).')
    for group in re.findall(r'\[([^\]]+)\]', value):
        for item in group.split(','):
            if '-' in item:
                lower, upper = map(int, item.split('-'))
                if lower > upper:
                    raise ValueError('Invalid scheduler field: exclude (descending host range).')


def site_profile(path):
    site = read_json(path)
    for key in ('nodes', 'ntasks'):
        if type(site.get(key)) is not int or site[key] <= 0:
            raise ValueError('Site resources need positive nodes and ntasks.')
    if site.get('scheduler') not in ('slurm', 'local'):
        raise ValueError('Site scheduler must be slurm or local.')
    for key in ('partition', 'account', 'time'):
        if key in site and not re.fullmatch(r'[A-Za-z0-9_:.-]+', site[key]):
            raise ValueError('Invalid scheduler field: ' + key)
    if 'exclude' in site:
        validate_slurm_exclude(site['exclude'])
    if not isinstance(site.get('launcher'), list) or not all(isinstance(v, str) for v in site['launcher']):
        raise ValueError('Launcher must be a JSON argv array.')
    if not isinstance(site.get('environment_args', []), list) or not all(isinstance(v, str) for v in site.get('environment_args', [])):
        raise ValueError('Environment setup arguments must be a JSON argv array.')
    if type(site.get('balance_tasks_across_nodes', False)) is not bool:
        raise ValueError('balance_tasks_across_nodes must be a JSON boolean.')
    if type(site.get('require_shared_wavecar', False)) is not bool:
        raise ValueError('require_shared_wavecar must be a JSON boolean.')
    if 'ntasks_per_node' in site:
        per_node = site['ntasks_per_node']
        if type(per_node) is not int or per_node <= 0:
            raise ValueError('ntasks_per_node must be a positive integer.')
        if site['scheduler'] != 'slurm':
            raise ValueError('ntasks_per_node is supported only for Slurm sites.')
        if site['nodes'] * per_node != site['ntasks']:
            raise ValueError('nodes * ntasks_per_node must equal ntasks.')
        # Older/custom profiles used this explicit field before the boolean
        # spelling was documented.  Honor it instead of silently allocating
        # all ranks on one node.
        site = dict(site, balance_tasks_across_nodes=True)
    return site


def environment_command(site, command):
    setup = site.get('environment_script')
    if not setup:
        return command
    if not Path(setup).is_file():
        raise ValueError('Environment setup script missing.')
    script = 'set -e; source ' + shlex.join([setup, *site.get('environment_args', [])]) + ' > /dev/null; exec "$@"'
    return ['bash', '-c', script, 'dft-environment', *command]


def resolved_launcher(site):
    values = {'ntasks': site['ntasks']}
    if 'ntasks_per_node' in site:
        values['ntasks_per_node'] = site['ntasks_per_node']
    result = []
    for arg in site['launcher']:
        if '{ntasks_per_node}' in arg and 'ntasks_per_node' not in values:
            raise ValueError('Launcher requests {ntasks_per_node} without a balanced node layout.')
        result.append(arg.replace('{ntasks}', str(values['ntasks']))
                         .replace('{ntasks_per_node}', str(values.get('ntasks_per_node', ''))))
    return result


def launch(site, executable, directory):
    command = resolved_launcher(site) + [executable]
    command = environment_command(site, command)
    with (directory/'stdout.log').open('x') as stdout, (directory/'stderr.log').open('x') as stderr:
        return subprocess.run(command, cwd=directory, stdout=stdout, stderr=stderr, check=False).returncode


def preflight(profile):
    site = site_profile(profile); problems = []; checks = {}
    for name, path in {**site['executables'], 'python': site['python']}.items():
        checks[name] = {'path': path, 'executable': Path(path).is_file() and os.access(path, os.X_OK)}
        if not checks[name]['executable']:
            problems.append('Unavailable executable: ' + name)
    setup = site.get('environment_script')
    if setup and not Path(setup).is_file():
        problems.append('Missing environment initialization script.')
    if not problems:
        code = ('import json,sys,numpy,matplotlib,jsonschema,scipy,shutil; '
                'from importlib.metadata import version; '
                'print(json.dumps(dict(python=sys.version.split()[0],numpy=numpy.__version__,'
                'matplotlib=matplotlib.__version__,scipy=scipy.__version__,jsonschema=version("jsonschema"),'
                'mpirun=shutil.which("mpirun"),sbatch=shutil.which("sbatch"))))')
        command = [site['python'], '-c', code]
        command = environment_command(site, command)
        result = subprocess.run(command, capture_output=True, text=True, timeout=60, check=False)
        if result.returncode:
            problems.append('Environment/import check failed: '+result.stderr[-1500:])
        else:
            checks['runtime'] = json.loads(result.stdout.strip().splitlines()[-1])
            if site['scheduler']=='slurm' and not checks['runtime']['sbatch']:
                problems.append('Slurm sbatch unavailable in the configured environment.')
            if site['launcher'] and site['launcher'][0]=='mpirun' and not checks['runtime']['mpirun']:
                problems.append('Configured MPI launcher unavailable.')
    return {'preflight_ok': not problems, 'checks': checks, 'problems': problems,
            'scope': 'read-only path and import checks; no executable launch, queue allocation or VASP interface validation'}


def run_stage(case, stage, profile):
    if stage not in STAGES:
        raise ValueError('Unknown DFT stage.')
    root, manifest = load_case(case); site = site_profile(profile); directory = stage_directory(root, stage, manifest)
    if stage not in manifest.get('prepared_stages', STAGES):
        raise ValueError('Requested stage was not prepared.')
    if site['scheduler'] == 'slurm' and not os.environ.get('SLURM_JOB_ID'):
        raise ValueError('Run this site inside its Slurm allocation; do not launch VASP on a login node.')
    if site['scheduler'] == 'slurm' and os.environ.get('SLURM_NTASKS') and int(os.environ['SLURM_NTASKS']) < site['ntasks']:
        raise ValueError('Slurm allocation has fewer tasks than this site profile requests.')
    if any((directory/name).exists() for name in ('OUTCAR', 'EIGENVAL', 'PROCAR', 'run.json', 'stdout.log', 'stderr.log')):
        raise ValueError('Stage already has execution artifacts; prepare a fresh case for retry.')
    executable = site['executables'][manifest['settings']['spin_mode']]
    if not Path(executable).is_file() or not os.access(executable, os.X_OK):
        raise ValueError('Selected VASP executable is unavailable.')
    lock = directory/'.running'
    with lock.open('x') as stream:
        stream.write(str(os.getpid()))
    started = datetime.now(timezone.utc).isoformat()
    try:
        density = None
        if stage != 'scf':
            source = inspect(root, 'scf')
            if not source['usable_outputs']:
                raise ValueError('SCF dependency failed: ' + '; '.join(source['problems']))
            shutil.copyfile(stage_directory(root, 'scf', manifest)/'CHGCAR', directory/'CHGCAR')
            density = sha(directory/'CHGCAR')
        code = launch(site, executable, directory)
        report = inspect(root, stage, require_record=False)
        report.update(returncode=code, started_utc=started, finished_utc=datetime.now(timezone.utc).isoformat(),
                      site_sha256=sha(profile), slurm_job_id=os.environ.get('SLURM_JOB_ID'),
                      density_sha256=density, prepared_input_hashes=manifest['input_hashes'])
        report['usable_outputs'] = report['usable_outputs'] and code == 0
        write_json(directory/'run.json', report)
        if not report['usable_outputs']:
            raise ValueError('Stage failed; inspect its run.json and logs. No downstream execution allowed.')
        return report
    finally:
        lock.unlink()


def submit(case, profile, stages=None, execute=False):
    root, manifest = load_case(case); site = site_profile(profile)
    if stages is None:
        stages = ['band'] if manifest['reuse_scf'] else ['scf', 'band']
    if site['scheduler'] != 'slurm':
        raise ValueError('Submission requires a Slurm site profile.')
    if len(set(stages)) != len(stages) or stages != sorted(stages, key=STAGES.index):
        raise ValueError('Stages must be unique and in scf, band, mesh order.')
    if any(stage not in manifest.get('prepared_stages', STAGES) for stage in stages):
        raise ValueError('Requested stage was not prepared; standalone mesh is optional compatibility work.')
    if manifest['reuse_scf'] and 'scf' in stages:
        raise ValueError('Reused SCF must not be submitted again.')
    if 'scf' not in stages and not inspect(root, 'scf')['usable_outputs']:
        raise ValueError('Complete SCF first or include it before downstream stages.')
    lines = ['#!/bin/bash', f'#SBATCH --nodes={site["nodes"]}', f'#SBATCH --ntasks={site["ntasks"]}',
             f'#SBATCH --job-name={manifest["settings"]["case_id"]}',
             f'#SBATCH --output={shlex.quote(str(root/"slurm-%j.out"))}',
             f'#SBATCH --error={shlex.quote(str(root/"slurm-%j.err"))}']
    for key in ('partition', 'account', 'time', 'exclude'):
        if site.get(key):
            lines.append(f'#SBATCH --{key}={site[key]}')
    lines += ['set -euo pipefail', 'ulimit -s unlimited', 'export PYTHONDONTWRITEBYTECODE=1']
    for stage in stages:
        lines.append(shlex.join([site['python'], str(Path(__file__).resolve()), 'run-stage', '--case', str(root),
                                 '--stage', stage, '--site', str(Path(profile).resolve())]))
    script = '\n'.join(lines) + '\n'
    if not execute:
        return {'submitted': False, 'script': script, 'stages': stages}
    scripts = root/'submissions'; scripts.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')
    path = scripts/(stamp+'.slurm'); path.write_text(script)
    result = subprocess.run(['sbatch', '--parsable', str(path)], capture_output=True, text=True, check=False)
    record = {'returncode': result.returncode, 'stdout': result.stdout.strip(), 'stderr': result.stderr.strip(),
              'script_sha256': sha(path), 'stages': stages, 'site_sha256': sha(profile)}
    write_json(path.with_suffix('.json'), record)
    if result.returncode:
        raise ValueError('Slurm submission failed; see submission record.')
    return {'submitted': True, 'job_id': result.stdout.strip(), 'script': str(path), 'scientific_success': False}


def verify_existing_cache(directory, scf_directory, stage, spin_mode):
    """Allow read-only reuse only for a cache authenticated against these outputs."""
    cache = directory/'charge.npz'
    with np.load(cache, allow_pickle=False) as z:
        meta = json.loads(str(z['metadata']))
    expected_sampling = 'band_path' if stage == 'band' else 'wannier_mesh'
    if meta.get('sampling') != expected_sampling or meta.get('spin_mode') != spin_mode:
        raise ValueError('Existing cache sampling/spin mode differs from this stage.')
    source_names = {'poscar':'POSCAR', 'eigenval':'EIGENVAL', 'procar':'PROCAR'}
    if stage == 'band':
        source_names['kpoints'] = 'KPOINTS'
    for key,name in source_names.items():
        if meta.get('source_files',{}).get(key,{}).get('sha256') != sha(directory/name):
            raise ValueError('Existing cache is stale for source file: '+name)
    if meta.get('procar_sha256') != sha(directory/'PROCAR'):
        raise ValueError('Existing cache PROCAR identity is stale.')
    provenance = meta.get('ef_provenance',{})
    if provenance.get('kind') != 'explicit_static_scf_outcar' or provenance.get('sha256') != sha(scf_directory/'OUTCAR'):
        raise ValueError('Existing cache does not bind the current static-SCF EF source.')
    if stage == 'band':
        fit = read_json(directory/'fit_path.json')
        if fit.get('cache_sha256') != sha(cache) or fit.get('procar_sha256') != meta['procar_sha256']:
            raise ValueError('Existing fit_path does not authenticate the current band cache.')
    return {'cache_reused': True, 'cache_sha256': sha(cache)}


def plot_collected_band(directory):
    """Always provide DFT/raw-orbital plots, including when cache data are reused."""
    directory = Path(directory)
    cache, fit, plots = directory/'charge.npz', directory/'fit_path.json', directory/'plots'
    names = ('dft_bands.png','dft_bands.pdf','projected_bands.png','projected_bands.pdf')
    # Preserve any existing user-facing artifacts; never silently replace them.
    for name in names:
        link = directory/name
        if (link.exists() or link.is_symlink()) and (not link.is_symlink() or link.resolve() != (plots/name).resolve()):
            raise ValueError('Existing band plot link/file conflicts with the collected plot: '+str(link))
    reused = plots.exists()
    if reused:
        report = read_json(plots/'manifest.json')
        if (report.get('format') != 'dft-band-plots-v1' or
                report.get('sources',{}).get('cache',{}).get('sha256') != sha(cache) or
                report.get('sources',{}).get('fit_path',{}).get('sha256') != sha(fit)):
            raise ValueError('Existing DFT plots are stale or unrecognized; preserve them and use a fresh case/review.')
        for name in names:
            if report.get('outputs',{}).get(name,{}).get('sha256') != sha(plots/name):
                raise ValueError('Existing DFT plot changed since its manifest: '+name)
    else:
        command = [sys.executable,'-B',str(Path(__file__).with_name('plot_dft_bands.py')),
            '--cache',str(cache),'--path',str(fit),'--out-dir',str(plots),'--title',directory.parent.name]
        result = subprocess.run(command,capture_output=True,text=True,check=False)
        if result.returncode:
            raise ValueError('DFT band plotting failed: '+result.stderr.strip())
    for name in names:
        link = directory/name
        if not link.is_symlink():
            link.symlink_to(Path('plots')/name)
    return dict(plots_reused=reused, plots_dir=str(plots), manifest=str(plots/'manifest.json'),
                user_facing_artifacts={name:str(directory/name) for name in names})


def collect(case, stage):
    if stage not in ('band','mesh'):
        raise ValueError('Collect supports band or explicitly prepared optional mesh only.')
    root, manifest = load_case(case)
    for dependency in ('scf', stage):
        report = inspect(root, dependency)
        if not report['usable_outputs']:
            raise ValueError('Cannot collect invalid stage: ' + '; '.join(report['problems']))
    directory = stage_directory(root, stage, manifest)
    scf_directory = stage_directory(root, 'scf', manifest)
    if (directory/'charge.npz').exists():
        conversion = verify_existing_cache(directory,scf_directory,stage,manifest['settings']['spin_mode'])
    else:
        command = [sys.executable, '-B', str(Path(__file__).with_name('vasp_to_cache.py')),
                   '--poscar', str(directory/'POSCAR'), '--eigenval', str(directory/'EIGENVAL'),
                   '--procar', str(directory/'PROCAR'), '--scf-outcar', str(scf_directory/'OUTCAR'),
                   '--spin-mode', manifest['settings']['spin_mode'], '--out', str(directory/'charge.npz')]
        command += ['--sampling', 'band_path' if stage == 'band' else 'wannier_mesh']
        if stage == 'band':
            command += ['--kpoints', str(directory/'KPOINTS'), '--fit-path-out', str(directory/'fit_path.json')]
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        if result.returncode:
            raise ValueError('Cache conversion failed: ' + result.stderr.strip())
        conversion = dict(cache_reused=False,adapter_output=result.stdout.strip())
    plots = plot_collected_band(directory) if stage == 'band' else None
    return {'cache': str(directory/'charge.npz'), **conversion, 'plots': plots,
            'wannier_fit_validated': False}


def interface_resources(site, manifest):
    """Preserve reviewed NBANDS with NCORE=KPAR=1, without editing the site."""
    nbands = manifest['expected']['nbands']
    if type(nbands) is not int or nbands <= 0:
        raise ValueError('Interface requires a positive integer actual NBANDS.')
    requested = site['ntasks']
    balanced = site.get('balance_tasks_across_nodes', False)
    effective = next(ranks for ranks in range(min(requested, nbands), 0, -1)
                     if nbands % ranks == 0 and (not balanced or ranks % site['nodes'] == 0))
    if site['nodes'] > effective:
        raise ValueError('Interface rank count is below the configured node count; use a smaller site node count.')
    launcher = site['launcher']
    if not launcher and requested != 1:
        raise ValueError('A launcher-free interface is serial and requires site ntasks=1.')
    if launcher and not any('{ntasks}' in arg for arg in launcher):
        raise ValueError('Interface launcher must explicitly use {ntasks} to honor the selected rank count.')
    per_node = effective // site['nodes'] if balanced else None
    launcher_name = Path(launcher[0]).name if launcher else ''
    if balanced and site['nodes'] > 1 and launcher_name != 'srun' and not any(
            '{ntasks_per_node}' in arg for arg in launcher):
        raise ValueError('Balanced multi-node MPI launchers other than srun must explicitly use '
                         '{ntasks_per_node}; an allocation directive alone does not prove rank placement.')
    execution_site = dict(site, ntasks=effective)
    if balanced:
        execution_site['ntasks_per_node'] = per_node
    record = dict(policy='interface-ncore1-kpar1-v1', actual_nbands=nbands,
                  site_ntasks=requested, effective_ntasks=effective, nodes=site['nodes'],
                  rank_selection='largest_divisor_of_actual_nbands_not_above_site_ntasks',
                  launcher_argv=resolved_launcher(execution_site))
    if balanced:
        record['ntasks_per_node'] = per_node
        record['rank_distribution'] = 'equal_tasks_per_node'
    return execution_site, record


def run_interface(trial, profile):
    from prepare_wannier_interface import validate
    trial = Path(trial).resolve(); prepared = validate(trial)
    manifest = read_json(trial/'trial_manifest.json'); site = site_profile(profile)
    if (site.get('require_shared_wavecar') and
            manifest.get('mode') != 'band_to_shared_wavecar_interface'):
        raise ValueError('This test profile requires a sealed shared WAVECAR and ALGO=None interface.')
    from decision_stages import require_execution_binding
    require_execution_binding(manifest.get('decision_stage'))
    execution_site, parallelism = interface_resources(site, manifest)
    if site['scheduler']=='slurm' and not os.environ.get('SLURM_JOB_ID'):
        raise ValueError('Interface execution requires the configured Slurm allocation.')
    if site['scheduler']=='slurm':
        try:
            allocated = int(os.environ['SLURM_NTASKS'])
        except (KeyError, ValueError) as error:
            raise ValueError('Interface needs SLURM_NTASKS to verify its allocation.') from error
        if allocated < execution_site['ntasks']:
            raise ValueError('Insufficient allocated Slurm tasks for the effective interface rank count.')
        parallelism['allocated_ntasks'] = allocated
    if any((trial/name).exists() for name in ('OUTCAR', 'EIGENVAL', 'PROCAR', 'OSZICAR', 'vasprun.xml',
                                             'stdout.log', 'stderr.log', 'interface_run.json',
                                             'wannier90.amn', 'wannier90.mmn', 'wannier90.eig')):
        raise ValueError('Interface trial already has execution artifacts; prepare a new trial.')
    executable = site['executables'][manifest['expected']['spin_mode']]
    if not Path(executable).is_file() or not os.access(executable, os.X_OK):
        raise ValueError('Selected VASP executable unavailable.')
    lock = trial/'.running'
    with lock.open('x') as stream: stream.write(str(os.getpid()))
    started=datetime.now(timezone.utc).isoformat();timer=time.monotonic()
    try:
        code = launch(execution_site, executable, trial)
        record = {'returncode': code, 'site_sha256': sha(profile), 'manifest_id': manifest['manifest_id'],
                  'started_utc': started, 'finished_utc': datetime.now(timezone.utc).isoformat(),
                  'wall_seconds': time.monotonic()-timer,
                  'parallelism': parallelism,
                  'slurm_job_id': os.environ.get('SLURM_JOB_ID'), 'interface_files_consistent': False,
                  'wannier_localization_executed': False, 'wannier_disentanglement_executed': False,
                  'wannier_policy': prepared['wannier_policy'], 'fit_pass': None,
                  'status': 'interface_validation_failed', 'ready_for_wannier': False,
                  'mesh_gate_pending': prepared.get('mesh_gate_pending', False)}
        if manifest.get('mode') == 'band_to_shared_wavecar_interface':
            record['preflight_wavecar_sha256'] = manifest['input_hashes']['WAVECAR']
        try:
            if code != 0: raise ValueError('VASP interface process failed.')
            text = (trial/'OUTCAR').read_text(errors='replace')
            shared_route = manifest.get('mode') == 'band_to_shared_wavecar_interface'
            if ('General timing and accounting' not in text or
                    (not shared_route and not re.search(r'aborting loop because EDIFF is reached', text, re.I))):
                raise ValueError('Interface OUTCAR lacks normal completion/electronic convergence evidence.')
            for tag, expected in [('NSW', 0), ('ICHARG', 11), ('NBANDS', manifest['expected']['nbands'])]:
                actual = re.findall(tag+r'\s*=\s*(\d+)', text)
                if not actual or int(actual[-1]) != expected:
                    raise ValueError('Interface OUTCAR setting differs: ' + tag)
            record['validation'] = validate(trial, outputs=True,
                preflight_wavecar_sha=record.get('preflight_wavecar_sha256'))
            record['interface_files_consistent'] = True
            record.update(status=record['validation']['status'],
                          ready_for_wannier=record['validation']['ready_for_wannier'], mesh_gate_pending=False)
            if 'mesh_gate' in record['validation']:
                record['mesh_gate'] = record['validation']['mesh_gate']
            output_names = ['OUTCAR', 'wannier90.win', 'wannier90.amn', 'wannier90.mmn', 'wannier90.eig']
            if manifest.get('mode') in ('band_to_interface', 'band_to_shared_wavecar_interface'):
                output_names.append('EIGENVAL')
            if shared_route:
                output_names.append('OSZICAR')
            record['output_hashes'] = {name: sha(trial/name) for name in output_names}
        except (ValueError, OSError, KeyError) as error:
            record['problem'] = str(error)
        write_json(trial/'interface_run.json', record)
        if not record['interface_files_consistent']:
            raise ValueError('Interface outputs failed checks; see interface_run.json.')
        if not record['ready_for_wannier']:
            raise ValueError('Interface matrices are consistent but the actual interface window/exclusion gate failed; see interface_run.json validation and any exclusion_recovery. Review the proposal before Wannier90; a changed exclusion requires matching new matrices.')
        return record
    finally:
        lock.unlink()


def submit_interface(trial, profile, execute=False):
    from prepare_wannier_interface import validate
    trial = Path(trial).resolve(); validate(trial); site = site_profile(profile)
    if (site.get('require_shared_wavecar') and
            read_json(trial/'trial_manifest.json').get('mode') != 'band_to_shared_wavecar_interface'):
        raise ValueError('This test profile requires a sealed shared WAVECAR and ALGO=None interface.')
    from decision_stages import require_execution_binding
    require_execution_binding(read_json(trial/'trial_manifest.json').get('decision_stage'))
    if site['scheduler'] != 'slurm': raise ValueError('Submission requires a Slurm site.')
    execution_site, parallelism = interface_resources(site, read_json(trial/'trial_manifest.json'))
    lines = ['#!/bin/bash', f'#SBATCH --nodes={site["nodes"]}', f'#SBATCH --ntasks={execution_site["ntasks"]}',
             '#SBATCH --job-name=wannier-interface',
             f'#SBATCH --output={shlex.quote(str(trial/"slurm-%j.out"))}',
             f'#SBATCH --error={shlex.quote(str(trial/"slurm-%j.err"))}']
    if parallelism.get('ntasks_per_node'):
        lines.append(f'#SBATCH --ntasks-per-node={parallelism["ntasks_per_node"]}')
    for key in ('partition', 'account', 'time', 'exclude'):
        if site.get(key): lines.append(f'#SBATCH --{key}={site[key]}')
    lines += ['set -euo pipefail', 'ulimit -s unlimited', 'export PYTHONDONTWRITEBYTECODE=1',
              shlex.join([site['python'], str(Path(__file__).resolve()), 'run-interface', '--trial', str(trial),
                          '--site', str(Path(profile).resolve())])]
    script = '\n'.join(lines)+'\n'
    if not execute: return {'submitted': False, 'script': script, 'parallelism': parallelism}
    destination = trial/'submissions'; destination.mkdir(exist_ok=True)
    path = destination/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')+'.slurm')
    path.write_text(script)
    process = subprocess.run(['sbatch', '--parsable', str(path)], capture_output=True, text=True, check=False)
    record = {'returncode': process.returncode, 'stdout': process.stdout.strip(), 'stderr': process.stderr.strip(),
              'script_sha256': sha(path), 'site_sha256': sha(profile), 'parallelism': parallelism}
    write_json(path.with_suffix('.json'), record)
    if process.returncode: raise ValueError('Interface submission failed; inspect its submission record.')
    return {'submitted': True, 'job_id': process.stdout.strip(), 'scientific_success': False, 'parallelism': parallelism}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('prepare')
    for flag in ('poscar', 'potcar', 'settings', 'out'):
        p.add_argument('--'+flag, required=True)
    p.add_argument('--reuse-scf-case')
    p.add_argument('--stages', nargs='+', choices=STAGES,
                   help='Default: scf band. Use scf band mesh only to explicitly prepare a standalone mesh.')
    for name in ('inspect', 'run-stage', 'collect'):
        p = sub.add_parser(name); p.add_argument('--case', required=True)
        p.add_argument('--stage', choices=('band','mesh') if name=='collect' else STAGES, required=True)
        if name == 'run-stage': p.add_argument('--site', required=True)
    p = sub.add_parser('submit'); p.add_argument('--case', required=True); p.add_argument('--site', required=True)
    p.add_argument('--stages', nargs='+', choices=STAGES,
                   help='Default: scf band (band only when reusing SCF). A standalone mesh stage is optional compatibility work.')
    p.add_argument('--execute', action='store_true', help='Actually call sbatch; omission only returns the reviewable script.')
    p = sub.add_parser('preflight'); p.add_argument('--site', required=True)
    for name in ('run-interface', 'submit-interface'):
        p = sub.add_parser(name); p.add_argument('--trial', required=True); p.add_argument('--site', required=True)
        if name == 'submit-interface': p.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    try:
        if args.command == 'prepare': result = prepare(args.poscar, args.potcar, args.settings, args.out, args.reuse_scf_case, args.stages)
        elif args.command == 'inspect': result = inspect(args.case, args.stage)
        elif args.command == 'run-stage': result = run_stage(args.case, args.stage, args.site)
        elif args.command == 'collect': result = collect(args.case, args.stage)
        elif args.command == 'submit': result = submit(args.case, args.site, args.stages, args.execute)
        elif args.command == 'run-interface': result = run_interface(args.trial, args.site)
        elif args.command == 'submit-interface': result = submit_interface(args.trial, args.site, args.execute)
        else: result = preflight(args.site)
        print(json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2))
        return 2 if (args.command=='inspect' and not result['usable_outputs']) or (args.command=='preflight' and not result['preflight_ok']) else 0
    except (ValueError, KeyError, OSError, IndexError, TypeError) as error:
        parser.exit(2, str(error)+'\n')


if __name__ == '__main__':
    raise SystemExit(main())
