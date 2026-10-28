import json
from unittest.mock import patch

import numpy as np

from pythonagent.artifacts import ExecutionFailure
from pythonagent.config import load_config
from pythonagent.contracts import schema_errors
from pythonagent.journal import Journal
from pythonagent.llm import LLMClient
from pythonagent.roles import role_definitions
from pythonagent.scheduler import SCHEDULER_SCHEMA
from pythonagent.specs import tool_specs_for
from pythonagent.tools import ToolBox
from pythonagent.plans import PlanStore
from pythonagent.workflow import Workflow
from pythonagent.execution import NativeExecutor, ExecutionConstraint
from pythonagent.artifacts import InterfaceMismatch
from support import CaseTest, FixtureExecutor, ScriptedLLM, author, approve


def execute():
    return (
        "scheduler",
        lambda s: {
            "action": "execute",
            "plan_id": s["current_plan"]["plan_id"],
            "reason": "Exact version is approved",
        },
    )


def complete():
    return (
        "evaluator",
        {
            "action": "complete",
            "reason": "Assessed fixture",
            "observations": [],
            "termination_basis": "objective_satisfied",
            "remaining_work": [],
        },
    )


def stop():
    return (
        "scheduler",
        {
            "action": "stop",
            "reason": "Observed reproducible fault; no supported repair",
            "remaining_work": ["Fix the execution adapter"],
        },
    )


class LoggedExecutor(FixtureExecutor):
    def execute(self, plan, plan_file, trial, evidence, baseline):
        directory = trial / "interface"
        directory.mkdir()
        (directory / "vasp.stdout").write_text("Computing AMN\n")
        (directory / "vasp.stderr").write_text("Observed fixture execution failure\n")
        return super().execute(plan, plan_file, trial, evidence, baseline)


class SchedulerTests(CaseTest):
    def workflow(self, script, executor=None, **options):
        config = {
            "execute": True,
            "plotting": {"enabled": False},
            "scheduler": {"enabled": True},
        }
        config.update(options)
        client = ScriptedLLM(script)
        backend = executor or FixtureExecutor()
        flow = Workflow(
            self.case, self.root / "runs", config, llm=client, executor=backend
        )
        return flow, client, backend

    def test_normal_delivery_preserves_science_and_scheduler_has_small_context(self):
        flow, client, backend = self.workflow(
            [*author(self.plan()), approve(), execute(), complete()]
        )
        result = flow.run()
        self.assertEqual(result["stop"], "model_complete", result)
        self.assertEqual(backend.executions, [self.plan()])
        self.assertEqual(result["scheduler"]["decisions"], 1)
        state = next(state for role, state, _ in client.seen if role == "scheduler")
        self.assertNotIn("basis", json.dumps(state))
        self.assertNotIn("history", state)
        self.assertNotIn("stage_guidance", state)
        self.assertFalse(client.script)

    def test_technical_retry_keeps_exact_plan_and_approval_with_separate_budget(self):
        backend = LoggedExecutor(
            [
                ExecutionFailure("vasp exited 255", stage="vasp", returncode=255),
                {"status": "completed", "observations": []},
            ]
        )
        script = [
            *author(self.plan()),
            approve(),
            execute(),
            (
                "scheduler",
                {
                    "tool": "read_record",
                    "arguments": {"path": "run/trials/trial_001/failure.json"},
                },
            ),
            (
                "scheduler",
                lambda s: {
                    "action": "retry_execution",
                    "plan_id": s["current_plan"]["plan_id"],
                    "reason": "Fixture reports a bounded transient retry purpose",
                    "diagnosis_refs": ["run/trials/trial_001/failure.json"],
                },
            ),
            complete(),
        ]
        flow, client, _ = self.workflow(script, backend, max_trials=1)
        result = flow.run()
        self.assertEqual(result["stop"], "model_complete", result)
        self.assertEqual(result["trial_attempts"], 2)
        self.assertEqual(result["scientific_trials"], 1)
        self.assertEqual(result["plan_versions"], 1)
        self.assertEqual(result["scheduler"]["execution_retries"], 1)
        self.assertEqual(backend.executions, [self.plan(), self.plan()])
        a, b = sorted((flow.run_dir / "trials").iterdir())
        self.assertEqual(
            (a / "approval.json").read_bytes(), (b / "approval.json").read_bytes()
        )
        failure = json.loads((a / "failure.json").read_text())
        self.assertEqual(failure["returncode"], 255)
        self.assertEqual(failure["stage"], "vasp")
        self.assertTrue(
            any(x["path"].endswith("interface/vasp.stderr") for x in failure["logs"])
        )

    def test_unread_diagnosis_cannot_trigger_retry(self):
        flow, _, backend = self.workflow(
            [
                *author(self.plan()),
                approve(),
                execute(),
                (
                    "scheduler",
                    lambda s: {
                        "action": "retry_execution",
                        "plan_id": s["current_plan"]["plan_id"],
                        "reason": "Blind retry",
                        "diagnosis_refs": ["run/trials/trial_001/failure.json"],
                    },
                ),
                stop(),
            ],
            LoggedExecutor([RuntimeError("failure")]),
        )
        result = flow.run()
        self.assertEqual(result["stop"], "operational_blocked", result)
        self.assertEqual(len(backend.executions), 1)
        self.assertEqual(result["plan_versions"], 1)

    def test_same_session_operational_continuation_preserves_full_handoff(self):
        query = {"tool": "anchor_table", "arguments": {"limit": 1}}
        script = [
            ("planner", query),
            (
                "planner",
                {
                    "action": "request_operations",
                    "reason": "Need operational clarification",
                    "request": "Continue with available source",
                },
            ),
            (
                "scheduler",
                {
                    "action": "resume_role",
                    "role": "planner",
                    "instruction": "Complete the original task using available source",
                    "reason": "Operation clarified",
                },
            ),
            ("planner", query),
            *author(self.plan()),
            approve(),
            execute(),
            complete(),
        ]
        flow, client, _ = self.workflow(script)
        result = flow.run()
        self.assertEqual(result["stop"], "model_complete", result)
        resumed = [x for x in client.seen if x[0] == "planner"][2]
        self.assertIn("original_handoff", resumed[1]["handoff"]["message"])
        self.assertEqual(
            resumed[2]["continuation"]["instruction"],
            "Complete the original task using available source",
        )
        self.assertNotIn("tool_rounds", json.dumps(resumed[2]))
        self.assertFalse(client.script)

    def test_more_than_128_queries_do_not_consume_role_or_experiment_rounds(self):
        query = ("planner", {"tool": "anchor_table", "arguments": {"limit": 1}})
        flow, client, _ = self.workflow(
            [
                *[query for _ in range(130)],
                *author(self.plan()),
                approve(),
                execute(),
                complete(),
            ],
            max_steps=3,
            max_trials=1,
        )
        result = flow.run()
        self.assertEqual(result["stop"], "model_complete", result)
        self.assertEqual(result["role_steps"], 3)
        self.assertEqual(result["scientific_trials"], 1)
        self.assertFalse(client.script)
        self.assertFalse(
            any(
                "remaining_tool_rounds" in json.dumps(instruction)
                for _, _, instruction in client.seen
            )
        )

    def test_plan_writes_have_no_independent_tool_quota(self):
        writes = [author(self.plan())[0] for _ in range(12)]
        submit = author(self.plan())[1]
        flow, _, _ = self.workflow([*writes, submit, approve(), execute(), complete()])
        result = flow.run()
        self.assertEqual(result["stop"], "model_complete", result)
        self.assertEqual(result["plan_versions"], 12)
        self.assertEqual(result["role_steps"], 3)

    def test_scheduler_cannot_write_plan_or_forge_approved_version(self):
        script = [
            *author(self.plan()),
            approve(),
            ("scheduler", {"tool": "write_plan", "arguments": {"plan": self.plan()}}),
            (
                "scheduler",
                {
                    "action": "execute",
                    "plan_id": "not-approved",
                    "reason": "Forged version",
                },
            ),
            execute(),
            complete(),
        ]
        flow, _, backend = self.workflow(script)
        result = flow.run()
        self.assertEqual(result["stop"], "model_complete", result)
        self.assertEqual(result["plan_versions"], 1)
        self.assertEqual(len(backend.executions), 1)

    def test_unsupported_data_operation_has_explicit_operational_blocker(self):
        flow, _, backend = self.workflow(
            [
                (
                    "planner",
                    {
                        "action": "request_data",
                        "reason": "Need new source",
                        "request": "Regenerate SCF",
                    },
                ),
                stop(),
            ]
        )
        result = flow.run()
        self.assertEqual(result["stop"], "operational_blocked", result)
        self.assertIn("Fix the execution adapter", result["remaining_work"])
        self.assertFalse(backend.executions)

    def test_scheduler_read_queries_are_not_capped_at_twelve(self):
        query = (
            "scheduler",
            {"tool": "list_records", "arguments": {"path": "run/plans"}},
        )
        flow, client, _ = self.workflow(
            [
                *author(self.plan()),
                approve(),
                *[query for _ in range(30)],
                execute(),
                complete(),
            ]
        )
        result = flow.run()
        self.assertEqual(result["stop"], "model_complete", result)
        self.assertEqual(result["scheduler"]["decisions"], 1)
        self.assertFalse(client.script)

    def test_role_decision_budget_remains_in_force(self):
        flow, _, backend = self.workflow(
            [*author(self.plan()), approve(), execute()], max_steps=2
        )
        result = flow.run()
        self.assertEqual(result["stop"], "role_budget_exhausted", result)
        self.assertEqual(len(backend.executions), 1)

    def test_total_scientific_experiment_budget_remains_in_force(self):
        flow, _, backend = self.workflow(
            [
                *author(self.plan()),
                approve(),
                execute(),
                (
                    "evaluator",
                    {
                        "action": "iterate",
                        "reason": "Need a new scientific trial",
                        "observations": [],
                        "questions": ["Next experiment?"],
                    },
                ),
            ],
            max_trials=1,
        )
        result = flow.run()
        self.assertEqual(result["stop"], "trial_budget_exhausted", result)
        self.assertEqual(len(backend.executions), 1)

    def test_old_tool_limit_options_are_visibly_retired_instead_of_enforced(self):
        path = self.root / "old-config.json"
        path.write_text(
            json.dumps(
                {
                    "llm": {"max_session_turns": 1},
                    "max_tool_rounds": 1,
                    "scheduler": {
                        "enabled": True,
                        "max_tool_rounds": 1,
                        "max_extra_tool_rounds": 0,
                    },
                }
            )
        )
        with self.assertWarnsRegex(UserWarning, "obsolete options ignored"):
            config = load_config(path)
        self.assertNotIn("max_tool_rounds", config)
        self.assertNotIn("max_session_turns", config["llm"])
        self.assertEqual(
            set(config["scheduler"]),
            {"enabled", "max_decisions", "max_execution_retries"},
        )

    def test_fifth_role_is_isolated_and_has_no_scientific_library(self):
        definitions = role_definitions(scheduler=True)
        scheduler = definitions["scheduler"]
        self.assertEqual(scheduler.guidance_sources, ())
        self.assertNotIn("FIRST-TRIAL", scheduler.system)
        self.assertEqual(
            set(tool_specs_for("scheduler")),
            {"read_text", "read_record", "list_records", "execution_facts"},
        )
        self.assertEqual(len(definitions), 5)
        client = LLMClient(
            {
                "backend": "dry_run",
                "scheduler_enabled": True,
                "compression": {"enabled": False},
            },
            Journal(self.run),
        )
        from pythonagent.sessions import RoleSessions

        first = RoleSessions(client, client.config)
        second = RoleSessions(client, client.config)
        self.assertEqual(len(first.heads), 5)
        self.assertNotEqual(
            first.heads["scheduler"]["session_id"],
            second.heads["scheduler"]["session_id"],
        )
        self.assertEqual(second.heads["scheduler"]["turns"], [])

    def test_artifact_discovery_and_errors_are_distinct(self):
        path = self.run / "trials/trial_001/interface"
        path.mkdir(parents=True)
        (path / "vasp.stderr").write_text("segmentation fault\n")
        rows = self.evidence.list_records("run/trials/trial_001")
        self.assertEqual(rows["rows"][0]["path"], "run/trials/trial_001/interface")
        self.assertEqual(
            self.evidence.resolve("run/trials/trial_001/interface/vasp.stderr"),
            path / "vasp.stderr",
        )
        with self.assertRaisesRegex(ValueError, "does not exist"):
            self.evidence.resolve("run/trials/trial_001/stdout.txt")
        with self.assertRaisesRegex(ValueError, "list_records"):
            self.evidence.resolve("run/trials/trial_001")
        with self.assertRaisesRegex(ValueError, "escapes"):
            self.evidence.list_records("run/../../")
        (self.run / "sessions").mkdir()
        with self.assertRaisesRegex(ValueError, "Private histories"):
            self.evidence.list_records("run/sessions")

    def test_large_pages_keep_real_pointer_and_can_be_read_to_completion(self):
        (self.run / "observations").mkdir(exist_ok=True)
        rows = [{"index": i, "payload": "x" * 800} for i in range(70)]
        (self.run / "observations/large.json").write_text(json.dumps({"rows": rows}))
        tools = ToolBox(
            self.evidence,
            PlanStore(self.run, self.evidence, Journal(self.run)),
            Journal(self.run),
        )
        recovered, offset = [], 0
        while True:
            page = tools.call(
                "planner",
                "read_record",
                {
                    "path": "run/observations/large.json",
                    "pointer": "/rows",
                    "offset": offset,
                    "limit": 70,
                },
            )
            self.assertNotIn("observation_too_large", page)
            recovered.extend(page["value"])
            if page["next_offset"] is None:
                break
            offset = page["next_offset"]
        self.assertEqual(recovered, rows)
        structure = tools.call(
            "planner", "read_record", {"path": "run/observations/large.json"}
        )
        self.assertNotIn("observation_too_large", structure)
        self.assertEqual(structure["view"], "structure")
        self.assertEqual(structure["rows"][0]["pointer"], "/rows")
        self.assertEqual(structure["rows"][0]["items"], 70)

    def test_public_configs_enable_scheduler_and_invalid_budgets_fail_early(self):
        path = self.root / "config.json"
        path.write_text(json.dumps({"llm": {}, "execution": {}}))
        self.assertTrue(load_config(path)["scheduler"]["enabled"])
        path.write_text(
            json.dumps(
                {
                    "llm": {},
                    "scheduler": {"enabled": True, "max_execution_retries": True},
                }
            )
        )
        with self.assertRaisesRegex(ValueError, "budget"):
            load_config(path)
        self.assertTrue(
            schema_errors(
                {
                    "action": "execute",
                    "plan_id": "x",
                    "reason": "ok",
                    "windows": [0, 2],
                },
                SCHEDULER_SCHEMA,
            )
        )

    def test_interface_mesh_mismatch_reports_actual_and_source_dimensions(self):
        coords = np.array([[0, 0, 0], [0.5, 0, 0]])
        with patch(
            "pythonagent.execution.eigenval",
            return_value=(np.zeros((2, 8)), np.array([[0, 0, 0], [0.25, 0, 0]])),
        ):
            with self.assertRaises(InterfaceMismatch) as error:
                NativeExecutor(
                    {"mesh_grid": [2, 1, 1]}, Journal(self.run)
                ).verify_interface(self.plan(), self.run, coords, self.evidence)
        self.assertEqual(error.exception.details["expected_kpoint_count"], 2)
        self.assertEqual(error.exception.details["source_num_bands"], 4)
        self.assertEqual(error.exception.details["actual_energy_shape"], [2, 8])
        self.assertFalse(error.exception.details["kpoint_sets_match"])

    def test_bad_json_list_pointer_returns_tool_error_instead_of_crashing(self):
        (self.run / "list.json").write_text("[1]")
        tools = ToolBox(
            self.evidence,
            PlanStore(self.run, self.evidence, Journal(self.run)),
            Journal(self.run),
        )
        result = tools.call(
            "scheduler", "read_record", {"path": "run/list.json", "pointer": "/10"}
        )
        self.assertIn("tool_error", result)

    def test_malformed_scheduler_tool_arguments_are_repairable(self):
        flow, _, _ = self.workflow(
            [
                *author(self.plan()),
                approve(),
                ("scheduler", {"tool": "read_record", "arguments": "not an object"}),
                execute(),
                complete(),
            ]
        )
        result = flow.run()
        self.assertEqual(result["stop"], "model_complete", result)

    def test_retry_budget_is_enforced_after_real_diagnostic_read(self):
        script = [
            *author(self.plan()),
            approve(),
            execute(),
            (
                "scheduler",
                {
                    "tool": "read_record",
                    "arguments": {"path": "run/trials/trial_001/failure.json"},
                },
            ),
            (
                "scheduler",
                lambda s: {
                    "action": "retry_execution",
                    "plan_id": s["current_plan"]["plan_id"],
                    "reason": "Retry",
                    "diagnosis_refs": ["run/trials/trial_001/failure.json"],
                },
            ),
            stop(),
        ]
        flow, _, backend = self.workflow(
            script,
            LoggedExecutor([RuntimeError("failure")]),
            scheduler={"enabled": True, "max_execution_retries": 0},
        )
        result = flow.run()
        self.assertEqual(result["stop"], "operational_blocked", result)
        self.assertEqual(len(backend.executions), 1)

    def test_caught_exception_cannot_be_sent_through_scientific_revision(self):
        flow, client, backend = self.workflow(
            [
                *author(self.plan()),
                approve(),
                execute(),
                (
                    "scheduler",
                    {
                        "action": "deliver_result",
                        "reason": "Ask science to repair execution",
                    },
                ),
                stop(),
            ],
            LoggedExecutor([RuntimeError("technical fault")]),
        )
        result = flow.run()
        self.assertEqual(result["stop"], "operational_blocked", result)
        self.assertEqual(result["plan_versions"], 1)
        self.assertFalse(any(role == "evaluator" for role, _, _ in client.seen))
        self.assertEqual(len(backend.executions), 1)

    def test_adapter_returned_result_can_still_be_assessed_by_scientific_role(self):
        script = [
            *author(self.plan()),
            approve(),
            execute(),
            (
                "scheduler",
                {
                    "action": "deliver_result",
                    "reason": "Assess recorded incomplete solver result",
                },
            ),
            (
                "evaluator",
                {
                    "action": "complete",
                    "reason": "Source blocker",
                    "observations": ["DIS unknown"],
                    "termination_basis": "data_blocker",
                    "remaining_work": ["Obtain solver evidence"],
                },
            ),
        ]
        flow, _, _ = self.workflow(
            script,
            FixtureExecutor(
                [{"status": "execution_failed", "observations": ["DIS unknown"]}]
            ),
        )
        result = flow.run()
        self.assertEqual(result["stop"], "model_data_blocked", result)

    def test_actual_constraints_remain_scientific_role_choices(self):
        revised = self.plan()
        revised["windows_relative_eV"]["frozen_eV"] = [-3, -0.6]
        backend = FixtureExecutor(
            [
                ExecutionConstraint(
                    [
                        {
                            "code": "frozen_dimension",
                            "message": "Actual grid dimension constraint",
                        }
                    ]
                ),
                {"status": "completed", "observations": []},
            ]
        )
        flow, client, _ = self.workflow(
            [
                *author(self.plan()),
                approve(),
                execute(),
                *author(revised),
                approve(),
                execute(),
                complete(),
            ],
            backend,
        )
        result = flow.run()
        self.assertEqual(result["stop"], "model_complete", result)
        self.assertEqual(result["plan_versions"], 2)
        self.assertEqual(backend.executions[1], revised)
        states = [state for role, state, _ in client.seen if role == "planner"]
        self.assertIn(
            "Actual grid dimension constraint", json.dumps(states[2]["handoff"])
        )
