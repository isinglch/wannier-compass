import json
import sys

import test_native_band_counts as native_fixtures
from support import CaseTest, ScriptedLLM, FixtureExecutor, author, approve
from pythonagent.execution import NativeExecutor, ExecutionConstraint
from pythonagent.execution_facts import ExecutionFacts, execution_facts, ranges
from pythonagent.journal import Journal
from pythonagent.plans import PlanStore
from pythonagent.scheduler import Scheduler
from pythonagent.specs import tool_specs_for
from pythonagent.state import immutable_json
from pythonagent.tools import ToolBox
from pythonagent.workflow import Workflow


class ExecutionFactTests(CaseTest):
    def make(self, *, frozen_failure=False):
        plan = self.plan()
        if frozen_failure:
            plan["windows_relative_eV"]["frozen_eV"] = [-3, 20]
        trial, interface, coords = native_fixtures.NativeBandCountTests.interface(
            self, 5, plan, name="trials/trial_001"
        )
        immutable_json(trial / "approved-plan.json", plan)
        (self.case / "02_band/INCAR").write_text("NBANDS = 2\nISPIN = 1\n")
        (interface / "INCAR").write_text("NBANDS = 4\nNUM_WANN = 3\n")
        rec = ExecutionFacts(trial, plan, self.evidence, "vasp_wannier")
        rec.materialized(
            {"NBANDS": "2", "ISPIN": "1"},
            {"NBANDS": "4", "ISPIN": "1", "NUM_WANN": "3"},
        )
        executor = NativeExecutor({}, Journal(self.run))
        executor.fact_recorder = rec
        return plan, trial, interface, coords, rec, executor

    def test_original_generated_observed_values_are_separate_and_no_nbands_equality_veto(
        self,
    ):
        plan, trial, interface, coords, rec, executor = self.make()
        facts = executor.verify_interface(plan, interface, coords, self.evidence)
        rec.finish("completed")
        value = execution_facts(self.evidence, "trial_001")
        self.assertEqual(value["source_settings"]["nbands"], 2)
        self.assertEqual(value["materialized_input"]["nbands"], 4)
        self.assertEqual(value["source_cache"]["num_bands"], 4)
        self.assertEqual(value["observed_interface"]["num_bands"], 5)
        self.assertEqual(value["observed_interface"]["matrix_identity"], "verified")
        self.assertEqual(facts["retained_num_bands"], 5)
        self.assertEqual(value["nbands_generation"]["basis"], "source_cache.num_bands")
        for name in ("inputs", "interface", "constraints", "commands"):
            self.assertEqual(
                execution_facts(self.evidence, "trial_001", name)["view"], name
            )

    def test_constraint_failure_preserves_actual_dimensions_and_full_coverage_without_raw_arrays(
        self,
    ):
        plan, trial, interface, coords, rec, executor = self.make(frozen_failure=True)
        with self.assertRaises(ExecutionConstraint) as caught:
            executor.verify_interface(plan, interface, coords, self.evidence)
        self.assertEqual(caught.exception.facts["interface_num_bands"], 5)
        rec.finish("input_constraint_failed", error=caught.exception)
        value = execution_facts(self.evidence, "trial_001")
        self.assertEqual(value["observed_interface"]["num_bands"], 5)
        self.assertEqual(value["observed_interface"]["matrix_identity"], "not_verified")
        self.assertEqual(value["window_counts"]["nfrozen_above_num_wann"]["count"], 2)
        self.assertEqual(
            value["window_counts"]["nfrozen_above_num_wann"]["k_ranges_1based"],
            [[1, 2]],
        )
        self.assertEqual(value["commands"]["wannier90"]["status"], "not_started")
        self.assertNotIn("raw_projection", json.dumps(value))
        self.assertNotIn("per_k", json.dumps(value))

    def test_query_is_trial_bound_and_rejects_tampered_inputs_and_records(self):
        _, trial, interface, _, rec, _ = self.make()
        rec.finish("input_constraint_failed")
        original = (interface / "INCAR").read_text()
        (interface / "INCAR").write_text("NBANDS = 999\n")
        with self.assertRaisesRegex(ValueError, "source changed"):
            execution_facts(self.evidence, "trial_001")
        (interface / "INCAR").write_text(original)
        data = json.loads(rec.path.read_text())
        data["trial_id"] = "trial_002"
        rec.path.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, "another trial"):
            execution_facts(self.evidence, "trial_001")
        for trial in ("../trial_001", "trial_001/../../case", "bad"):
            with self.assertRaises(ValueError):
                execution_facts(self.evidence, trial)

    def test_unknown_stages_stay_unknown_for_preparation_failure(self):
        trial = self.run / "trials/trial_001"
        trial.mkdir(parents=True)
        immutable_json(trial / "approved-plan.json", self.plan())
        rec = ExecutionFacts(trial, self.plan(), self.evidence, "vasp_wannier")
        rec.finish("execution_failed", error=ValueError("missing source"))
        v = execution_facts(self.evidence, "trial_001")
        self.assertEqual(v["materialized_input"]["status"], "not_created")
        self.assertIsNone(v["observed_interface"]["num_bands"])
        self.assertEqual(v["window_counts"]["status"], "not_measured")
        self.assertEqual(v["commands"]["vasp"]["status"], "not_started")

    def test_real_command_failure_records_exit_code_and_never_claims_wannier_started(
        self,
    ):
        _, _, interface, _, rec, executor = self.make()
        with self.assertRaisesRegex(RuntimeError, "exited 7"):
            executor.command(
                [sys.executable, "-c", "raise SystemExit(7)"], interface, "vasp"
            )
        rec.finish("execution_failed")
        v = execution_facts(self.evidence, "trial_001")
        self.assertEqual(v["commands"]["vasp"]["returncode"], 7)
        self.assertEqual(v["commands"]["vasp"]["status"], "failed")
        self.assertEqual(v["commands"]["wannier90"]["status"], "not_started")

    def test_timeout_is_recorded_as_interrupted_with_process_exit_status(self):
        _, _, interface, _, rec, executor = self.make()
        executor.config = {"timeout_seconds": 0.03}
        with self.assertRaisesRegex(RuntimeError, "interrupted"):
            executor.command(
                [sys.executable, "-c", "import time; time.sleep(30)"], interface, "vasp"
            )
        rec.finish("execution_failed")
        v = execution_facts(self.evidence, "trial_001")
        self.assertEqual(v["commands"]["vasp"]["status"], "interrupted")
        self.assertIsNotNone(v["commands"]["vasp"]["returncode"])
        self.assertNotEqual(v["commands"]["vasp"]["returncode"], 0)

    def test_all_five_roles_share_identical_read_only_query(self):
        _, _, _, _, rec, _ = self.make()
        rec.finish("input_constraint_failed")
        box = ToolBox(
            self.evidence,
            PlanStore(self.run, self.evidence, Journal(self.run)),
            Journal(self.run),
        )
        values = []
        for role in ("planner", "reviewer", "evaluator", "strategist", "scheduler"):
            self.assertIn("execution_facts", tool_specs_for(role))
            value = box.call(role, "execution_facts", {"trial": "trial_001"})
            self.assertNotIn("tool_error", value)
            values.append(value)
        self.assertTrue(all(value == values[0] for value in values))
        self.assertEqual(
            box.call(
                "scheduler",
                "read_record",
                {"path": "run/trials/trial_001/execution-facts.json"},
            )["value"]["format"],
            "execution-facts-v1",
        )

    def test_published_metadata_is_bound_to_the_result_record(self):
        _, trial, _, _, rec, _ = self.make()
        rec.finish("input_constraint_failed")
        immutable_json(trial / "result.json", {"execution_facts": rec.summary()})
        execution_facts(self.evidence, "trial_001")
        value = json.loads(rec.path.read_text())
        value["observed_interface"]["num_bands"] = 999
        rec.path.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError, "Published execution facts changed"):
            execution_facts(self.evidence, "trial_001")

    def test_successful_adapter_without_dimension_observations_keeps_them_unknown(self):
        llm = ScriptedLLM(
            [
                *author(self.plan()),
                approve(),
                (
                    "evaluator",
                    {
                        "action": "complete",
                        "termination_basis": "objective_satisfied",
                        "remaining_work": [],
                        "observations": ["fixture"],
                        "reason": "fixture",
                    },
                ),
            ]
        )
        flow = Workflow(
            self.case,
            self.root / "runs",
            {
                "execute": True,
                "scheduler": {"enabled": False},
                "plotting": {"enabled": False},
            },
            llm=llm,
            executor=FixtureExecutor(),
        )
        outcome = flow.run()
        self.assertEqual(outcome["stop"], "model_complete", outcome)
        value = execution_facts(flow.evidence, "trial_001")
        self.assertEqual(value["status"], "completed")
        self.assertEqual(value["observed_interface"]["status"], "not_observed")
        self.assertIsNone(value["observed_interface"]["num_bands"])

    def test_ranges_reconstruct_every_affected_coordinate(self):
        values = [1, 2, 3, 7, 8, 10, 12, 13]
        encoded = ranges(values)
        self.assertEqual([k for a, b in encoded for k in range(a, b + 1)], values)

    def test_workflow_failure_handoff_and_scheduler_receive_same_snapshot(self):
        class ConstraintExecutor(NativeExecutor):
            def validate(self):
                pass

            def execute(backend, plan, plan_file, trial, evidence, baseline):
                interface = trial / "interface"
                interface.mkdir()
                (interface / "INCAR").write_text("NBANDS = 4\nNUM_WANN = 3\n")
                (interface / "EIGENVAL").write_text("test-only source\n")
                rec = backend.fact_recorder
                rec.materialized({"NBANDS": "2"}, {"NBANDS": "4", "NUM_WANN": "3"})
                rec.phase("interface_verification")
                rec.observed((2, 5), interface / "EIGENVAL")
                facts = {
                    "source_num_bands": 4,
                    "interface_num_bands": 5,
                    "retained_num_bands": 5,
                    "num_wann": 3,
                    "min_nouter": 2,
                    "max_nfrozen": 1,
                    "sampling": "actual_interface_grid",
                    "nouter_below_num_wann_kpoints": [1, 2],
                }
                rec.counts(facts)
                raise ExecutionConstraint(
                    [
                        {
                            "code": "outer_dimension",
                            "path": "/windows_relative_eV",
                            "message": "N_outer < num_wann",
                            "kpoints": [1, 2],
                        }
                    ],
                    facts=facts,
                )

        (self.case / "02_band/INCAR").write_text("NBANDS = 2\n")

        def resumed(state):
            facts = state["handoff"]["message"]["execution_facts"]
            self.assertEqual(facts, state["current_result"]["execution_facts"])
            self.assertEqual(facts["observed_interface"]["num_bands"], 5)
            return {
                "action": "request_data",
                "reason": "fixture ends after inspecting complete execution metadata",
                "request": "fixture unsupported operation",
            }

        llm = ScriptedLLM([*author(self.plan()), approve(), ("planner", resumed)])
        flow = Workflow(
            self.case,
            self.root / "runs",
            {
                "execute": True,
                "plotting": {"enabled": False},
                "scheduler": {"enabled": False},
            },
            llm=llm,
            executor=ConstraintExecutor({}, Journal(self.run)),
        )
        outcome = flow.run()
        self.assertEqual(outcome["stop"], "data_operation_unavailable", outcome)
        record = json.loads((flow.run_dir / "trials/trial_001/result.json").read_text())
        self.assertEqual(
            record["execution_facts"]["observed_interface"]["num_bands"], 5
        )
        state = Scheduler(flow).state({"kind": "execution_failed"}, ["stop"])
        self.assertEqual(
            state["last_execution"]["execution_facts"], record["execution_facts"]
        )
        self.assertIsNone(flow.executor.fact_recorder)
        flow.llm = ScriptedLLM(
            [
                (
                    "scheduler",
                    {"tool": "execution_facts", "arguments": {"trial": "trial_001"}},
                ),
                (
                    "scheduler",
                    {
                        "action": "stop",
                        "reason": "fixture metadata inspected",
                        "remaining_work": ["fixture"],
                    },
                ),
            ]
        )
        dispatcher = Scheduler(flow)
        dispatcher.decide({"kind": "execution_failed"}, ["stop"])
        self.assertIn(
            "run/trials/trial_001/execution-facts.json", dispatcher.read_paths
        )
