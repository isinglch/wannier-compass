"""Execution artifacts and factual failure records; no scientific diagnosis rules."""

from pathlib import Path

from .state import file_sha, immutable_json


class ExecutionFailure(RuntimeError):
    def __init__(self, message, *, stage, returncode=None):
        super().__init__(message)
        self.stage = stage
        self.returncode = returncode


class InterfaceMismatch(ValueError):
    def __init__(self, details):
        super().__init__(
            "Actual interface k-point count or mesh differs from configured input"
        )
        self.details = details


def artifact_rows(trial, run):
    trial, run = Path(trial).resolve(), Path(run).resolve()
    rows = []
    for path in sorted(trial.rglob("*")):
        if path.is_file() and path.resolve().is_relative_to(trial):
            rows.append(
                {
                    "path": "run/" + str(path.relative_to(run)),
                    "bytes": path.stat().st_size,
                }
            )
    return rows


def failure_record(trial, error, run):
    """Expose actual filenames and bounded verbatim log tails, even on partial runs."""
    trial, run = Path(trial), Path(run)
    logs = []
    for directory in (trial / "interface", trial / "wannier"):
        for suffix in ("stdout", "stderr"):
            for path in sorted(directory.glob("*." + suffix)):
                if not path.resolve().is_relative_to(trial.resolve()):
                    continue
                with path.open("rb") as stream:
                    stream.seek(max(0, path.stat().st_size - 6000))
                    tail = stream.read().decode("utf-8", errors="replace")
                logs.append(
                    {
                        "path": "run/" + str(path.relative_to(run)),
                        "sha256": file_sha(path),
                        "bytes": path.stat().st_size,
                        "tail": tail,
                        "tail_may_start_mid_line": path.stat().st_size > 6000,
                    }
                )
    record = {
        "error_type": type(error).__name__,
        "message": str(error),
        "stage": getattr(error, "stage", "adapter_validation_or_postprocessing"),
        "returncode": getattr(error, "returncode", None),
        "details": getattr(error, "details", {}),
        "logs": logs,
        "artifacts": artifact_rows(trial, run),
        "scope": "Observed execution facts only; no inferred cause or scientific verdict.",
    }
    immutable_json(trial / "failure.json", record)
    return "run/" + str((trial / "failure.json").relative_to(run))
