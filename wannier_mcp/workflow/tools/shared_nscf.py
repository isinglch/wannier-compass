#!/usr/bin/env python3
"""Seal and verify a fixed-density VASP WAVECAR source for repeated Wannier trials.

This tool never computes wavefunctions. Only a normally completed fixed-density
run with an automatic full mesh, a matching EIGENVAL and an intact WAVECAR can
become a shared source. Sealing is independent of any projection or window.
"""
import argparse
import json
from pathlib import Path
import re
import struct

import orbital_evidence as evidence
from prepare_wannier_interface import (automatic_mesh_spec, completed_source,
                                       incar, mesh_geometry, physical_contract,
                                       require_file, sha, spin_mode)
from vasp_to_cache import read_eigenval

FORMAT = 'wannier-shared-nscf-v1'
INPUTS = ('INCAR', 'POSCAR', 'POTCAR', 'KPOINTS', 'CHGCAR')
OUTPUTS = ('OUTCAR', 'EIGENVAL', 'WAVECAR', 'process_exit.json')


def wavecar_header(path):
    path = require_file(path)
    with path.open('rb') as stream:
        first = stream.read(24)
        if len(first) != 24:
            raise ValueError('Incomplete WAVECAR header.')
        recl, nspin, precision_tag = struct.unpack('ddd', first)
        if (not 256 <= recl <= 1_000_000_000 or int(recl) != recl or
                nspin != 1 or precision_tag not in (45200., 45210.)):
            raise ValueError('Unsupported or corrupt spinor/scalar WAVECAR header.')
        stream.seek(int(recl))
        second = stream.read(24)
        if len(second) != 24:
            raise ValueError('Incomplete WAVECAR dimensions.')
        nk, nb, encut = struct.unpack('ddd', second)
    if (not 1 <= nk <= 1_000_000 or not 1 <= nb <= 100_000 or
            int(nk) != nk or int(nb) != nb or not 0 < encut < 100_000):
        raise ValueError('Invalid WAVECAR k-point, band or cutoff dimensions.')
    # Two global records, then a k-point header plus NBANDS coefficient
    # records for every spin/k point.  Counting coefficients alone can accept
    # a file with missing final wavefunctions.  Keep the returned seal fields
    # unchanged so intact sources sealed by earlier versions remain verifiable.
    minimum_bytes = int(recl) * (2 + int(nspin) * int(nk) * (int(nb) + 1))
    if path.stat().st_size < minimum_bytes:
        raise ValueError('WAVECAR is shorter than its declared k-point/band records.')
    return dict(record_bytes=int(recl), nspin=int(nspin), precision_tag=int(precision_tag),
                nkpoints=int(nk), nbands=int(nb), encut_eV=float(encut),
                file_bytes=path.stat().st_size)


def input_evidence(directory, scf_dir, band_dir):
    directory, scf_dir, band_dir = map(Path, (directory, scf_dir, band_dir))
    prep = json.loads(require_file(directory / 'PREPARATION.json').read_text())
    if prep.get('format') != 'shared-nscf-preparation-v1':
        raise ValueError('Missing shared-NSCF preparation provenance.')
    for name, digest in prep['prepared_files'].items():
        if name not in (*INPUTS, 'run.sbatch') or sha(require_file(directory / name)) != digest:
            raise ValueError('Shared NSCF prepared input changed: ' + name)
    for name in INPUTS:
        require_file(directory / name)
    if sha(directory / 'CHGCAR') != sha(require_file(scf_dir / 'CHGCAR')):
        raise ValueError('Shared fixed charge differs from the reviewed SCF CHGCAR.')
    for name in ('POSCAR', 'POTCAR'):
        if sha(directory / name) != sha(require_file(scf_dir / name)) or sha(directory / name) != sha(require_file(band_dir / name)):
            raise ValueError('Shared structure/pseudopotential differs from SCF or projected band: ' + name)
    tags, scf_tags, band_tags = (incar(directory / 'INCAR'), incar(scf_dir / 'INCAR'),
                                 incar(band_dir / 'INCAR'))
    physical_contract(tags, scf_tags)
    physical_contract(band_tags, scf_tags, require_full_mesh=False)
    if spin_mode(tags) != spin_mode(band_tags):
        raise ValueError('Shared wavefunctions and projected band have different spin modes.')
    if (tags.get('ISTART') != '0' or tags.get('ICHARG') != '11' or
            tags.get('ISYM') != '-1' or tags.get('LWAVE', '').upper().strip('.') not in ('T', 'TRUE') or
            tags.get('LWANNIER90', '').upper().strip('.') not in ('F', 'FALSE')):
        raise ValueError('Shared source must be a fresh, fixed-charge, full-mesh wavefunction run.')
    mode, grid, shift = automatic_mesh_spec(directory / 'KPOINTS')
    if mode != 'g' or any(abs(v) > 1e-9 for v in shift) or grid != prep.get('grid'):
        raise ValueError('Shared source grid differs from its declared Gamma mesh.')
    return prep, tags, grid


def build_record(directory, scf_dir, band_dir):
    directory, scf_dir, band_dir = map(lambda p: Path(p).resolve(), (directory, scf_dir, band_dir))
    if not (directory / 'PREPARATION.json').exists() and (directory.parent / 'case.json').exists():
        return build_dft_mesh_record(directory, scf_dir, band_dir)
    prep, tags, grid = input_evidence(directory, scf_dir, band_dir)
    process = json.loads(require_file(directory / 'process_exit.json').read_text())
    if (process.get('returncode') != 0 or not isinstance(process.get('elapsed_seconds'), int)
            or process['elapsed_seconds'] < 0):
        raise ValueError('Shared NSCF process did not exit successfully.')
    eigen = read_eigenval(require_file(directory / 'EIGENVAL'))
    header = wavecar_header(directory / 'WAVECAR')
    if (eigen['energies'].shape != (header['nkpoints'], header['nbands']) or
            eigen['nions'] < 1 or header['nbands'] < prep['requested_nbands'] or
            abs(header['encut_eV'] - float(tags['ENCUT'])) > 1e-4):
        raise ValueError('WAVECAR dimensions/cutoff disagree with EIGENVAL or requested bands.')
    if mesh_geometry(directory / 'KPOINTS', eigen['kcoords'], eigen['kweights']) != grid:
        raise ValueError('EIGENVAL is not the complete declared shared mesh.')
    completion = completed_source(directory, True, header['nbands'])
    files = {name: sha(directory / name) for name in (*INPUTS, *OUTPUTS, 'PREPARATION.json')}
    record = dict(format=FORMAT, directory=str(directory), scf_directory=str(scf_dir),
                  band_directory=str(band_dir), grid=grid, spin_mode=spin_mode(tags),
                  actual_nbands=header['nbands'], actual_nkpoints=header['nkpoints'],
                  wavecar_header=header, process=process, completion=completion,
                  file_sha256=files,
                  scope='Fixed-density wavefunction provenance and internal dimensions only; no Wannier basis/window or fit approved.')
    record['source_id'] = evidence.digest(record)
    return record


def build_dft_mesh_record(directory, scf_dir, band_dir):
    """Seal an official dft_workflow mesh using its original case/run records.

    This route never manufactures a legacy PREPARATION/process_exit record after
    execution. The DFT manifest hashes inputs; run.json hashes actual outputs.
    """
    import dft_workflow as dft

    root, manifest = dft.load_case(directory.parent)
    expected = {stage: dft.stage_directory(root, stage, manifest).resolve()
                for stage in ('scf', 'band', 'mesh')}
    if (directory != expected['mesh'] or scf_dir != expected['scf'] or
            band_dir != expected['band'] or
            'mesh' not in manifest.get('prepared_stages', ())):
        raise ValueError('DFT mesh, SCF and projected band must belong to one prepared case.')
    for stage in ('scf', 'band', 'mesh'):
        result = dft.inspect(root, stage)
        if not result['usable_outputs']:
            raise ValueError('DFT ' + stage + ' stage is not verified: ' + '; '.join(result['problems']))
    for name in INPUTS:
        require_file(directory / name)
    for name in ('POSCAR', 'POTCAR'):
        if (sha(directory / name) != sha(require_file(scf_dir / name)) or
                sha(directory / name) != sha(require_file(band_dir / name))):
            raise ValueError('DFT mesh source differs in structure or potential: ' + name)
    if sha(directory / 'CHGCAR') != sha(require_file(scf_dir / 'CHGCAR')):
        raise ValueError('DFT mesh fixed density differs from the verified SCF.')
    tags, scf_tags, band_tags = (incar(directory / 'INCAR'), incar(scf_dir / 'INCAR'),
                                 incar(band_dir / 'INCAR'))
    physical_contract(tags, scf_tags)
    physical_contract(band_tags, scf_tags, require_full_mesh=False)
    if spin_mode(tags) != spin_mode(band_tags):
        raise ValueError('DFT mesh and projected band have different spin modes.')
    if (tags.get('ISTART') != '0' or tags.get('ICHARG') != '11' or
            tags.get('ISYM') != '-1' or
            tags.get('LWAVE', '').upper().strip('.') not in ('T', 'TRUE') or
            tags.get('LWANNIER90', '').upper().strip('.') not in ('F', 'FALSE')):
        raise ValueError('DFT mesh is not a fresh, full-grid fixed-density WAVECAR run.')
    mode, grid, shift = automatic_mesh_spec(directory / 'KPOINTS')
    if mode != 'g' or any(abs(v) > 1e-9 for v in shift) or grid != manifest['settings']['wannier_mesh']:
        raise ValueError('DFT mesh differs from the declared full Gamma grid.')
    run = json.loads(require_file(directory / 'run.json').read_text())
    if run.get('returncode') != 0 or not run.get('started_utc') or not run.get('finished_utc'):
        raise ValueError('DFT mesh lacks a successful timed execution record.')
    eigen = read_eigenval(require_file(directory / 'EIGENVAL'))
    header = wavecar_header(directory / 'WAVECAR')
    if (eigen['energies'].shape != (header['nkpoints'], header['nbands']) or
            eigen['nions'] < 1 or header['nbands'] < manifest['nbands_budget']['requested'] or
            abs(header['encut_eV'] - float(tags['ENCUT'])) > 1e-4):
        raise ValueError('DFT mesh WAVECAR dimensions/cutoff disagree with its EIGENVAL or budget.')
    if mesh_geometry(directory / 'KPOINTS', eigen['kcoords'], eigen['kweights']) != grid:
        raise ValueError('DFT mesh EIGENVAL does not cover its declared full grid.')
    completion = completed_source(directory, True, header['nbands'])
    files = {name: sha(directory / name) for name in (*INPUTS, 'OUTCAR', 'EIGENVAL',
                                                    'WAVECAR', 'run.json')}
    files['case.json'] = sha(root / 'case.json')
    record = dict(format=FORMAT, directory=str(directory), scf_directory=str(scf_dir),
                  band_directory=str(band_dir), grid=grid, spin_mode=spin_mode(tags),
                  actual_nbands=header['nbands'], actual_nkpoints=header['nkpoints'],
                  wavecar_header=header,
                  process={key: run.get(key) for key in ('returncode', 'slurm_job_id',
                                                         'started_utc', 'finished_utc')},
                  completion=completion, file_sha256=files,
                  preparation_route='verified_dft_workflow_mesh',
                  scope='Verified DFT stage and full-grid wavefunction identity only; no Wannier basis/window or fit approved.')
    record['source_id'] = evidence.digest(record)
    return record


def seal(directory, scf_dir, band_dir):
    directory = Path(directory)
    target = directory / 'SOURCE_SEAL.json'
    if target.exists():
        raise ValueError('Shared source is already sealed; use verify.')
    record = build_record(directory, scf_dir, band_dir)
    target.write_text(json.dumps(record, ensure_ascii=False, indent=2) + '\n')
    return record


def verify(directory, scf_dir=None, band_dir=None):
    directory = Path(directory).resolve()
    record = json.loads(require_file(directory / 'SOURCE_SEAL.json').read_text())
    if (record.get('format') != FORMAT or record.get('directory') != str(directory) or
            evidence.digest({key: value for key, value in record.items() if key != 'source_id'}) != record.get('source_id')):
        raise ValueError('Shared source seal identity mismatch.')
    scf_dir = Path(scf_dir or record['scf_directory']).resolve()
    band_dir = Path(band_dir or record['band_directory']).resolve()
    if str(scf_dir) != record['scf_directory'] or str(band_dir) != record['band_directory']:
        raise ValueError('Shared source is bound to different SCF/band inputs.')
    current = build_record(directory, scf_dir, band_dir)
    if current != record:
        raise ValueError('Shared source or its run evidence changed after sealing.')
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('seal', 'verify'))
    parser.add_argument('--directory', required=True)
    parser.add_argument('--scf-dir')
    parser.add_argument('--band-dir')
    args = parser.parse_args()
    if args.command == 'seal' and (not args.scf_dir or not args.band_dir):
        parser.error('seal requires --scf-dir and --band-dir')
    try:
        result = (seal(args.directory, args.scf_dir, args.band_dir) if args.command == 'seal'
                  else verify(args.directory, args.scf_dir, args.band_dir))
        print(json.dumps({'source_id': result['source_id'], 'grid': result['grid'],
                          'actual_nbands': result['actual_nbands'],
                          'directory': result['directory']}, ensure_ascii=False))
    except (ValueError, KeyError, TypeError, OSError, json.JSONDecodeError, struct.error) as exc:
        parser.exit(2, 'Shared NSCF error: ' + str(exc) + '\n')


if __name__ == '__main__':
    main()
