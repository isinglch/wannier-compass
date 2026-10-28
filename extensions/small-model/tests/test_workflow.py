import json

from pythonagent.execution import ExecutionConstraint
from pythonagent.workflow import Workflow
from support import CaseTest, FixtureExecutor, ScriptedLLM, approve, author


class WorkflowTests(CaseTest):
    def workflow(self, script, executor=None, **config):
        client = ScriptedLLM(script)
        backend = executor or FixtureExecutor()
        flow = Workflow(
            self.case,
            self.root / "runs",
            {
                "execute": True,
                "scheduler": {"enabled": False},
                "max_trials": 3,
                "plotting": {"enabled": False},
                **config,
            },
            llm=client,
            executor=backend,
        )
        return flow, client, backend

    def test_model_approved_low_projection_wide_window_runs_exactly(self):
        flow, client, backend = self.workflow(
            [
                *author(self.plan()),
                approve(),
                (
                    "evaluator",
                    {
                        "action": "complete",
                        "termination_basis": "objective_satisfied",
                        "remaining_work": [],
                        "reason": "Fixture task assessed",
                        "observations": ["measured"],
                    },
                ),
            ]
        )
        result = flow.run()
        self.assertEqual(result["stop"], "model_complete", result)
        self.assertEqual(backend.executions, [self.plan()])
        self.assertFalse(client.script)
        self.assertEqual(
            [r[0] for r in client.seen], ["planner", "planner", "reviewer", "evaluator"]
        )

    def test_reviewer_returns_scientific_issue_to_planner_not_a_program_veto(self):
        revised = self.plan()
        revised["windows_relative_eV"]["dis_eV"] = [-5, 20]
        flow, client, backend = self.workflow(
            [
                *author(self.plan()),
                (
                    "reviewer",
                    lambda state: {
                        "action": "request_revision",
                        "plan_id": state["current_plan"]["plan_id"],
                        "issues": ["Review upper target/background role"],
                        "reason": "Scientific concern",
                    },
                ),
                *author(revised),
                approve(),
                (
                    "evaluator",
                    {
                        "action": "complete",
                        "termination_basis": "objective_satisfied",
                        "remaining_work": [],
                        "reason": "done",
                        "observations": [],
                    },
                ),
            ]
        )
        result = flow.run()
        self.assertEqual(result["stop"], "model_complete", result)
        self.assertEqual(len(backend.executions), 1)
        self.assertEqual(backend.executions[0], revised)
        self.assertEqual(result["plan_versions"], 2)
        planner = [f for role, f, _ in client.seen if role == "planner"]
        self.assertIn(
            "Review upper target/background role", json.dumps(planner[2]["handoff"])
        )

    def test_failed_run_evaluation_strategy_revision_and_second_approval(self):
        revised = self.plan()
        revised["solver"]["dis_num_iter"] = 20
        backend = FixtureExecutor(
            [
                {"status": "execution_failed", "observations": ["solver diagnostic"]},
                {"status": "completed", "observations": ["second trial"]},
            ]
        )
        script = [
            *author(self.plan()),
            approve(),
            (
                "evaluator",
                {
                    "action": "iterate",
                    "reason": "Test solver hypothesis",
                    "observations": ["failure"],
                    "questions": [
                        "Was a longer disentanglement iteration budget needed?"
                    ],
                },
            ),
            (
                "strategist",
                {
                    "action": "request_revision",
                    "reason": "Test explicit solver change",
                    "strategy": "Choose a disentanglement budget trial",
                    "questions": ["Compare same target"],
                },
            ),
            *author(revised),
            approve(),
            (
                "evaluator",
                {
                    "action": "complete",
                    "termination_basis": "objective_satisfied",
                    "remaining_work": [],
                    "reason": "assessed",
                    "observations": [],
                },
            ),
        ]
        flow, client, _ = self.workflow(script, backend)
        result = flow.run()
        self.assertEqual(result["stop"], "model_complete", result)
        self.assertEqual(len(backend.executions), 2)
        self.assertEqual(backend.executions[1]["solver"]["dis_num_iter"], 20)

    def test_actual_execution_constraint_goes_to_planner_then_reviewer(self):
        backend = FixtureExecutor(
            [
                ExecutionConstraint(
                    [
                        {
                            "code": "frozen_dimension",
                            "message": "actual mesh has 4 frozen states for 3 Wannier functions",
                        }
                    ]
                ),
                {"status": "completed", "observations": []},
            ]
        )
        revised = self.plan()
        revised["windows_relative_eV"]["frozen_eV"] = [-3, -0.6]
        flow, client, _ = self.workflow(
            [
                *author(self.plan()),
                approve(),
                *author(revised),
                approve(),
                (
                    "evaluator",
                    {
                        "action": "complete",
                        "termination_basis": "objective_satisfied",
                        "remaining_work": [],
                        "reason": "done",
                        "observations": [],
                    },
                ),
            ],
            backend,
        )
        result = flow.run()
        self.assertEqual(result["stop"], "model_complete", result)
        self.assertEqual(len(backend.executions), 2)
        self.assertIn("actual mesh", json.dumps(client.seen[3][1]["handoff"]))

    def test_execution_disabled_still_produces_reviewed_artifact(self):
        flow, client, backend = self.workflow(
            [*author(self.plan()), approve()], execute=False
        )
        result = flow.run()
        self.assertEqual(result["stop"], "reviewed_plan_ready", result)
        self.assertFalse(backend.executions)

    def test_data_request_is_explicit_model_decision_and_capability_limit(self):
        flow, _, backend = self.workflow(
            [
                (
                    "planner",
                    {
                        "action": "request_data",
                        "reason": "Need wavefunction evidence",
                        "request": "Collect a supported wavefunction-overlap source",
                    },
                )
            ]
        )
        result = flow.run()
        self.assertEqual(result["stop"], "data_operation_unavailable", result)
        self.assertIn("scientific_decision", result)
        self.assertFalse(backend.executions)

    def test_no_execution_without_approval_even_when_protocol_budget_exhausted(self):
        flow, _, backend = self.workflow(
            [
                *author(self.plan()),
                (
                    "reviewer",
                    {
                        "action": "approve",
                        "plan_id": "historical-other-run",
                        "reason": "bad reference",
                    },
                ),
            ],
            max_repair_attempts=1,
        )
        result = flow.run()
        self.assertEqual(result["stop"], "protocol_budget_exhausted", result)
        self.assertFalse(backend.executions)

    def test_reviewer_cannot_write_and_can_recover_with_valid_action(self):
        flow, client, backend = self.workflow(
            [
                *author(self.plan()),
                (
                    "reviewer",
                    {"tool": "write_plan", "arguments": {"plan": self.plan()}},
                ),
                approve(),
                (
                    "evaluator",
                    {
                        "action": "complete",
                        "termination_basis": "objective_satisfied",
                        "remaining_work": [],
                        "reason": "done",
                        "observations": [],
                    },
                ),
            ]
        )
        result = flow.run()
        self.assertEqual(result["stop"], "model_complete", result)
        self.assertEqual(result["plan_versions"], 1)
        self.assertIn("unavailable", json.dumps(client.seen[3][2]))

    def test_operator_trial_budget_cannot_be_overridden_by_role(self):
        flow, client, backend = self.workflow(
            [
                *author(self.plan()),
                approve(),
                (
                    "evaluator",
                    {
                        "action": "iterate",
                        "reason": "want more",
                        "observations": [],
                        "questions": ["another trial"],
                    },
                ),
            ],
            max_trials=1,
        )
        result = flow.run()
        self.assertEqual(result["stop"], "trial_budget_exhausted", result)
        self.assertEqual(len(backend.executions), 1)

    def test_same_folder_new_workflow_has_no_reused_decisions(self):
        first, _, _ = self.workflow([*author(self.plan()), approve()], execute=False)
        one = first.run()
        second, client, _ = self.workflow(
            [*author(self.plan()), approve()], execute=False
        )
        two = second.run()
        self.assertNotEqual(one["run_dir"], two["run_dir"])
        self.assertIsNone(client.seen[0][1]["current_plan"])
        self.assertEqual(client.seen[0][1]["history"], [])

    def test_execution_mutating_approved_copy_is_an_integrity_failure(self):
        class Tamper(FixtureExecutor):
            def execute(self, plan, plan_file, *unused):
                plan_file.write_text("{}")
                return {"status": "completed", "observations": []}

        flow, _, _ = self.workflow([*author(self.plan()), approve()], Tamper())
        result = flow.run()
        self.assertEqual(result["stop"], "operational_error", result)
        self.assertIn("altered", result["detail"])
