"""Local API checks; the invented fixture cannot prove physical selection quality."""

from __future__ import annotations

import hashlib
import re
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from wannier_mcp import __version__, server


REPO = Path(__file__).resolve().parents[1]


class AdapterSmoke(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.environment = patch.dict(os.environ, {"WAN_WORKSPACE_ROOT": str(self.root)}, clear=False)
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def test_synthetic_band_inspection_and_review_packet(self) -> None:
        case = self.root / "invented"
        subprocess.run(
            [sys.executable, str(REPO / "examples/make_synthetic_case.py"),
             "--out-dir", str(case)], check=True, capture_output=True, text=True,
        )
        cache = case / "02_band/charge.npz"
        evidence = server.prepare_evidence(str(cache), str(case / "evidence"))
        self.assertTrue(evidence["ok"], evidence)
        self.assertEqual(evidence["status"], "scientific_decision_required")
        self.assertTrue((case / "evidence/inspection.json").is_file())
        self.assertTrue((case / "evidence/packet/plots/projected_bands.png").is_file())
        import json
        draft = json.loads((case / "evidence/review.json").read_text())
        self.assertTrue(all(row["action"] is None for row in draft["orbital_decisions"]))
        self.assertNotIn("window_override", draft)

    def test_workspace_confinement_and_sealed_record(self) -> None:
        with self.assertRaisesRegex(ValueError, "within WAN_WORKSPACE_ROOT"):
            server.save_draft_json("../outside/review.json", {"x": 1})
        draft = self.root / "case/review.json"
        saved = server.save_draft_json(str(draft), {"x": 1})
        self.assertEqual(saved["sha256"], hashlib.sha256(draft.read_bytes()).hexdigest())
        with self.assertRaisesRegex(ValueError, "current SHA256"):
            server.save_draft_json(str(draft), {"x": 2})
        (draft.parent / "STAGE.json").write_text("{}")
        with self.assertRaisesRegex(ValueError, "Sealed decision"):
            server.save_draft_json(str(draft), {"x": 2}, saved["sha256"])

    def test_submit_is_opt_in_and_trial_id_is_local_name(self) -> None:
        with patch.dict(os.environ, {"WAN_ALLOW_SUBMIT": "0"}):
            with self.assertRaisesRegex(ValueError, "Submission disabled"):
                server.submit_wannier("case", "site.json", execute=True)
        with self.assertRaisesRegex(ValueError, "trial_id"):
            server._trial_id("../another-case")

    def test_bounded_document_access(self) -> None:
        guide = server.read_text("workflow/docs/FIRST-TRIAL-CARD.md", limit=32)
        self.assertEqual(len(guide["text"]), 32)
        self.assertGreater(guide["total_chars"], 32)
        with self.assertRaisesRegex(ValueError, "escapes"):
            server.read_text("workflow/../../private.md")
        (self.root / "WAVECAR").write_bytes(b"not a text artifact")
        with self.assertRaisesRegex(ValueError, "Only text"):
            server.read_text("WAVECAR")

    def test_guide_paths_are_readable_without_workspace(self) -> None:
        with patch.dict(os.environ, {"WAN_WORKSPACE_ROOT": ""}):
            guide = server.workflow_guide()
            for key in ("runtime_card", "decision_card", "retry_card", "stage_records",
                        "interface_contract", "optional_slurm_example", "documentation_policy", "skill"):
                record = server.read_text(guide[key], limit=50)
                self.assertEqual(record["path"], guide[key])
                self.assertEqual(record["sha256"], hashlib.sha256(Path(guide[key]).read_bytes()).hexdigest())
                self.assertGreater(record["next_offset"], 0)
            with self.assertRaisesRegex(ValueError, "Markdown"):
                server.read_text(str(server.TOOLS / "orbital_evidence.py"))
        with tempfile.TemporaryDirectory() as outside:
            path = Path(outside) / "private.md"
            path.write_text("outside the authorized workspace")
            with self.assertRaisesRegex(ValueError, "within WAN_WORKSPACE_ROOT"):
                server.read_text(str(path))

    def test_guide_version_matches_package(self) -> None:
        project = (REPO / "pyproject.toml").read_text()
        declared = re.search(r'^version\s*=\s*"([^"]+)"', project, re.M).group(1)
        self.assertEqual(server.workflow_guide()["version"], __version__)
        self.assertEqual(__version__, declared)


if __name__ == "__main__":
    unittest.main()
