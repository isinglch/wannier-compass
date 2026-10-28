"""Scientific figures from exact source data. Rendering never decides a plan or fit."""

from __future__ import annotations

import html
import json
import math
import os
from pathlib import Path
import time

import numpy as np

from .formats import hr_energies, match_target
from .plot_paths import verified_path, raw_scale, tick_locations, window_display_ranges
from .state import atomic_json, digest, file_sha, immutable_json
from .validation import validate_plan
from .results import FIT_STATUSES


def coordinates(evidence):
    evidence.ensure_current()
    path, k, x, reciprocal = verified_path(
        evidence.cache_path,
        None,
        evidence.revision,
        evidence.meta,
        len(evidence.energy),
    )
    current = evidence.case / "02_band/KPOINTS"
    expected = (evidence.meta.get("source_files", {}).get("kpoints") or {}).get(
        "sha256"
    )
    if current.exists() and file_sha(current) != expected:
        raise ValueError("Plot geometry does not match the current KPOINTS source")
    return path, k, x, reciprocal


def _pyplot():
    # Use an explicit writable cache location on headless cluster installations.
    os.environ.setdefault(
        "MPLCONFIGDIR",
        str(Path(os.environ.get("TMPDIR", "/tmp")) / "pythonagent-matplotlib"),
    )
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    return plt


def _prepare(evidence, out):
    path, k, x, reciprocal = coordinates(evidence)
    directory = Path(out).resolve()
    directory.mkdir(parents=True, exist_ok=False)
    return directory, path, k, x, reciprocal


def _decorate(ax, path, x, limits):
    positions, labels = tick_locations(path, x)
    for position in positions:
        ax.axvline(position, color=".88", lw=0.6)
    if positions:
        ax.set_xticks(positions, labels)
    ax.axhline(0, color="#52637a", lw=0.8, ls=":")
    ax.set(
        xlim=(float(x[0]), float(x[-1])),
        ylim=limits,
        ylabel=r"$E-E_F$ (eV)",
        xlabel=r"Reciprocal-path distance ($\mathrm{\AA}^{-1}$); jumps excluded",
    )
    ax.tick_params(labelsize=8)


def _bands(ax, path, x, energies, **kwargs):
    for segment in path["segments"]:
        sl = slice(segment["start"], segment["stop"])
        ax.plot(x[sl], energies[sl], **kwargs)


def _windows(ax, windows):
    for key, color in (("dis_eV", "#26916b"), ("frozen_eV", "#db9b2c")):
        values = windows[key]
        ax.axhspan(*values, color=color, alpha=0.10)
        for value in values:
            ax.axhline(value, color=color, lw=0.8, ls="--")


def _full(energies):
    low, high = float(np.min(energies)), float(np.max(energies))
    padding = max(0.1, (high - low) * 0.015)
    return [low - padding, high + padding]


def _views(energies, plan=None):
    result = [("All computed states", _full(energies))]
    if plan:
        ranges = window_display_ranges(plan["windows_relative_eV"])
        result += [
            ("Outer + display context", ranges["outer_context"]),
            ("Frozen + display context", ranges["frozen_context"]),
        ]
    return result


def _save(fig, directory, stem):
    plt = _pyplot()
    try:
        for extension in ("png", "pdf"):
            fig.savefig(directory / (stem + "." + extension), dpi=170)
    finally:
        plt.close(fig)


def _manifest(evidence, directory, kind, path, x, reciprocal, **values):
    evidence.ensure_current()
    outputs = {
        p.name: {"path": str(p), "sha256": file_sha(p), "bytes": p.stat().st_size}
        for p in sorted(directory.iterdir())
        if p.suffix in (".png", ".pdf", ".json")
    }
    record = {
        "format": "pythonagent-plots-v1",
        "kind": kind,
        "status": "rendered",
        "fit_pass": None,
        "renderer": {
            "module_sha256": file_sha(__file__),
            "path_module_sha256": file_sha(Path(__file__).with_name("plot_paths.py")),
            "numpy_version": np.__version__,
        },
        "source": evidence.source,
        "ef_eV": evidence.meta["ef"],
        "energy_reference": "Original absolute energies minus the same cache SCF EF; no fitted shift.",
        "path": {
            "segments": path["segments"],
            "distance_inv_angstrom": x.tolist(),
            "reciprocal_lattice_2pi_per_angstrom": reciprocal.tolist(),
            "k_sequence_retained": True,
            "duplicate_endpoints_retained": True,
            "cross_segment_lines": False,
            "coordinate_reordering": False,
        },
        "limitations": [
            "Display context is not an acceptance interval or a scientific recommendation.",
            "Raw charge projections are not normalized probabilities or BZ populations.",
            "Energy-order curves and energy-only matching do not establish branch identity.",
            "Figure generation does not certify convergence, fit quality or scientific approval.",
        ],
        **values,
        "outputs": outputs,
    }
    immutable_json(directory / "manifest.json", record)
    return record


def render_dft(evidence, out):
    directory, path, _, x, reciprocal = _prepare(evidence, out)
    plt = _pyplot()
    energy = evidence.energy
    limits = _full(energy)
    fig, ax = plt.subplots(figsize=(9, 5.3), layout="constrained")
    _bands(ax, path, x, energy, color="#253a52", lw=0.65, rasterized=True)
    _decorate(ax, path, x, limits)
    ax.set_title("DFT bands | all computed states")
    _save(fig, directory, "dft_bands")
    scale = raw_scale(evidence.groups, 48.0)
    from matplotlib.colors import Normalize
    from matplotlib.cm import ScalarMappable

    norm = Normalize(vmin=scale["color_vmin"], vmax=scale["color_vmax"], clip=False)
    groups = list(evidence.groups.items())
    xx = np.broadcast_to(x[:, None], energy.shape)
    for start in range(0, len(groups), 6):
        page = groups[start : start + 6]
        rows = math.ceil(len(page) / 2)
        fig, axes = plt.subplots(
            rows, 2, figsize=(13, 3.5 * rows), squeeze=False, layout="constrained"
        )
        for ax, (name, weight) in zip(axes.flat, page):
            _bands(
                ax, path, x, energy, color=".73", lw=0.45, alpha=0.65, rasterized=True
            )
            visible = weight > 0
            ax.scatter(
                xx[visible],
                energy[visible],
                s=48.0 * weight[visible],
                c=weight[visible],
                cmap="viridis",
                norm=norm,
                linewidths=0,
                alpha=0.8,
                rasterized=True,
            )
            _decorate(ax, path, x, limits)
            ax.set_title(name + " | raw complete-shell weight", fontsize=10)
        for ax in list(axes.flat)[len(page) :]:
            ax.set_visible(False)
        fig.colorbar(
            ScalarMappable(norm=norm, cmap="viridis"),
            ax=list(axes.flat),
            fraction=0.018,
            pad=0.015,
            label="Raw projection (common scale)",
        )
        fig.suptitle(
            "DFT orbital projections | all channels; no normalization", fontsize=12
        )
        suffix = "" if len(groups) <= 6 else "_" + str(start // 6 + 1).zfill(3)
        _save(fig, directory, "projected_bands" + suffix)
    return _manifest(
        evidence,
        directory,
        "dft",
        path,
        x,
        reciprocal,
        display_range_eV=limits,
        raw_projection_scale=scale,
        groups=list(evidence.groups),
    )


def _check_plan(evidence, plan):
    report = validate_plan(plan, evidence)
    if not report["valid"]:
        raise ValueError(
            "Cannot plot a mismatched/invalid plan: " + json.dumps(report["errors"])
        )


def render_plan(evidence, plan, out):
    _check_plan(evidence, plan)
    directory, path, _, x, reciprocal = _prepare(evidence, out)
    plt = _pyplot()
    energy = evidence.energy
    views = _views(energy, plan)
    target = sorted(
        {
            b - 1
            for lo, hi in plan["target_band_ranges_1based"]
            for b in range(lo, hi + 1)
        }
    )
    excluded = [b - 1 for b in plan["excluded_bands_1based"]]
    selected = sum(
        (evidence.groups[name] for name in plan["basis"]), np.zeros_like(energy)
    )
    xx = np.broadcast_to(x[:, None], energy.shape)
    fig, axes = plt.subplots(
        1,
        len(views),
        figsize=(6 * len(views), 5.4),
        squeeze=False,
        layout="constrained",
    )
    for ax, (title, limits) in zip(axes.flat, views):
        _bands(ax, path, x, energy, color=".78", lw=0.5, rasterized=True)
        if excluded:
            _bands(
                ax,
                path,
                x,
                energy[:, excluded],
                color="#b57a28",
                lw=0.8,
                ls=":",
                rasterized=True,
            )
        _bands(ax, path, x, energy[:, target], color="#176b8e", lw=0.8, rasterized=True)
        visible = (energy >= limits[0]) & (energy <= limits[1]) & (selected > 0)
        ax.scatter(
            xx[visible],
            energy[visible],
            s=22 * selected[visible],
            color="#176b8e",
            alpha=0.42,
            linewidths=0,
            rasterized=True,
        )
        _windows(ax, plan["windows_relative_eV"])
        _decorate(ax, path, x, limits)
        ax.set_title(title, fontsize=11)
    from matplotlib.lines import Line2D

    handles = [
        Line2D([], [], color="#176b8e", label="Declared target / selected raw weight"),
        Line2D([], [], color=".7", label="Other DFT"),
        Line2D([], [], color="#26916b", ls="--", label="Outer"),
        Line2D([], [], color="#db9b2c", ls="--", label="Frozen"),
    ]
    if excluded:
        handles.append(Line2D([], [], color="#b57a28", ls=":", label="Excluded DFT"))
    axes[0, 0].legend(handles=handles, fontsize=8, loc="best")
    fig.suptitle(
        "Model proposal | "
        + ", ".join(plan["basis"])
        + f" | {plan['num_wann']} WFs | scientific approval not implied",
        fontsize=12,
    )
    _save(fig, directory, "selection")
    return _manifest(
        evidence,
        directory,
        "plan",
        path,
        x,
        reciprocal,
        plan_sha256=digest(plan),
        windows_relative_eV=plan["windows_relative_eV"],
        display_ranges_eV={name: limits for name, limits in views},
        target_band_ranges_1based=plan["target_band_ranges_1based"],
        excluded_bands_1based=plan["excluded_bands_1based"],
        selected_weight_marker_area_per_raw_unit=22.0,
    )


def save_comparison(
    path,
    evidence,
    plan,
    kcoords,
    wannier,
    *,
    baseline=None,
    status="completed",
    converged=None,
    disentanglement=None,
    sources=None,
):
    """Persist exact shared-k spectra so drawing never reruns VASP/Wannier90."""
    evidence.ensure_current()
    metadata = {
        "format": "pythonagent-comparison-v1",
        "input_revision": evidence.revision,
        "plan_sha256": digest(plan),
        "energy_reference": "relative_to_scf_ef",
        "ef_eV": evidence.meta["ef"],
        "execution_status": status,
        "disentanglement_converged": converged,
        "disentanglement": disentanglement,
        "baseline_target_band_ranges_1based": (baseline or plan)[
            "target_band_ranges_1based"
        ],
        "sources": sources or {},
    }
    with Path(path).open("xb") as stream:
        np.savez_compressed(
            stream,
            metadata=json.dumps(metadata, allow_nan=False),
            kcoords=kcoords,
            dft_relative_eV=evidence.energy,
            wannier_relative_eV=wannier,
        )


def read_comparison(evidence, plan, path):
    _, coords, _, _ = coordinates(evidence)
    with np.load(path, allow_pickle=False) as data:
        meta = json.loads(str(data["metadata"]))
        k = np.array(data["kcoords"])
        dft = np.array(data["dft_relative_eV"])
        wan = np.array(data["wannier_relative_eV"])
    if (
        meta.get("format") != "pythonagent-comparison-v1"
        or meta.get("input_revision") != evidence.revision
        or meta.get("plan_sha256") != digest(plan)
    ):
        raise ValueError("Comparison is not bound to this exact source and plan")
    if (
        meta.get("energy_reference") != "relative_to_scf_ef"
        or meta.get("ef_eV") != evidence.meta["ef"]
    ):
        raise ValueError(
            "Comparison energy reference differs; no fitted shift is allowed"
        )
    if k.shape != coords.shape or not np.allclose(k, coords, atol=1e-10, rtol=0):
        raise ValueError("Comparison k sequence differs; no remapping allowed")
    if dft.shape != evidence.energy.shape or not np.array_equal(dft, evidence.energy):
        raise ValueError("Comparison DFT spectrum differs from its source cache")
    if wan.shape != (len(dft), plan["num_wann"]) or not np.isfinite(wan).all():
        raise ValueError("Comparison Wannier spectrum has invalid dimensions/values")
    if meta.get("execution_status") not in FIT_STATUSES:
        raise ValueError(
            "Cannot draw a successful comparison for an incomplete execution"
        )
    dis = meta.get("disentanglement")
    expected_states = {
        "completed_converged": "converged",
        "completed_iteration_limit": "iteration_limit",
        "completed_no_disentanglement_required": "not_required",
    }
    expected = expected_states.get(meta["execution_status"])
    if (
        expected is not None
        and (not isinstance(dis, dict) or dis.get("state") != expected)
    ) or (
        isinstance(dis, dict)
        and dis.get("fit_readiness") not in ("available", "provisional")
    ):
        raise ValueError(
            "Comparison execution/DIS state is inconsistent or unconfirmed"
        )
    baseline = meta.get("baseline_target_band_ranges_1based")
    if (
        not isinstance(baseline, list)
        or not baseline
        or any(
            not isinstance(pair, list)
            or len(pair) != 2
            or any(type(v) is not int for v in pair)
            or not 1 <= pair[0] <= pair[1] <= dft.shape[1]
            for pair in baseline
        )
    ):
        raise ValueError("Invalid original target in comparison metadata")
    return meta, dft, wan


def comparison_from_hr(evidence, plan, hr, out):
    _check_plan(evidence, plan)
    _, coords, _, _ = coordinates(evidence)
    win = Path(hr).with_name(Path(hr).name.removesuffix("_hr.dat") + ".win")
    if not win.is_file():
        raise ValueError(
            "HR rendering requires its executed .win for window/basis identity"
        )
    from .formats import win_settings

    scalars, blocks = win_settings(win)
    if "num_iter" not in scalars or float(scalars["num_iter"]) != 0:
        raise ValueError("HR source must have num_iter=0")
    if [v.replace(" ", "").lower() for v in blocks.get("projections", [])] != [
        v.replace(" ", "").lower() for v in plan["basis"]
    ]:
        raise ValueError("HR source projections differ from the supplied plan")
    for keys, values in (
        (("dis_win_min", "dis_win_max"), plan["windows_relative_eV"]["dis_eV"]),
        (("dis_froz_min", "dis_froz_max"), plan["windows_relative_eV"]["frozen_eV"]),
    ):
        if any(
            key not in scalars
            or not np.isclose(
                float(scalars[key]), value + evidence.meta["ef"], atol=1e-7, rtol=0
            )
            for key, value in zip(keys, values)
        ):
            raise ValueError("HR source windows differ from the supplied plan")
    for key, value in plan["solver"].items():
        if key not in scalars or not np.isclose(
            float(scalars[key]), value, atol=1e-12, rtol=1e-12
        ):
            raise ValueError("HR source solver differs from the supplied plan")
    ws = Path(hr).with_name(Path(hr).name.removesuffix("_hr.dat") + "_wsvec.dat")
    if not ws.is_file():
        raise ValueError("HR plotting requires the matching WS translation file")
    wan = hr_energies(hr, coords, require_ws=True) - evidence.meta["ef"]
    if wan.shape != (len(coords), plan["num_wann"]):
        raise ValueError("HR dimension differs from the plan")
    save_comparison(
        out,
        evidence,
        plan,
        coords,
        wan,
        status="completed",
        sources={str(Path(p).resolve()): file_sha(p) for p in (hr, win, ws)},
    )


def render_fit(evidence, plan, comparison, out):
    _check_plan(evidence, plan)
    meta, dft, wan = read_comparison(evidence, plan, comparison)
    directory, path, _, x, reciprocal = _prepare(evidence, out)
    plt = _pyplot()
    views = _views(np.column_stack((dft, wan)), plan)
    fig, axes = plt.subplots(
        1,
        len(views),
        figsize=(6 * len(views), 5.4),
        squeeze=False,
        layout="constrained",
    )
    dis = meta.get("disentanglement")
    provisional = (
        dis.get("fit_readiness") != "available"
        if dis is not None
        else meta.get("disentanglement_converged") is not True
    )
    for ax, (title, limits) in zip(axes.flat, views):
        _bands(ax, path, x, dft, color=".6", lw=0.7, rasterized=True)
        _bands(ax, path, x, wan, color="#c33443", lw=0.85, ls="--", rasterized=True)
        _windows(ax, plan["windows_relative_eV"])
        _decorate(ax, path, x, limits)
        ax.set_title(title, fontsize=11)
    from matplotlib.lines import Line2D

    axes[0, 0].legend(
        handles=[
            Line2D([], [], color=".6", label="DFT"),
            Line2D([], [], color="#c33443", ls="--", label="Wannier"),
        ],
        fontsize=9,
    )
    label = (
        "PROVISIONAL: DIS convergence unconfirmed"
        if provisional
        else "DIS converged; scientific fit not assessed"
    )
    fig.suptitle("Same-k DFT / Wannier comparison | " + label, fontsize=12)
    _save(fig, directory, "fit_review")
    reports = {"target": match_target(dft, wan, plan["target_band_ranges_1based"])}
    baseline = meta["baseline_target_band_ranges_1based"]
    if baseline != plan["target_band_ranges_1based"]:
        reports["baseline_target"] = match_target(dft, wan, baseline)
    fig, axes = plt.subplots(2, 1, figsize=(11, 7), sharex=True, layout="constrained")
    colors = ("#c33443", "#246b9b")
    for (name, report), color in zip(reports.items(), colors):
        xx = []
        errors = []
        for row in report["per_k"]:
            for pair in row["pairs"]:
                xx.append(x[row["k_index_1based"] - 1])
                errors.append(pair["error_eV"])
        axes[0].scatter(
            xx, errors, s=13, color=color, alpha=0.65, label=name, rasterized=True
        )
        for segment in path["segments"]:
            sl = slice(segment["start"], segment["stop"])
            rows = report["per_k"][sl]
            axes[1].plot(
                x[sl],
                [len(r["unmatched_dft_bands"]) for r in rows],
                color=color,
                lw=1.3,
                label=name + " missing" if segment is path["segments"][0] else None,
            )
        counts = report["counts"]
        axes[0].text(
            0.01,
            0.97 - 0.07 * list(reports).index(name),
            f"{name}: matched {counts['matched_states']} / {counts['expected_states']}, missing {counts['missing_states']}",
            transform=axes[0].transAxes,
            va="top",
            fontsize=9,
            color=color,
        )
    axes[0].axhline(0, color=".5", lw=0.7)
    axes[0].set_ylabel("Wannier - DFT (eV)")
    axes[0].legend(loc="lower right")
    axes[1].set_ylabel("Missing target states / k")
    axes[1].legend(loc="best")
    maximum_missing = max(
        len(row["unmatched_dft_bands"])
        for report in reports.values()
        for row in report["per_k"]
    )
    axes[1].set_ylim(0, max(1, maximum_missing + 0.5))
    from matplotlib.ticker import MaxNLocator

    axes[1].yaxis.set_major_locator(MaxNLocator(integer=True))
    positions, labels = tick_locations(path, x)
    axes[1].set_xticks(positions, labels)
    axes[1].set_xlabel(r"Reciprocal-path distance ($\mathrm{\AA}^{-1}$)")
    axes[0].set_title(
        "Energy-only assignment residuals | "
        + label
        + "\nNo matching cutoff; matched errors do not include missing states",
        fontsize=11,
    )
    _save(fig, directory, "residuals")
    immutable_json(directory / "residuals.json", reports)
    return _manifest(
        evidence,
        directory,
        "comparison",
        path,
        x,
        reciprocal,
        comparison_source={
            "path": str(Path(comparison).resolve()),
            "sha256": file_sha(comparison),
        },
        plan_sha256=digest(plan),
        execution_status=meta["execution_status"],
        disentanglement_converged=meta.get("disentanglement_converged"),
        provisional=provisional,
        display_ranges_eV={name: limits for name, limits in views},
        metrics={
            name: {k: v for k, v in report.items() if k != "per_k"}
            for name, report in reports.items()
        },
    )


class PlotManager:
    """Automatic display artifacts with explicit errors, independent of scientific routing."""

    def __init__(self, evidence, journal, config=None):
        raw = {} if config is None else config
        if (
            not isinstance(raw, dict)
            or set(raw) - {"enabled"}
            or type(raw.get("enabled", True)) is not bool
        ):
            raise ValueError("plotting accepts only enabled: boolean")
        self.evidence, self.journal, self.enabled = (
            evidence,
            journal,
            raw.get("enabled", True),
        )
        self.records = []
        self.dft_record = None

    def context(self):
        return {
            "enabled": self.enabled,
            "dft": self.dft_record,
            "index": "run/plots/index.html" if self.records else None,
            "scope": "PNG/PDF display artifacts; paths are not proof that a text-only model has inspected images. Use source numerical evidence for decisions.",
        }

    def _run(self, kind, out, callback):
        if not self.enabled:
            return {"status": "disabled"}
        start = time.monotonic()
        try:
            result = callback()
            manifest = Path(out) / "manifest.json"
            record = {
                "kind": kind,
                "status": "rendered",
                "manifest": "run/" + str(manifest.relative_to(self.evidence.run)),
                "images": [
                    "run/" + str(Path(item["path"]).relative_to(self.evidence.run))
                    for name, item in result["outputs"].items()
                    if name.endswith(".png")
                ],
            }
        except (
            ValueError,
            OSError,
            KeyError,
            TypeError,
            ImportError,
            RuntimeError,
        ) as error:
            record = {
                "kind": kind,
                "status": "failed",
                "error": str(error),
                "output_dir": "run/" + str(Path(out).relative_to(self.evidence.run)),
            }
        record["wall_seconds"] = time.monotonic() - start
        self.records.append(record)
        self.journal.record("plotting", record)
        self._index()
        return record

    def _index(self):
        root = self.evidence.run / "plots"
        root.mkdir(exist_ok=True)
        atomic_json(
            root / "index.json",
            {
                "format": "pythonagent-plot-index-v1",
                "records": self.records,
                "fit_pass": None,
            },
        )
        body = [
            '<!doctype html><meta charset="utf-8"><title>Scientific figures</title><h1>Scientific figures</h1><p>Figures display recorded data; scientific approval is not implied.</p>'
        ]
        for record in self.records:
            body.append(
                "<h2>"
                + html.escape(record["kind"])
                + " — "
                + html.escape(record["status"])
                + "</h2>"
            )
            if record.get("error"):
                body.append("<pre>" + html.escape(record["error"]) + "</pre>")
            for ref in record.get("images", []):
                relative = os.path.relpath(
                    self.evidence.run / ref.removeprefix("run/"), root
                )
                body.append(
                    '<p><a href="'
                    + html.escape(relative, quote=True)
                    + '">'
                    + html.escape(Path(relative).name)
                    + '</a> · <a href="'
                    + html.escape(str(Path(relative).with_suffix(".pdf")), quote=True)
                    + '">PDF</a></p><img style="max-width:100%" src="'
                    + html.escape(relative, quote=True)
                    + '">'
                )
        (root / "index.html").write_text("\n".join(body))

    def dft(self):
        self.dft_record = self._run(
            "dft",
            self.evidence.run / "plots/dft",
            lambda: render_dft(self.evidence, self.evidence.run / "plots/dft"),
        )
        return self.dft_record

    def plan(self, plan, directory):
        return self._run(
            "plan",
            directory / "plots",
            lambda: render_plan(self.evidence, plan, directory / "plots"),
        )

    def trial(self, plan, directory, *, execution_status="completed"):
        comparison = directory / "comparison.npz"
        if not self.enabled:
            return {"status": "disabled"}
        if execution_status not in FIT_STATUSES:
            return {
                "status": "unavailable",
                "reason": "Execution status is "
                + execution_status
                + "; no successful fit image is fabricated.",
            }
        if not comparison.is_file():
            return {
                "status": "unavailable",
                "reason": "Execution produced no bound same-k comparison spectrum; no curves fabricated.",
            }
        return self._run(
            "comparison",
            directory / "plots",
            lambda: render_fit(self.evidence, plan, comparison, directory / "plots"),
        )
