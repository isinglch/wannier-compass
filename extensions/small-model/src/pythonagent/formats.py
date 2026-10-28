"""Scientific file formats and energy interpolation; no plan selection policy."""

from pathlib import Path
import re

import numpy as np
from scipy.optimize import linear_sum_assignment


def eigenval(path):
    with Path(path).open() as stream:
        header = [stream.readline() for _ in range(6)]
        if any(not x for x in header) or int(header[0].split()[-1]) != 1:
            raise ValueError("Require a complete single-spectrum EIGENVAL")
        _, nk, nb = header[5].split()
        nk, nb = int(nk), int(nb)
        if min(nk, nb) < 1:
            raise ValueError("Invalid EIGENVAL dimensions")
        rows = (s.split() for s in stream if s.strip())
        coords = []
        energies = []

        def read_row():
            row = next(rows, None)
            if row is None:
                raise ValueError("Truncated EIGENVAL")
            return row

        for k in range(nk):
            row = read_row()
            if len(row) != 4:
                raise ValueError("Malformed EIGENVAL k row")
            coords.append([float(v) for v in row[:3]])
            values = []
            for b in range(nb):
                row = read_row()
                if len(row) != 3 or int(row[0]) != b + 1:
                    raise ValueError("Malformed EIGENVAL band row")
                values.append(float(row[1]))
            energies.append(values)
        if next(rows, None) is not None:
            raise ValueError("Unexpected EIGENVAL tail")
    e, k = np.asarray(energies), np.asarray(coords)
    if not np.isfinite(e).all() or not np.isfinite(k).all():
        raise ValueError("Nonfinite EIGENVAL")
    return e, k


def incar(path):
    text = Path(path).read_text()
    if re.search(r"\bWANNIER90_WIN\s*=", text, re.I):
        raise ValueError(
            "Base INCAR must not contain an inherited WANNIER90_WIN proposal"
        )
    values = {}
    for line in text.splitlines():
        line = re.split(r"[!#]", line, 1)[0]
        for part in line.split(";"):
            if not part.strip():
                continue
            if "=" not in part:
                raise ValueError("Unrecognized base INCAR line")
            key, value = part.split("=", 1)
            key = key.strip().upper()
            if key in values:
                raise ValueError("Duplicate INCAR tag: " + key)
            values[key] = value.strip()
    return values


def win_settings(path):
    scalars = {}
    blocks = {}
    active = None
    for line in Path(path).read_text().splitlines():
        line = re.split(r"[!#]", line, 1)[0].strip()
        if not line:
            continue
        if line.lower().startswith("begin "):
            active = line.split(None, 1)[1].strip().lower()
            if active in blocks:
                raise ValueError("Duplicate win block")
            blocks[active] = []
        elif line.lower().startswith("end "):
            active = None
        elif active:
            blocks[active].append(line)
        elif "=" in line or ":" in line:
            key, value = re.split(r"[=:]", line, 1)
            key = key.strip().lower()
            if key in scalars:
                raise ValueError("Duplicate win scalar: " + key)
            scalars[key] = value.strip()
    if active:
        raise ValueError("Unclosed win block")
    return scalars, blocks


def _translated_hamiltonian(path, vectors, h, deg):
    """Wannier90 ws_write_vec stores translations to ADD to each HR R.

    The Fourier weight is 1/(ndegen(R)*wdist_ndeg(m,n,R)), as in
    Wannier90 3.1 postw90_common.F90. Never drop an incomplete translation file.
    """
    result = {}
    lookup = {r: i for i, r in enumerate(vectors)}
    seen = set()
    nw = h.shape[1]
    with path.open() as stream:
        header = stream.readline().lower()
        inactive = "use_ws_distance=.false." in header
        if not inactive and "use_ws_distance=.true." not in header:
            raise ValueError("Unrecognized WS translation header")
        for line in stream:
            row = list(map(int, line.split()))
            if len(row) != 5:
                raise ValueError("Malformed WS translation key")
            vector, m, n = tuple(row[:3]), row[3] - 1, row[4] - 1
            key = (vector, m, n)
            if (
                vector not in lookup
                or not 0 <= m < nw
                or not 0 <= n < nw
                or key in seen
            ):
                raise ValueError("Invalid or duplicate WS translation key")
            seen.add(key)
            count = int(stream.readline())
            if count < 1:
                raise ValueError("Invalid WS translation multiplicity")
            r = lookup[vector]
            translations = set()
            for _ in range(count):
                translation = tuple(map(int, stream.readline().split()))
                if len(translation) != 3 or translation in translations:
                    raise ValueError("Incomplete or duplicate WS translations")
                translations.add(translation)
                if inactive and (count != 1 or translation != (0, 0, 0)):
                    raise ValueError("Inactive WS translations must be zero")
                effective = tuple(a + b for a, b in zip(vector, translation))
                matrix = result.setdefault(effective, np.zeros((nw, nw), complex))
                matrix[m, n] += h[r, m, n] / (deg[r] * count)
    if len(seen) != len(vectors) * nw * nw:
        raise ValueError("Incomplete WS translation matrix")
    return np.asarray(list(result)), np.asarray(list(result.values()))


def hr_energies(path, kcoords, *, require_ws=False):
    path = Path(path)
    with Path(path).open() as stream:
        stream.readline()
        nw = int(stream.readline())
        nr = int(stream.readline())
        deg = []
        if min(nw, nr) < 1:
            raise ValueError("Invalid HR dimensions")
        while len(deg) < nr:
            line = stream.readline()
            if not line:
                raise ValueError("Truncated HR degeneracies")
            deg.extend(map(int, line.split()))
        if len(deg) != nr or min(deg) < 1:
            raise ValueError("Invalid HR degeneracies")
        h = np.zeros((nr, nw, nw), complex)
        vectors = []
        for r in range(nr):
            vector = None
            seen = set()
            for _ in range(nw * nw):
                row = stream.readline().split()
                if len(row) != 7:
                    raise ValueError("Truncated HR matrix")
                current = tuple(map(int, row[:3]))
                m, n = int(row[3]) - 1, int(row[4]) - 1
                if (
                    not 0 <= m < nw
                    or not 0 <= n < nw
                    or (m, n) in seen
                    or vector is not None
                    and current != vector
                ):
                    raise ValueError("Invalid HR matrix indices")
                vector = current
                seen.add((m, n))
                h[r, m, n] = float(row[5]) + 1j * float(row[6])
            vectors.append(vector)
        if stream.read().strip():
            raise ValueError("Unexpected HR tail")
    if not np.isfinite(h).all():
        raise ValueError("Nonfinite HR matrix")
    if len(set(vectors)) != nr:
        raise ValueError("Duplicate HR lattice vector")
    ws_path = path.with_name(path.name.removesuffix("_hr.dat") + "_wsvec.dat")
    if ws_path.is_file():
        vectors, h = _translated_hamiltonian(ws_path, vectors, h, deg)
    else:
        if require_ws:
            raise ValueError("Missing required Wannier90 WS translation file")
        h = h / np.asarray(deg)[:, None, None]
    ks = np.asarray(kcoords)
    if ks.ndim != 2 or ks.shape[1] != 3 or not np.isfinite(ks).all():
        raise ValueError("Invalid interpolation k coordinates")
    phase = np.exp(2j * np.pi * ks @ np.asarray(vectors).T)
    ham = np.einsum("kr,rmn->kmn", phase, h)
    defect = float(np.max(np.abs(ham - ham.conj().transpose(0, 2, 1))))
    if defect > 1e-6:
        raise ValueError("Interpolated Hamiltonian is not numerically Hermitian")
    return np.linalg.eigvalsh(ham)


def match_target(dft, wan, ranges):
    bands = sorted({b - 1 for lo, hi in ranges for b in range(lo, hi + 1)})
    rows = []
    errors = []
    missing = 0
    for k, (de, we) in enumerate(zip(dft, wan), 1):
        target = de[bands]
        ri, wi = linear_sum_assignment(np.abs(target[:, None] - we[None, :]))
        pair = []
        for a, b in zip(ri, wi):
            error = float(we[b] - target[a])
            errors.append(error)
            pair.append(
                {
                    "dft_band_1based": bands[a] + 1,
                    "wannier_band_1based": int(b) + 1,
                    "dft_eV": float(target[a]),
                    "wannier_eV": float(we[b]),
                    "error_eV": error,
                }
            )
        missing_bands = [bands[i] + 1 for i in range(len(bands)) if i not in set(ri)]
        missing += len(missing_bands)
        rows.append(
            {"k_index_1based": k, "pairs": pair, "unmatched_dft_bands": missing_bands}
        )
    absolute = np.abs(errors)
    return {
        "counts": {
            "expected_states": len(dft) * len(bands),
            "matched_states": len(errors),
            "missing_states": missing,
        },
        "matched_error_metrics": {
            "mae_eV": float(absolute.mean()),
            "max_abs_eV": float(absolute.max()),
        }
        if len(errors)
        else None,
        "per_k": rows,
        "method": "Independent per-k minimum absolute energy assignment; no cutoff or fitted shift. Not branch identity.",
    }
