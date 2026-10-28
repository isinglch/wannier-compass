"""Source-bound execution metadata. No raw spectra, recommendations or acceptance."""

import copy
import json
from pathlib import Path
import re

from .state import atomic_json, file_sha, digest

FORMAT = "execution-facts-v1"
POLICY = """execution_facts(trial) is the shared source of actual execution metadata.
Distinguish source_settings (historical case input), materialized_input (generated
settings), and observed_interface (parsed output dimensions). A requested NBANDS
is not an observed band count. Missing/unverified fields remain explicitly unknown.
For a failed execution, read execution_facts in the handoff/current_result before
inferring missing data. Window state counts are for that plan, sampling and source;
they are not the total number of bands. The program reports published algebraic
constraints, never chooses how to repair basis/windows or judges scientific quality.
"""


def integer_value(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def ranges(indices):
    """Lossless run encoding of 1-based coordinates, no selected subset."""
    output = []
    for value in sorted(set(indices)):
        if output and value == output[-1][1] + 1:
            output[-1][1] = value
        else:
            output.append([value, value])
    return output


def count_summary(facts):
    value = copy.deepcopy(facts)
    for key in (
        "nouter_below_num_wann_kpoints",
        "nfrozen_above_num_wann_kpoints",
        "computed_top_at_or_below_dis_upper_kpoints",
    ):
        coordinates = value.pop(key, [])
        value[key.removesuffix("_kpoints")] = {
            "count": len(coordinates),
            "k_ranges_1based": ranges(coordinates),
        }
    value["method"] = (
        "Inclusive window membership counted over every retained eigenvalue at every sampled k; coordinate ranges losslessly encode all affected k points."
    )
    return value


class ExecutionFacts:
    def __init__(self, trial, plan, evidence, adapter, *, mode="live"):
        self.trial, self.evidence = Path(trial), evidence
        self.path = self.trial / "execution-facts.json"
        self.data = {
            "format": FORMAT,
            "mode": mode,
            "run_id": evidence.run.name,
            "trial_id": self.trial.name,
            "input_revision": evidence.revision,
            "adapter": adapter,
            "revision": 0,
            "status": "running",
            "phase": "preparation",
            "plan_sha256": digest(plan),
            "plan_source": self.source(self.trial / "approved-plan.json"),
            "plan": {
                k: copy.deepcopy(plan[k])
                for k in (
                    "basis",
                    "num_wann",
                    "target_band_ranges_1based",
                    "excluded_bands_1based",
                    "windows_relative_eV",
                    "solver",
                )
            },
            "source_cache": {
                "num_kpoints": int(evidence.energy.shape[0]),
                "num_bands": int(evidence.energy.shape[1]),
                "sampling": evidence.meta.get("sampling"),
                "source": evidence.source,
            },
            "source_settings": {"status": "not_read", "nbands": None},
            "materialized_input": {"status": "not_created", "nbands": None},
            "observed_interface": {
                "status": "not_observed",
                "num_bands": None,
                "num_kpoints": None,
                "retained_num_bands": None,
                "matrix_identity": "not_verified",
            },
            "window_counts": {"status": "not_measured"},
            "commands": {
                name: {"status": "not_started", "returncode": None}
                for name in ("vasp", "wannier90")
            }
            if adapter == "vasp_wannier"
            else {},
            "scope": "Observed execution facts only. No remedy, scientific verdict or numeric acceptance is supplied.",
        }
        self.save()

    def source(self, path):
        path = Path(path).resolve()
        for name, root in (("case", self.evidence.case), ("run", self.evidence.run)):
            if path.is_relative_to(root):
                return {
                    "path": name + "/" + str(path.relative_to(root)),
                    "sha256": file_sha(path),
                }
        raise ValueError("Execution fact source outside case/run")

    def save(self):
        self.data["revision"] += 1
        atomic_json(self.path, self.data)

    def phase(self, phase):
        self.data["phase"] = phase
        self.save()

    def source_input(self, original):
        self.data["source_settings"] = {
            "status": "read",
            "nbands": integer_value(original.get("NBANDS")),
            "parameters": dict(original),
            "source": self.source(self.evidence.case / "02_band/INCAR"),
        }
        self.save()

    def materialized(self, original, generated, *, reason_source=None):
        """Called with the exact tags used by NativeExecutor.prepare."""
        original_source = self.source(self.evidence.case / "02_band/INCAR")
        generated_source = self.source(self.trial / "interface/INCAR")
        self.data["source_settings"] = {
            "status": "read",
            "nbands": integer_value(original.get("NBANDS")),
            "parameters": dict(original),
            "source": original_source,
        }
        self.data["materialized_input"] = {
            "status": "written",
            "nbands": integer_value(generated.get("NBANDS")),
            "parameters": dict(generated),
            "source": generated_source,
            "overrides": {
                key: {
                    "original": original.get(key),
                    "generated": value,
                    "owner": "NativeExecutor.prepare",
                    "basis": "source_cache.num_bands"
                    if key == "NBANDS"
                    else "plan.num_wann"
                    if key == "NUM_WANN"
                    else "registered interface mechanics",
                }
                for key, value in generated.items()
                if original.get(key) != value
            },
        }
        if reason_source:
            self.data["materialized_input"]["generator_source"] = reason_source
        self.save()

    def observed(self, shape, source):
        self.data["observed_interface"].update(
            status="dimensions_observed",
            num_kpoints=int(shape[0]),
            num_bands=int(shape[1]),
            source=self.source(source),
        )
        self.save()

    def counts(self, facts):
        self.data["window_counts"] = {
            "status": "measured",
            **count_summary(facts),
            "source": self.data["observed_interface"].get("source"),
        }
        self.data["observed_interface"]["retained_num_bands"] = facts[
            "retained_num_bands"
        ]
        self.save()

    def verified(self):
        self.data["observed_interface"].update(
            status="verified", matrix_identity="verified"
        )
        self.save()

    def command_started(self, label, argv, directory):
        self.data["commands"][label] = {
            "status": "running",
            "argv": list(argv),
            "directory": "run/" + str(Path(directory).relative_to(self.evidence.run)),
            "returncode": None,
        }
        self.phase(label)

    def command_finished(
        self, label, returncode, seconds, directory, *, interrupted=False
    ):
        record = self.data["commands"][label]
        record.update(
            status="interrupted"
            if interrupted
            else "completed"
            if returncode == 0
            else "failed",
            returncode=returncode,
            seconds=seconds,
            logs=[
                self.source(Path(directory) / (label + "." + suffix))
                for suffix in ("stdout", "stderr")
                if (Path(directory) / (label + "." + suffix)).is_file()
            ],
        )
        self.save()

    def finish(self, status, *, error=None):
        self.data["status"] = status
        self.data["stopped_at"] = self.data["phase"]
        if error is not None:
            self.data["failure"] = {
                "type": type(error).__name__,
                "message": str(error),
                "stage": getattr(error, "stage", self.data["phase"]),
            }
        self.save()

    def summary(self):
        data = self.data
        return {
            "source": self.source(self.path),
            "trial_id": data["trial_id"],
            "status": data["status"],
            "phase": data["phase"],
            "mode": data["mode"],
            "source_settings": {
                k: v for k, v in data["source_settings"].items() if k != "parameters"
            },
            "source_cache": data["source_cache"],
            "materialized_input": {
                k: v
                for k, v in data["materialized_input"].items()
                if k in ("status", "nbands", "source")
            },
            "nbands_generation": data["materialized_input"]
            .get("overrides", {})
            .get("NBANDS"),
            "observed_interface": data["observed_interface"],
            "plan": {
                k: data["plan"][k]
                for k in (
                    "basis",
                    "num_wann",
                    "excluded_bands_1based",
                    "windows_relative_eV",
                )
            },
            "window_counts": data["window_counts"],
            "commands": {
                name: {
                    k: v
                    for k, v in value.items()
                    if k not in ("argv", "directory", "logs")
                }
                for name, value in data["commands"].items()
            },
            "failure": data.get("failure"),
            "scope": data["scope"],
        }


def execution_facts(evidence, trial, view="summary"):
    if not re.fullmatch(r"trial_[0-9]+", trial):
        raise ValueError("Use an explicit trial identifier")
    if view not in ("summary", "inputs", "interface", "constraints", "commands"):
        raise ValueError("Unknown execution fact view")
    path = evidence.resolve(f"run/trials/{trial}/execution-facts.json")
    if path.stat().st_size > 2_000_000:
        raise ValueError("Execution facts exceed the metadata size bound")
    result_path = path.parent / "result.json"
    if result_path.exists():
        result = json.loads(result_path.read_text())
        published = result.get("execution_facts", {}).get("source", {})
        if published.get(
            "path"
        ) != f"run/trials/{trial}/execution-facts.json" or published.get(
            "sha256"
        ) != file_sha(path):
            raise ValueError(
                "Published execution facts changed or lack a result binding"
            )
    data = json.loads(path.read_text())
    if (
        data.get("format") != FORMAT
        or data.get("trial_id") != trial
        or data.get("run_id") != evidence.run.name
        or data.get("input_revision") != evidence.revision
        or data.get("plan_source", {}).get("path")
        != f"run/trials/{trial}/approved-plan.json"
    ):
        raise ValueError("Execution facts belong to another trial/run/input")

    def verify(value):
        if isinstance(value, dict):
            if set(value) >= {"path", "sha256"}:
                if file_sha(evidence.resolve(value["path"])) != value["sha256"]:
                    raise ValueError("Execution fact source changed: " + value["path"])
            for child in value.values():
                verify(child)
        elif isinstance(value, list):
            for child in value:
                verify(child)

    verify(data)
    plan = json.loads(evidence.resolve(data["plan_source"]["path"]).read_text())
    if digest(plan) != data["plan_sha256"]:
        raise ValueError("Execution fact plan binding changed")
    record = object.__new__(ExecutionFacts)
    record.trial, record.evidence, record.path, record.data = (
        path.parent,
        evidence,
        path,
        data,
    )
    if view == "summary":
        return record.summary()
    groups = {
        "inputs": ("source_settings", "source_cache", "materialized_input", "plan"),
        "interface": ("observed_interface",),
        "constraints": ("window_counts",),
        "commands": ("commands",),
    }
    return {
        "source": record.source(path),
        "trial_id": trial,
        "status": data["status"],
        "view": view,
        **{key: data[key] for key in groups[view]},
        "scope": data["scope"],
    }
