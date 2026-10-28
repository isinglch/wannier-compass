"""Verified path geometry and display ranges migrated from MCP plotting. No scientific selection."""

from pathlib import Path
import json
import numpy as np


def verify_path(cache, kcoords, x, path, tolerance):
    with np.load(cache, allow_pickle=False) as z:
        if "kcoords" not in z:
            raise ValueError(
                "DFT cache needs kcoords; energy arrays alone cannot align paths."
            )
        dft_k = np.asarray(z["kcoords"], dtype=float)
    if dft_k.shape != (1, len(kcoords), 3) or not np.isfinite(dft_k).all():
        raise ValueError(
            "DFT kcoords must have shape (1, nk, 3), matching Wannier exactly."
        )
    delta = float(np.max(np.abs(dft_k[0] - kcoords)))
    if delta > tolerance:
        raise ValueError(
            f"DFT/Wannier k sequences differ by {delta:g}; no rescaling or remapping allowed."
        )
    if path.get("coordinate_system") != "fractional_reciprocal":
        raise ValueError("Path must explicitly use fractional_reciprocal coordinates.")
    segments = path.get("segments", [])
    if not segments:
        raise ValueError(
            "Explicit path segments are required; gaps cannot be inferred from energy curves."
        )
    previous_stop = 0
    for i, segment in enumerate(segments):
        start, stop = segment["start"], segment["stop"]
        if type(start) is not int or type(stop) is not int:
            raise ValueError("Segment start/stop must be zero-based integer slices.")
        if not 0 <= start < stop <= len(kcoords) or stop - start < 2:
            raise ValueError("Each segment needs at least two in-range points.")
        allowed_starts = [0] if i == 0 else [previous_stop - 1, previous_stop]
        if start not in allowed_starts or stop <= previous_stop:
            raise ValueError(
                "Segments must cover the sequence in order, with at most one shared endpoint."
            )
        endpoints = np.asarray(
            [segment["start_fractional"], segment["end_fractional"]], dtype=float
        )
        if endpoints.shape != (2, 3) or not np.isfinite(endpoints).all():
            raise ValueError(
                "Segment endpoints must be finite fractional three-vectors."
            )
        expected = np.linspace(endpoints[0], endpoints[1], stop - start)
        if not np.allclose(kcoords[start:stop], expected, atol=tolerance, rtol=0):
            raise ValueError(
                f"Segment {i + 1} does not match the explicitly declared straight path."
            )
        if (np.diff(x[start:stop]) <= 0).any():
            raise ValueError(
                "Distance must strictly increase within each declared segment."
            )
        previous_stop = stop
    if previous_stop != len(kcoords):
        raise ValueError("Declared segments do not cover every sampled point.")
    return delta


def interval(values, name):
    if len(values) != 2 or not np.isfinite(values).all() or values[0] >= values[1]:
        raise ValueError(
            f"{name} must be two finite increasing energies relative to EF."
        )
    return list(map(float, values))


def window_display_ranges(windows, frozen_padding=1.0, outer_padding=3.0):
    """Context around physical windows, never a fixed interval around EF."""
    if (
        not np.isfinite([frozen_padding, outer_padding]).all()
        or min(frozen_padding, outer_padding) < 0
    ):
        raise ValueError("Display padding must be finite and nonnegative.")
    dis = interval(windows["dis_eV"], "Outer window")
    frozen = interval(windows["frozen_eV"], "Frozen window")
    if not dis[0] <= frozen[0] < frozen[1] <= dis[1]:
        raise ValueError("Current decision windows must be nested.")
    return dict(
        frozen_context=[frozen[0] - frozen_padding, frozen[1] + frozen_padding],
        outer_context=[dis[0] - outer_padding, dis[1] + outer_padding],
    )


def verified_path(cache, path_file, cache_hash, meta, nk):
    with np.load(cache, allow_pickle=False) as z:
        coords = np.asarray(z["kcoords"], dtype=float)
        if "path_geometry" not in z:
            raise ValueError(
                "A verified band_path geometry is required for the standard plots."
            )
        geometry = json.loads(str(z["path_geometry"]))
    if (
        meta.get("sampling") != "band_path"
        or coords.shape != (1, nk, 3)
        or not np.isfinite(coords).all()
    ):
        raise ValueError(
            "Require the existing one-channel band_path cache with its complete finite k sequence."
        )
    if path_file is None:
        source = meta.get("source_files", {}).get("kpoints", {})
        if not source.get("sha256") or source["sha256"] != geometry.get(
            "source_kpoints_sha256"
        ):
            raise ValueError(
                "Embedded geometry must authenticate the original KPOINTS source."
            )
        labels = {
            tick["index_1based"]: tick["label"] for tick in geometry.get("ticks", [])
        }
        segments = []
        for first, last in geometry.get("segments_1based", []):
            if (
                type(first) is not int
                or type(last) is not int
                or not 1 <= first < last <= nk
            ):
                raise ValueError("Invalid embedded path segment.")
            segments.append(
                dict(
                    start=first - 1,
                    stop=last,
                    start_fractional=coords[0, first - 1].tolist(),
                    end_fractional=coords[0, last - 1].tolist(),
                    start_label=labels.get(first, ""),
                    end_label=labels.get(last, ""),
                )
            )
        path = dict(
            coordinate_system="fractional_reciprocal",
            cache_sha256=cache_hash,
            procar_sha256=meta.get("procar_sha256"),
            source_kpoints_sha256=source["sha256"],
            segments=segments,
        )
    else:
        path = json.loads(Path(path_file).read_text())
    if path.get("cache_sha256") != cache_hash or path.get("procar_sha256") != meta.get(
        "procar_sha256"
    ):
        raise ValueError(
            "fit_path.json does not authenticate this exact cache and PROCAR."
        )
    if (
        geometry.get("kcoords_type") != "fractional"
        or path.get("coordinate_system") != "fractional_reciprocal"
    ):
        raise ValueError("Explicit fractional reciprocal coordinates are required.")
    if (
        not geometry.get("source_kpoints_sha256")
        or path.get("source_kpoints_sha256") != geometry["source_kpoints_sha256"]
    ):
        raise ValueError("DFT path/cache KPOINTS source hashes differ or are missing.")
    reciprocal = np.asarray(
        geometry.get("reciprocal_lattice_2pi_per_angstrom"), dtype=float
    )
    if (
        reciprocal.shape != (3, 3)
        or not np.isfinite(reciprocal).all()
        or abs(np.linalg.det(reciprocal)) < 1e-12
    ):
        raise ValueError(
            "Require three nonsingular reciprocal row vectors, including 2pi, in inverse Angstrom."
        )
    k, x, stop_previous = coords[0], np.full(nk, np.nan), 0
    segments = path.get("segments", [])
    if not segments:
        raise ValueError(
            "Explicit path segments are required; no inferred connections across gaps."
        )
    for index, segment in enumerate(segments):
        start, stop = segment["start"], segment["stop"]
        if (
            type(start) is not int
            or type(stop) is not int
            or not 0 <= start < stop <= nk
            or stop - start < 2
        ):
            raise ValueError(
                "Each segment must be a valid half-open integer slice with at least two samples."
            )
        if (
            start not in ([0] if index == 0 else [stop_previous - 1, stop_previous])
            or stop <= stop_previous
        ):
            raise ValueError(
                "Segments must cover the stored sequence in order, with at most one shared endpoint."
            )
        steps = np.linalg.norm(np.diff(k[start:stop], axis=0) @ reciprocal, axis=1)
        if not np.isfinite(steps).all() or np.any(steps <= 0):
            raise ValueError(
                "Each segment must have strictly increasing physical distance."
            )
        origin = 0.0 if index == 0 else x[stop_previous - 1]
        x[start:stop] = origin + np.r_[0.0, np.cumsum(steps)]
        stop_previous = stop
    if stop_previous != nk or not np.isfinite(x).all():
        raise ValueError("Path segments must cover all stored samples.")
    if geometry.get("segments_1based") != [
        [s["start"] + 1, s["stop"]] for s in segments
    ]:
        raise ValueError(
            "Embedded geometry and fit_path disagree about segment slices."
        )
    verify_path(cache, k, x, path, 1e-6)
    return path, k, x, reciprocal


def raw_scale(groups, marker_area):
    """One common color scale; marker area remains raw weight times fixed area."""
    if not np.isfinite(marker_area) or marker_area <= 0:
        raise ValueError("Marker area per unit raw weight must be finite and positive.")
    maximum = max(float(w.max()) for w in groups.values())
    return dict(
        color_vmin=0.0,
        color_vmax=max(1.0, float(np.ceil(maximum * 10) / 10)),
        marker_area_per_raw_unit_pt2=float(marker_area),
        observed_global_raw_max=maximum,
        self_normalization=False,
        per_atom_averaging=False,
        weight_clipping=False,
        aggregation="sum of original charge projections over every atom of the element and every m component of the complete shell",
    )


def tick_locations(path, x):
    ticks = {}
    for s in path["segments"]:
        for index, label in (
            (s["start"], s.get("start_label", "")),
            (s["stop"] - 1, s.get("end_label", "")),
        ):
            if label:
                bucket = ticks.setdefault(float(x[index]), [])
                if label not in bucket:
                    bucket.append(label)
    return list(ticks), [" | ".join(labels) for labels in ticks.values()]
