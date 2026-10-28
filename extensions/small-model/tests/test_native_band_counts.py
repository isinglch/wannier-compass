import json
from unittest.mock import patch

import numpy as np

from pythonagent.artifacts import InterfaceMismatch
from pythonagent.execution import NativeExecutor
from pythonagent.journal import Journal
from support import CaseTest


class NativeBandCountTests(CaseTest):
    def interface(self, total_bands, plan, *, name="trial"):
        trial = self.run / name
        interface = trial / "interface"
        interface.mkdir(parents=True)
        coords = np.array([[0, 0, 0], [0.5, 0, 0]])
        energies = np.array([[3, 4, 6, 10, 15], [3, 4.5, 7, 11, 16]])[:, :total_bands]
        rows = [
            "1 1 1 1",
            "fixture",
            "fixture",
            "fixture",
            "fixture",
            f"1 2 {total_bands}",
        ]
        for k, values in zip(coords, energies):
            rows.append(" ".join(map(str, k)) + " 1")
            rows.extend(f"{b} {e} 0" for b, e in enumerate(values, 1))
        (interface / "EIGENVAL").write_text("\n".join(rows) + "\n")
        scalars = {
            **plan["solver"],
            "num_bands": total_bands - len(plan["excluded_bands_1based"]),
            "num_wann": plan["num_wann"],
        }
        for keys, values in [
            (("dis_win_min", "dis_win_max"), plan["windows_relative_eV"]["dis_eV"]),
            (
                ("dis_froz_min", "dis_froz_max"),
                plan["windows_relative_eV"]["frozen_eV"],
            ),
        ]:
            scalars.update({key: value + 5 for key, value in zip(keys, values)})
        if plan["excluded_bands_1based"]:
            scalars["exclude_bands"] = "1-" + str(len(plan["excluded_bands_1based"]))
        (interface / "wannier90.win").write_text(
            "\n".join(f"{key} = {value}" for key, value in scalars.items())
            + "\nbegin projections\n"
            + "\n".join(plan["basis"])
            + "\nend projections\nbegin kpoints\n"
            + "\n".join(" ".join(map(str, k)) for k in coords)
            + "\nend kpoints\n"
        )
        retained = [
            b for b in range(total_bands) if b + 1 not in plan["excluded_bands_1based"]
        ]
        nb, nk, nw = len(retained), len(coords), plan["num_wann"]
        (interface / "wannier90.eig").write_text(
            "\n".join(
                f"{b} {k} {energies[k - 1, original]}"
                for k in range(1, nk + 1)
                for b, original in enumerate(retained, 1)
            )
            + "\n"
        )
        (interface / "wannier90.amn").write_text(
            f"fixture\n{nb} {nk} {nw}\n"
            + "\n".join(
                f"{b} {w} {k} 0 0"
                for k in range(1, nk + 1)
                for w in range(1, nw + 1)
                for b in range(1, nb + 1)
            )
            + "\n"
        )
        (interface / "wannier90.mmn").write_text(
            f"fixture\n{nb} {nk} 1\n"
            + "".join(
                f"{k} {k} 0 0 0\n" + "0 0\n" * (nb * nb) for k in range(1, nk + 1)
            )
        )
        (interface / "OUTCAR").write_text(
            "General timing and accounting informations\n"
        )
        (trial / "input_binding.json").write_text('{"source_files":{}}\n')
        return trial, interface, coords

    def executor(self):
        return NativeExecutor(
            {"vasp": ["fixture"], "wannier90": ["fixture"]}, Journal(self.run)
        )

    def test_interface_band_difference_is_recorded_not_rejected(self):
        for total in (3, 5):
            with self.subTest(total_bands=total):
                plan = self.plan()
                _, interface, coords = self.interface(total, plan, name=str(total))
                facts = self.executor().verify_interface(
                    plan, interface, coords, self.evidence
                )
                self.assertEqual(facts["source_num_bands"], 4)
                self.assertEqual(facts["interface_num_bands"], total)
                self.assertEqual(facts["retained_num_bands"], total)

    def test_retained_dimensions_follow_actual_bands_after_approved_exclusions(self):
        plan = self.plan()
        plan["excluded_bands_1based"] = [1]
        _, interface, coords = self.interface(5, plan)
        facts = self.executor().verify_interface(plan, interface, coords, self.evidence)
        self.assertEqual(facts["retained_num_bands"], 4)
        self.assertEqual(facts["num_wann"], 3)

    def test_kpoint_count_mismatch_is_still_rejected(self):
        plan = self.plan()
        _, interface, coords = self.interface(5, plan)
        with self.assertRaises(InterfaceMismatch) as error:
            self.executor().verify_interface(plan, interface, coords[:1], self.evidence)
        self.assertEqual(error.exception.details["expected_kpoint_count"], 1)
        self.assertEqual(error.exception.details["actual_energy_shape"], [2, 5])

    def test_actual_win_and_matrix_dimensions_and_completeness_are_still_required(self):
        for broken in ("win", "amn_header", "amn_truncated", "mmn_header", "eig"):
            with self.subTest(broken=broken):
                plan = self.plan()
                _, interface, coords = self.interface(5, plan, name=broken)
                if broken == "win":
                    path = interface / "wannier90.win"
                    path.write_text(
                        path.read_text().replace("num_bands = 5", "num_bands = 4")
                    )
                elif broken in ("amn_header", "mmn_header"):
                    suffix = broken.split("_")[0]
                    path = interface / f"wannier90.{suffix}"
                    rows = path.read_text().splitlines()
                    rows[1] = rows[1].replace("5", "4", 1)
                    path.write_text("\n".join(rows) + "\n")
                elif broken == "amn_truncated":
                    path = interface / "wannier90.amn"
                    path.write_text(
                        "\n".join(path.read_text().splitlines()[:-1]) + "\n"
                    )
                else:
                    path = interface / "wannier90.eig"
                    path.write_text(path.read_text().replace("1 1 3.0", "1 1 30.0"))
                with self.assertRaises(ValueError):
                    self.executor().verify_interface(
                        plan, interface, coords, self.evidence
                    )

    def test_execute_uses_actual_retained_nb_for_dis_in_both_directions(self):
        converged = "1 1.0 <-- DIS\nDisentanglement convergence criteria satisfied\nAll done: wannier90 exiting"
        done = "All done: wannier90 exiting"
        for total, nw, excluded, text, expected in (
            (5, 4, [], converged, "converged"),
            (5, 4, [], done, "unknown"),
            (3, 3, [], done, "not_required"),
            (5, 3, [1], converged, "converged"),
        ):
            name = f"{total}-{nw}-{len(excluded)}-{expected}"
            with self.subTest(case=name):
                plan = self.plan()
                plan["num_wann"] = nw
                if nw == 4:
                    plan["basis"] = ["Cu:s", "Cu:p"]
                plan["excluded_bands_1based"] = excluded
                trial, interface, coords = self.interface(total, plan, name=name)
                executor = self.executor()

                def command(argv, directory, label):
                    if label != "wannier90":
                        return
                    (directory / "wannier90.wout").write_text(text)
                    (directory / "wannier90_hr.dat").write_text(
                        f"fixture\n{nw}\n1\n1\n"
                        + "".join(
                            f"0 0 0 {i} {j} {i if i == j else 0} 0\n"
                            for j in range(1, nw + 1)
                            for i in range(1, nw + 1)
                        )
                    )
                    (directory / "wannier90_wsvec.dat").write_text(
                        "## use_ws_distance=.true.\n"
                        + "".join(
                            f"0 0 0 {i} {j}\n1\n0 0 0\n"
                            for j in range(1, nw + 1)
                            for i in range(1, nw + 1)
                        )
                    )

                with (
                    patch.object(executor, "prepare", return_value=(interface, coords)),
                    patch.object(executor, "verify_binding"),
                    patch.object(executor, "command", side_effect=command),
                ):
                    result = executor.execute(
                        plan, trial / "plan.json", trial, self.evidence, plan
                    )
                dis = json.loads((trial / "disentanglement.json").read_text())
                self.assertEqual(dis["state"], expected)
                facts = json.loads((trial / "actual-input-validation.json").read_text())
                self.assertEqual(facts["retained_num_bands"], total - len(excluded))
                if expected == "unknown":
                    self.assertEqual(result["status"], "execution_failed")
                    self.assertEqual(
                        result["actual_input_facts"]["retained_num_bands"],
                        total - len(excluded),
                    )
                else:
                    self.assertEqual(
                        result["actual_input_facts"]["retained_num_bands"],
                        total - len(excluded),
                    )
                    self.assertNotEqual(result["status"], "execution_failed")
                    # Fit/plot populations remain on original cache bands, not added interface bands.
                    with np.load(trial / "comparison.npz", allow_pickle=False) as data:
                        np.testing.assert_array_equal(
                            data["dft_relative_eV"], self.evidence.energy
                        )
