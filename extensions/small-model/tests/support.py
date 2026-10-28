import copy
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np

from pythonagent.channels import SPD_COLUMNS
from pythonagent.evidence import Evidence


class CaseTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.case = self.root / "case"
        (self.case / "02_band").mkdir(parents=True)
        self.run = self.root / "run"
        self.run.mkdir()
        meta = {
            "species": ["Cu"],
            "counts": [1],
            "columns": list(SPD_COLUMNS),
            "spin_mode": "scalar",
            "sampling": "band_path",
            "ef": 5.0,
        }
        energy = np.array([[[-2.0, -1.0, 1.0, 5.0], [-2.0, -0.5, 2.0, 6.0]]]) + 5
        raw = np.zeros((1, 2, 4, 1, 9))
        raw[..., 0] = 0.7
        raw[..., 1] = 0.001
        raw[..., 4] = 0.9
        np.savez(
            self.case / "02_band/charge.npz",
            metadata=json.dumps(meta),
            energies=energy,
            raw=raw,
            occupations=np.zeros_like(energy),
            kcoords=np.array([[[0, 0, 0], [0.5, 0, 0]]]),
            path_geometry=json.dumps({"kcoords_type": "fractional"}),
        )
        self.evidence = Evidence(self.case, self.run)

    def plan(self):
        return {
            "schema_version": "model-plan-v1",
            "objective": "Fixture scientific trial",
            "input_revision": self.evidence.revision,
            "basis": ["Cu:p"],
            "num_wann": 3,
            "target_band_ranges_1based": [[1, 3]],
            "excluded_bands_1based": [],
            "windows_relative_eV": {"dis_eV": [-5, 100], "frozen_eV": [-3, 0]},
            "solver": {
                "num_iter": 0,
                "dis_num_iter": 10,
                "dis_conv_tol": 1e-8,
                "dis_conv_window": 3,
            },
            "reason": "Explicit model fixture decision, not a program-generated recommendation.",
            "evidence_refs": [self.evidence.source],
        }


class ScriptedLLM:
    def __init__(self, script):
        self.script = list(script)
        self.sessions = self
        self.seen = []

    def ask(self, role, instruction, *, facts=None, **unused):
        self.seen.append((role, copy.deepcopy(facts), copy.deepcopy(instruction)))
        if not self.script:
            raise AssertionError("Unexpected model call: " + role)
        expected, value = self.script.pop(0)
        if expected != role:
            raise AssertionError(f"Expected {expected}, got {role}")
        if callable(value):
            value = value(facts)
        return json.dumps(value), {}


def author(plan):
    return [
        ("planner", {"tool": "write_plan", "arguments": {"plan": copy.deepcopy(plan)}}),
        (
            "planner",
            lambda state: {
                "action": "submit",
                "plan_id": state["current_plan"]["plan_id"],
            },
        ),
    ]


def approve():
    return (
        "reviewer",
        lambda state: {
            "action": "approve",
            "plan_id": state["current_plan"]["plan_id"],
            "reason": "Reviewed fixture trial.",
        },
    )


class FixtureExecutor:
    def __init__(self, results=None):
        self.results = results or [{"status": "completed", "observations": ["fixture"]}]
        self.executions = []

    def validate(self):
        pass

    def capabilities(self):
        return {"adapter": "test-only-fixture", "checks": "No scientific defaults"}

    def execute(self, plan, plan_file, trial, evidence, baseline):
        self.executions.append(copy.deepcopy(plan))
        value = self.results.pop(0)
        if isinstance(value, Exception):
            raise value
        return value
