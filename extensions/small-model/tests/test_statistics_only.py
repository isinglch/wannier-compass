import json
from types import SimpleNamespace
from support import CaseTest
import test_analysis_tools as fixtures
from pythonagent.journal import Journal
from pythonagent.specs import tool_specs_for
from pythonagent.tools import ToolBox


class StatisticsOnlyTests(CaseTest):
    def setUp(self):
        super().setUp()
        fixtures.AnalysisToolTests.path_cache(self)
        self.box = ToolBox(
            self.evidence,
            SimpleNamespace(read=lambda _: (self.plan(), {"plan_id": "fixture"}, {})),
            Journal(self.run),
        )

    def call(self, name, **args):
        return self.box.call("planner", name, args)

    def test_removed_raw_tool_is_not_advertised_or_callable(self):
        for role in ("planner", "reviewer", "evaluator", "strategist", "scheduler"):
            self.assertNotIn("band_slice", tool_specs_for(role))
        self.assertIn("tool_error", self.call("band_slice", band_lo=1, band_hi=4))

    def test_file_and_alias_raw_bypasses_are_denied_for_both_readers(self):
        for name in ("EIGENVAL", "PROCAR", "OUTCAR", "wannier90.eig", "comparison.npz"):
            p = self.case / "02_band" / name
            p.write_text('{"raw":[1,2,3]}')
            for tool in ("read_text", "read_record"):
                self.assertIn(
                    "tool_error", self.call(tool, path="case/02_band/" + name)
                )
        alias = self.case / "02_band/POSCAR"
        alias.symlink_to(self.case / "02_band/EIGENVAL")
        self.assertIn("tool_error", self.call("read_text", path="case/02_band/POSCAR"))
        self.assertIn(
            "tool_error", self.call("read_record", path="case/02_band/charge.npz")
        )

    def test_diagnostics_view_preserves_statistics_but_denies_all_pairs(self):
        p = self.run / "trials/trial_001"
        p.mkdir(parents=True)
        data = {
            "counts": {"expected_states": 10},
            "matched_error_metrics": {"mae_eV": 0.2},
            "per_k": [{"pairs": [{"raw": 999}]}],
            "method": "fixture",
        }
        (p / "diagnostics.json").write_text(
            json.dumps({"target": data, "baseline_target": data})
        )
        v = self.call("read_record", path="run/trials/trial_001/diagnostics.json")
        self.assertNotIn("tool_error", v)
        self.assertNotIn("per_k", v["value"]["target"])
        self.assertEqual(v["value"]["target"]["counts"], data["counts"])
        self.assertIn("/target/per_k", v["access_policy"]["omitted_pointers"])
        self.assertIn(
            "tool_error",
            self.call(
                "read_record",
                path="run/trials/trial_001/diagnostics.json",
                pointer="/target/per_k",
            ),
        )
        self.assertIn(
            "tool_error",
            self.call("read_text", path="run/trials/trial_001/diagnostics.json"),
        )

    def test_summary_statistics_and_exact_witnesses_remain_available(self):
        for tool, args in [
            ("anchor_table", {}),
            ("orbital_character", {"band": 1}),
            ("ef_crossings", {}),
        ]:
            self.assertNotIn("tool_error", self.call(tool, **args))
        v = self.call("projection_audit", plan_id="fixture")
        self.assertNotIn("tool_error", v)
        self.assertTrue(v["coverage"]["complete_computational_scan"])
        self.assertTrue(v["witnesses"])
        self.assertNotIn("states_pointer", v)
        f = self.evidence.resolve(v["full_record"])
        self.assertNotIn("states", json.loads(f.read_text()))
        summary = self.call("read_record", path=v["full_record"])
        self.assertNotIn("tool_error", summary)
        self.assertIn(
            "tool_error",
            self.call("read_record", path=v["full_record"], pointer="/states"),
        )

    def test_guides_settings_and_execution_logs_remain_readable(self):
        (self.case / "CALCULATION.md").write_text("fixture configuration\n")
        p = self.run / "trials/trial_001"
        p.mkdir(parents=True)
        (p / "wannier90.wout").write_text("All done: wannier90 exiting\n")
        (p / "vasp.stdout").write_text("converged\n")
        for name in (
            "guides/planner.md",
            "case/CALCULATION.md",
            "run/trials/trial_001/wannier90.wout",
            "run/trials/trial_001/vasp.stdout",
        ):
            self.assertNotIn("tool_error", self.call("read_text", path=name))
        for name in ("journal.jsonl", "leaked.json"):
            (self.run / name).write_text('{"raw":[1,2,3]}')
            self.assertIn("tool_error", self.call("read_record", path="run/" + name))

    def test_legacy_projection_archive_cannot_reexpose_state_table(self):
        p = self.run / "observations"
        p.mkdir()
        (p / "legacy.json").write_text(
            json.dumps(
                {
                    "coverage": {"kpoints": 2},
                    "states": [{"raw_projection": {"Cu:s": 0.8}}],
                }
            )
        )
        v = self.call("read_record", path="run/observations/legacy.json")
        self.assertNotIn("states", v["value"])
        self.assertIn(
            "tool_error",
            self.call(
                "read_record", path="run/observations/legacy.json", pointer="/states"
            ),
        )
