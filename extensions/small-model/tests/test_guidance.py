import hashlib
import shutil
from unittest.mock import patch

from pythonagent import guidance
from pythonagent.guidance import original, role_guidance, source_catalog
from pythonagent.journal import Journal
from pythonagent.plans import PlanStore
from pythonagent.roles import role_definitions
from pythonagent.tools import ToolBox
from pythonagent.workflow import Workflow
from support import CaseTest, FixtureExecutor


class GuidanceTests(CaseTest):
    def test_full_mcp_source_library_and_both_project_skills_are_bundled(self):
        records = source_catalog()["documents"]
        self.assertEqual(len(records), 28)
        sources = {d["source_repository_path"] for d in records}
        self.assertIn("SKILL.md", sources)
        self.assertIn("wannier_mcp/workflow/SKILL.md", sources)
        self.assertIn("wannier_mcp/workflow/docs/DFT-PREPARATION.md", sources)
        self.assertIn("wannier_mcp/workflow/docs/MANIFOLD-SELECTION.md", sources)
        for record in records:
            raw = (guidance.BASE / record["path"].removeprefix("guides/")).read_bytes()
            self.assertEqual(hashlib.sha256(raw).hexdigest(), record["sha256"])
            self.assertEqual(len(raw.decode().splitlines()), record["total_lines"])

    def test_role_scientific_text_is_exact_original_with_explicit_provenance(self):
        for role, definition in role_definitions().items():
            for fragment in role_guidance(role):
                source = fragment["source"]
                raw = (
                    guidance.BASE / source["path"].removeprefix("guides/")
                ).read_text()
                lines = raw.splitlines(keepends=True)
                expected = "".join(
                    lines[
                        fragment["start_line_1based"] - 1 : fragment["end_line_1based"]
                    ]
                )
                self.assertEqual(fragment["content"], expected)
                self.assertEqual(definition.system.count(expected), 1)
                self.assertIn(source["sha256"], definition.system)
                self.assertIn(source["source_repository_path"], definition.system)
            self.assertNotIn(
                "This document guides scientific judgment by the four roles.",
                definition.system,
            )
            self.assertIn("guides/mcp-sources.json", definition.system)

    def test_role_routing_provides_original_decision_and_result_guidance(self):
        definitions = role_definitions()
        for role in ("planner", "reviewer"):
            self.assertIn("# First-trial decision card", definitions[role].system)
            self.assertIn(
                "# Orbital manifolds and windows: canonical decision sequence",
                definitions[role].system,
            )
            self.assertNotIn("## Tool loop", definitions[role].system)
            self.assertNotIn("# Window retry card", definitions[role].system)
        for role in ("evaluator", "strategist"):
            self.assertIn("# Window retry card", definitions[role].system)
            self.assertIn("Diagnostics use per-k one-to-one", definitions[role].system)
            self.assertNotIn("# First-trial decision card", definitions[role].system)
        self.assertIn(
            "authoritative operational interpretation", definitions["planner"].system
        )
        self.assertIn("There is no propose/seal tool", definitions["planner"].system)

    def test_skill_excerpt_does_not_silently_include_other_numbered_steps(self):
        fragments = original("SKILL.md", paragraphs=("1. Declare the physical task",))
        self.assertEqual(len(fragments), 1)
        self.assertNotIn("2. Use the decision card", fragments[0]["content"])
        self.assertEqual(
            fragments[0]["start_line_1based"], fragments[0]["end_line_1based"]
        )

    def test_remaining_originals_can_be_read_through_existing_tools(self):
        journal = Journal(self.run)
        tools = ToolBox(
            self.evidence, PlanStore(self.run, self.evidence, journal), journal
        )
        index = tools.call(
            "planner",
            "read_record",
            {"path": "guides/mcp-sources.json", "pointer": "/documents", "limit": 5},
        )
        self.assertEqual(index["total_rows"], 28)
        result = tools.call(
            "planner",
            "read_text",
            {"path": "guides/mcp/docs/DFT-PREPARATION.md", "limit": 12},
        )
        self.assertNotIn("tool_error", result)
        self.assertEqual(
            result["source"]["sha256"],
            next(
                d["sha256"]
                for d in source_catalog()["documents"]
                if d["path"] == result["source"]["path"]
            ),
        )
        self.assertIn("#", result["text"])

    def test_retry_scientific_card_is_delivered_only_after_execution_attempt(self):
        flow = Workflow(
            self.case,
            self.root / "flows",
            {"plotting": {"enabled": False}},
            executor=FixtureExecutor(),
        )
        self.assertEqual(flow.context("planner", None)["stage_guidance"], [])
        flow.history = [{"record_path": "run/trials/trial_001/result.json"}]
        for role in ("planner", "reviewer"):
            actual = flow.context(role, None)["stage_guidance"]
            self.assertEqual(actual, guidance.retry_guidance())
            self.assertIn("# Window retry card", actual[0]["content"])
        self.assertEqual(flow.context("evaluator", None)["stage_guidance"], [])

    def test_changed_source_is_rejected_instead_of_using_an_untracked_summary(self):
        copied = self.root / "copied-guides"
        shutil.copytree(guidance.BASE, copied)
        changed = copied / "mcp/docs/CONSTRUCTION-OVERVIEW.md"
        changed.write_text(changed.read_text() + "\nUntracked change\n")
        guidance.source_catalog.cache_clear()
        try:
            with patch("pythonagent.guidance.BASE", copied):
                with self.assertRaisesRegex(
                    ValueError, "differs from its recorded original"
                ):
                    source_catalog()
        finally:
            guidance.source_catalog.cache_clear()
