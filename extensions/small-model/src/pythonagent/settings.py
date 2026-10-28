"""Read operator settings and model profiles without importing scientific libraries."""

import copy
import json
import os
from pathlib import Path
import re


def default_config(project_root=None):
    roots = [Path(project_root)] if project_root else [Path.cwd()]
    source_root = Path(__file__).resolve().parents[2]
    if (source_root / "src/pythonagent").is_dir() and source_root not in roots:
        roots.append(source_root)
    for root in roots:
        for name in ("agent.local.json", "agent.json"):
            if (root / name).is_file():
                return root / name
    raise ValueError("No agent.json found; use --config /path/to/agent.json")


def merge(base, overrides):
    result = copy.deepcopy(base)
    for key, value in overrides.items():
        result[key] = (
            merge(result[key], value)
            if isinstance(result.get(key), dict) and isinstance(value, dict)
            else copy.deepcopy(value)
        )
    return result


def read_settings(path, model=None):
    value = json.loads(Path(path).read_text())
    if not isinstance(value, dict):
        raise ValueError("Configuration must be an object")
    profiles = value.pop("models", None)
    selected = model or value.pop("default_model", None)
    value.pop("default_model", None)
    if selected is not None and (not isinstance(selected, str) or not selected):
        raise ValueError("default_model/--model must be nonempty text")
    if profiles is not None:
        if not isinstance(profiles, dict) or not profiles:
            raise ValueError("models must be a nonempty object")
        if not selected or selected not in profiles:
            raise ValueError(
                "Select a configured model using --model; available: "
                + ", ".join(sorted(profiles))
            )
        if not isinstance(value.get("llm", {}), dict) or not isinstance(
            profiles[selected], dict
        ):
            raise ValueError("llm and each model profile must be objects")
        value["llm"] = merge(value.get("llm", {}), profiles[selected])
    elif selected:
        raise ValueError("--model/default_model requires a models registry")
    return value


def expand(value):
    if not isinstance(value, str):
        raise ValueError("Environment values and paths must be strings")
    missing = [
        m.group(1) or m.group(2)
        for m in re.finditer(
            r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)", value
        )
        if (m.group(1) or m.group(2)) not in os.environ
    ]
    if missing:
        raise ValueError(
            "Missing environment variables: " + ", ".join(sorted(set(missing)))
        )
    return os.path.expandvars(value)


def config_path(value, config_file):
    path = Path(expand(value)).expanduser()
    return path if path.is_absolute() else Path(config_file).resolve().parent / path
