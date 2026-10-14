"""Synthetic VASP dialect regression; no real material data is bundled."""

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import numpy as np


PARSER = Path(__file__).resolve().parents[1] / "wannier_mcp/workflow/tools/vasp_to_cache.py"


class SingleIonProcar(unittest.TestCase):
    def test_missing_redundant_tot_and_scalar_occupation_convention(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "POSCAR").write_text("Invented single atom\n1\n1 0 0\n0 1 0\n0 0 1\nH\n1\nDirect\n0 0 0\n")
            (root / "EIGENVAL").write_text(
                "1 1 1 1\nheader\nheader\nheader\nheader\n2 1 2\n"
                "0 0 0 1\n1 -1.0 1.0\n2 1.0 0.0\n"
            )
            (root / "PROCAR").write_text(
                "PROCAR lm decomposed\n# of k-points: 1 # of bands: 2 # of ions: 1\n"
                "k-point 1 : 0 0 0 weight = 1\n"
                "band 1 # energy -1.0 # occ. 2.0\n"
                "ion s py pz px dxy dyz dz2 dxz x2-y2 tot\n"
                "1 0.6 0 0 0 0 0 0 0 0 0.6\n"
                "band 2 # energy 1.0 # occ. 0.0\n"
                "ion s py pz px dxy dyz dz2 dxz x2-y2 tot\n"
                "1 0.1 0 0 0 0 0 0 0 0 0.1\n"
            )
            source = root / "ef_source.txt"
            source.write_text("Invented reference for parser test only.\n")
            (root / "ef.json").write_text(json.dumps({
                "kind": "reviewed_scf_reference", "ef_eV": 0.0,
                "source_path": str(source),
                "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                "spin_mode": "scalar", "reason": "Synthetic parser regression only",
            }))
            result = subprocess.run([
                sys.executable, str(PARSER), "--poscar", str(root / "POSCAR"),
                "--eigenval", str(root / "EIGENVAL"), "--procar", str(root / "PROCAR"),
                "--ef-json", str(root / "ef.json"), "--out", str(root / "charge.npz"),
            ], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            with np.load(root / "charge.npz") as cache:
                metadata = json.loads(cache["metadata"].item())
                self.assertEqual(metadata["inferred_single_ion_tot_rows"], 2)
                self.assertEqual(metadata["procar_occupation_to_eigenval_factor"], 2.0)
                np.testing.assert_allclose(cache["occupations"][0, 0], [1.0, 0.0])


if __name__ == "__main__":
    unittest.main()
