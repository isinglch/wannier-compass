"""Sampled-path facts: explicit connectivity, sign brackets and raw leaders."""

import numpy as np

from .observations import page_rows
from .plotting import coordinates


def path_data(evidence):
    # Reuse the plotting geometry verifier, including current KPOINTS identity.
    return coordinates(evidence)


def path_facts(evidence):
    try:
        path, coords, _, _ = path_data(evidence)
    except (ValueError, KeyError, OSError) as error:
        return {"status": "unavailable", "reason": str(error)}
    return {
        "status": "available",
        "source_kpoints_sha256": path["source_kpoints_sha256"],
        "coordinate_system": "fractional_reciprocal",
        "segments": [
            {
                "segment": i + 1,
                "k_range_1based": [s["start"] + 1, s["stop"]],
                "start_label": s.get("start_label", ""),
                "end_label": s.get("end_label", ""),
                "start_fractional": coords[s["start"]].tolist(),
                "end_fractional": coords[s["stop"] - 1].tolist(),
                "connected_to_previous": bool(
                    i
                    and np.allclose(
                        coords[path["segments"][i - 1]["stop"] - 1],
                        coords[s["start"]],
                        atol=1e-10,
                        rtol=0,
                    )
                ),
            }
            for i, s in enumerate(path["segments"])
        ],
        "method": "Authenticated stored line segments; no inferred links across gaps.",
    }


def leaders(evidence, k, b):
    weights = {name: float(w[k, b]) for name, w in evidence.groups.items()}
    maximum = max(weights.values())
    return [name for name, w in weights.items() if w == maximum and w > 0]


def witness(evidence, k, b, coords=None):
    result = {
        "k_band_1based": [k + 1, b + 1],
        "energy_relative_eV": float(evidence.energy[k, b]),
        "raw_projection": {name: float(w[k, b]) for name, w in evidence.groups.items()},
        "leading_channels": leaders(evidence, k, b),
    }
    if coords is not None:
        result["k_fractional"] = coords[k].tolist()
    return result


def character_segments(evidence, band, k_lo, k_hi, offset=0, limit=24):
    facts = path_facts(evidence)
    if facts["status"] != "available":
        return {"segment_status": "unavailable", "segment_reason": facts["reason"]}
    rows = []
    b = band - 1
    for s in facts["segments"]:
        first, last = s["k_range_1based"]
        lo, hi = max(first, k_lo) - 1, min(last, k_hi)
        start, current = lo, None
        for k in range(lo, hi):
            group = leaders(evidence, k, b)
            if current is not None and group != current:
                rows.append(_run(evidence, b, start, k, current, s["segment"]))
                start = k
            current = group
        if current is not None:
            rows.append(_run(evidence, b, start, hi, current, s["segment"]))
    return {
        "segment_status": "available",
        "row_kind": "leading_channel_run",
        **page_rows(rows, offset, limit, budget=5500),
        "segment_method": "Exact positive raw-weight argmax sets, ties preserved; empty set means all zero. Runs never cross declared path segments. No orbital inclusion judgment.",
    }


def _run(evidence, b, lo, hi, group, segment):
    e = evidence.energy[lo:hi, b]
    return {
        "segment": segment,
        "k_range_1based": [lo + 1, hi],
        "leading_channels": group,
        "samples": hi - lo,
        "energy_range_relative_eV": [float(e.min()), float(e.max())],
        "start_witness": witness(evidence, lo, b),
    }


def ef_crossings(
    evidence, band_lo=1, band_hi=None, k_lo=1, k_hi=None, offset=0, limit=24
):
    evidence.ensure_current()
    nk, nb = evidence.energy.shape
    band_hi = nb if band_hi is None else band_hi
    k_hi = nk if k_hi is None else k_hi
    if not 1 <= band_lo <= band_hi <= nb or not 1 <= k_lo <= k_hi <= nk:
        raise ValueError("Invalid source range")
    path, coords, _, _ = path_data(evidence)
    rows = []
    for b in range(band_lo - 1, band_hi):
        seen_zeros = set()
        for i, segment in enumerate(path["segments"], 1):
            lo, hi = max(segment["start"], k_lo - 1), min(segment["stop"], k_hi)
            for k in range(lo, hi):
                e0 = evidence.energy[k, b]
                if e0 == 0 and k not in seen_zeros:
                    seen_zeros.add(k)
                    rows.append(
                        {
                            "kind": "sample_at_ef",
                            "band": b + 1,
                            "segment": i,
                            "witnesses": [witness(evidence, k, b, coords)],
                            "linear_estimate": None,
                        }
                    )
                if k + 1 >= hi:
                    continue
                e1 = evidence.energy[k + 1, b]
                if e0 != 0 and e1 != 0 and np.signbit(e0) != np.signbit(e1):
                    fraction = float(-e0 / (e1 - e0))
                    rows.append(
                        {
                            "kind": "sign_change_bracket",
                            "band": b + 1,
                            "segment": i,
                            "witnesses": [
                                witness(evidence, k, b, coords),
                                witness(evidence, k + 1, b, coords),
                            ],
                            "linear_estimate": {
                                "fraction_between_samples": fraction,
                                "k_fractional": (
                                    coords[k] + fraction * (coords[k + 1] - coords[k])
                                ).tolist(),
                            },
                        }
                    )
    return {
        "source": evidence.source,
        "ef_eV": evidence.meta["ef"],
        "band_range_1based": [band_lo, band_hi],
        "k_range_1based": [k_lo, k_hi],
        "bands_with_sign_changes_1based": sorted(
            {r["band"] for r in rows if r["kind"] == "sign_change_bracket"}
        ),
        "bands_with_exact_ef_samples_1based": sorted(
            {r["band"] for r in rows if r["kind"] == "sample_at_ef"}
        ),
        "sample_at_ef_count": sum(r["kind"] == "sample_at_ef" for r in rows),
        "sign_change_bracket_count": sum(
            r["kind"] == "sign_change_bracket" for r in rows
        ),
        **page_rows(rows, offset, limit),
        "method": "E minus supplied SCF EF; exact zero samples and strict adjacent sign changes within authenticated segments. Intersections are linear estimates, not computed states; projections are reported only at real endpoints. Band indices are energy-ordered, not wavefunction-tracked. Path sampling is not full-BZ/Fermi-surface coverage or a basis decision.",
    }
