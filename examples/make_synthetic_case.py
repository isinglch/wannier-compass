#!/usr/bin/env python3
"""Create a tiny artificial projected band-path cache for API smoke tests.

The numbers are invented, have no material interpretation, and contain no
DFT calculation, POTCAR, or historical Wannier choice.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", required=True)
    args = parser.parse_args()
    root = Path(args.out_dir)
    if root.exists():
        raise ValueError("Use a new synthetic output directory.")
    band = root / "02_band"
    band.mkdir(parents=True)
    nk, nb, ni = 6, 8, 2
    k = np.linspace(0.0, 0.5, nk)
    base = np.array([-7.0, -6.0, -3.0, -1.0, 0.5, 1.5, 3.0, 5.0])
    energies = (base[None, :] + 0.08 * np.sin(2 * np.pi * k[:, None])).reshape(1, nk, nb)
    occupations = np.zeros_like(energies)
    occupations[:, :, :4] = 1.0
    raw = np.zeros((1, nk, nb, ni, 9))
    raw[:, :, :2, :, 0] = 0.4
    raw[:, :, 2:6, :, 1:4] = 0.12
    raw[:, :, 6:, :, 4:9] = 0.025
    kcoords = np.zeros((1, nk, 3))
    kcoords[0, :, 0] = k
    fake_kpoints_sha = "0" * 64
    meta = {
        "format": "synthetic-non-DFT-smoke-fixture",
        "species": ["Si"], "counts": [2], "columns":
            ["s", "py", "pz", "px", "dxy", "dyz", "dz2", "dxz", "x2-y2"],
        "ef": 0.0, "ef_source": "invented", "sampling": "band_path",
        "spin_mode": "scalar", "source_files": {"kpoints": {"sha256": fake_kpoints_sha}},
        "procar_sha256": "0" * 64,
    }
    geometry = {
        "kcoords_type": "fractional",
        "reciprocal_lattice_2pi_per_angstrom": [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
        "segments_1based": [[1, nk]], "source_kpoints_sha256": fake_kpoints_sha,
        "ticks": [{"index_1based": 1, "label": "G"}, {"index_1based": nk, "label": "X"}],
    }
    cache = band / "charge.npz"
    np.savez_compressed(cache, metadata=json.dumps(meta), energies=energies,
                        occupations=occupations, raw=raw, kcoords=kcoords,
                        path_geometry=json.dumps(geometry))
    (root / "README.md").write_text(
        "# Synthetic API fixture\n\nInvented numbers only. Use for parser, MCP and plotting smoke tests; "
        "never for scientific selection or acceptance.\n"
    )
    print(json.dumps({"cache": str(cache.resolve()),
                      "sha256": hashlib.sha256(cache.read_bytes()).hexdigest()}))


if __name__ == "__main__":
    main()
