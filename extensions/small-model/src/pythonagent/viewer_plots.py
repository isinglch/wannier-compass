"""Read only named trial figures from their manifests; no arbitrary file serving."""

import hashlib
import json
import re
import struct
from urllib.parse import urlencode


PLOTS = {"fit_review": "能带拟合", "residuals": "拟合残差"}
MIME = {"png": "image/png", "pdf": "application/pdf"}


def trial_path(run, trial):
    if not re.fullmatch(r"trial_[0-9]+", trial):
        raise ValueError("Invalid trial identifier")
    return checked_file(run, "trials/" + trial)


def checked_file(run, relative):
    path = run / relative
    if not path.resolve().is_relative_to(run.resolve()):
        raise ValueError("Figure path escapes run")
    # An ancestor link inside the run must not turn a plot route into private data.
    if any(
        p.is_symlink() for p in [path, *path.parents] if p != run and run in p.parents
    ):
        raise ValueError("Symlinked figure paths are unavailable")
    return path


def read_json(path):
    if path.stat().st_size > 20_000_000:
        raise ValueError("Display record too large")
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError("Invalid display record")
    return value


def manifest(run, trial):
    trial_path(run, trial)
    value = read_json(checked_file(run, f"trials/{trial}/plots/manifest.json"))
    if (
        value.get("format") != "pythonagent-plots-v1"
        or value.get("kind") != "comparison"
    ):
        raise ValueError("Not a trial comparison manifest")
    result_path = checked_file(run, f"trials/{trial}/result.json")
    if result_path.exists():
        result = read_json(result_path)
        if result.get("plan_sha256") and result["plan_sha256"] != value.get(
            "plan_sha256"
        ):
            raise ValueError("Figure belongs to another plan")
    return value


def read_plot(run, trial, plot, extension, expected_hash=None):
    if plot not in PLOTS or extension not in MIME:
        raise ValueError("Unknown trial figure")
    data = manifest(run, trial)
    name = plot + "." + extension
    outputs = data.get("outputs")
    record = outputs.get(name, {}) if isinstance(outputs, dict) else {}
    if not isinstance(record, dict):
        raise ValueError("Invalid figure record")
    expected = record.get("sha256")
    if not isinstance(expected, str) or not re.fullmatch(r"[a-f0-9]{64}", expected):
        raise ValueError("Figure has no checksum")
    if expected_hash is not None and expected_hash != expected:
        raise ValueError("Figure version changed")
    # Ignore manifest absolute paths; resolve only this fixed public plot filename.
    path = checked_file(run, f"trials/{trial}/plots/{name}")
    if path.stat().st_size > 50_000_000:
        raise ValueError("Figure too large")
    body = path.read_bytes()
    if hashlib.sha256(body).hexdigest() != expected or len(body) != record.get("bytes"):
        raise ValueError("Figure differs from its manifest")
    dimensions = None
    if extension == "png":
        if len(body) < 24 or body[:8] != b"\x89PNG\r\n\x1a\n" or body[12:16] != b"IHDR":
            raise ValueError("Invalid PNG figure")
        dimensions = struct.unpack(">II", body[16:24])
        if not all(0 < n <= 100000 for n in dimensions):
            raise ValueError("Invalid PNG dimensions")
    elif not body.startswith(b"%PDF-"):
        raise ValueError("Invalid PDF figure")
    return body, MIME[extension], expected, dimensions


def trial_results(run):
    folder = checked_file(run, "trials")
    if not folder.is_dir():
        return []
    rows = []
    for directory in sorted(folder.iterdir(), key=lambda p: p.name):
        trial = directory.name
        if (
            directory.is_symlink()
            or not re.fullmatch(r"trial_[0-9]+", trial)
            or not directory.is_dir()
        ):
            continue
        try:
            path = checked_file(run, f"trials/{trial}/result.json")
            result = read_json(path) if path.exists() else {}
        except (OSError, ValueError):
            result = {}
        visualization = result.get("visualization")
        visualization = visualization if isinstance(visualization, dict) else {}
        row = {
            "trial": trial,
            "number": int(trial.split("_")[1]),
            "plan_id": result.get("plan_id"),
            "status": result.get("status", "running"),
            "images": [],
            "image_status": visualization.get("status", "pending"),
        }
        populations = result.get("metrics")
        populations = populations if isinstance(populations, dict) else {}
        population = "baseline_target" if "baseline_target" in populations else "target"
        row["metric_population"] = population
        measurements = populations.get(population)
        row["metrics"] = None
        if isinstance(measurements, dict):
            row["metrics"] = {
                name: {key: values[key] for key in keys if key in values}
                for name, keys in (
                    ("counts", ("expected_states", "matched_states", "missing_states")),
                    ("matched_error_metrics", ("mae_eV", "max_abs_eV")),
                )
                if isinstance(values := measurements.get(name), dict)
            }
        dis = result.get("disentanglement")
        dis = dis if isinstance(dis, dict) else {}
        row["disentanglement_state"] = dis.get("state")
        try:
            plots = manifest(run, trial)
            for plot, title in PLOTS.items():
                try:
                    _, _, checksum, size = read_plot(run, trial, plot, "png")
                    query = {
                        "run": run.name,
                        "trial": trial,
                        "plot": plot,
                        "format": "png",
                        "sha256": checksum,
                    }
                    image = {
                        "title": title,
                        "plot": plot,
                        "url": "/api/trial-plot?" + urlencode(query),
                        "sha256": checksum,
                        "width": size[0],
                        "height": size[1],
                    }
                    try:
                        _, _, pdf_hash, _ = read_plot(run, trial, plot, "pdf")
                        image["pdf_url"] = "/api/trial-plot?" + urlencode(
                            {**query, "format": "pdf", "sha256": pdf_hash}
                        )
                    except (OSError, ValueError, TypeError, KeyError):
                        pass
                    row["images"].append(image)
                except (OSError, ValueError, TypeError, KeyError):
                    continue
            row["image_status"] = (
                "rendered"
                if len(row["images"]) == len(PLOTS)
                else "partial"
                if row["images"]
                else "unavailable"
            )
            if not result:
                row["status"] = plots.get("execution_status", "running")
        except FileNotFoundError:
            if result and row["image_status"] == "pending":
                row["image_status"] = "unavailable"
        except (OSError, ValueError, TypeError, KeyError):
            row["image_status"] = "unavailable"
        rows.append(row)
    return sorted(rows, key=lambda row: row["number"])
