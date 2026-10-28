import copy
import json
import io
from contextlib import redirect_stdout
from unittest.mock import patch

import numpy as np

from pythonagent.evidence import Evidence
from pythonagent.journal import Journal
from pythonagent.plotting import (
    PlotManager,
    coordinates,
    render_dft,
    render_plan,
    render_fit,
    save_comparison,
    read_comparison,
    _bands,
)
from pythonagent.state import file_sha
from pythonagent.workflow import Workflow
from support import CaseTest, FixtureExecutor, ScriptedLLM, author, approve
from pythonagent.cli import main
from pythonagent.results import disentanglement_state


class PlottingTests(CaseTest):
    def test_no_dis_required_is_available_not_failed_or_provisional(self):
        self.prepared()
        plan = self.plan()
        dis = disentanglement_state("All done: wannier90 exiting", 10, 3, 3)
        path = self.run / "comparison.npz"
        save_comparison(
            path,
            self.evidence,
            plan,
            coordinates(self.evidence)[1],
            self.evidence.energy[:, :3],
            status="completed_no_disentanglement_required",
            disentanglement=dis,
        )
        manager = PlotManager(self.evidence, Journal(self.run))
        # The same detailed status must pass the plotting route and metadata checks.
        result = render_fit(self.evidence, plan, path, self.run / "no-dis-fit")
        self.assertFalse(result["provisional"])
        self.assertIsNone(result["disentanglement_converged"])
        self.assertIsNone(result["fit_pass"])
        self.assertEqual(
            manager.trial(
                plan, self.run, execution_status="completed_no_disentanglement_required"
            )["status"],
            "rendered",
        )

    def prepared(self, *, disconnected=False):
        """An authenticated path fixture, including repeated/disconnected endpoints."""
        with np.load(self.evidence.cache_path, allow_pickle=False) as data:
            payload = {key: np.array(data[key]) for key in data.files}
        meta = json.loads(str(payload["metadata"]))
        if disconnected:
            payload["energies"] = np.repeat(payload["energies"], 2, axis=1)
            payload["raw"] = np.repeat(payload["raw"], 2, axis=1)
            payload["occupations"] = np.repeat(payload["occupations"], 2, axis=1)
            payload["kcoords"] = np.array(
                [[[0, 0, 0], [0.5, 0, 0], [0, 0.5, 0], [0, 1, 0]]]
            )
            segments = [[1, 2], [3, 4]]
        else:
            segments = [[1, 2]]
        kpoints = self.case / "02_band/KPOINTS"
        kpoints.write_text("explicit fixture path identity\n")
        source = file_sha(kpoints)
        meta["source_files"] = {"kpoints": {"sha256": source}}
        payload["metadata"] = np.array(json.dumps(meta))
        geometry = {
            "kcoords_type": "fractional",
            "segments_1based": segments,
            "ticks": [
                {"index_1based": 1, "label": "G"},
                {"index_1based": 2, "label": "X"},
            ],
            "reciprocal_lattice_2pi_per_angstrom": (np.eye(3) * 2 * np.pi).tolist(),
            "source_kpoints_sha256": source,
        }
        payload["path_geometry"] = np.array(json.dumps(geometry))
        np.savez(self.evidence.cache_path, **payload)
        self.evidence = Evidence(self.case, self.run)
        return self.evidence

    def spectrum(self, path, plan=None, *, converged=True, baseline=None):
        plan = plan or self.plan()
        _, coords, _, _ = coordinates(self.evidence)
        wan = self.evidence.energy[:, : plan["num_wann"]] + 0.02
        save_comparison(
            path,
            self.evidence,
            plan,
            coords,
            wan,
            converged=converged,
            baseline=baseline,
        )

    def test_disconnected_path_has_no_jump_distance_or_cross_segment_line(self):
        self.prepared(disconnected=True)
        path, k, x, _ = coordinates(self.evidence)
        np.testing.assert_allclose(x, [0, np.pi, np.pi, 2 * np.pi])
        calls = []

        class Axes:
            def plot(self, *args, **kwargs):
                calls.append(args)

        _bands(Axes(), path, x, self.evidence.energy, color="grey")
        self.assertEqual([len(args[0]) for args in calls], [2, 2])
        self.assertEqual(k.shape, (4, 3))

    def test_mismatched_path_identity_is_rejected(self):
        self.prepared()
        (self.case / "02_band/KPOINTS").write_text("modified\n")
        with self.assertRaisesRegex(ValueError, "current KPOINTS"):
            coordinates(self.evidence)

    def test_real_png_pdf_dft_and_plan_preserve_raw_weights_and_plan(self):
        self.prepared()
        plan = self.plan()
        before = copy.deepcopy(plan)
        dft = render_dft(self.evidence, self.run / "dft")
        proposal = render_plan(self.evidence, plan, self.run / "plan")
        self.assertEqual(plan, before)
        self.assertIsNone(dft["fit_pass"])
        self.assertIsNone(proposal["fit_pass"])
        self.assertEqual(dft["groups"], ["Cu:s", "Cu:p", "Cu:d"])
        self.assertFalse(dft["raw_projection_scale"]["self_normalization"])
        self.assertEqual(proposal["windows_relative_eV"], plan["windows_relative_eV"])
        for directory, stems in (
            (self.run / "dft", ("dft_bands", "projected_bands")),
            (self.run / "plan", ("selection",)),
        ):
            for stem in stems:
                self.assertTrue(
                    (directory / (stem + ".png")).read_bytes().startswith(b"\x89PNG")
                )
                self.assertTrue(
                    (directory / (stem + ".pdf")).read_bytes().startswith(b"%PDF")
                )
        with self.assertRaises(FileExistsError):
            render_dft(self.evidence, self.run / "dft")

    def test_comparison_rejects_other_plan_k_order_and_energy_reference(self):
        self.prepared()
        plan = self.plan()
        path = self.run / "comparison.npz"
        self.spectrum(path, plan)
        read_comparison(self.evidence, plan, path)
        other = copy.deepcopy(plan)
        other["reason"] = "different exact plan"
        with self.assertRaisesRegex(ValueError, "exact source and plan"):
            read_comparison(self.evidence, other, path)
        with np.load(path, allow_pickle=False) as data:
            payload = {k: np.array(data[k]) for k in data.files}
        payload["kcoords"] = payload["kcoords"][::-1]
        np.savez(self.run / "bad-order.npz", **payload)
        with self.assertRaisesRegex(ValueError, "k sequence"):
            read_comparison(self.evidence, plan, self.run / "bad-order.npz")
        payload["kcoords"] = payload["kcoords"][::-1]
        meta = json.loads(str(payload["metadata"]))
        meta["energy_reference"] = "absolute"
        payload["metadata"] = np.array(json.dumps(meta))
        np.savez(self.run / "bad-reference.npz", **payload)
        with self.assertRaisesRegex(ValueError, "energy reference"):
            read_comparison(self.evidence, plan, self.run / "bad-reference.npz")

    def test_fit_reports_missing_baseline_states_and_provisional_convergence(self):
        self.prepared()
        plan = self.plan()
        baseline = copy.deepcopy(plan)
        baseline["target_band_ranges_1based"] = [[1, 4]]
        path = self.run / "comparison.npz"
        self.spectrum(path, plan, converged=False, baseline=baseline)
        result = render_fit(self.evidence, plan, path, self.run / "fit")
        self.assertTrue(result["provisional"])
        self.assertIsNone(result["fit_pass"])
        self.assertEqual(result["metrics"]["target"]["counts"]["missing_states"], 0)
        self.assertEqual(
            result["metrics"]["baseline_target"]["counts"]["missing_states"], 2
        )
        self.assertIn("fit_review.png", result["outputs"])
        self.assertIn("residuals.pdf", result["outputs"])
        self.assertTrue((self.run / "fit/residuals.json").is_file())

    def test_workflow_automatically_publishes_dft_plan_and_fit_before_evaluation(self):
        self.prepared()

        class ExecutedSpectrum(FixtureExecutor):
            def execute(self, plan, plan_file, trial, evidence, baseline):
                self.executions.append(copy.deepcopy(plan))
                _, coords, _, _ = coordinates(evidence)
                save_comparison(
                    trial / "comparison.npz",
                    evidence,
                    plan,
                    coords,
                    evidence.energy[:, : plan["num_wann"]],
                    baseline=baseline,
                    converged=True,
                )
                return {
                    "status": "completed",
                    "observations": ["fixture calculation, real plot pipeline"],
                }

        script = [
            *author(self.plan()),
            approve(),
            (
                "evaluator",
                {
                    "action": "complete",
                    "termination_basis": "objective_satisfied",
                    "remaining_work": [],
                    "reason": "fixture result",
                    "observations": [],
                },
            ),
        ]
        client = ScriptedLLM(script)
        flow = Workflow(
            self.case,
            self.root / "flows",
            {"execute": True, "scheduler": {"enabled": False}},
            llm=client,
            executor=ExecutedSpectrum(),
        )
        result = flow.run()
        self.assertEqual(result["stop"], "model_complete", result)
        self.assertTrue((flow.run_dir / "plots/dft/dft_bands.png").is_file())
        self.assertTrue(
            (
                flow.run_dir / "plans" / flow.plans.latest / "plots/selection.pdf"
            ).is_file()
        )
        self.assertTrue(
            (flow.run_dir / "trials/trial_001/plots/fit_review.png").is_file()
        )
        self.assertEqual(
            client.seen[0][1]["visualization"]["dft"]["status"], "rendered"
        )
        evaluator = next(state for role, state, _ in client.seen if role == "evaluator")
        self.assertEqual(
            evaluator["current_result"]["visualization"]["status"], "rendered"
        )
        self.assertEqual(flow.plans.read()[0], self.plan())
        self.assertTrue((flow.run_dir / "plots/index.html").is_file())

    def test_plot_failure_is_reported_without_a_scientific_veto(self):
        script = [*author(self.plan()), approve()]
        client = ScriptedLLM(script)
        with (
            patch(
                "pythonagent.plotting.render_dft",
                side_effect=ValueError("fixture broken plotting"),
            ),
            patch(
                "pythonagent.plotting.render_plan",
                side_effect=RuntimeError("fixture renderer unavailable"),
            ),
        ):
            flow = Workflow(
                self.case,
                self.root / "flows",
                {"execute": False, "scheduler": {"enabled": False}},
                llm=client,
                executor=FixtureExecutor(),
            )
            result = flow.run()
        self.assertEqual(result["stop"], "reviewed_plan_ready", result)
        self.assertEqual(len(result["plotting_warnings"]), 2)
        self.assertTrue(flow.plans.approved)
        self.assertIn(
            "fixture broken plotting", (flow.run_dir / "plots/index.html").read_text()
        )

    def test_missing_spectrum_is_explicit_and_does_not_fabricate_fit(self):
        manager = PlotManager(self.evidence, Journal(self.run))
        trial = self.run / "trial"
        trial.mkdir()
        record = manager.trial(self.plan(), trial)
        self.assertEqual(record["status"], "unavailable")
        self.assertFalse((trial / "plots").exists())

    def test_cli_plan_and_comparison_have_separate_immutable_plot_directories(self):
        self.prepared()
        plan_path = self.root / "plan.json"
        plan_path.write_text(json.dumps(self.plan()))
        comparison = self.root / "comparison.npz"
        self.spectrum(comparison)
        out = self.root / "cli-plots"
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            status = main(
                [
                    "plot",
                    "--case-dir",
                    str(self.case),
                    "--plan",
                    str(plan_path),
                    "--comparison",
                    str(comparison),
                    "--out-dir",
                    str(out),
                ]
            )
        self.assertEqual(status, 0, stdout.getvalue())
        self.assertTrue((out / "supplied-plan/plots/selection.png").is_file())
        self.assertTrue((out / "supplied-comparison/plots/fit_review.png").is_file())
        self.assertEqual(
            [r["status"] for r in json.loads(stdout.getvalue())["records"]],
            ["rendered"] * 3,
        )
