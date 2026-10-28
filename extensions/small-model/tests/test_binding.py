import json

from pythonagent.execution import NativeExecutor
from pythonagent.journal import Journal
from pythonagent.plans import PlanStore
from pythonagent.state import file_sha, immutable_json
from support import CaseTest


class BindingTests(CaseTest):
    def test_metadata_and_approval_cannot_rebind_modified_documents(self):
        store = PlanStore(self.run, self.evidence, Journal(self.run))
        written = store.write("planner", self.plan())
        store.approve("reviewer", written["plan_id"], "Explicit fixture review")
        folder = store.root / written["plan_id"]
        metadata = json.loads((folder / "metadata.json").read_text())
        metadata["author"] = "other"
        (folder / "metadata.json").write_text(json.dumps(metadata))
        with self.assertRaisesRegex(ValueError, "version binding"):
            store.execution_input()
        (folder / "metadata.json").write_text(
            json.dumps(store.versions[written["plan_id"]])
        )
        approval = json.loads((folder / "approval.json").read_text())
        approval["reason"] = "External edit"
        (folder / "approval.json").write_text(json.dumps(approval))
        with self.assertRaisesRegex(ValueError, "approval record changed"):
            store.execution_input()

    def test_native_binding_checks_sources_and_materialized_inputs(self):
        interface = self.run / "interface"
        interface.mkdir()
        (interface / "INCAR").write_text("NUM_WANN = 3\n")
        (self.run / "approved-plan.json").write_text(json.dumps(self.plan()))
        source = self.case / "02_band/INCAR"
        source.write_text("ENCUT = 400\n")
        binding = {
            "plan_sha256": file_sha(self.run / "approved-plan.json"),
            "input_files": {"INCAR": file_sha(interface / "INCAR")},
            "source_files": {"02_band/INCAR": file_sha(source)},
        }
        immutable_json(self.run / "input_binding.json", binding)
        NativeExecutor.verify_binding(self.run, self.evidence)
        source.write_text("ENCUT = 500\n")
        with self.assertRaisesRegex(ValueError, "Source input changed"):
            NativeExecutor.verify_binding(self.run, self.evidence)
        source.write_text("ENCUT = 400\n")
        (interface / "INCAR").write_text("NUM_WANN = 8\n")
        with self.assertRaisesRegex(ValueError, "execution input changed"):
            NativeExecutor.verify_binding(self.run, self.evidence)
