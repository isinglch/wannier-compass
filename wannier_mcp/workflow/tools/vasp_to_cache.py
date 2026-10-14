#!/usr/bin/env python3
"""Convert explicit, completed VASP outputs to a charge-only evidence cache.

No directory discovery, material rules, projection normalization or job execution.
The source POSCAR must belong to this static calculation; PROCAR has ion indices,
not species identifiers, so that provenance remains the caller's responsibility.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

import numpy as np
from orbital_channels import SPD_COLUMNS, procar_columns

FLOAT = r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[EeDd][-+]?\d+)?"
COLS = list(SPD_COLUMNS)  # Compatibility name; actual cache columns come from PROCAR.


def number(text):
    value = float(text.replace('D', 'E').replace('d', 'e'))
    if not np.isfinite(value):
        raise ValueError('Nonfinite numeric input.')
    return value


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def read_poscar(path):
    lines = Path(path).read_text().splitlines()
    if len(lines) < 8:
        raise ValueError('Incomplete POSCAR.')
    scale = [number(v) for v in lines[1].split()]
    if len(scale) != 1 or scale[0] == 0:
        raise ValueError('This adapter supports one nonzero POSCAR scale factor.')
    lattice = np.asarray([[number(v) for v in row.split()] for row in lines[2:5]])
    if lattice.shape != (3, 3) or abs(np.linalg.det(lattice)) < 1e-12:
        raise ValueError('POSCAR lattice must be nonsingular with three row vectors.')
    factor = scale[0] if scale[0] > 0 else (-scale[0] / abs(np.linalg.det(lattice))) ** (1/3)
    lattice *= factor
    species = lines[5].split()
    if not species or any(not re.fullmatch(r'[A-Z][a-z]?', s) for s in species):
        raise ValueError('VASP 5 POSCAR with explicit element symbols is required.')
    counts = [int(v) for v in lines[6].split()]
    if len(counts) != len(species) or any(v <= 0 for v in counts):
        raise ValueError('Invalid POSCAR species/counts.')
    cursor = 7
    if lines[cursor].strip().lower().startswith('s'):
        cursor += 1
    mode = lines[cursor].strip().lower()
    if not mode.startswith(('d', 'c', 'k')):
        raise ValueError('Missing Direct/Cartesian POSCAR coordinate mode.')
    rows = lines[cursor+1:cursor+1+sum(counts)]
    if len(rows) != sum(counts) or any(len(row.split()) < 3 for row in rows):
        raise ValueError('Incomplete POSCAR atomic coordinates.')
    coordinates = np.asarray([[number(v) for v in row.split()[:3]] for row in rows])
    fractional = coordinates if mode.startswith('d') else coordinates * factor @ np.linalg.inv(lattice)
    return dict(species=species, counts=counts, lattice=lattice,
                fractional_positions=fractional,
                ion_species=[s for s, n in zip(species, counts) for _ in range(n)])


def read_eigenval(path):
    with Path(path).open() as stream:
        header = [stream.readline() for _ in range(6)]
        if any(not row for row in header):
            raise ValueError('Incomplete EIGENVAL header.')
        first = header[0].split()
        if len(first) != 4 or int(first[-1]) != 1:
            raise ValueError('Only a single EIGENVAL energy spectrum is supported; collinear ISPIN=2 is rejected.')
        dims = header[5].split()
        if len(dims) != 3:
            raise ValueError('EIGENVAL line 6 must contain NELECT, NKPTS and NBANDS.')
        nelect, nk, nb = number(dims[0]), int(dims[1]), int(dims[2])
        if nk < 1 or nb < 1:
            raise ValueError('EIGENVAL NKPTS/NBANDS must be positive.')
        rows = (line.strip() for line in stream if line.strip())
        coords, weights = np.empty((nk, 3)), np.empty(nk)
        energies, occupations = np.empty((nk, nb)), np.empty((nk, nb))
        try:
            for k in range(nk):
                fields = next(rows).split()
                if len(fields) != 4:
                    raise ValueError('EIGENVAL k row requires fractional x y z and weight.')
                coords[k], weights[k] = [number(v) for v in fields[:3]], number(fields[3])
                for b in range(nb):
                    fields = next(rows).split()
                    if len(fields) != 3 or int(fields[0]) != b+1:
                        raise ValueError('EIGENVAL requires sequential single-spectrum band/energy/occupation rows.')
                    energies[k, b], occupations[k, b] = map(number, fields[1:])
            if next(rows, None) is not None:
                raise ValueError('Unexpected trailing EIGENVAL content or additional spin channel.')
        except StopIteration as exc:
            raise ValueError('Truncated EIGENVAL.') from exc
    if (weights < 0).any():
        raise ValueError('Negative EIGENVAL k weights.')
    return dict(energies=energies, occupations=occupations, kcoords=coords,
                kweights=weights, nelect=nelect, nions=int(first[0]))


class NonblankLines:
    def __init__(self, stream):
        self.rows = (line.strip() for line in stream if line.strip())
        self.pending = None

    def peek(self):
        if self.pending is None:
            self.pending = next(self.rows, None)
        return self.pending

    def pop(self):
        result = self.peek()
        self.pending = None
        if result is None:
            raise ValueError('Truncated PROCAR.')
        return result


def read_procar(path, nk, nb, ni):
    header_re = re.compile(r'# of k-points:\s*(\d+)\s*# of bands:\s*(\d+)\s*# of ions:\s*(\d+)')
    k_re = re.compile(r'k-point\s+(\d+)\s*:\s*('+FLOAT+r')\s*('+FLOAT+r')\s*('+FLOAT+r')\s+weight\s*=\s*('+FLOAT+r')\s*$')
    b_re = re.compile(r'band\s+(\d+)\s+#?\s*energy\s+('+FLOAT+r')\s+#?\s*occ\.?\s+('+FLOAT+r')\s*$')
    raw, columns = None, None
    totals = np.empty((nk, nb))
    energies, occupations = np.empty((nk, nb)), np.empty((nk, nb))
    coords, weights = np.empty((nk, 3)), np.empty(nk)
    table_counts = set()
    inferred_single_ion_tot_rows = 0
    with Path(path).open() as stream:
        lines = NonblankLines(stream)
        # Standard PROCAR has a descriptive title followed by its dimensions.
        line = lines.pop()
        if not header_re.fullmatch(line):
            if not line.upper().startswith('PROCAR'):
                raise ValueError('Unrecognized PROCAR title.')
            line = lines.pop()
        match = header_re.fullmatch(line)
        if not match or tuple(map(int, match.groups())) != (nk, nb, ni):
            raise ValueError('PROCAR dimensions disagree with EIGENVAL/POSCAR.')
        for k in range(nk):
            match = k_re.fullmatch(lines.pop())
            if not match or int(match[1]) != k+1:
                raise ValueError('Nonsequential PROCAR k points, extra spin channel, or unsupported dialect.')
            coords[k], weights[k] = list(map(number, match.groups()[1:4])), number(match[5])
            for b in range(nb):
                match = b_re.fullmatch(lines.pop())
                if not match or int(match[1]) != b+1:
                    raise ValueError('Nonsequential PROCAR band headers.')
                energies[k, b], occupations[k, b] = number(match[2]), number(match[3])
                table_count = 0
                while lines.peek() is not None and (lines.peek().split()[0] == 'ion'
                        or (table_count > 0 and lines.peek().split()[0] == '1')):
                    # VASP normally prints the lm header only before charge;
                    # some writers repeat it before each magnetization table.
                    if lines.peek().split()[0] == 'ion':
                        fields = lines.pop().split()
                        if fields[-1] != 'tot':
                            raise ValueError('PROCAR resolved charge header must end in tot.')
                        current = procar_columns(fields[1:-1])
                        if columns is None:
                            columns = current
                            raw = np.empty((nk, nb, ni, len(columns)))
                        elif current != columns:
                            raise ValueError('PROCAR component layout changed between states or charge/magnetization tables.')
                    elif columns is None or table_count == 0:
                        raise ValueError('Every charge table needs a resolved ion header.')
                    width = len(columns) + 1  # components and printed per-ion total
                    values = np.empty((ni, width))
                    for ion in range(ni):
                        row = lines.pop().split()
                        if len(row) != width + 1 or row[0] != str(ion+1):
                            raise ValueError('PROCAR ion rows must follow the exact POSCAR index order.')
                        values[ion] = [number(v) for v in row[1:]]
                    next_line = lines.peek()
                    next_marker = next_line.split()[0] if next_line is not None else None
                    if next_marker == 'tot':
                        row = lines.pop().split()
                        if len(row) != width + 1:
                            raise ValueError('Missing complete PROCAR tot row.')
                        total_row = np.asarray([number(v) for v in row[1:]])
                    elif ni == 1 and next_marker in (None, 'band', 'k-point', 'ion'):
                        # VASP can omit the redundant total row for a single ion.
                        # The ion row still prints its own total; keep the raw
                        # orbital values and record this dialect in metadata.
                        total_row = values[0]
                        inferred_single_ion_tot_rows += 1
                    else:
                        raise ValueError('Missing complete PROCAR tot row.')
                    if table_count == 0:
                        if (values < 0).any() or (total_row < 0).any():
                            raise ValueError('Negative charge projection; magnetization cannot be used as charge.')
                        raw[k, b] = values[:, :-1]
                        totals[k, b] = total_row[-1]
                    table_count += 1
                if table_count not in (1, 4):
                    raise ValueError('Every band needs exactly 1 scalar or 4 spinor tables (charge, mx, my, mz).')
                table_counts.add(table_count)
        if lines.peek() is not None:
            raise ValueError('Unexpected trailing PROCAR content, complex phase tables, or collinear second spin spectrum.')
    if len(table_counts) != 1:
        raise ValueError('Mixed scalar/spinor or incomplete PROCAR tables.')
    return dict(raw=raw, columns=columns, printed_total=totals, inferred_single_ion_tot_rows=inferred_single_ion_tot_rows,
                energies=energies, occupations=occupations,
                kcoords=coords, kweights=weights,
                spin_mode='spinor' if table_counts == {4} else 'scalar')


def read_ef(scf_outcar, ef_json, expected_mode):
    if scf_outcar:
        path = Path(scf_outcar).resolve()
        content = path.read_text()
        icharg = re.findall(r'\bICHARG\s*=\s*(\d+)', content)
        nsw = re.findall(r'\bNSW\s*=\s*(\d+)', content)
        if not icharg or int(icharg[-1]) >= 10 or not nsw or int(nsw[-1]) != 0:
            raise ValueError('EF source must explicitly be a static self-consistent OUTCAR (ICHARG < 10, NSW = 0).')
        if 'General timing and accounting' not in content:
            raise ValueError('SCF OUTCAR lacks the normal completion footer.')
        flags = [re.findall(r'\b'+key+r'\s*=\s*([TF])', content) for key in ['LSORBIT', 'LNONCOLLINEAR']]
        ispin = re.findall(r'\bISPIN\s*=\s*(\d+)', content)
        if not ispin or any(not flag for flag in flags):
            raise ValueError('SCF OUTCAR must identify ISPIN, LSORBIT and LNONCOLLINEAR.')
        mode = 'spinor' if any(flag[-1] == 'T' for flag in flags) else 'scalar'
        if mode != expected_mode or (mode == 'scalar' and int(ispin[-1]) != 1):
            raise ValueError('SCF energy-reference spin mode disagrees with the projection calculation.')
        values = re.findall(r'E-fermi\s*:\s*('+FLOAT+r')', content)
        if not values:
            raise ValueError('SCF OUTCAR has no E-fermi.')
        value = number(values[-1])
        return value, str(path), dict(kind='explicit_static_scf_outcar', path=str(path), sha256=sha256(path),
            spin_mode=mode, source_checks='static SCF tags, spin mode and normal completion; electronic convergence must be checked by the execution workflow')
    path = Path(ef_json).resolve()
    record = json.loads(path.read_text())
    required = ['kind', 'ef_eV', 'source_path', 'source_sha256', 'spin_mode', 'reason']
    if any(k not in record for k in required) or record['kind'] != 'reviewed_scf_reference':
        raise ValueError('EF JSON must be an explicit reviewed_scf_reference record.')
    source = Path(record['source_path'])
    if not source.is_absolute():
        source = path.parent / source
    if not isinstance(record['reason'], str) or not record['reason'].strip() or record['spin_mode'] != expected_mode:
        raise ValueError('EF JSON needs a review reason and matching spin_mode.')
    if sha256(source) != record['source_sha256']:
        raise ValueError('Reviewed EF source hash mismatch.')
    value = number(str(record['ef_eV']))
    return value, str(path), {**record, 'record_path': str(path), 'record_sha256': sha256(path),
                             'source_path': str(source.resolve())}


def line_geometry(path, lattice, actual_k, tolerance):
    lines = Path(path).read_text().splitlines()
    if len(lines) < 6 or not lines[2].strip().lower().startswith('l') or not lines[3].strip().lower().startswith('r'):
        raise ValueError('Geometry adapter supports explicit reciprocal Line-mode KPOINTS only.')
    # VASP accepts an inline comment after the number of points per segment.
    # Keep the original KPOINTS file and its source hash unchanged.
    points = int(lines[1].split('!', 1)[0].split('#', 1)[0].strip())
    if points < 2:
        raise ValueError('Each KPOINTS segment needs at least two points.')
    endpoints = []
    for line in lines[4:]:
        if not line.strip():
            continue
        body, _, label = line.partition('!')
        fields = body.split()
        if len(fields) not in (3, 4):
            raise ValueError('Line-mode endpoints need x y z [label], optionally ! label.')
        endpoints.append(([number(v) for v in fields[:3]], label.strip() or (fields[3] if len(fields) == 4 else '')))
    if not endpoints or len(endpoints) % 2:
        raise ValueError('Line-mode KPOINTS requires start/end pairs.')
    expected = np.concatenate([np.linspace(endpoints[i][0], endpoints[i+1][0], points) for i in range(0, len(endpoints), 2)])
    if expected.shape != actual_k.shape or not np.allclose(expected, actual_k, atol=tolerance, rtol=0):
        raise ValueError('KPOINTS segments do not exactly reproduce the stored EIGENVAL k sequence; combined meshes and collapsed endpoints are unsupported.')
    segments, ticks, fit_segments = [], [], []
    for i in range(0, len(endpoints), 2):
        start = i//2*points
        stop = start+points
        first, last = endpoints[i], endpoints[i+1]
        if np.linalg.norm((np.asarray(last[0])-first[0]) @ (2*np.pi*np.linalg.inv(lattice).T)) <= 1e-12:
            raise ValueError('Zero-length KPOINTS segment.')
        segments.append([start+1, stop])
        for index, point in [(start+1, first), (stop, last)]:
            if point[1]:
                ticks.append(dict(index_1based=index, label=point[1]))
        fit_segments.append(dict(start=start, stop=stop, start_fractional=first[0],
                                 end_fractional=last[0], start_label=first[1], end_label=last[1]))
    geometry = dict(kcoords_type='fractional', reciprocal_lattice_2pi_per_angstrom=(2*np.pi*np.linalg.inv(lattice).T).tolist(),
                    segments_1based=segments, ticks=ticks,
                    source_kpoints=str(Path(path).resolve()), source_kpoints_sha256=sha256(path))
    fit = dict(coordinate_system='fractional_reciprocal', segments=fit_segments,
               source_kpoints=geometry['source_kpoints'], source_kpoints_sha256=geometry['source_kpoints_sha256'])
    return geometry, fit


def convert(args):
    out = Path(args.out)
    if out.exists() or (args.fit_path_out and Path(args.fit_path_out).exists()):
        raise ValueError('Output already exists; use a new revision path.')
    if args.fit_path_out and Path(args.fit_path_out).resolve() == out.resolve():
        raise ValueError('Cache and fit-path outputs must be distinct files.')
    if args.fit_path_out and not args.kpoints:
        raise ValueError('--fit-path-out requires --kpoints.')
    if not 0 < args.energy_tolerance <= 1e-3 or not 0 < args.k_tolerance <= 1e-4:
        raise ValueError('Tolerances must be positive and no greater than 1e-3 eV / 1e-4 fractional k.')
    paths = dict(poscar=Path(args.poscar), eigenval=Path(args.eigenval), procar=Path(args.procar))
    if args.kpoints:
        paths['kpoints'] = Path(args.kpoints)
    stat_before = {key: (path.stat().st_size, path.stat().st_mtime_ns) for key, path in paths.items()}
    pos = read_poscar(paths['poscar'])
    eigen = read_eigenval(paths['eigenval'])
    nk, nb = eigen['energies'].shape
    ni = sum(pos['counts'])
    if eigen['nions'] != ni:
        raise ValueError('EIGENVAL ion count disagrees with POSCAR.')
    pro = read_procar(paths['procar'], nk, nb, ni)
    if args.spin_mode and args.spin_mode != pro['spin_mode']:
        raise ValueError('Requested spin mode disagrees with actual PROCAR table layout.')
    if args.sampling == 'band_path' and not args.kpoints:
        raise ValueError('band_path requires explicit line-mode --kpoints verification.')
    if args.kpoints and args.sampling == 'wannier_mesh':
        raise ValueError('A verified line path cannot be labelled wannier_mesh.')
    tolerances = dict(energies=args.energy_tolerance, occupations=1e-5, kcoords=args.k_tolerance, kweights=1e-7)
    occupation_factor = 1.0
    if pro['spin_mode'] == 'scalar' and np.max(np.abs(eigen['occupations']-pro['occupations'])) > tolerances['occupations']:
        # In some VASP scalar outputs EIGENVAL prints occupations per spin
        # (0..1), while PROCAR prints the spin-degenerate total (0..2).
        if np.max(np.abs(eigen['occupations']-pro['occupations']/2.0)) <= tolerances['occupations']:
            occupation_factor = 2.0
    mismatch = {}
    for key, tolerance in tolerances.items():
        compared_procar = pro[key]/occupation_factor if key == 'occupations' else pro[key]
        mismatch[key] = float(np.max(np.abs(eigen[key]-compared_procar)))
        if mismatch[key] > tolerance:
            raise ValueError(f'EIGENVAL/PROCAR {key} mismatch: {mismatch[key]:g} > {tolerance:g}.')
    ef, ef_source, ef_record = read_ef(args.scf_outcar, args.ef_json, pro['spin_mode'])
    geometry, fit = (line_geometry(args.kpoints, pos['lattice'], eigen['kcoords'], args.k_tolerance)
                     if args.kpoints else (None, None))
    source = {key: dict(path=str(path.resolve()), sha256=sha256(path), bytes=path.stat().st_size) for key, path in paths.items()}
    if any((path.stat().st_size, path.stat().st_mtime_ns) != stat_before[key] for key, path in paths.items()):
        raise ValueError('An input changed during conversion; wait for the calculation to finish.')
    meta = dict(format='wannier-charge-cache-v1', species=pos['species'], counts=pos['counts'], columns=pro['columns'],
                ef=ef, ef_source=ef_source, ef_provenance=ef_record, procar_sha256=source['procar']['sha256'],
                source_files=source, spin_mode=pro['spin_mode'], nk=nk, nb=nb, ni=ni, channels=1,
                sampling='band_path' if geometry else args.sampling,
                nelect=eigen['nelect'], ion_species_in_poscar_order=pos['ion_species'],
                fractional_positions=pos['fractional_positions'].tolist(),
                source_alignment_max_difference=mismatch,
                procar_occupation_to_eigenval_factor=occupation_factor,
                max_total_rounding_difference=float(np.max(np.abs(pro['raw'].sum(axis=(-1,-2))-pro['printed_total']))),
                inferred_single_ion_tot_rows=pro['inferred_single_ion_tot_rows'],
                normalization='none; charge table only, all printed orbital weights retained; scalar occupation factor affects validation only',
                atom_order_scope='sequential PROCAR ion indices assigned to this explicitly supplied POSCAR; PROCAR alone cannot authenticate species provenance',
                mesh_scope='stored k coordinates and weights only; no regular mesh dimensions inferred from NKPTS')
    payload = dict(metadata=np.array(json.dumps(meta, allow_nan=False)), raw=pro['raw'][None],
                   printed_total=pro['printed_total'][None], **{k:eigen[k][None] for k in ['energies', 'occupations', 'kcoords', 'kweights']})
    if geometry:
        payload['path_geometry'] = np.array(json.dumps(geometry, allow_nan=False))
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open('xb') as stream:
        np.savez_compressed(stream, **payload)
    if fit:
        fit.update(cache_sha256=sha256(out), procar_sha256=source['procar']['sha256'])
        if args.fit_path_out:
            target = Path(args.fit_path_out)
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open('x') as stream:
                json.dump(fit, stream, indent=2, allow_nan=False)
                stream.write('\n')
    return dict(cache=str(out.resolve()), cache_sha256=sha256(out), nkpoints=nk, nbands=nb,
                spin_mode=pro['spin_mode'], path_geometry=geometry is not None,
                fit_path=str(Path(args.fit_path_out).resolve()) if args.fit_path_out else None)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ['poscar', 'eigenval', 'procar', 'out']:
        parser.add_argument('--'+name, required=True)
    ef = parser.add_mutually_exclusive_group(required=True)
    ef.add_argument('--scf-outcar')
    ef.add_argument('--ef-json')
    parser.add_argument('--kpoints', help='Optional reciprocal Line-mode KPOINTS from this calculation.')
    parser.add_argument('--fit-path-out', help='Optional segment JSON for plot_fit_review; requires --kpoints.')
    parser.add_argument('--spin-mode', choices=['scalar', 'spinor'], help='Optional expected mode, checked against actual PROCAR tables.')
    parser.add_argument('--sampling', choices=['band_path', 'wannier_mesh', 'sampled_kpoints'], default='sampled_kpoints',
                        help='Caller-declared purpose; wannier_mesh still needs explicit mesh validation by the workflow.')
    parser.add_argument('--energy-tolerance', type=float, default=2e-5)
    parser.add_argument('--k-tolerance', type=float, default=2e-7)
    args = parser.parse_args()
    try:
        print(json.dumps(convert(args), indent=2, allow_nan=False))
    except (ValueError, OSError, KeyError, IndexError, TypeError) as exc:
        parser.exit(2, f'error: {exc}\n')


if __name__ == '__main__':
    main()
