import copy
import json

from pythonagent.contracts import parse_answer
from pythonagent.journal import Journal
from pythonagent.plans import PlanStore
from pythonagent.state import RunLock, digest
from pythonagent.tools import ToolBox
from pythonagent.validation import validate_plan, execution_count_errors
from support import CaseTest


class BoundaryTests(CaseTest):
    def test_weak_projection_and_top_clipping_are_facts_not_vetoes(self):
        report = validate_plan(self.plan(), self.evidence)
        self.assertTrue(report["valid"], report)
        self.assertEqual(
            report["facts"]["computed_top_at_or_below_dis_upper_kpoints"], [1, 2]
        )
        self.assertFalse(execution_count_errors(report["facts"]))
        self.assertNotIn("requires_more_data", report["facts"])
        self.assertNotIn("status", report["facts"])

    def test_complete_windows_required_and_never_filled(self):
        plan = self.plan()
        del plan["windows_relative_eV"]
        before = copy.deepcopy(plan)
        report = validate_plan(plan, self.evidence)
        self.assertFalse(report["valid"])
        self.assertEqual(before, plan)

    def test_fact_errors_batched_without_scientific_suggestions(self):
        plan = self.plan()
        plan["num_wann"] = 2
        plan["evidence_refs"] = [
            {
                **self.evidence.source,
                "k_band_1based": [1, 1],
                "energy_relative_eV": 123,
                "channel": "Cu:p",
                "raw_projection": 0.2,
            }
        ]
        report = validate_plan(plan, self.evidence)
        self.assertFalse(report["valid"])
        self.assertEqual(
            {r["code"] for r in report["errors"]},
            {"dimension_mismatch", "source_value_mismatch"},
        )
        self.assertEqual(len(report["errors"]), 3)

    def test_dis_solver_settings_remain_explicit_with_zero_localization(self):
        plan = self.plan()
        plan["solver"].update(num_iter=0, dis_num_iter=20000, dis_conv_tol=1e-10)
        self.assertTrue(validate_plan(plan, self.evidence)["valid"])

    def test_objective_window_order_and_range_checks(self):
        plan = self.plan()
        plan["windows_relative_eV"]["frozen_eV"] = [-10, 101]
        plan["target_band_ranges_1based"] = [[1, 5]]
        codes = {e["code"] for e in validate_plan(plan, self.evidence)["errors"]}
        self.assertIn("window_order", codes)
        self.assertIn("source_range", codes)

    def test_source_revision_changes_require_new_facts(self):
        with (self.case / "02_band/charge.npz").open("ab") as stream:
            stream.write(b"changed")
        report = validate_plan(self.plan(), self.evidence)
        self.assertFalse(report["valid"])
        self.assertIn("source_changed", {r["code"] for r in report["errors"]})

    def test_plan_version_and_approval_integrity(self):
        journal = Journal(self.run)
        store = PlanStore(self.run, self.evidence, journal)
        first = store.write("planner", self.plan())
        store.approve("reviewer", first["plan_id"], "scientific reason")
        self.assertEqual(store.execution_input()[0], self.plan())
        second = store.write("planner", self.plan())
        self.assertNotEqual(first["plan_id"], second["plan_id"])
        self.assertIsNone(store.approved)
        with self.assertRaises(ValueError):
            store.execution_input()
        with self.assertRaises(ValueError):
            store.approve("reviewer", first["plan_id"], "stale")
        with self.assertRaises(ValueError):
            store.write("reviewer", self.plan())
        with self.assertRaises(ValueError):
            store.approve("planner", second["plan_id"], "wrong role")

    def test_tampered_document_is_not_approved_or_executed(self):
        store = PlanStore(self.run, self.evidence, Journal(self.run))
        row = store.write("planner", self.plan())
        path = store.root / row["plan_id"] / "plan.json"
        path.write_text(path.read_text() + " ")
        with self.assertRaisesRegex(ValueError, "changed outside"):
            store.read()

    def test_each_window_diagnostic_is_bound_to_current_plan(self):
        journal = Journal(self.run)
        store = PlanStore(self.run, self.evidence, journal)
        tools = ToolBox(self.evidence, store, journal)
        first = store.write("planner", self.plan())
        plan = self.plan()
        plan["windows_relative_eV"]["frozen_eV"] = [-3, 2.5]
        second = store.write("planner", plan)
        old = tools.call(
            "reviewer", "window_diagnostics", {"plan_id": first["plan_id"]}
        )
        current = tools.call(
            "reviewer", "window_diagnostics", {"plan_id": second["plan_id"]}
        )
        self.assertIn("tool_error", old)
        self.assertEqual(current["windows_relative_eV"]["frozen_eV"], [-3, 2.5])

    def test_plan_errors_return_from_write_tool_and_remain_auditable(self):
        journal = Journal(self.run)
        store = PlanStore(self.run, self.evidence, journal)
        tools = ToolBox(self.evidence, store, journal)
        plan = self.plan()
        del plan["solver"]
        result = tools.call("planner", "write_plan", {"plan": plan})
        self.assertFalse(result["valid"])
        self.assertTrue((store.root / result["plan_id"] / "plan.json").exists())
        with self.assertRaises(ValueError):
            store.read()

    def test_read_scope_blocks_escape_symlinks_and_old_sessions(self):
        outside = self.root / "secret.json"
        outside.write_text("{}")
        (self.case / "escape.json").symlink_to(outside)
        for path in [
            "case/../secret.json",
            "case/escape.json",
            "case/.agent/sessions/old.json",
        ]:
            with self.assertRaises(ValueError):
                self.evidence.resolve(path)

    def test_lock_and_hash_chain(self):
        with RunLock(self.run / "lock"):
            with self.assertRaises(RuntimeError):
                with RunLock(self.run / "lock"):
                    pass
        journal = Journal(self.run)
        journal.record("first", prompt="p", response="r")
        journal.record("second")
        rows = [json.loads(s) for s in journal.path.read_text().splitlines()]
        previous = None
        for row in rows:
            sha = row.pop("event_sha256")
            self.assertEqual(digest(row), sha)
            self.assertEqual(row["previous_event"], previous)
            previous = sha

    def test_reasoning_and_answer_parsing_rejects_ambiguous_or_nonfinite_json(self):
        self.assertEqual(
            parse_answer(
                '<think>private reasoning</think>```json\n{"action":"stop"}\n```'
            ),
            {"action": "stop"},
        )
        for text in ['{"a":1}{"a":2}', '{"x":NaN}', "<think>unfinished"]:
            with self.assertRaises(ValueError):
                parse_answer(text)
