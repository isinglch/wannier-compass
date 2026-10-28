"""Set the process environment before importing NumPy, HTTP or the workflow."""

import argparse
import hashlib
import importlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys

from .settings import config_path, default_config, expand, read_settings

THREAD_VARIABLES = (
    "OPENBLAS_NUM_THREADS",
    "OMP_NUM_THREADS",
    "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
)


def load_env_file(path):
    """Credentials are literal data. Never source, evaluate or interpolate them."""
    if not path.is_file():
        return
    for index, line in enumerate(path.read_text().splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        name, sep, value = line.partition("=")
        name = name.strip()
        if not sep or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            raise ValueError(f"Invalid environment assignment at line {index}")
        try:
            parts = shlex.split(value, comments=False)
        except ValueError:
            raise ValueError(f"Invalid environment quoting at line {index}") from None
        if len(parts) > 1:
            raise ValueError(
                f"Quote environment values containing spaces at line {index}"
            )
        os.environ.setdefault(name, parts[0] if parts else "")


def load_modules(options, config_file):
    if not isinstance(options, dict) or set(options) - {"init", "load", "purge"}:
        raise ValueError("runtime.modules accepts init, load and purge")
    modules = options.get("load", [])
    if not isinstance(modules, list) or any(
        not isinstance(v, str) or not v or v.startswith("-") for v in modules
    ):
        raise ValueError("runtime.modules.load must be an array of module names")
    if type(options.get("purge", False)) is not bool:
        raise ValueError("runtime.modules.purge must be boolean")
    if not modules:
        return
    init = options.get("init")
    if not isinstance(init, str) or not init:
        raise ValueError("runtime.modules.init is required when loading modules")
    script = 'set -e; source "$1" >&2; shift; purge="$1"; shift; if [ "$purge" = true ]; then module purge >&2; fi; module load "$@" >&2; env -0'
    command = [
        "bash",
        "--noprofile",
        "--norc",
        "-c",
        script,
        "pythonagent-modules",
        str(config_path(init, config_file)),
        str(options.get("purge", False)).lower(),
        *modules,
    ]
    result = subprocess.run(command, capture_output=True, timeout=60, check=False)
    if result.returncode:
        raise ValueError(
            "Software module initialization failed; check runtime.modules.init/load (exit "
            + str(result.returncode)
            + ")"
        )
    environment = {}
    for item in result.stdout.split(b"\0"):
        if item:
            name, sep, value = item.partition(b"=")
            if not sep:
                raise ValueError("Module loader returned an invalid environment")
            environment[os.fsdecode(name)] = os.fsdecode(value)
    if not environment.get("PATH"):
        raise ValueError("Module loader returned no PATH")
    os.environ.clear()
    os.environ.update(environment)


def prepare_environment(settings, config_file):
    options = settings.get("runtime", {})
    if not isinstance(options, dict) or set(options) - {
        "python",
        "env_file",
        "environment",
        "prepend_path",
        "prepend_library_path",
        "modules",
        "required_env",
    }:
        raise ValueError("Unknown or invalid runtime settings")
    load_modules(options.get("modules", {}), config_file)
    if options.get("env_file"):
        load_env_file(config_path(options["env_file"], config_file))
    variables = options.get("environment", {})
    if not isinstance(variables, dict):
        raise ValueError("runtime.environment must be an object")
    for name, value in variables.items():
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            raise ValueError("Invalid runtime environment variable name")
        os.environ[name] = expand(value)
    for setting, variable in (
        ("prepend_path", "PATH"),
        ("prepend_library_path", "LD_LIBRARY_PATH"),
    ):
        paths = options.get(setting, [])
        if not isinstance(paths, list):
            raise ValueError("runtime." + setting + " must be an array")
        if paths:
            prefix = [str(config_path(path, config_file)) for path in paths]
            suffix = os.environ.get(variable, "").split(os.pathsep)
            os.environ[variable] = os.pathsep.join(
                dict.fromkeys(v for v in prefix + suffix if v)
            )
    required = options.get("required_env", [])
    if not isinstance(required, list) or any(
        not isinstance(v, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", v)
        for v in required
    ):
        raise ValueError("runtime.required_env must be an array of variable names")
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        raise ValueError(
            "Missing required environment variables: " + ", ".join(missing)
        )
    python = options.get("python")
    if python:
        python = expand(python)
        target = (
            str(config_path(python, config_file))
            if "/" in python
            else shutil.which(python)
        )
        if not target or not os.path.isfile(target) or not os.access(target, os.X_OK):
            raise ValueError("Configured Python executable was not found")
        return os.path.abspath(target)
    return os.path.abspath(sys.executable)


def check_environment(settings, config_file, case=None, model=None):
    from .config import load_config

    config = load_config(config_file, model)
    dependencies = {}
    for name in ("numpy", "scipy", "jsonschema", "matplotlib"):
        try:
            importlib.import_module(name)
            dependencies[name] = True
        except (ImportError, OSError):
            dependencies[name] = False
    execution = config.get("execution", {})
    commands = {}
    arrays = (
        [execution.get(name, []) for name in ("vasp", "wannier90")]
        if execution.get("adapter") == "vasp_wannier"
        else execution.get("commands", [])
    )
    for argv in arrays:
        if (
            not isinstance(argv, list)
            or not argv
            or any(not isinstance(v, str) for v in argv)
        ):
            raise ValueError("Execution commands must be nonempty argv arrays")
        # Check both mpirun and registered program when an MPI wrapper is used.
        indices = (
            [0, 3]
            if Path(argv[0]).name in ("mpirun", "mpiexec")
            and len(argv) > 3
            and argv[1] in ("-np", "-n")
            else [0]
        )
        for index in indices:
            commands[argv[index]] = shutil.which(argv[index])
    key_name = config["llm"].get("api_key_env")
    return {
        "config": str(Path(config_file).resolve()),
        "python": sys.executable,
        "dependencies": dependencies,
        "executables": commands,
        "environment": {name: os.environ.get(name) for name in THREAD_VARIABLES},
        "api_key_env": key_name,
        "model": config["llm"].get("model"),
        "api_key_present": bool(
            config["llm"].get("api_key") or (key_name and os.environ.get(key_name))
        ),
        "proxy_mode": config["llm"].get("proxy_mode", "environment"),
        "case_dir": str(case) if case else None,
        "case_exists": Path(case).is_dir() if case else None,
        "ok": all(dependencies.values())
        and all(commands.values())
        and (Path(case).is_dir() if case else True),
    }


def main(argv=None, *, project_root=None):
    arguments = list(sys.argv[1:] if argv is None else argv)
    probe = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
    probe.add_argument("--config")
    probe.add_argument("--model")
    known, remaining = probe.parse_known_args(arguments)
    try:
        # Explicit data-only commands remain usable without an operator config.
        try:
            config_file = (
                Path(known.config).expanduser().resolve()
                if known.config
                else default_config(project_root)
            )
        except ValueError:
            if (
                not known.config
                and remaining
                and remaining[0]
                in ("cache", "inspect", "validate", "plot", "init-config")
            ):
                from .cli import main as cli_main

                return cli_main(arguments)
            raise
        settings = read_settings(config_file, known.model)
        signature = hashlib.sha256(
            str(Path(config_file).resolve()).encode()
            + Path(config_file).read_bytes()
            + str(known.model).encode()
        ).hexdigest()
        if os.environ.pop("PYTHONAGENT_STARTUP_REEXEC", None) != signature:
            python = prepare_environment(settings, config_file)
            environment = dict(os.environ)
            package_root = str(Path(__file__).resolve().parent.parent)
            environment["PYTHONAGENT_STARTUP_REEXEC"] = signature
            if not known.config:
                arguments = ["--config", str(config_file), *arguments]
            bootstrap = "import sys; sys.path.insert(0, sys.argv.pop(1)); from pythonagent.startup import main; raise SystemExit(main())"
            os.execve(
                python,
                [python, "-B", "-u", "-c", bootstrap, package_root, *arguments],
                environment,
            )
        from .cli import main as cli_main

        return cli_main(arguments, default_config_file=config_file)
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        print(
            json.dumps(
                {"stop": "configuration_error", "error": str(error)}, ensure_ascii=False
            ),
            file=sys.stderr,
        )
        return 2
