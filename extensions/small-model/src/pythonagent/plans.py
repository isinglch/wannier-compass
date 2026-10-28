"""Planner-only immutable proposals and version-bound reviewer approval."""

from pathlib import Path
import json

from .state import RunLock, digest, file_sha, immutable_json
from .validation import validate_plan


class PlanStore:
    def __init__(self, run, evidence, journal, plotter=None):
        self.run, self.evidence, self.journal = Path(run), evidence, journal
        self.root = self.run / "plans"
        self.root.mkdir(exist_ok=True)
        self.latest = None
        self.approved = None
        self.counter = 0
        self.versions = {}
        self.plotter = plotter
        self.visualizations = {}

    def write(self, role, plan):
        if role != "planner":
            raise ValueError("Only planner may write a scientific plan")
        with RunLock(self.run / "plan.lock"):
            self.counter += 1
            plan_hash = digest(plan)
            identifier = f"plan-{self.counter:04d}-{plan_hash[:12]}"
            directory = self.root / identifier
            directory.mkdir()
            immutable_json(directory / "plan.json", plan)
            report = validate_plan(plan, self.evidence)
            metadata = {
                "plan_id": identifier,
                "plan_sha256": plan_hash,
                "document_sha256": file_sha(directory / "plan.json"),
                "input_revision": self.evidence.revision,
                "author": "planner",
            }
            immutable_json(directory / "metadata.json", metadata)
            self.versions[identifier] = dict(metadata)
            immutable_json(directory / "validation.json", report)
            self.latest = identifier
            self.approved = None
            if report["valid"] and self.plotter is not None:
                self.visualizations[identifier] = self.plotter.plan(plan, directory)
            self.journal.record(
                "plan_written",
                {**metadata, "valid": report["valid"], "errors": report["errors"]},
            )
            return {
                **metadata,
                "path": f"run/plans/{identifier}/plan.json",
                **report,
                "scientific_approval": None,
                "visualization": self.visualizations.get(identifier),
                "next_step": "Submit this version to reviewer only when validation is valid; otherwise repair it.",
            }

    def read(self, identifier=None, *, require_valid=True):
        identifier = identifier or self.latest
        if not identifier or identifier != self.latest:
            raise ValueError(
                "Use the current plan version; older versions are historical"
            )
        directory = self.root / identifier
        plan = json.loads((directory / "plan.json").read_text())
        meta = json.loads((directory / "metadata.json").read_text())
        if (
            meta != self.versions[identifier]
            or digest(plan) != meta["plan_sha256"]
            or file_sha(directory / "plan.json") != meta["document_sha256"]
        ):
            raise ValueError(
                "Plan changed outside its write tool; version binding failed"
            )
        report = validate_plan(plan, self.evidence)
        if require_valid and not report["valid"]:
            raise ValueError(
                "Plan is not valid: " + json.dumps(report["errors"], ensure_ascii=False)
            )
        return plan, meta, report

    def approve(self, role, identifier, reason):
        if role != "reviewer":
            raise ValueError("Only reviewer may approve a scientific plan")
        _, meta, _ = self.read(identifier)
        record = {**meta, "reviewer": "reviewer", "reason": reason}
        immutable_json(self.root / identifier / "approval.json", record)
        self.approved = record
        self.journal.record("plan_approved", record)
        return record

    def execution_input(self):
        if not self.approved or self.approved["plan_id"] != self.latest:
            raise ValueError("Current plan has no reviewer approval")
        plan, meta, _ = self.read(self.latest)
        if self.approved["plan_sha256"] != meta["plan_sha256"]:
            raise ValueError("Approval belongs to another plan")
        approval_path = self.root / self.latest / "approval.json"
        if json.loads(approval_path.read_text()) != self.approved:
            raise ValueError("Reviewer approval record changed outside its decision")
        return plan, self.root / self.latest / "plan.json", dict(self.approved)

    def context(self):
        if self.latest is None:
            return None
        plan, meta, validation = self.read(require_valid=False)
        return {
            **meta,
            "plan": plan,
            "validation": validation,
            "approval": self.approved,
            "visualization": self.visualizations.get(self.latest),
        }
