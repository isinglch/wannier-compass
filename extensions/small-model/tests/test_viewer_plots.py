import base64
import hashlib
import json
import threading
import urllib.error
import urllib.parse
import urllib.request

from pythonagent.journal import Journal
from pythonagent.viewer import RunReader, make_server
from support import CaseTest


PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aTl0AAAAASUVORK5CYII="
)
PDF = b"%PDF-1.4\nfixture figure\n%%EOF\n"


class ViewerPlotTests(CaseTest):
    def setUp(self):
        super().setUp()
        self.runs = self.root / "runs"
        self.directory = self.runs / "history"
        self.journal = Journal(self.directory)
        self.journal.record("run_start", {"workflow_id": "history"})
        self.reader = RunReader(self.runs)

    def figures(self, trial="trial_001"):
        directory = self.directory / "trials" / trial
        plots = directory / "plots"
        plots.mkdir(parents=True)
        outputs = {}
        for stem in ("fit_review", "residuals"):
            for extension, body in (("png", PNG), ("pdf", PDF)):
                name = stem + "." + extension
                path = plots / name
                path.write_bytes(body)
                outputs[name] = {
                    "path": str(path),
                    "bytes": len(body),
                    "sha256": hashlib.sha256(body).hexdigest(),
                }
        data = {
            "format": "pythonagent-plots-v1",
            "kind": "comparison",
            "outputs": outputs,
            "plan_sha256": "a" * 64,
        }
        (plots / "manifest.json").write_text(json.dumps(data))
        result = {
            "plan_id": "approved-plan",
            "plan_sha256": "a" * 64,
            "status": "completed_converged",
            "visualization": {"status": "rendered"},
            "metrics": {
                "baseline_target": {
                    "counts": {
                        "expected_states": 10,
                        "matched_states": 10,
                        "missing_states": 0,
                    },
                    "matched_error_metrics": {"mae_eV": 0.01, "max_abs_eV": 0.1},
                }
            },
        }
        (directory / "result.json").write_text(json.dumps(result))
        return directory, data

    def test_archived_trials_have_png_pdf_metrics_and_dimensions_without_new_events(
        self,
    ):
        self.figures()
        before = self.journal.path.read_bytes()
        rows = self.reader.trials("history")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["metric_population"], "baseline_target")
        self.assertEqual(rows[0]["metrics"]["matched_error_metrics"]["mae_eV"], 0.01)
        self.assertEqual(
            [p["plot"] for p in rows[0]["images"]], ["fit_review", "residuals"]
        )
        self.assertEqual(
            (rows[0]["images"][0]["width"], rows[0]["images"][0]["height"]), (1, 1)
        )
        self.assertIn("pdf_url", rows[0]["images"][0])
        self.assertNotIn(str(self.root), json.dumps(rows))
        self.assertEqual(self.journal.path.read_bytes(), before)
        self.figures("trial_002")
        self.assertEqual(
            [r["trial"] for r in self.reader.trials("history")],
            ["trial_001", "trial_002"],
        )

    def test_pending_disabled_and_failed_rounds_stay_visible(self):
        directory = self.directory / "trials/trial_001"
        directory.mkdir(parents=True)
        self.assertEqual(self.reader.trials("history")[0]["image_status"], "pending")
        (directory / "result.json").write_text(
            json.dumps(
                {"status": "execution_failed", "visualization": {"status": "failed"}}
            )
        )
        row = self.reader.trials("history")[0]
        self.assertEqual(row["status"], "execution_failed")
        self.assertEqual(row["image_status"], "failed")
        self.assertEqual(row["images"], [])
        (directory / "result.json").write_text(
            json.dumps({"status": "completed", "visualization": {"status": "disabled"}})
        )
        self.assertEqual(self.reader.trials("history")[0]["image_status"], "disabled")

    def test_tampered_image_and_plan_are_not_displayed_as_this_round(self):
        directory, manifest = self.figures()
        (directory / "plots/fit_review.png").write_bytes(b"changed")
        row = self.reader.trials("history")[0]
        self.assertEqual(row["image_status"], "partial")
        self.assertEqual([p["plot"] for p in row["images"]], ["residuals"])
        with self.assertRaises(ValueError):
            self.reader.trial_plot("history", "trial_001", "fit_review", "png")
        result = json.loads((directory / "result.json").read_text())
        result["plan_sha256"] = "b" * 64
        (directory / "result.json").write_text(json.dumps(result))
        self.assertEqual(self.reader.trials("history")[0]["images"], [])

    def test_manifest_absolute_paths_cannot_select_another_file(self):
        directory, data = self.figures()
        data["outputs"]["fit_review.png"]["path"] = str(self.root / "private.json")
        (directory / "plots/manifest.json").write_text(json.dumps(data))
        body, mime, _, _ = self.reader.trial_plot(
            "history", "trial_001", "fit_review", "png"
        )
        self.assertEqual(body, PNG)
        self.assertEqual(mime, "image/png")
        with self.assertRaises(ValueError):
            self.reader.trial_plot(
                "history", "trial_001", "fit_review", "png", "0" * 64
            )

    def test_path_traversal_and_symlinks_even_within_run_are_denied(self):
        directory, _ = self.figures()
        for trial, plot, extension in [
            ("../trial_001", "fit_review", "png"),
            ("trial_001", "../../calls/private", "png"),
            ("trial_001", "fit_review", "html"),
            ("trial_001", "manifest", "json"),
        ]:
            with (
                self.subTest(trial=trial, plot=plot, extension=extension),
                self.assertRaises(ValueError),
            ):
                self.reader.trial_plot("history", trial, plot, extension)
        actual = directory / "plots/fit_review.png"
        actual.unlink()
        secret = self.directory / "secret.png"
        secret.write_bytes(PNG)
        actual.symlink_to(secret)
        with self.assertRaises(ValueError):
            self.reader.trial_plot("history", "trial_001", "fit_review", "png")
        moved = directory / "other-plots"
        (directory / "plots").rename(moved)
        (directory / "plots").symlink_to(moved, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.reader.trial_plot("history", "trial_001", "residuals", "png")
        (self.directory / "trials/trial_999").symlink_to(
            directory, target_is_directory=True
        )
        self.assertEqual(len(self.reader.trials("history")), 1)

    def test_http_png_pdf_and_metadata_are_exact_and_do_not_open_generic_files(self):
        self.figures()
        server = make_server(self.runs, 0)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.addCleanup(server.stopping.set)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        url = f"http://127.0.0.1:{server.server_port}"
        with opener.open(url + "/api/trials?run=history") as r:
            rows = json.load(r)
        image = rows[0]["images"][0]
        for route, expected, mime in [
            (image["url"], PNG, "image/png"),
            (image["pdf_url"], PDF, "application/pdf"),
        ]:
            with opener.open(url + route) as r:
                self.assertEqual(r.read(), expected)
                self.assertEqual(r.headers["Content-Type"], mime)
                self.assertEqual(r.headers["X-Content-Type-Options"], "nosniff")
        for params in [
            {"run": "history", "trial": "../trial_001", "plot": "fit_review"},
            {
                "run": "history",
                "trial": "trial_001",
                "plot": "result",
                "format": "json",
            },
            {
                "run": "history",
                "trial": "trial_001",
                "plot": "fit_review",
                "sha256": "bad",
            },
        ]:
            with self.assertRaises(urllib.error.HTTPError) as error:
                opener.open(url + "/api/trial-plot?" + urllib.parse.urlencode(params))
            self.assertEqual(error.exception.code, 404)
