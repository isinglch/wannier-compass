"""Read-only replay of real Cu artifacts with the new independent parsers/checks.

No model request, VASP run or modification of the source case is performed.
The input plan is reconstructed ONLY as a historical validation fixture, never
treated as a newly model-authored or reviewer-approved scientific proposal.
"""

import argparse
import json
from pathlib import Path
import tempfile

import numpy as np

from pythonagent.evidence import Evidence
from pythonagent.execution import NativeExecutor
from pythonagent.formats import eigenval, hr_energies, win_settings, match_target
from pythonagent.journal import Journal
from pythonagent.state import immutable_json
from pythonagent.validation import validate_plan


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--case-dir", required=True)
    parser.add_argument("--trial-dir", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    trial = Path(args.trial_dir).resolve()
    with tempfile.TemporaryDirectory(prefix="pythonagent-replay-") as temporary:
        evidence = Evidence(args.case_dir, temporary)
        selection = json.loads((trial / "records/selection/selection.json").read_text())
        scalars, _ = win_settings(trial / "wannier/wannier90.win")
        plan = {
            "schema_version": "model-plan-v1",
            "objective": "Historical Cu validation fixture; no new scientific decision",
            "input_revision": evidence.revision,
            "basis": selection["basis"],
            "num_wann": selection["num_wann"],
            "target_band_ranges_1based": selection["target_band_ranges_1based"],
            "excluded_bands_1based": selection["band_selection"][
                "exclude_bands_1based"
            ],
            "windows_relative_eV": {
                key: selection["windows_relative_eV"][key]
                for key in ("dis_eV", "frozen_eV")
            },
            "solver": {
                "num_iter": int(scalars["num_iter"]),
                "dis_num_iter": int(scalars["dis_num_iter"]),
                "dis_conv_tol": float(scalars["dis_conv_tol"]),
                "dis_conv_window": int(scalars["dis_conv_window"]),
            },
            "reason": "Replay an archived executed record for format/math verification only.",
            "evidence_refs": [evidence.source],
        }
        validation = validate_plan(plan, evidence)
        if not validation["valid"]:
            raise ValueError(validation["errors"])
        _, coords = eigenval(trial / "interface/EIGENVAL")
        executor = NativeExecutor({}, Journal(temporary))
        actual = executor.verify_interface(plan, trial / "interface", coords, evidence)
        with np.load(evidence.cache_path, allow_pickle=False) as data:
            kcoords = np.asarray(data["kcoords"])[0]
        energies = hr_energies(trial / "wannier/wannier90_hr.dat", kcoords)
        original = np.loadtxt(trial / "comparison/wannier90_geninterp.dat")[
            :, -1
        ].reshape(energies.shape)
        delta = float(np.max(np.abs(energies - original)))
        # HR stores real/imaginary matrix elements at 6 decimal places. Weyl's
        # eigenvalue perturbation bound from that published text precision:
        # ||delta H||_2 <= nw * sum_R(1/ndegen_R) * sqrt(2) * 0.5e-6.
        with (trial / "wannier/wannier90_hr.dat").open() as stream:
            stream.readline()
            nw = int(stream.readline())
            nr = int(stream.readline())
            deg = []
            while len(deg) < nr:
                deg.extend(map(int, stream.readline().split()))
        precision_bound = float(
            nw * np.sum(1 / np.asarray(deg)) * np.sqrt(2) * 0.5e-6 + 0.5e-9
        )
        if delta > precision_bound:
            raise ValueError(
                "HR interpolation exceeds the text-precision bound: " + str(delta)
            )
        metrics = match_target(
            evidence.energy,
            energies - evidence.meta["ef"],
            plan["target_band_ranges_1based"],
        )
        # Exercise input materialization separately, using existing source hashes.
        prepared = Path(temporary) / "prepared"
        prepared.mkdir()
        immutable_json(prepared / "approved-plan.json", plan)
        executor.config = {"mesh_grid": [8, 8, 8]}
        executor.prepare(plan, prepared, evidence)
        report = {
            "scope": "Read-only archived Cu replay; no model/DFT/Wannier execution and no new scientific approval.",
            "case": str(evidence.case),
            "trial": str(trial),
            "input_revision": evidence.revision,
            "plan_validation": validation,
            "actual_input_validation": actual,
            "hr_vs_postw90_max_abs_eV": delta,
            "hr_text_precision_eigenvalue_bound_eV": precision_bound,
            "target_metrics": {k: v for k, v in metrics.items() if k != "per_k"},
            "native_input_materialization": "passed; temporary input directory removed after this replay",
        }
        immutable_json(Path(args.output), report)
        print(
            json.dumps(
                {
                    "output": str(Path(args.output).resolve()),
                    "hr_vs_postw90_max_abs_eV": delta,
                    "target_counts": metrics["counts"],
                },
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    main()
