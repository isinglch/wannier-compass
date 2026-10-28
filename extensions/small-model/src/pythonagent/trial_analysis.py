"""Explicit population filters and comparable trial measurements, no ranking of plans."""

import json
from pathlib import Path

from .formats import match_target
from .plotting import read_comparison
from .state import digest, file_sha


def trial_file(evidence, trial, name):
    if not trial or Path(trial).name != trial or trial in (".", ".."):
        raise ValueError("Use a trial identifier")
    return evidence.resolve(f"run/trials/{trial}/{name}")


def residual_ranking(
    evidence,
    trial,
    top_n=3,
    population="baseline_target",
    *,
    k_lo=1,
    k_hi=None,
    band_lo=1,
    band_hi=None,
    energy_range_eV=None,
    region="all",
):
    evidence.ensure_current()
    path = trial_file(evidence, trial, "diagnostics.json")
    data = json.loads(path.read_text())
    if population not in ("baseline_target", "target") or population not in data:
        raise ValueError(
            "Requested target population is unavailable; no implicit fallback"
        )
    k_hi = len(evidence.energy) if k_hi is None else k_hi
    band_hi = evidence.energy.shape[1] if band_hi is None else band_hi
    if (
        not 1 <= k_lo <= k_hi <= len(evidence.energy)
        or not 1 <= band_lo <= band_hi <= evidence.energy.shape[1]
    ):
        raise ValueError("Invalid source coordinate filter")
    if energy_range_eV is not None and energy_range_eV[0] > energy_range_eV[1]:
        raise ValueError("Energy range must be ordered")
    interval = None
    plan_source = None
    if region != "all":
        if region not in ("frozen", "outer", "above_outer"):
            raise ValueError("Unknown region")
        plan_path = trial_file(evidence, trial, "approved-plan.json")
        plan = json.loads(plan_path.read_text())
        if plan["input_revision"] != evidence.revision:
            raise ValueError("Trial source differs from current evidence")
        interval = plan["windows_relative_eV"][
            "frozen_eV" if region == "frozen" else "dis_eV"
        ]
        plan_source = {
            "path": f"run/trials/{trial}/approved-plan.json",
            "sha256": file_sha(plan_path),
        }

    def selected(k, band, energy):
        if not (k_lo <= k <= k_hi and band_lo <= band <= band_hi):
            return False
        if (
            energy_range_eV is not None
            and not energy_range_eV[0] <= energy <= energy_range_eV[1]
        ):
            return False
        if interval is not None:
            return (
                energy > interval[1]
                if region == "above_outer"
                else interval[0] <= energy <= interval[1]
            )
        return True

    population_data = data[population]
    pairs, missing = [], []
    for row in population_data.get("per_k", []):
        k = row["k_index_1based"]
        for pair in row.get("pairs", []):
            if selected(k, pair["dft_band_1based"], pair["dft_eV"]):
                pairs.append({"k": k, **pair})
        for band in row.get("unmatched_dft_bands", []):
            energy = float(evidence.energy[k - 1, band - 1])
            if selected(k, band, energy):
                missing.append({"k": k, "dft_band_1based": band, "dft_eV": energy})
    pairs.sort(key=lambda p: (-abs(p["error_eV"]), p["k"], p["dft_band_1based"]))
    errors = [abs(p["error_eV"]) for p in pairs]
    return {
        "population": population,
        "source": {
            "path": f"run/trials/{trial}/diagnostics.json",
            "sha256": file_sha(path),
        },
        "region_plan_source": plan_source,
        "filters": {
            "k_range_1based": [k_lo, k_hi],
            "band_range_1based": [band_lo, band_hi],
            "energy_range_eV": energy_range_eV,
            "region": region,
        },
        "rows": pairs[:top_n],
        "missing_witnesses": missing[:top_n],
        "filtered_counts": {
            "matched_states": len(pairs),
            "missing_states": len(missing),
        },
        "filtered_metrics": {
            "mae_eV": sum(errors) / len(errors),
            "max_abs_eV": max(errors),
        }
        if errors
        else None,
        "counts": population_data.get("counts"),
        "metrics": population_data.get("matched_error_metrics"),
        "method": "Filter before sorting by absolute matched energy residual. Inclusive DFT-energy/k/band bounds; above_outer is strictly above that trial's approved outer edge. counts/metrics describe the whole population; filtered_* describe the query. Missing states are separate, not zero error. No fit verdict or branch identity.",
    }


def _bands(ranges):
    return sorted({b for lo, hi in ranges for b in range(lo, hi + 1)})


def _read_trial(evidence, trial, population):
    plan_path = trial_file(evidence, trial, "approved-plan.json")
    plan = json.loads(plan_path.read_text())
    path = trial_file(evidence, trial, "comparison.npz")
    binding_path = trial_file(evidence, trial, "result_binding.json")
    binding = json.loads(binding_path.read_text())
    if binding.get("comparison_sha256") != file_sha(path):
        raise ValueError("Trial comparison differs from its result binding")
    meta, dft, wan = read_comparison(evidence, plan, path)
    ranges = (
        meta["baseline_target_band_ranges_1based"]
        if population == "baseline_target"
        else plan["target_band_ranges_1based"]
    )
    measurements = match_target(dft, wan, ranges)
    identities = {
        (row["k_index_1based"], p["dft_band_1based"])
        for row in measurements["per_k"]
        for p in row["pairs"]
    }
    return {
        "trial": trial,
        "plan_sha256": digest(plan),
        "plan": plan,
        "target_bands_1based": _bands(ranges),
        "counts": measurements["counts"],
        "metrics": measurements["matched_error_metrics"],
        "execution_status": meta["execution_status"],
        "disentanglement": meta.get("disentanglement"),
        "source": {
            "path": f"run/trials/{trial}/comparison.npz",
            "sha256": file_sha(path),
        },
        "binding_source": {
            "path": f"run/trials/{trial}/result_binding.json",
            "sha256": file_sha(binding_path),
        },
    }, identities


def trial_compare(evidence, left, right, population="baseline_target"):
    evidence.ensure_current()
    if population not in ("baseline_target", "target"):
        raise ValueError("Unknown population")
    a, a_ids = _read_trial(evidence, left, population)
    b, b_ids = _read_trial(evidence, right, population)
    changes = []

    def differences(x, y, path):
        if x == y:
            return
        if isinstance(x, dict) and isinstance(y, dict):
            for key in sorted(x.keys() | y.keys()):
                differences(x.get(key), y.get(key), path + "/" + key)
        else:
            changes.append({"field": path, "left": x, "right": y})

    for field in (
        "basis",
        "num_wann",
        "target_band_ranges_1based",
        "excluded_bands_1based",
        "windows_relative_eV",
        "solver",
    ):
        differences(a["plan"].get(field), b["plan"].get(field), "/" + field)
    del a["plan"], b["plan"]
    same_population = a["target_bands_1based"] == b["target_bands_1based"]
    same_matches = a_ids == b_ids
    comparable = same_population and same_matches
    return {
        "source": evidence.source,
        "population": population,
        "ef_eV": evidence.meta["ef"],
        "energy_reference": "relative_to_scf_ef",
        "left": a,
        "right": b,
        "changed_plan_fields": changes,
        "comparison_facts": {
            "same_source_and_k_sequence": True,
            "same_target_population": same_population,
            "same_matched_dft_states": same_matches,
            "aggregate_delta_available": comparable
            and bool(a["metrics"])
            and bool(b["metrics"]),
        },
        "metric_delta_right_minus_left": {
            key: b["metrics"][key] - a["metrics"][key]
            for key in ("mae_eV", "max_abs_eV")
        }
        if comparable and a["metrics"] and b["metrics"]
        else None,
        "method": "Recomputed with the native per-k energy assignment on authenticated shared-source spectra. Metric deltas require identical target and matched DFT-state populations. Missing coverage and DIS status remain explicit; no best plan, acceptance verdict, fitted shift or wavefunction branch identity.",
    }
