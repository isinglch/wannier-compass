"""Read-only GLM diagnosis of archived failures. No VASP/Wannier or science calls.

Input archive paths are explicit test inputs, never implicit runtime dependencies.
Only bounded logs and small input/approval/result records are copied into fresh
replay runs. Existing role conversations and sessions are never loaded.
"""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
from types import SimpleNamespace
import uuid

import numpy as np

from pythonagent.artifacts import ExecutionFailure, InterfaceMismatch, failure_record
from pythonagent.config import load_config
from pythonagent.evidence import Evidence
from pythonagent.formats import eigenval
from pythonagent.journal import Journal
from pythonagent.llm import LLMClient
from pythonagent.plans import PlanStore
from pythonagent.scheduler import Scheduler
from pythonagent.state import atomic_json, file_sha
from pythonagent.tools import ToolBox


class HistoricalPlans:
    def __init__(self, approval):
        self.latest = approval["plan_id"] if approval else None
        self.versions = {self.latest: approval} if approval else {}
        self.approved = approval

    def execution_input(self):
        raise ValueError(
            "Read-only historical replay cannot execute or create scientific approval"
        )


def replay(archive, output, config, name):
    archive_case = archive / "cases" / name
    old = json.loads((archive / "runs" / name / "active-run.json").read_text())
    old_run = Path(old["run_dir"])
    trials = sorted((old_run / "trials").glob("trial_*"))
    if not trials:
        return replay_role_request(archive, output, config, name, old_run)
    old_trial = trials[-1]
    run = output / name
    run.mkdir(parents=True, exist_ok=False)
    trial = run / "trials" / old_trial.name
    (trial / "interface").mkdir(parents=True)
    sources = []
    for relative in [
        "result.json",
        "approved-plan.json",
        "approval.json",
        "input_binding.json",
        "interface/vasp.stdout",
        "interface/vasp.stderr",
        "interface/INCAR",
        "interface/KPOINTS",
        "interface/wannier90.win",
    ]:
        path = old_trial / relative
        if path.is_file():
            target = trial / relative
            shutil.copyfile(path, target)
            sources.append(
                {
                    "path": str(path),
                    "sha256": file_sha(path),
                    "copy": str(target.relative_to(run)),
                }
            )
    result = json.loads((trial / "result.json").read_text())
    message = result["observations"][0]
    if "dimensions/k mesh" in message:
        with (old_trial / "interface/EIGENVAL").open() as stream:
            header = [stream.readline() for _ in range(6)]
        _, nk, nb = map(int, header[-1].split())
        _, coords = eigenval(old_trial / "interface/EIGENVAL")
        grid = list(
            map(int, (trial / "interface/KPOINTS").read_text().splitlines()[3].split())
        )
        points = np.array(
            [
                (i / grid[0], j / grid[1], k / grid[2])
                for i in range(grid[0])
                for j in range(grid[1])
                for k in range(grid[2])
            ]
        )

        def wrapped(values):
            return sorted(map(tuple, np.round(np.mod(values, 1), 7) % 1))

        tags = (trial / "interface/INCAR").read_text().splitlines()
        nbands = int(
            next(
                line.split("=", 1)[1].strip()
                for line in tags
                if line.strip().startswith("NBANDS")
            )
        )
        error = InterfaceMismatch(
            {
                "requested_bands": nbands,
                "actual_bands": nb,
                "actual_kpoints": nk,
                "expected_energy_shape": [len(points), nbands],
                "actual_energy_shape": [nk, nb],
                "configured_mesh": grid,
                "kpoint_sets_match": wrapped(coords) == wrapped(points),
                "source": str(old_trial / "interface/EIGENVAL"),
            }
        )
    else:
        error = ExecutionFailure(message, stage="vasp", returncode=255)
    result["failure_record"] = failure_record(trial, error, run)
    result["record_paths"] = [result["failure_record"]]
    journal = Journal(run)
    evidence = Evidence(archive_case, run)
    approval = json.loads((trial / "approval.json").read_text())
    client_config = {**config["llm"], "scheduler_enabled": True, "max_tokens": 8192}
    client = LLMClient(client_config, journal)
    flow = SimpleNamespace(
        scheduler_config=config["scheduler"],
        plans=HistoricalPlans(approval),
        result=result,
        executor=SimpleNamespace(
            capabilities=lambda: {
                "adapter": "read_only_historical_replay",
                "additional_data_operations": [],
                "instruction": "Read existing failure evidence only. No repair or execution is supported in this replay.",
            }
        ),
        config={"execute": False},
        scientific_trials=1,
        max_trials=3,
        max_steps=32,
        steps=0,
        max_repairs=8,
        llm=client,
        evidence=evidence,
        journal=journal,
        tools=ToolBox(evidence, PlanStore(run, evidence, journal), journal),
    )
    scheduler = Scheduler(flow)
    decision = scheduler.decide(
        {
            "kind": "execution_failed",
            "trial_id": result["trial_id"],
            "mode": "read_only_historical_replay",
            "archive_scope": "Only this attempt's copied artifacts are supplied; preceding attempts were not inspected.",
        },
        ["stop"],
    )
    events = [json.loads(line) for line in journal.path.read_text().splitlines()]
    calls = [event for event in events if event["kind"] == "scheduler"]
    usage = {
        key: sum((event.get("usage") or {}).get(key, 0) for event in calls)
        for key in ("prompt_tokens", "completion_tokens", "total_tokens")
    }
    report = {
        "case": name,
        "decision": decision,
        "source_run": str(old_run),
        "run_dir": str(run),
        "sources": sources,
        "model": client.model,
        "proxy_mode": client.proxy_mode,
        "usage": usage,
        "model_calls": len(calls),
        "tool_calls": sum(e["kind"] == "tool" for e in events),
        "scheduler_rejections": sum(e["kind"] == "scheduler_rejected" for e in events),
        "scope": "New live scheduler decisions on archived outputs; no new scientific calculation or approval.",
    }
    atomic_json(run / "replay-report.json", report)
    print(
        json.dumps(
            {
                key: report[key]
                for key in ("case", "decision", "usage", "model_calls", "tool_calls")
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    return report


def replay_role_request(archive, output, config, name, old_run):
    """Test operational classification of a real request, without rerunning planner."""
    run = output / name
    run.mkdir(parents=True, exist_ok=False)
    source = archive / "runs" / name / "outcome.json"
    old = json.loads(source.read_text())
    request = old["scientific_decision"]
    operational = {
        "source": {"path": str(source), "sha256": file_sha(source)},
        "source_stop": old["stop"],
        "plan_versions": old["plan_versions"],
        "role": "planner",
        "request": request["request"],
    }
    atomic_json(run / "operational-request.json", operational)
    journal = Journal(run)
    evidence = Evidence(archive / "cases" / name, run)
    client = LLMClient(
        {**config["llm"], "scheduler_enabled": True, "max_tokens": 8192}, journal
    )
    flow = SimpleNamespace(
        scheduler_config=config["scheduler"],
        plans=HistoricalPlans(None),
        result=None,
        executor=SimpleNamespace(
            capabilities=lambda: {
                "adapter": "read_only_historical_replay",
                "additional_data_operations": [],
            }
        ),
        config={"execute": False},
        scientific_trials=0,
        max_trials=3,
        max_steps=32,
        steps=1,
        max_repairs=8,
        llm=client,
        evidence=evidence,
        journal=journal,
        tools=ToolBox(evidence, PlanStore(run, evidence, journal), journal),
    )
    decision = Scheduler(flow).decide(
        {
            "kind": "role_request",
            "role": "planner",
            "request": request["request"],
            "reason": "No plan was saved before the role requested more tool rounds; inspect the operational request.",
            "handoff_path": "run/operational-request.json",
            "mode": "Scheduling-only replay; returning resume_role does not itself invoke planner or execute anything.",
        },
        ["resume_role", "stop"],
    )
    events = [json.loads(line) for line in journal.path.read_text().splitlines()]
    calls = [event for event in events if event["kind"] == "scheduler"]
    report = {
        "case": name,
        "decision": decision,
        "source_run": str(old_run),
        "run_dir": str(run),
        "sources": [{"path": str(source), "sha256": file_sha(source)}],
        "model": client.model,
        "proxy_mode": client.proxy_mode,
        "usage": {
            key: sum((event.get("usage") or {}).get(key, 0) for event in calls)
            for key in ("prompt_tokens", "completion_tokens", "total_tokens")
        },
        "model_calls": len(calls),
        "tool_calls": sum(e["kind"] == "tool" for e in events),
        "scheduler_rejections": sum(e["kind"] == "scheduler_rejected" for e in events),
        "scope": "Live scheduler classification only; no planner continuation or new calculation was executed.",
    }
    atomic_json(run / "replay-report.json", report)
    print(
        json.dumps(
            {
                key: report[key]
                for key in ("case", "decision", "usage", "model_calls", "tool_calls")
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive-root", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--model")
    parser.add_argument("--output-root", required=True)
    parser.add_argument(
        "--proxy-mode",
        choices=["direct", "environment"],
        help="Explicit test-only transport override",
    )
    parser.add_argument(
        "--cases", nargs="+", default=["soc-0010", "soc-0018", "soc-0023", "soc-0037"]
    )
    args = parser.parse_args()
    config = load_config(args.config, args.model)
    if args.proxy_mode:
        config["llm"]["proxy_mode"] = args.proxy_mode
    output = Path(args.output_root) / (
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        + "-"
        + uuid.uuid4().hex[:8]
    )
    output.mkdir(parents=True, exist_ok=False)
    reports = []
    for name in args.cases:
        reports.append(replay(Path(args.archive_root), output, config, name))
        atomic_json(output / "summary.json", {"cases": reports})
    print("Replay output: " + str(output), flush=True)


if __name__ == "__main__":
    main()
