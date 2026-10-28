"""Operator configuration; scientific plans are separate model-authored documents."""

import copy
import json
from pathlib import Path
import warnings

from .settings import expand, read_settings


def without_tool_limits(value):
    """Migrate old configurations visibly; retired counters never gate a run."""
    value = copy.deepcopy(value)
    removed = []
    for parent, keys, prefix in [
        (value, ("max_tool_rounds",), ""),
        (
            value.get("scheduler"),
            ("max_tool_rounds", "max_extra_tool_rounds"),
            "scheduler.",
        ),
        (value.get("llm"), ("max_session_turns",), "llm."),
    ]:
        if isinstance(parent, dict):
            for key in keys:
                if key in parent:
                    parent.pop(key)
                    removed.append(prefix + key)
    if removed:
        warnings.warn(
            "Tool/message-count limits were removed; obsolete options ignored: "
            + ", ".join(removed),
            UserWarning,
            stacklevel=2,
        )
    return value


EXAMPLE = {
    "task": "Construct and evaluate the relevant valence, Fermi crossings and related low conduction from the supplied DFT evidence.",
    "execute": False,
    "max_trials": 3,
    "max_steps": 32,
    "max_repair_attempts": 8,
    "plotting": {"enabled": True},
    "scheduler": {
        "enabled": True,
        "max_decisions": 24,
        "max_execution_retries": 2,
    },
    "llm": {
        "backend": "openai",
        "api": "chat_completions",
        "base_url": "",
        "model": "MODEL_ID",
        "api_key_env": "MODEL_API_KEY",
        "temperature": 0.2,
        "max_tokens": 65536,
        "context_tokens": 131072,
        "timeout_seconds": 1800,
        "max_attempts": 2,
        "proxy_mode": "direct",
        "role_sessions": True,
        "stream": True,
        "request_extra": {"response_format": {"type": "json_object"}},
        "chat_template_kwargs": {"enable_thinking": True},
        "length_recovery": {"enabled": True, "max_retries": 2},
        "compression": {
            "enabled": True,
            "token_counting": "measured",
            "trigger_fraction": 0.8,
            "target_fraction": 0.5,
            "safety_tokens": 8192,
            "summary_max_tokens": 8192,
        },
    },
    "execution": {
        "adapter": "vasp_wannier",
        "vasp": ["mpirun", "-np", "4", "vasp_std"],
        "wannier90": ["mpirun", "-np", "4", "wannier90.x"],
        "mesh_grid": [8, 8, 8],
        "timeout_seconds": 3600,
    },
}


def example_config(models_file=None, profile=None):
    value = copy.deepcopy(EXAMPLE)
    if models_file or profile:
        if not models_file or not profile:
            raise ValueError("Supply both models_file and profile")
        registry = json.loads(Path(models_file).expanduser().read_text())
        value["llm"] = copy.deepcopy(registry["profiles"][profile])
        value["llm"]["role_sessions"] = True
        # Explicit opt-out of environment proxies for this standalone example.
        value["llm"]["proxy_mode"] = "direct"
    return value


def load_config(path, model=None):
    value = read_settings(path, model)
    if not isinstance(value, dict):
        raise ValueError("Configuration must be an object")
    value = without_tool_limits(value)
    for operator_key in ("runtime", "paths", "viewer"):
        value.pop(operator_key, None)
    allowed = {
        "task",
        "execute",
        "max_trials",
        "max_steps",
        "max_repair_attempts",
        "llm",
        "execution",
        "plotting",
        "scheduler",
    }
    if set(value) - allowed:
        raise ValueError(
            "Unknown configuration keys: " + str(sorted(set(value) - allowed))
        )
    if "task" in value and (
        not isinstance(value["task"], str) or not value["task"].strip()
    ):
        raise ValueError("task must be nonempty text")
    if type(value.get("execute", False)) is not bool:
        raise ValueError("execute must be boolean")
    if not isinstance(value.get("llm"), dict) or not isinstance(
        value.get("execution", {}), dict
    ):
        raise ValueError("llm/execution must be objects")
    if value["llm"].get("role_sessions", True) is not True:
        raise ValueError("The four-role workflow requires role_sessions=true")
    from .scheduler import scheduler_options

    value["scheduler"] = scheduler_options(value.get("scheduler", {"enabled": True}))
    for key in ("vasp", "wannier90"):
        if key in value.get("execution", {}):
            argv = value["execution"][key]
            if not isinstance(argv, list) or any(not isinstance(v, str) for v in argv):
                raise ValueError("execution." + key + " must be an argv array")
            value["execution"][key] = [expand(v) for v in argv]
    if "commands" in value.get("execution", {}):
        commands = value["execution"]["commands"]
        if not isinstance(commands, list) or any(
            not isinstance(c, list) or any(not isinstance(v, str) for v in c)
            for c in commands
        ):
            raise ValueError("execution.commands must contain argv arrays")
        value["execution"]["commands"] = [[expand(v) for v in c] for c in commands]
    return value
