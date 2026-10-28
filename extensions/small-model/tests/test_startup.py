import contextlib
import io
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from pythonagent.config import load_config
from pythonagent.execution import Runner
from pythonagent.journal import Journal
from pythonagent.llm import LLMClient
from pythonagent.settings import config_path, default_config, read_settings
from pythonagent.startup import load_env_file, load_modules, prepare_environment
from pythonagent.cli import main as cli_main


class StartupTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="agent-startup-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.config_file = self.root / "agent.json"
        self.settings = {
            "execute": False,
            "default_model": "one",
            "models": {
                "one": {"model": "first", "api_key_env": "FIXTURE_AGENT_KEY"},
                "two": {
                    "model": "second",
                    "chat_template_kwargs": {"enable_thinking": False},
                },
            },
            "llm": {
                "backend": "dry_run",
                "proxy_mode": "direct",
                "chat_template_kwargs": {"enable_thinking": True},
                "compression": {"enabled": True, "safety_tokens": 8192},
            },
            "execution": {
                "adapter": "command",
                "commands": [[sys.executable, "-c", "pass"]],
            },
            "paths": {"case_dir": "case", "runs_root": "results"},
            "viewer": {"port": 8766},
            "runtime": {
                "environment": {
                    name: "1"
                    for name in (
                        "OMP_NUM_THREADS",
                        "MKL_NUM_THREADS",
                        "OPENBLAS_NUM_THREADS",
                        "NUMEXPR_NUM_THREADS",
                    )
                }
            },
        }
        (self.root / "case").mkdir()
        self.write()

    def write(self):
        self.config_file.write_text(json.dumps(self.settings))

    def test_profile_merge_and_operator_settings_never_enter_workflow_config(self):
        config = load_config(self.config_file, "two")
        self.assertEqual(config["llm"]["model"], "second")
        self.assertFalse(config["llm"]["chat_template_kwargs"]["enable_thinking"])
        self.assertEqual(config["llm"]["compression"]["safety_tokens"], 8192)
        self.assertEqual(load_config(self.config_file)["llm"]["model"], "first")
        self.assertFalse(
            set(config) & {"runtime", "paths", "viewer", "models", "default_model"}
        )

    def test_unknown_profile_and_invalid_registry_fail_before_launch(self):
        with self.assertRaisesRegex(ValueError, "available: one, two"):
            read_settings(self.config_file, "missing")
        self.settings["models"]["one"] = []
        self.write()
        with self.assertRaises(ValueError):
            read_settings(self.config_file)

    def test_legacy_flat_config_still_loads(self):
        self.settings.pop("models")
        self.settings.pop("default_model")
        self.write()
        self.assertEqual(load_config(self.config_file)["llm"]["backend"], "dry_run")
        with self.assertRaises(ValueError):
            load_config(self.config_file, "one")

    def test_config_relative_paths_and_local_override_are_independent_of_cwd(self):
        self.assertEqual(
            config_path("results", self.config_file), self.root / "results"
        )
        with patch("pythonagent.settings.Path.cwd", return_value=Path("/tmp")):
            self.assertEqual(default_config(self.root), self.config_file)
        local = self.root / "agent.local.json"
        local.write_text("{}")
        self.assertEqual(default_config(self.root), local)

    def test_env_file_preserves_existing_credentials_and_never_evaluates_shell(self):
        marker = self.root / "must-not-exist"
        secret_file = self.root / ".env"
        secret_file.write_text(
            f"FIXTURE_AGENT_KEY=from-file\nexport LITERAL_SECRET='$(touch {marker}) $HOME'\n"
        )
        with patch.dict(os.environ, {"FIXTURE_AGENT_KEY": "from-shell"}, clear=True):
            load_env_file(secret_file)
            self.assertEqual(os.environ["FIXTURE_AGENT_KEY"], "from-shell")
            self.assertEqual(os.environ["LITERAL_SECRET"], f"$(touch {marker}) $HOME")
        self.assertFalse(marker.exists())

    def test_bad_env_file_error_does_not_disclose_credential_value(self):
        path = self.root / ".env"
        path.write_text('FIXTURE_AGENT_KEY="PRIVATE_VALUE\n')
        with self.assertRaises(ValueError) as caught:
            load_env_file(path)
        self.assertNotIn("PRIVATE_VALUE", str(caught.exception))

    def test_env_file_key_reaches_model_client_without_shell_interpolation(self):
        (self.root / ".env").write_text("FIXTURE_AGENT_KEY=literal#key$VALUE\n")
        self.settings["runtime"]["env_file"] = ".env"
        with patch.dict(os.environ, {}, clear=True):
            prepare_environment(self.settings, self.config_file)
            client = LLMClient(
                {
                    "backend": "openai",
                    "base_url": "http://unit.invalid/v1",
                    "model": "fixture",
                    "api_key_env": "FIXTURE_AGENT_KEY",
                }
            )
            self.assertEqual(client.api_key, "literal#key$VALUE")

    def test_environment_overrides_inherited_threads_and_prepends_paths(self):
        self.settings["runtime"].update(
            prepend_path=["bin with spaces"], prepend_library_path=["lib"]
        )
        with patch.dict(
            os.environ, {"PATH": "/usr/bin", "OMP_NUM_THREADS": "8"}, clear=True
        ):
            prepare_environment(self.settings, self.config_file)
            self.assertEqual(os.environ["OMP_NUM_THREADS"], "1")
            self.assertEqual(
                os.environ["PATH"].split(os.pathsep),
                [str(self.root / "bin with spaces"), "/usr/bin"],
            )
            self.assertEqual(os.environ["LD_LIBRARY_PATH"], str(self.root / "lib"))

    def test_required_and_referenced_environment_variables_fail_explicitly(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, "MISSING_AGENT_KEY"):
                prepare_environment(
                    {"runtime": {"required_env": ["MISSING_AGENT_KEY"]}},
                    self.config_file,
                )
            with self.assertRaisesRegex(ValueError, "MISSING_AGENT_KEY"):
                prepare_environment(
                    {"runtime": {"environment": {"TEST": "${MISSING_AGENT_KEY}"}}},
                    self.config_file,
                )

    def test_module_initialization_uses_literal_names_and_inherits_exports(self):
        init = self.root / "module init.sh"
        init.write_text(
            'module() { if [ "$1" = load ]; then export FIXTURE_MODULE="$2"; fi; }\n'
        )
        name = "literal; no shell evaluation"
        with patch.dict(os.environ, os.environ.copy()):
            load_modules(
                {"init": str(init), "load": [name], "purge": True}, self.config_file
            )
            self.assertEqual(os.environ["FIXTURE_MODULE"], name)

    def test_failed_module_does_not_dump_environment_or_stderr_secrets(self):
        init = self.root / "bad-module.sh"
        init.write_text("echo PRIVATE_VALUE >&2; return 7\n")
        with self.assertRaises(ValueError) as caught:
            load_modules({"init": str(init), "load": ["fixture"]}, self.config_file)
        self.assertNotIn("PRIVATE_VALUE", str(caught.exception))

    def test_real_execution_child_receives_configured_environment(self):
        trial = self.root / "trial"
        trial.mkdir()
        with patch.dict(os.environ, {"OMP_NUM_THREADS": "8"}):
            prepare_environment(self.settings, self.config_file)
            Runner({}, Journal(trial)).command(
                [
                    sys.executable,
                    "-c",
                    "import os; print(os.environ['OMP_NUM_THREADS']); print(os.environ['OPENBLAS_NUM_THREADS'])",
                ],
                trial,
                "probe",
            )
        self.assertEqual((trial / "probe.stdout").read_text(), "1\n1\n")

    def call_cli(self, args):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            status = cli_main(args)
        return status, output.getvalue()

    def test_short_run_selects_profile_and_config_paths_without_old_flags(self):
        with patch("pythonagent.cli.Workflow") as workflow:
            workflow.return_value.run.return_value = {"stop": "reviewed_plan_ready"}
            status, _ = self.call_cli(
                ["run", "--config", str(self.config_file), "--model", "two"]
            )
        self.assertEqual(status, 0)
        case, runs, config = workflow.call_args.args
        self.assertEqual(case, self.root / "case")
        self.assertEqual(runs, self.root / "results")
        self.assertEqual(config["llm"]["model"], "second")

    def test_positional_case_and_review_only_override_config(self):
        self.settings["execute"] = True
        self.write()
        with patch("pythonagent.cli.Workflow") as workflow:
            workflow.return_value.run.return_value = {"stop": "reviewed_plan_ready"}
            status, _ = self.call_cli(
                [
                    "--config",
                    str(self.config_file),
                    "run",
                    str(self.root / "case"),
                    "--review-only",
                ]
            )
        self.assertEqual(status, 0)
        self.assertFalse(workflow.call_args.args[2]["execute"])

    def test_view_uses_same_config_and_does_not_start_workflow(self):
        with (
            patch("pythonagent.viewer.serve") as serve,
            patch("pythonagent.cli.Workflow") as workflow,
        ):
            status, _ = self.call_cli(["view", "--config", str(self.config_file)])
        self.assertEqual(status, 0)
        serve.assert_called_once_with(self.root / "results", 8766)
        workflow.assert_not_called()

    def test_missing_case_returns_configuration_error_without_workflow(self):
        self.settings["paths"]["case_dir"] = None
        self.write()
        with patch("pythonagent.cli.Workflow") as workflow:
            status, output = self.call_cli(["run", "--config", str(self.config_file)])
        self.assertEqual(status, 2)
        self.assertIn("case directory", output)
        workflow.assert_not_called()

    def test_check_selects_requested_model_and_redacts_api_key(self):
        self.settings["models"]["two"]["api_key_env"] = "SECOND_AGENT_KEY"
        self.write()
        with patch.dict(os.environ, {"SECOND_AGENT_KEY": "PRIVATE_VALUE"}):
            status, output = self.call_cli(
                ["check", "--config", str(self.config_file), "--model", "two"]
            )
        self.assertEqual(status, 0)
        value = json.loads(output)
        self.assertEqual(value["api_key_env"], "SECOND_AGENT_KEY")
        self.assertTrue(value["api_key_present"])
        self.assertNotIn("PRIVATE_VALUE", output)

    def test_fresh_launcher_sets_environment_before_scientific_imports(self):
        hook = self.root / "sitecustomize.py"
        hook.write_text(
            "import sys,os\nclass Guard:\n def find_spec(self, fullname, *args):\n  if fullname=='numpy' and os.environ.get('OMP_NUM_THREADS')!='1': raise RuntimeError('environment applied too late')\n  if fullname=='numpy' and os.environ.get('FIXTURE_PREFIX')!='xold': raise RuntimeError('environment applied more than once')\n  return None\nsys.meta_path.insert(0,Guard())\n"
        )
        self.settings["runtime"]["env_file"] = ".env"
        self.settings["runtime"]["environment"]["FIXTURE_PREFIX"] = "x${FIXTURE_PREFIX}"
        (self.root / ".env").write_text("FIXTURE_AGENT_KEY=PRIVATE_VALUE\n")
        self.write()
        env = {
            **os.environ,
            "PYTHONPATH": str(self.root),
            "OMP_NUM_THREADS": "8",
            "FIXTURE_PREFIX": "old",
        }
        launcher = Path(__file__).resolve().parents[1] / "agent"
        result = subprocess.run(
            [
                sys.executable,
                "-B",
                str(launcher),
                "check",
                "--config",
                str(self.config_file),
            ],
            cwd="/tmp",
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(data["environment"]["OMP_NUM_THREADS"], "1")
        self.assertTrue(data["api_key_present"])
        self.assertNotIn("PRIVATE_VALUE", result.stdout + result.stderr)

    def test_launcher_switches_python_without_losing_config_or_profile(self):
        wrapper = self.root / "python with spaces"
        marker = self.root / "interpreter-used"
        wrapper.write_text(
            "#!/bin/sh\nprintf used > "
            + shlex.quote(str(marker))
            + "\nexec "
            + shlex.quote(sys.executable)
            + ' "$@"\n'
        )
        wrapper.chmod(0o755)
        self.settings["runtime"]["python"] = str(wrapper)
        self.write()
        launcher = Path(__file__).resolve().parents[1] / "agent"
        result = subprocess.run(
            [
                sys.executable,
                "-B",
                str(launcher),
                "check",
                "--config",
                str(self.config_file),
                "--model",
                "two",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(marker.is_file())
        self.assertEqual(json.loads(result.stdout)["api_key_env"], None)

    def test_checkout_launcher_from_other_cwd_keeps_default_config_after_reexec(self):
        source = Path(__file__).resolve().parents[1]
        checkout = self.root / "checkout with spaces"
        checkout.mkdir()
        (checkout / "case").mkdir()
        shutil.copy2(source / "agent", checkout / "agent")
        (checkout / "src").symlink_to(source / "src", target_is_directory=True)
        interpreter = self.root / "python-wrapper"
        interpreter.write_text(
            "#!/bin/sh\nexec " + shlex.quote(sys.executable) + ' "$@"\n'
        )
        interpreter.chmod(0o755)
        self.settings["runtime"]["python"] = str(interpreter)
        self.write()
        shutil.copy2(self.config_file, checkout / "agent.json")
        result = subprocess.run(
            [sys.executable, "-B", str(checkout / "agent"), "check"],
            cwd="/tmp",
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout)["config"], str(checkout / "agent.json")
        )
