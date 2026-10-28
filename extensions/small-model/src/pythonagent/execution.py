"""Execute the exact approved plan. No scientific heuristics or legacy decision engine."""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import time

import numpy as np

from .formats import eigenval, hr_energies, incar, match_target, win_settings
from .state import file_sha, immutable_json
from .validation import execution_count_errors
from .plotting import save_comparison
from .contracts import require_disentanglement_only
from .results import disentanglement_state, METRIC_CONTRACT
from .artifacts import ExecutionFailure, InterfaceMismatch


class ExecutionConstraint(ValueError):
    def __init__(self, issues, *, facts=None):
        super().__init__("Actual execution inputs violate a published constraint")
        self.issues = issues
        self.facts = facts


class Runner:
    def __init__(self, config, journal):
        self.config, self.journal = config, journal

    def command(self, argv, directory, label):
        if (
            not isinstance(argv, list)
            or not argv
            or any(not isinstance(v, str) for v in argv)
        ):
            raise ValueError("Operator commands must be nonempty argv arrays")
        start = time.monotonic()
        recorder = getattr(self, "fact_recorder", None)
        if recorder is not None:
            recorder.command_started(label, argv, directory)
        self.journal.record(
            "command_start", {"label": label, "argv": argv, "cwd": str(directory)}
        )
        with (
            (directory / (label + ".stdout")).open("w") as out,
            (directory / (label + ".stderr")).open("w") as err,
        ):
            process = subprocess.Popen(
                argv, cwd=directory, stdout=out, stderr=err, start_new_session=True
            )
            try:
                returncode = process.wait(
                    timeout=self.config.get("timeout_seconds", 3600)
                )
            except BaseException:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
                if recorder is not None:
                    recorder.command_finished(
                        label,
                        process.returncode,
                        time.monotonic() - start,
                        directory,
                        interrupted=True,
                    )
                self.journal.record(
                    "command_interrupted",
                    {"label": label, "seconds": time.monotonic() - start},
                )
                raise RuntimeError(
                    label + " was interrupted or exceeded its configured time budget"
                ) from None
        self.journal.record(
            "command_finished",
            {
                "label": label,
                "returncode": returncode,
                "seconds": time.monotonic() - start,
            },
        )
        if recorder is not None:
            recorder.command_finished(
                label, returncode, time.monotonic() - start, directory
            )
        if returncode:
            raise ExecutionFailure(
                f"{label} exited {returncode}; inspect run artifacts",
                stage=label,
                returncode=returncode,
            )


class CommandExecutor(Runner):
    """Operator-registered pipeline receives plan/result paths through literal placeholders."""

    def capabilities(self):
        return {
            "adapter": "command",
            "commands": "Operator-owned argv arrays, shell=False",
            "result_contract": {
                "required": ["status", "observations"],
                "optional": ["metrics", "record_paths"],
            },
            "instruction": "Commands must read plan_file and emit result_file; they must not invent scientific defaults.",
        }

    def validate(self):
        if not self.config.get("commands"):
            raise ValueError("Configure execution.commands before starting models")
        for command in self.config["commands"]:
            if (
                not isinstance(command, list)
                or not command
                or any(not isinstance(v, str) for v in command)
            ):
                raise ValueError("Execution commands require argv arrays")
            if shutil.which(command[0]) is None:
                raise ValueError("Executable not found: " + command[0])

    def execute(self, plan, plan_file, trial, evidence, baseline):
        require_disentanglement_only(plan)
        result_path = trial / "result.external.json"
        replacements = {
            "plan_file": str(plan_file),
            "result_file": str(result_path),
            "trial_dir": str(trial),
            "case_dir": str(evidence.case),
        }
        for index, command in enumerate(self.config["commands"]):
            argv = []
            for value in command:
                for key, replacement in replacements.items():
                    value = value.replace("{" + key + "}", replacement)
                argv.append(value)
            self.command(argv, trial, "command-" + str(index + 1))
        if not result_path.is_file():
            raise RuntimeError("Command pipeline did not emit result.external.json")
        result = json.loads(result_path.read_text())
        if (
            not isinstance(result, dict)
            or not isinstance(result.get("status"), str)
            or not isinstance(result.get("observations"), list)
        ):
            raise ValueError("Command result needs status and observations")
        return result


class NativeExecutor(Runner):
    """One fixed-density VASP interface then Wannier90, using explicit planner windows."""

    def capabilities(self):
        return {
            "adapter": "vasp_wannier",
            "basis": "Whole angular shells; scalar ISPIN=1 or both-spin spinor",
            "exclusions": "Contiguous original-band prefix only; no occupation or global-gap policy",
            "windows": "Explicit planner values; shifted only by the supplied SCF EF for software input",
            "solver": "num_iter must be 0. Planner chooses dis_num_iter, dis_conv_tol and dis_conv_window.",
            "mesh_grid": self.config.get("mesh_grid"),
            "checks": "Actual input-grid state counts and matrix identity. Top margin is advisory only.",
            "band_counts": "VASP interface bands may differ from the source cache. Actual retained interface bands govern matrix and DIS dimensions; target comparisons stay on the approved source-cache population.",
            "additional_data_operations": [],
        }

    def validate(self):
        for key in ("vasp", "wannier90"):
            argv = self.config.get(key)
            if (
                not isinstance(argv, list)
                or not argv
                or any(not isinstance(v, str) for v in argv)
            ):
                raise ValueError(
                    "Configure execution." + key + " as an operator-owned argv array"
                )
            if shutil.which(argv[0]) is None:
                raise ValueError("Executable not found: " + argv[0])
        grid = self.config.get("mesh_grid")
        if (
            not isinstance(grid, list)
            or len(grid) != 3
            or any(type(v) is not int or v < 1 for v in grid)
        ):
            raise ValueError("Explicit positive integer execution.mesh_grid required")

    def prepare(self, plan, trial, evidence):
        require_disentanglement_only(plan)
        case = evidence.case
        band = case / "02_band"
        scf = case / "01_scf"
        for key, name in (
            ("procar", "PROCAR"),
            ("eigenval", "EIGENVAL"),
            ("poscar", "POSCAR"),
            ("kpoints", "KPOINTS"),
        ):
            expected = (evidence.meta.get("source_files", {}).get(key) or {}).get(
                "sha256"
            )
            if expected is None or expected != file_sha(band / name):
                raise ValueError(
                    "Cache does not identify the current band source: " + name
                )
        for name in ("POSCAR", "POTCAR"):
            if file_sha(band / name) != file_sha(scf / name):
                raise ValueError("Band/SCF source mismatch: " + name)
        scf_output = (scf / "OUTCAR").read_text()
        references = re.findall(r"E-fermi\s*:\s*([-+\d.Ee]+)", scf_output)
        if not references or not np.isclose(
            float(references[-1]), evidence.meta["ef"], atol=5e-5, rtol=0
        ):
            raise ValueError(
                "Cache SCF energy reference differs from current SCF OUTCAR"
            )
        if "General timing and accounting informations" not in scf_output:
            raise ValueError("SCF source did not finish normally")
        tags = incar(band / "INCAR")
        recorder = getattr(self, "fact_recorder", None)
        if recorder is not None:
            recorder.source_input(tags)
        scf_tags = incar(scf / "INCAR")
        for key in (
            "ENCUT",
            "GGA",
            "NELECT",
            "ISPIN",
            "LSORBIT",
            "LNONCOLLINEAR",
            "LDAU",
            "LHFCALC",
            "METAGGA",
        ):
            if tags.get(key) != scf_tags.get(key):
                raise ValueError("SCF/band physical setting mismatch: " + key)

        def boolean(key):
            return tags.get(key, "F").strip(".").upper() in ("TRUE", "T")

        spinor = boolean("LSORBIT") or boolean("LNONCOLLINEAR")
        if int(tags.get("ISPIN", "1")) != 1 or spinor != (
            evidence.meta["spin_mode"] == "spinor"
        ):
            raise ValueError("Native adapter/source spin representation mismatch")
        excluded = plan["excluded_bands_1based"]
        if sorted(excluded) != list(range(1, len(excluded) + 1)):
            raise ExecutionConstraint(
                [
                    {
                        "path": "/excluded_bands_1based",
                        "code": "adapter_mapping",
                        "message": "Native adapter supports a contiguous original-band prefix; choose a capable adapter for another mapping.",
                    }
                ]
            )
        interface = trial / "interface"
        interface.mkdir()
        for name, source in [
            ("POSCAR", band / "POSCAR"),
            ("POTCAR", band / "POTCAR"),
            ("CHGCAR", scf / "CHGCAR"),
        ]:
            shutil.copyfile(source, interface / name)
        grid = self.config["mesh_grid"]
        points = np.array(
            [
                (i / grid[0], j / grid[1], k / grid[2])
                for i in range(grid[0])
                for j in range(grid[1])
                for k in range(grid[2])
            ]
        )
        (interface / "KPOINTS").write_text(
            "Configured uniform interface mesh\n0\nGamma\n"
            + " ".join(map(str, grid))
            + "\n0 0 0\n"
        )
        scalars = {**plan["solver"], "write_hr": ".true.", "use_ws_distance": ".true."}
        ef = evidence.meta["ef"]
        for keys, values in [
            (("dis_win_min", "dis_win_max"), plan["windows_relative_eV"]["dis_eV"]),
            (
                ("dis_froz_min", "dis_froz_max"),
                plan["windows_relative_eV"]["frozen_eV"],
            ),
        ]:
            scalars.update(
                {key: format(value + ef, ".15g") for key, value in zip(keys, values)}
            )
        if excluded:
            scalars["exclude_bands"] = "1-" + str(len(excluded))
        payload = (
            "\n".join(f"{k} = {v}" for k, v in scalars.items())
            + "\n\nbegin projections\n"
            + "\n".join(plan["basis"])
            + "\nend projections\n"
        )
        original_tags = dict(tags)
        tags.pop("NPAR", None)
        # Interface mechanics preserve source physics and never choose windows/basis.
        tags.update(
            ICHARG="11",
            ISTART="0",
            ISYM="-1",
            NSW="0",
            IBRION="-1",
            NBANDS=str(evidence.energy.shape[1]),
            NUM_WANN=str(plan["num_wann"]),
            LWANNIER90=".TRUE.",
            LWANNIER90_RUN=".FALSE.",
            LWRITE_MMN_AMN=".TRUE.",
            LCHARG=".FALSE.",
            LWAVE=".FALSE.",
            NCORE="1",
            KPAR="1",
        )
        (interface / "INCAR").write_text(
            "\n".join(f"{k} = {v}" for k, v in tags.items())
            + '\nWANNIER90_WIN = "\n'
            + payload
            + '"\n'
        )
        recorder = getattr(self, "fact_recorder", None)
        if recorder is not None:
            recorder.materialized(original_tags, tags)
        immutable_json(
            trial / "input_binding.json",
            {
                "plan_sha256": file_sha(trial / "approved-plan.json"),
                "cache_sha256": evidence.revision,
                "input_files": {
                    p.name: file_sha(p) for p in interface.iterdir() if p.is_file()
                },
                "source_files": {
                    str(p.relative_to(case)): file_sha(p)
                    for p in (
                        band / "INCAR",
                        scf / "INCAR",
                        band / "POSCAR",
                        scf / "POSCAR",
                        band / "POTCAR",
                        scf / "POTCAR",
                        scf / "CHGCAR",
                        scf / "OUTCAR",
                    )
                },
            },
        )
        return interface, points

    @staticmethod
    def verify_binding(trial, evidence):
        binding = json.loads((trial / "input_binding.json").read_text())
        if binding["plan_sha256"] != file_sha(trial / "approved-plan.json"):
            raise ValueError("Execution plan binding changed")
        for name, expected in binding["input_files"].items():
            if file_sha(trial / "interface" / name) != expected:
                raise ValueError("Materialized execution input changed: " + name)
        for name, expected in binding["source_files"].items():
            if file_sha(evidence.case / name) != expected:
                raise ValueError("Source input changed during execution: " + name)

    def verify_interface(self, plan, interface, points, evidence):
        require_disentanglement_only(plan)
        energies, coords = eigenval(interface / "EIGENVAL")
        recorder = getattr(self, "fact_recorder", None)
        if recorder is not None:
            recorder.observed(energies.shape, interface / "EIGENVAL")

        def wrapped(values):
            return sorted(map(tuple, np.round(np.mod(values, 1), 7) % 1))

        if energies.shape[0] != len(points) or wrapped(coords) != wrapped(points):
            raise InterfaceMismatch(
                {
                    "expected_kpoint_count": len(points),
                    "actual_energy_shape": list(energies.shape),
                    "source_num_bands": int(evidence.energy.shape[1]),
                    "configured_mesh": self.config.get("mesh_grid"),
                    "kpoint_sets_match": wrapped(coords) == wrapped(points),
                    "files": [
                        "EIGENVAL",
                        "KPOINTS",
                        "wannier90.win",
                        "wannier90.amn",
                        "wannier90.mmn",
                    ],
                }
            )
        facts = evidence.window_diagnostics(
            plan, energies - evidence.meta["ef"], "actual_interface_grid"
        )
        nb = energies.shape[1] - len(plan["excluded_bands_1based"])
        nw = plan["num_wann"]
        facts.update(
            source_num_bands=int(evidence.energy.shape[1]),
            interface_num_bands=int(energies.shape[1]),
            retained_num_bands=int(nb),
            num_wann=nw,
        )
        if recorder is not None:
            recorder.counts(facts)
        errors = execution_count_errors(facts)
        if errors:
            raise ExecutionConstraint(errors, facts=facts)
        nk = len(points)
        scalars, blocks = win_settings(interface / "wannier90.win")
        if "num_iter" not in scalars or float(scalars["num_iter"]) != 0:
            raise ValueError("Generated win must explicitly have num_iter=0")
        spinors = scalars.get("spinors", ".false.").strip(".").lower()
        if spinors not in ("true", "false", "t", "f") or (spinors in ("true", "t")) != (
            evidence.meta["spin_mode"] == "spinor"
        ):
            raise ValueError("Generated win spin representation differs from source")
        for key, value in plan["solver"].items():
            if key not in scalars or not np.isclose(
                float(scalars[key]), value, atol=1e-12, rtol=1e-12
            ):
                raise ValueError(
                    "Generated solver setting differs from approved plan: " + key
                )
        for keys, values in [
            (("dis_win_min", "dis_win_max"), plan["windows_relative_eV"]["dis_eV"]),
            (
                ("dis_froz_min", "dis_froz_max"),
                plan["windows_relative_eV"]["frozen_eV"],
            ),
        ]:
            for key, value in zip(keys, values):
                if key not in scalars or not np.isclose(
                    float(scalars[key]), value + evidence.meta["ef"], atol=1e-7, rtol=0
                ):
                    raise ValueError(
                        "Generated window differs from approved plan: " + key
                    )
        if int(scalars["num_wann"]) != nw or int(scalars["num_bands"]) != nb:
            raise ValueError("Generated win dimensions differ from plan")

        def normalized(values):
            return [v.replace(" ", "").lower() for v in values]

        if normalized(blocks.get("projections", [])) != normalized(plan["basis"]):
            raise ValueError("Generated projections differ from plan")
        win_coords = np.array(
            [[float(v) for v in row.split()] for row in blocks.get("kpoints", [])]
        )
        if win_coords.shape != coords.shape or not np.allclose(
            np.round(np.mod(win_coords, 1), 7) % 1,
            np.round(np.mod(coords, 1), 7) % 1,
            atol=1e-7,
            rtol=0,
        ):
            raise ValueError("Win/EIGENVAL k ordering differs")
        eig = np.atleast_2d(np.loadtxt(interface / "wannier90.eig"))
        if eig.shape != (nb * nk, 3) or not np.isfinite(eig).all():
            raise ValueError("Invalid interface eig shape/values")
        if not np.array_equal(
            eig[:, 0], np.tile(np.arange(1, nb + 1), nk)
        ) or not np.array_equal(eig[:, 1], np.repeat(np.arange(1, nk + 1), nb)):
            raise ValueError("Interface eig ordering differs from actual k/band order")
        retained = [
            b
            for b in range(energies.shape[1])
            if b + 1 not in plan["excluded_bands_1based"]
        ]
        if not np.allclose(
            eig[:, 2].reshape(nk, nb), energies[:, retained], atol=2e-5, rtol=0
        ):
            raise ValueError("Interface eig/EIGENVAL mismatch")
        self._matrix_dimensions(interface, nb, nk, nw)
        if recorder is not None:
            recorder.verified()
        return facts

    @staticmethod
    def _matrix_dimensions(directory, nb, nk, nw):
        with (directory / "wannier90.amn").open() as stream:
            stream.readline()
            dims = list(map(int, stream.readline().split()))
            if dims != [nb, nk, nw]:
                raise ValueError("AMN dimensions differ")
            seen = set()
            for line in stream:
                row = line.split()
                if len(row) != 5:
                    raise ValueError("Malformed AMN row")
                b, w, k = map(int, row[:3])
                key = (b, w, k)
                if (
                    not 1 <= b <= nb
                    or not 1 <= w <= nw
                    or not 1 <= k <= nk
                    or key in seen
                    or not np.isfinite(list(map(float, row[3:]))).all()
                ):
                    raise ValueError("Invalid AMN indices/values")
                seen.add(key)
            if len(seen) != nb * nw * nk:
                raise ValueError("Incomplete AMN")
        with (directory / "wannier90.mmn").open() as stream:
            stream.readline()
            dims = list(map(int, stream.readline().split()))
            if len(dims) != 3 or dims[:2] != [nb, nk] or dims[2] < 1:
                raise ValueError("MMN dimensions differ")
            counts = {}
            for _ in range(nk * dims[2]):
                row = list(map(int, stream.readline().split()))
                if len(row) != 5 or not 1 <= row[0] <= nk or not 1 <= row[1] <= nk:
                    raise ValueError("Invalid MMN neighbor index")
                key = tuple(row)
                if key in counts:
                    raise ValueError("Duplicate MMN neighbor")
                counts[key] = True
                for _ in range(nb * nb):
                    values = list(map(float, stream.readline().split()))
                    if len(values) != 2 or not np.isfinite(values).all():
                        raise ValueError("Malformed MMN matrix")
            if stream.read().strip():
                raise ValueError("Unexpected MMN tail")
            if any(
                sum(key[0] == k for key in counts) != dims[2] for k in range(1, nk + 1)
            ):
                raise ValueError("Incomplete MMN neighbor list")

    def execute(self, plan, plan_file, trial, evidence, baseline):
        require_disentanglement_only(plan)
        interface, points = self.prepare(plan, trial, evidence)
        self.command(self.config["vasp"], interface, "vasp")
        self.verify_binding(trial, evidence)
        outcar = (interface / "OUTCAR").read_text()
        if "General timing and accounting informations" not in outcar:
            raise RuntimeError("VASP did not finish normally")
        recorder = getattr(self, "fact_recorder", None)
        if recorder is not None:
            recorder.phase("interface_verification")
        try:
            facts = self.verify_interface(plan, interface, points, evidence)
        except ExecutionConstraint as error:
            if error.facts is not None:
                immutable_json(trial / "actual-input-validation.json", error.facts)
            raise
        nb = facts["retained_num_bands"]
        immutable_json(trial / "actual-input-validation.json", facts)
        wannier = trial / "wannier"
        wannier.mkdir()
        for extension in ("win", "eig", "amn", "mmn"):
            shutil.copyfile(
                interface / ("wannier90." + extension),
                wannier / ("wannier90." + extension),
            )
        wannier_inputs = {p.name: file_sha(p) for p in wannier.iterdir()}
        self.command([*self.config["wannier90"], "wannier90"], wannier, "wannier90")
        self.verify_binding(trial, evidence)
        for name, expected in wannier_inputs.items():
            if file_sha(wannier / name) != expected:
                raise ValueError("Wannier90 input changed during execution: " + name)
        if recorder is not None:
            recorder.phase("result_analysis")
        wout = (wannier / "wannier90.wout").read_text()
        dis = disentanglement_state(
            wout,
            plan["solver"]["dis_num_iter"],
            nb,
            plan["num_wann"],
        )
        immutable_json(trial / "disentanglement.json", dis)
        product_names = ("wannier90_hr.dat", "wannier90_wsvec.dat")
        products = {
            name: (wannier / name).is_file() and (wannier / name).stat().st_size > 0
            for name in product_names
        }
        if (
            not dis["normal_termination"]
            or dis["state"] in ("unknown", "inconsistent")
            or not all(products.values())
        ):
            return {
                "status": "execution_failed",
                "observations": [
                    "Inspect explicit DIS status and required HR/wsvec products."
                ],
                "disentanglement": dis,
                "disentanglement_converged": dis["converged"],
                "products": products,
                "fit_acceptance": None,
                "actual_input_facts": {
                    k: v for k, v in facts.items() if not isinstance(v, list)
                },
                "record_paths": [
                    f"run/trials/{trial.name}/actual-input-validation.json",
                    f"run/trials/{trial.name}/disentanglement.json",
                    f"run/trials/{trial.name}/wannier/wannier90.wout",
                ],
            }
        status = {
            "converged": "completed_converged",
            "iteration_limit": "completed_iteration_limit",
            "not_required": "completed_no_disentanglement_required",
        }[dis["state"]]
        with np.load(evidence.cache_path, allow_pickle=False) as data:
            coords = np.asarray(data["kcoords"])
            geometry = json.loads(str(data["path_geometry"]))
        if coords.ndim == 3:
            coords = coords[0]
        if geometry.get("kcoords_type") != "fractional":
            raise ValueError(
                "Native HR comparison requires fractional reciprocal source k coordinates"
            )
        if coords.shape != (len(evidence.energy), 3):
            raise ValueError("Comparison k coordinate shape mismatch")
        wan = (
            hr_energies(wannier / "wannier90_hr.dat", coords, require_ws=True)
            - evidence.meta["ef"]
        )
        if wan.shape != (len(coords), plan["num_wann"]):
            raise ValueError("HR dimensions differ from approved plan")
        diagnostics = {
            "target": match_target(
                evidence.energy, wan, plan["target_band_ranges_1based"]
            ),
            "baseline_target": match_target(
                evidence.energy, wan, baseline["target_band_ranges_1based"]
            ),
            "target_changed": plan["target_band_ranges_1based"]
            != baseline["target_band_ranges_1based"],
            "fit_acceptance": None,
            "metric_contract": METRIC_CONTRACT,
        }
        immutable_json(trial / "diagnostics.json", diagnostics)
        converged = dis["converged"]
        save_comparison(
            trial / "comparison.npz",
            evidence,
            plan,
            coords,
            wan,
            baseline=baseline,
            converged=converged,
            status=status,
            disentanglement=dis,
            sources={
                "hr_sha256": file_sha(wannier / "wannier90_hr.dat"),
                "wsvec_sha256": file_sha(wannier / "wannier90_wsvec.dat"),
            },
        )
        result_inputs = {
            "comparison_sha256": file_sha(trial / "comparison.npz"),
            "source_files": json.loads((trial / "input_binding.json").read_text())[
                "source_files"
            ],
            "wannier_inputs": wannier_inputs,
            "result_files": {
                p.name: file_sha(p) for p in wannier.iterdir() if p.is_file()
            },
        }
        immutable_json(trial / "result_binding.json", result_inputs)
        return {
            "status": status,
            "observations": [
                "VASP and Wannier90 terminated normally. Scientific assessment remains with evaluator."
            ],
            "disentanglement_converged": converged,
            "disentanglement": dis,
            "products": products,
            "fit_acceptance": None,
            "metric_contract": METRIC_CONTRACT,
            "metrics": {
                k: {a: v for a, v in b.items() if a != "per_k"}
                for k, b in diagnostics.items()
                if isinstance(b, dict) and k in ("target", "baseline_target")
            },
            "actual_input_facts": {
                k: v for k, v in facts.items() if not isinstance(v, list)
            },
            "record_paths": [
                f"run/trials/{trial.name}/diagnostics.json",
                f"run/trials/{trial.name}/disentanglement.json",
                f"run/trials/{trial.name}/comparison.npz",
                f"run/trials/{trial.name}/actual-input-validation.json",
                f"run/trials/{trial.name}/result_binding.json",
                f"run/trials/{trial.name}/wannier/wannier90.wout",
                f"run/trials/{trial.name}/interface/OUTCAR",
            ],
        }


def make_executor(config, journal):
    kind = config.get("adapter", "vasp_wannier")
    if kind == "vasp_wannier":
        return NativeExecutor(config, journal)
    if kind == "command":
        return CommandExecutor(config, journal)
    raise ValueError("Supported execution.adapter: vasp_wannier or command")
