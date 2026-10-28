import copy
import json
from pathlib import Path
from unittest.mock import patch

import numpy as np

from pythonagent.contracts import ACTION_SCHEMAS, PLAN_SCHEMA, schema_errors
from pythonagent.execution import CommandExecutor, NativeExecutor
from pythonagent.guidance import role_guidance, retry_guidance
from pythonagent.journal import Journal
from pythonagent.plans import PlanStore
from pythonagent.results import disentanglement_state
from pythonagent.roles import role_definitions
from pythonagent.state import digest
from pythonagent.tools import ToolBox
from pythonagent.validation import validate_plan
from pythonagent.workflow import Workflow
from support import CaseTest, FixtureExecutor, ScriptedLLM, author, approve


class PromptFixTests(CaseTest):
    def test_native_execution_emits_detailed_dis_state_and_metric_contract(self):
        plan = self.plan()
        trial = self.run / "trial"
        trial.mkdir()
        interface = trial / "interface"
        interface.mkdir()
        for ext in ("win", "eig", "amn", "mmn"):
            (interface / ("wannier90." + ext)).write_text("fixture")
        (interface / "OUTCAR").write_text("General timing and accounting informations")
        (trial / "input_binding.json").write_text('{"source_files":{}}')
        executor = NativeExecutor(
            {"vasp": ["fixture"], "wannier90": ["fixture"]}, Journal(self.run)
        )
        coords = np.array([[0, 0, 0], [0.5, 0, 0]])

        def command(argv, directory, label):
            if label == "wannier90":
                (directory / "wannier90.wout").write_text(
                    "1 1.0 <-- DIS\nDisentanglement convergence criteria satisfied\nAll done: wannier90 exiting"
                )
                for name in ("wannier90_hr.dat", "wannier90_wsvec.dat"):
                    (directory / name).write_text("fixture product")

        with (
            patch.object(executor, "prepare", return_value=(interface, coords)),
            patch.object(executor, "verify_binding"),
            patch.object(
                executor, "verify_interface", return_value={"retained_num_bands": 4}
            ),
            patch.object(executor, "command", side_effect=command),
            patch(
                "pythonagent.execution.hr_energies",
                return_value=self.evidence.energy[:, :3] + 5,
            ),
        ):
            result = executor.execute(
                plan, trial / "plan.json", trial, self.evidence, plan
            )
        self.assertEqual(result["status"], "completed_converged")
        self.assertEqual(set(result["metrics"]), {"target", "baseline_target"})
        self.assertIn("without frozen/outer", result["metric_contract"]["target"])
        with np.load(trial / "comparison.npz", allow_pickle=False) as data:
            metadata = json.loads(str(data["metadata"]))
        self.assertEqual(metadata["disentanglement"]["state"], "converged")
        self.assertEqual(metadata["execution_status"], result["status"])

    def test_nonzero_localization_cannot_validate_approve_or_reach_executors(self):
        for value in (1, 80, -1, True, False, "0", None, "missing"):
            with self.subTest(value=value):
                plan = self.plan()
                plan["solver"]["num_iter"] = value
                if value == "missing":
                    del plan["solver"]["num_iter"]
                self.assertTrue(schema_errors(plan, PLAN_SCHEMA))
                self.assertFalse(validate_plan(plan, self.evidence)["valid"])
                run = self.root / ("reject-" + repr(value))
                run.mkdir()
                store = PlanStore(run, self.evidence, Journal(run))
                result = store.write("planner", plan)
                with self.assertRaisesRegex(ValueError, "not valid"):
                    store.approve("reviewer", result["plan_id"], "Cannot waive")
                self.assertEqual(
                    json.loads(
                        (store.root / result["plan_id"] / "plan.json").read_text()
                    ),
                    plan,
                )
                for executor in (
                    CommandExecutor({}, Journal(run)),
                    NativeExecutor({}, Journal(run)),
                ):
                    with patch.object(executor, "command") as command:
                        with self.assertRaisesRegex(ValueError, "num_iter"):
                            executor.execute(
                                plan, run / "p.json", run, self.evidence, plan
                            )
                        command.assert_not_called()
                with self.assertRaisesRegex(ValueError, "num_iter"):
                    NativeExecutor({}, Journal(run)).prepare(plan, run, self.evidence)

    def test_generated_win_nonzero_is_rejected_even_when_close_to_zero(self):
        interface = self.root / "interface"
        interface.mkdir()
        (interface / "wannier90.win").write_text("num_iter = 1e-13\n")
        coords = np.array([[0, 0, 0], [0.5, 0, 0]])
        with patch(
            "pythonagent.execution.eigenval",
            return_value=(self.evidence.energy + 5, coords),
        ):
            with self.assertRaisesRegex(ValueError, "num_iter=0"):
                NativeExecutor({}, Journal(self.run)).verify_interface(
                    self.plan(), interface, coords, self.evidence
                )

    def test_scan_covers_nonrepresentative_states_and_keeps_threshold_explicit(self):
        plan = self.plan()
        self.evidence.groups["Cu:p"][1, 1] = 0
        report = self.evidence.projection_audit(plan, include_states=True)
        self.assertEqual(report["coverage"]["region_states"], 4)
        self.assertEqual(report["stronger_omitted_channel_count"], 4)
        self.assertIsNone(report["weak_indicator"]["count"])
        self.assertEqual(report["selected_raw_quantiles"]["min"], 0)
        self.assertIn([2, 2], [w["k_band_1based"] for w in report["witnesses"]])
        indicator = self.evidence.projection_audit(plan, weak_selected_max=0.0005)
        self.assertEqual(indicator["weak_indicator"]["count"], 1)
        self.assertTrue(validate_plan(plan, self.evidence)["valid"])
        self.assertNotIn("scientific_approval", report)

    def test_projection_audit_summary_record_and_plan_binding(self):
        journal = Journal(self.run)
        store = PlanStore(self.run, self.evidence, journal)
        tools = ToolBox(self.evidence, store, journal)
        plan = self.plan()
        saved = store.write("planner", plan)
        summary = saved["facts"]["frozen_projection_audit"]
        self.assertTrue(summary["coverage"]["complete_computational_scan"])
        result = tools.call(
            "reviewer",
            "projection_audit",
            {"plan_id": saved["plan_id"], "region": "all_retained"},
        )
        self.assertEqual(result["plan_sha256"], digest(plan))
        self.assertNotIn("states", result)
        page = tools.call(
            "reviewer",
            "read_record",
            {"path": result["full_record"], "pointer": "/states", "limit": 3},
        )
        self.assertIn("tool_error", page)
        self.assertNotIn("states_pointer", result)
        archive = tools.call("reviewer", "read_record", {"path": result["full_record"]})
        self.assertNotIn("states", archive["value"])
        self.assertEqual(archive["value"]["coverage"]["region_states"], 8)
        changed = copy.deepcopy(plan)
        changed["windows_relative_eV"]["frozen_eV"] = [50, 60]
        store.write("planner", changed)
        self.assertIn(
            "tool_error",
            tools.call("reviewer", "projection_audit", {"plan_id": saved["plan_id"]}),
        )
        empty = self.evidence.projection_audit(changed)
        self.assertEqual(empty["coverage"]["region_states"], 0)
        self.assertIsNone(empty["selected_raw_quantiles"])

    def test_projection_scan_is_not_reused_for_interface_grid(self):
        facts = self.evidence.window_diagnostics(
            self.plan(), self.evidence.energy, "actual_interface_grid"
        )
        self.assertNotIn("frozen_projection_audit", facts)

    def test_dis_states_require_explicit_consistent_evidence(self):
        done = "\nAll done: wannier90 exiting"
        rows = "1 1.0 <-- DIS\n2 0.5 <-- DIS\n"
        cases = [
            (
                rows + "Disentanglement convergence criteria satisfied" + done,
                4,
                "converged",
                True,
            ),
            (
                rows
                + "Maximum number of disentanglement iterations reached\nDisentanglement convergence criteria not satisfied"
                + done,
                4,
                "iteration_limit",
                False,
            ),
            (done, 3, "not_required", None),
            (done, 4, "unknown", None),
            (
                rows + "Disentanglement convergence criteria satisfied",
                4,
                "unknown",
                None,
            ),
            (
                "2 0.5 <-- DIS\nDisentanglement convergence criteria satisfied" + done,
                4,
                "inconsistent",
                None,
            ),
            (
                rows
                + "Disentanglement convergence criteria satisfied\nDisentanglement convergence criteria not satisfied"
                + done,
                4,
                "inconsistent",
                None,
            ),
        ]
        for text, nb, state, converged in cases:
            with self.subTest(state=state, text=text):
                actual = disentanglement_state(text, 2, nb, 3)
                self.assertEqual(actual["state"], state)
                self.assertIs(actual["converged"], converged)

    def test_result_population_never_silently_falls_back(self):
        trial = self.run / "trials/trial_001"
        trial.mkdir(parents=True)
        report = {
            "target": {"counts": {"missing_states": 0}, "per_k": []},
            "baseline_target": {"counts": {"missing_states": 2}, "per_k": []},
        }
        path = trial / "diagnostics.json"
        path.write_text(json.dumps(report))
        journal = Journal(self.run)
        tools = ToolBox(
            self.evidence, PlanStore(self.run, self.evidence, journal), journal
        )
        result = tools.call("evaluator", "residual_ranking", {"trial": "trial_001"})
        self.assertEqual(result["population"], "baseline_target")
        self.assertEqual(result["counts"]["missing_states"], 2)
        del report["baseline_target"]
        path.write_text(json.dumps(report))
        self.assertIn(
            "tool_error",
            tools.call("evaluator", "residual_ranking", {"trial": "trial_001"}),
        )

    def test_unexplained_completion_rejected_and_failure_not_reported_as_satisfaction(
        self,
    ):
        self.assertTrue(
            schema_errors(
                {"action": "complete", "reason": "done", "observations": []},
                ACTION_SCHEMAS["evaluator"],
            )
        )
        bad = {
            "action": "complete",
            "termination_basis": "objective_satisfied",
            "remaining_work": [],
            "reason": "done",
            "observations": [],
        }
        corrected = {
            **bad,
            "termination_basis": "data_blocker",
            "remaining_work": ["Missing interface source"],
        }
        client = ScriptedLLM(
            [
                *author(self.plan()),
                approve(),
                ("evaluator", bad),
                ("evaluator", corrected),
            ]
        )
        flow = Workflow(
            self.case,
            self.root / "flows",
            {
                "execute": True,
                "plotting": {"enabled": False},
                "scheduler": {"enabled": False},
            },
            llm=client,
            executor=FixtureExecutor(
                [{"status": "execution_failed", "observations": ["Missing source"]}]
            ),
        )
        outcome = flow.run()
        self.assertEqual(outcome["stop"], "model_data_blocked")
        self.assertEqual(
            outcome["scientific_decision"]["termination_basis"], "data_blocker"
        )
        self.assertIsNone(outcome["numerical_acceptance"])
        self.assertIn("contradicts", client.seen[-1][2]["validator_notice"]["error"])

    def test_current_mapping_follows_all_quotes_and_covers_result_roles(self):
        for role, definition in role_definitions().items():
            system = definition.system
            self.assertGreater(
                system.index("Current workflow protocol: SOURCE-ADAPTATION.md"),
                system.rindex("END QUOTED MCP REFERENCE"),
            )
            for phrase in (
                "solver.num_iter MUST be explicitly 0",
                "task_scope_review",
                "reference",
                "baseline_target",
                "termination_basis",
                "no image-view tool",
            ):
                self.assertIn(phrase, system)
        for role in ("evaluator", "strategist"):
            sources = role_guidance(role)
            self.assertTrue(
                any(
                    f["source"]["path"].endswith("WANNIER-EXECUTION.md")
                    for f in sources
                )
            )
        self.assertIn("SOURCE-ADAPTATION", retry_guidance()[0]["operational_scope"])

    def test_exported_schemas_match_runtime_constraints(self):
        root = Path(__file__).resolve().parents[1]
        self.assertEqual(
            json.loads((root / "schemas/plan.schema.json").read_text()), PLAN_SCHEMA
        )
        self.assertEqual(
            json.loads((root / "schemas/actions.schema.json").read_text()),
            ACTION_SCHEMAS,
        )
