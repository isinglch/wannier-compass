"""Standalone entry points; no parent repository imports or implicit registry reads."""

import argparse
import json
from pathlib import Path
import sys
import tempfile

from .config import example_config, load_config
from .settings import config_path, default_config, read_settings
from .cache import add_arguments, convert
from .evidence import Evidence
from .state import immutable_json
from .validation import validate_plan
from .workflow import Workflow
from .plotting import PlotManager, comparison_from_hr
from .journal import Journal
import shutil


def main(argv=None, *, default_config_file=None):
    parser = argparse.ArgumentParser(prog="pythonagent-scheduler")
    parser.add_argument("--config", default=default_config_file)
    parser.add_argument("--model")
    sub = parser.add_subparsers(dest="command", required=True)
    add_arguments(
        sub.add_parser(
            "cache",
            help="Convert explicit completed VASP outputs into a source-bound evidence cache",
        )
    )
    init = sub.add_parser("init-config")
    init.add_argument("--output", required=True)
    init.add_argument("--models-file")
    init.add_argument("--profile")
    inspect = sub.add_parser("inspect")
    inspect.add_argument("--case-dir", required=True)
    check = sub.add_parser("validate")
    check.add_argument("--case-dir", required=True)
    check.add_argument("--plan", required=True)
    run = sub.add_parser("run")
    check_environment = sub.add_parser(
        "check",
        help="Check configuration, Python and software environment without model or calculation calls",
    )
    view = sub.add_parser("view", help="Read-only local webpage for workflow output")
    for command in (run, view, check_environment):
        command.add_argument("--config", default=argparse.SUPPRESS)
        command.add_argument("--model", default=argparse.SUPPRESS)
    view.add_argument("--runs-root")
    view.add_argument("--port", type=int)
    check_environment.add_argument("case", nargs="?")
    plot = sub.add_parser(
        "plot",
        help="Render current DFT, plan and optional same-k fit data into a new directory",
    )
    plot.add_argument("--case-dir", required=True)
    plot.add_argument("--out-dir", required=True)
    plot.add_argument(
        "--plan",
        help="Complete model-plan-v1 JSON; annotations do not imply scientific approval",
    )
    spectrum = plot.add_mutually_exclusive_group()
    spectrum.add_argument(
        "--comparison", help="Bound comparison.npz from a current trial"
    )
    spectrum.add_argument(
        "--hr",
        help="Existing HR with matching win/wsvec; interpolation only, no software/model run",
    )
    run.add_argument(
        "case",
        nargs="?",
        help="DFT evidence directory; alternatively set paths.case_dir",
    )
    run.add_argument("--case-dir")
    run.add_argument("--runs-root")
    execution_mode = run.add_mutually_exclusive_group()
    execution_mode.add_argument(
        "--execute",
        action="store_const",
        const=True,
        default=None,
        help="Operator authorization to execute configured commands after reviewer approval",
    )
    execution_mode.add_argument(
        "--review-only",
        dest="execute",
        action="store_const",
        const=False,
        help="Stop after reviewer approval",
    )
    args = parser.parse_args(argv)
    try:
        if args.command in ("run", "view", "check"):
            config_file = args.config or default_config()
            settings = read_settings(config_file, args.model)
            config = load_config(config_file, args.model)
            paths = settings.get("paths", {})
            viewer = settings.get("viewer", {})
            if not isinstance(paths, dict) or set(paths) - {"case_dir", "runs_root"}:
                raise ValueError("paths accepts case_dir and runs_root")
            if not isinstance(viewer, dict) or set(viewer) - {"port"}:
                raise ValueError("viewer accepts port")
            runs_root = getattr(args, "runs_root", None) or config_path(
                paths.get("runs_root", "runs"), config_file
            )
            cli_case = getattr(args, "case", None) or getattr(args, "case_dir", None)
            case = (
                Path(cli_case).expanduser().resolve()
                if cli_case
                else config_path(paths["case_dir"], config_file)
                if paths.get("case_dir")
                else None
            )
            if getattr(args, "case", None) and getattr(args, "case_dir", None):
                raise ValueError("Supply either positional case or --case-dir")
            if args.command == "check":
                from .startup import check_environment as environment_report

                result = environment_report(settings, config_file, case, args.model)
                print(json.dumps(result, ensure_ascii=False, indent=2))
                return 0 if result["ok"] else 2
        if args.command == "view":
            from .viewer import serve

            port = args.port if args.port is not None else viewer.get("port", 8765)
            if type(port) is not int or not 0 <= port <= 65535:
                raise ValueError("viewer.port must be an integer between 0 and 65535")
            serve(runs_root, port)
            return 0
        if args.command == "plot":
            if (args.comparison or args.hr) and not args.plan:
                raise ValueError("--comparison/--hr requires an explicit --plan")
            output = Path(args.out_dir).resolve()
            if output.exists():
                raise ValueError(
                    "Use a new plot output directory; existing results are never overwritten"
                )
            evidence = Evidence(args.case_dir, output)
            plan = json.loads(Path(args.plan).read_text()) if args.plan else None
            if plan is not None:
                check = validate_plan(plan, evidence)
                if not check["valid"]:
                    raise ValueError(
                        "Invalid plot plan: " + json.dumps(check["errors"])
                    )
            output.mkdir(parents=True, exist_ok=False)
            manager = PlotManager(evidence, Journal(output))
            records = [manager.dft()]
            if plan is not None:
                directory = output / "supplied-plan"
                directory.mkdir()
                immutable_json(directory / "plan.json", plan)
                records.append(manager.plan(plan, directory))
                if args.hr or args.comparison:
                    directory = output / "supplied-comparison"
                    directory.mkdir()
                    immutable_json(directory / "plan.json", plan)
                if args.hr:
                    comparison_from_hr(
                        evidence,
                        plan,
                        Path(args.hr).resolve(),
                        directory / "comparison.npz",
                    )
                elif args.comparison:
                    shutil.copyfile(
                        Path(args.comparison).resolve(), directory / "comparison.npz"
                    )
                if args.hr or args.comparison:
                    records.append(manager.trial(plan, directory))
            result = {
                "output_dir": str(output),
                "index": str(output / "plots/index.html"),
                "records": records,
                "fit_pass": None,
            }
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if all(r["status"] == "rendered" for r in records) else 2
        if args.command == "cache":
            print(json.dumps(convert(args), ensure_ascii=False, indent=2))
            return 0
        if args.command == "init-config":
            immutable_json(
                Path(args.output), example_config(args.models_file, args.profile)
            )
            print(
                json.dumps(
                    {"config": str(Path(args.output).resolve()), "execute": False}
                )
            )
            return 0
        if args.command in ("inspect", "validate"):
            with tempfile.TemporaryDirectory(prefix="pythonagent-read-") as temporary:
                evidence = Evidence(
                    args.case_dir,
                    temporary
                    if args.command == "inspect"
                    else Path(args.plan).resolve().parent,
                )
                result = (
                    evidence.overview()
                    if args.command == "inspect"
                    else validate_plan(
                        json.loads(Path(args.plan).read_text()), evidence
                    )
                )
                print(json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2))
                return 0 if result.get("valid", True) else 2
        if case is None:
            raise ValueError(
                "Supply a case directory: agent run /path/to/case, or configure paths.case_dir"
            )
        if args.execute is not None:
            config["execute"] = args.execute
        result = Workflow(case, runs_root, config).run()
        print(json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2))
        return (
            0
            if result["stop"] in ("model_complete", "model_stop", "reviewed_plan_ready")
            else 2
        )
    except (ValueError, OSError, KeyError) as error:
        print(
            json.dumps(
                {"stop": "configuration_error", "error": str(error)}, ensure_ascii=False
            ),
            file=sys.stderr,
        )
        return 2
