import argparse

import numpy as np

from pythonagent.cache import add_arguments, convert
from pythonagent.evidence import Evidence
from pythonagent.tools import ToolBox
from pythonagent.journal import Journal
from pythonagent.plans import PlanStore
from support import CaseTest


class CacheTests(CaseTest):
    def inputs(self):
        folder = self.root / "source"
        folder.mkdir()
        (folder / "POSCAR").write_text(
            "Cu fixture\n1\n1 0 0\n0 1 0\n0 0 1\nCu\n1\nDirect\n0 0 0\n"
        )
        (folder / "KPOINTS").write_text(
            "Fixture path\n2\nLine-mode\nReciprocal\n0 0 0 ! G\n0.5 0 0 ! X\n"
        )
        (folder / "OUTCAR").write_text(
            "ICHARG = 2 NSW = 0 ISPIN = 1 LSORBIT = F LNONCOLLINEAR = F\nE-fermi : 5.0\nGeneral timing and accounting\n"
        )
        eig = ["1 1 1 1", "a", "a", "a", "a", "1 2 2"]
        pro = ["PROCAR fixture", "# of k-points: 2 # of bands: 2 # of ions: 1"]
        for k, x in enumerate((0, 0.5), 1):
            eig.append(f"{x} 0 0 1")
            pro.append(f"k-point {k} : {x} 0 0 weight = 1")
            for b, energy in enumerate((3, 6), 1):
                eig.append(f"{b} {energy} 0")
                pro.extend(
                    [
                        f"band {b} # energy {energy} # occ. 0",
                        "ion s py pz px dxy dyz dz2 dxz dx2 tot",
                        "1 0.1 0.2 0 0 0 0 0 0 0 0.3",
                        "tot 0.1 0.2 0 0 0 0 0 0 0 0.3",
                    ]
                )
        (folder / "EIGENVAL").write_text("\n".join(eig) + "\n")
        (folder / "PROCAR").write_text("\n".join(pro) + "\n")
        args = add_arguments(argparse.ArgumentParser()).parse_args(
            [
                "--poscar",
                str(folder / "POSCAR"),
                "--eigenval",
                str(folder / "EIGENVAL"),
                "--procar",
                str(folder / "PROCAR"),
                "--kpoints",
                str(folder / "KPOINTS"),
                "--scf-outcar",
                str(folder / "OUTCAR"),
                "--sampling",
                "band_path",
                "--out",
                str(self.root / "prepared/02_band/charge.npz"),
            ]
        )
        return folder, args

    def test_independent_cache_retains_raw_values_and_source_identity(self):
        folder, args = self.inputs()
        result = convert(args)
        evidence = Evidence(self.root / "prepared", self.run)
        np.testing.assert_allclose(evidence.energy, [[-2, 1], [-2, 1]])
        np.testing.assert_allclose(evidence.groups["Cu:p"], 0.2)
        self.assertEqual(evidence.meta["normalization"].split(";")[0], "none")
        self.assertEqual(result["nkpoints"], 2)
        with self.assertRaisesRegex(ValueError, "already exists"):
            convert(args)

    def test_conflicting_outputs_are_rejected_without_creating_cache(self):
        folder, args = self.inputs()
        path = folder / "PROCAR"
        path.write_text(path.read_text().replace("energy 3", "energy 4"))
        with self.assertRaisesRegex(ValueError, "energies mismatch"):
            convert(args)
        self.assertFalse((self.root / "prepared/02_band/charge.npz").exists())

    def test_large_text_can_be_read_in_bounded_pages(self):
        path = self.run / "trials/trial_001/interface/vasp.stdout"
        path.parent.mkdir(parents=True)
        path.write_text("line\n" * 420000 + "final marker\n")
        journal = Journal(self.run)
        tools = ToolBox(
            self.evidence, PlanStore(self.run, self.evidence, journal), journal
        )
        result = tools.call(
            "evaluator",
            "read_text",
            {
                "path": "run/trials/trial_001/interface/vasp.stdout",
                "offset": 420000,
                "limit": 2,
            },
        )
        self.assertEqual(result["text"], "final marker\n")
        self.assertEqual(result["total_lines"], 420001)
        beyond = tools.call(
            "evaluator",
            "read_text",
            {
                "path": "run/trials/trial_001/interface/vasp.stdout",
                "offset": 500000,
                "limit": 2,
            },
        )
        self.assertEqual(beyond["total_lines"], 420001)
        self.assertEqual(beyond["text"], "")
