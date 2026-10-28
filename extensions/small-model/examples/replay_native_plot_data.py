"""Verify the native comparison-data path with explicitly supplied archived outputs.

No VASP, Wannier90 or model process is launched. Real native input materialization,
read-back checks, interpolation, diagnostics and comparison.npz writing do execute.
Archive outputs stand in for the two commands; this is regression verification,
not a new scientific trial or an authorization/approval record.
"""

import argparse
import json
from pathlib import Path
import shutil
import tempfile

from pythonagent.evidence import Evidence
from pythonagent.execution import NativeExecutor
from pythonagent.formats import win_settings
from pythonagent.journal import Journal
from pythonagent.state import file_sha, immutable_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case-dir", required=True)
    parser.add_argument("--archived-trial", required=True)
    parser.add_argument(
        "--plan",
        required=True,
        help="Explicit historical model-plan-v1 fixture, never generated here",
    )
    parser.add_argument("--out-dir", required=True)
    args = parser.parse_args()
    output = Path(args.out_dir).resolve()
    output.mkdir(parents=True, exist_ok=False)
    archive = Path(args.archived_trial).resolve()
    plan = json.loads(Path(args.plan).read_text())
    scalars, _ = win_settings(archive / "interface/wannier90.win")

    class Replay(NativeExecutor):
        def command(self, argv, directory, label):
            # These are file-based fixtures, never subprocess commands.
            if label == "vasp":
                source = archive / "interface"
                names = (
                    "OUTCAR",
                    "EIGENVAL",
                    "wannier90.win",
                    "wannier90.eig",
                    "wannier90.amn",
                    "wannier90.mmn",
                )
            elif label == "wannier90":
                source = archive / "wannier"
                names = ("wannier90.wout", "wannier90_hr.dat", "wannier90_wsvec.dat")
            else:
                raise ValueError("Unexpected replay command")
            for name in names:
                shutil.copyfile(source / name, directory / name)
            self.journal.record(
                "archived_command_fixture",
                {
                    "label": label,
                    "no_command_executed": True,
                    "source_files": {
                        str(source / name): file_sha(source / name) for name in names
                    },
                },
            )

    with tempfile.TemporaryDirectory(prefix="pythonagent-native-plot-replay-") as temp:
        trial = Path(temp)
        evidence = Evidence(args.case_dir, trial)
        journal = Journal(trial)
        immutable_json(trial / "approved-plan.json", plan)
        executor = Replay(
            {
                "mesh_grid": list(map(int, scalars["mp_grid"].split())),
                "vasp": ["ARCHIVED_FIXTURE_NO_COMMAND"],
                "wannier90": ["ARCHIVED_FIXTURE_NO_COMMAND"],
            },
            journal,
        )
        result = executor.execute(
            plan, trial / "approved-plan.json", trial, evidence, plan
        )
        shutil.copyfile(trial / "comparison.npz", output / "comparison.npz")
        shutil.copyfile(trial / "journal.jsonl", output / "replay-journal.jsonl")
        immutable_json(
            output / "report.json",
            {
                "scope": __doc__,
                "source_trial": str(archive),
                "plan_sha256": file_sha(args.plan),
                "comparison_sha256": file_sha(output / "comparison.npz"),
                "status": result["status"],
                "disentanglement_converged": result["disentanglement_converged"],
                "metrics": result["metrics"],
                "record_note": "Temporary native inputs/outputs removed; journal source hashes and comparison retained.",
            },
        )
    print(
        json.dumps(
            {
                "output": str(output),
                "status": result["status"],
                "no_new_scientific_execution": True,
            }
        )
    )


if __name__ == "__main__":
    main()
