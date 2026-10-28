import json
import sys

import numpy as np

from pythonagent.execution import CommandExecutor, NativeExecutor, ExecutionConstraint
from pythonagent.formats import hr_energies, eigenval, match_target
from pythonagent.journal import Journal
from pythonagent.state import immutable_json
from support import CaseTest


class ExecutionTests(CaseTest):
    def test_ws_translations_change_off_grid_interpolation_and_are_required(self):
        path = self.root / "fixture_hr.dat"
        path.write_text("fixture\n1\n1\n1\n0 0 0 1 1 2 0\n")
        with self.assertRaisesRegex(ValueError, "Missing required"):
            hr_energies(path, [[0, 0, 0]], require_ws=True)
        ws = self.root / "fixture_wsvec.dat"
        ws.write_text("## use_ws_distance=.true.\n0 0 0 1 1\n2\n1 0 0\n-1 0 0\n")
        result = hr_energies(path, [[0, 0, 0], [0.25, 0, 0]], require_ws=True)
        np.testing.assert_allclose(result[:, 0], [2, 0], atol=1e-12)
        ws.write_text("## use_ws_distance=.true.\n")
        with self.assertRaisesRegex(ValueError, "Incomplete"):
            hr_energies(path, [[0, 0, 0]], require_ws=True)

    def test_hr_interpolation_identity_and_target_missing_coverage(self):
        path = self.root / "minimal_hr.dat"
        path.write_text(
            "fixture\n2\n1\n1\n0 0 0 1 1 -1 0\n0 0 0 2 1 0 0\n0 0 0 1 2 0 0\n0 0 0 2 2 2 0\n"
        )
        result = hr_energies(path, [[0, 0, 0], [0.5, 0, 0]])
        np.testing.assert_allclose(result, [[-1, 2], [-1, 2]])
        report = match_target(np.array([[-1, 0, 2], [-1, 0, 2]]), result, [[1, 3]])
        self.assertEqual(report["counts"]["missing_states"], 2)
        self.assertEqual(report["matched_error_metrics"]["mae_eV"], 0)

    def test_hr_truncated_degeneracy_fails_without_looping(self):
        path = self.root / "bad_hr.dat"
        path.write_text("fixture\n2\n1\n")
        with self.assertRaisesRegex(ValueError, "degeneracies"):
            hr_energies(path, [[0, 0, 0]])

    def test_command_adapter_executes_registered_argv_with_literal_plan_path(self):
        script = self.root / "adapter.py"
        script.write_text(
            'import json,sys\np=json.load(open(sys.argv[1]))\njson.dump({"status":"completed","observations":[p["reason"]]},open(sys.argv[2],"w"))\n'
        )
        plan_path = self.run / "approved-plan.json"
        immutable_json(plan_path, self.plan())
        executor = CommandExecutor(
            {
                "commands": [
                    [sys.executable, str(script), "{plan_file}", "{result_file}"]
                ]
            },
            Journal(self.run),
        )
        executor.validate()
        result = executor.execute(
            self.plan(), plan_path, self.run, self.evidence, self.plan()
        )
        self.assertEqual(result["observations"], [self.plan()["reason"]])
        self.assertEqual(json.loads(plan_path.read_text()), self.plan())

    def test_native_actual_grid_counts_are_constraints_but_top_clipping_is_not(self):
        interface = self.run / "interface"
        interface.mkdir()
        # Counts fail before matrix parsing: four states are inside frozen, num_wann=3.
        (interface / "EIGENVAL").write_text(
            "1 1 1 1\nblank\nblank\nblank\nblank\n1 1 4\n\n0 0 0 1\n1 3 1\n2 4 1\n3 6 0\n4 10 0\n"
        )
        plan = self.plan()
        plan["windows_relative_eV"]["frozen_eV"] = [-3, 10]
        executor = NativeExecutor({}, Journal(self.run))
        # Use a 4-band one-k cache for the verified actual matrix dimension check.
        with self.assertRaises(ExecutionConstraint) as caught:
            executor.verify_interface(
                plan, interface, np.array([[0, 0, 0]]), self.evidence
            )
        self.assertEqual(caught.exception.issues[0]["code"], "frozen_dimension")

    def test_noncontiguous_exclusion_is_an_adapter_limit_not_a_physical_gap_rule(self):
        self.assertIn(
            "no occupation or global-gap policy",
            NativeExecutor({}, Journal(self.run)).capabilities()["exclusions"],
        )

    def test_truncated_eigenval_is_a_reportable_file_error(self):
        path = self.root / "EIGENVAL"
        path.write_text("1 1 1 1\na\na\na\na\n1 1 2\n0 0 0 1\n1 -1 1\n")
        with self.assertRaisesRegex(ValueError, "Truncated"):
            eigenval(path)
