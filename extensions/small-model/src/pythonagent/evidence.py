"""Bounded source-linked facts. No window recommendation, projection veto or scientific status."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from .observations import page_rows, encoded_size, structure
from .band_analysis import path_facts, character_segments, ef_crossings
from .channels import resolved_shells
from .state import file_sha, digest
from .statistics_only import require_access, record_view


class Evidence:
    def __init__(self, case, run):
        self.case, self.run = Path(case).resolve(), Path(run).resolve()
        self.cache_path = self.case / "02_band/charge.npz"
        self.revision = file_sha(self.cache_path)
        with np.load(self.cache_path, allow_pickle=False) as data:
            self.meta = json.loads(str(data["metadata"]))
            raw, energy = (
                np.array(data["raw"], dtype=float),
                np.array(data["energies"], dtype=float),
            )
            self.occupations = (
                np.array(data["occupations"], dtype=float)
                if "occupations" in data
                else None
            )
        if (
            energy.ndim != 3
            or energy.shape[0] != 1
            or raw.ndim != 5
            or raw.shape[:3] != energy.shape
        ):
            raise ValueError(
                "Supported cache: one charge channel, matching k/band/ion/orbital axes"
            )
        if (
            not np.isfinite(energy).all()
            or not np.isfinite(raw).all()
            or (raw < 0).any()
        ):
            raise ValueError("Nonfinite energies or invalid raw charge projections")
        if not np.isfinite(self.meta["ef"]):
            raise ValueError("Missing finite SCF energy reference")
        self.energy = energy[0] - self.meta["ef"]
        if self.occupations is not None:
            if self.occupations.ndim == 3:
                self.occupations = self.occupations[0]
            if (
                self.occupations.shape != self.energy.shape
                or not np.isfinite(self.occupations).all()
            ):
                raise ValueError("Occupation axes must match energies")
        species, counts, columns = (
            self.meta["species"],
            self.meta["counts"],
            self.meta["columns"],
        )
        if (
            len(species) != len(counts)
            or any(type(n) is not int or n <= 0 for n in counts)
            or sum(counts) != raw.shape[3]
        ):
            raise ValueError("Source species/populations disagree with raw axes")
        if len(columns) != raw.shape[-1]:
            raise ValueError("Orbital columns disagree with raw axes")
        self.groups = {}
        start = 0
        for element, count in zip(species, counts):
            for shell, labels in resolved_shells(columns).items():
                name = element + ":" + shell
                weight = raw[0, :, :, start : start + count, :][
                    ..., [columns.index(v) for v in labels]
                ].sum(axis=(-1, -2))
                self.groups[name] = self.groups.get(name, 0) + weight
            start += count
        if self.meta.get("spin_mode") not in ("scalar", "spinor"):
            raise ValueError("Explicit scalar/spinor metadata required")

    @property
    def source(self):
        return {"path": "case/02_band/charge.npz", "sha256": self.revision}

    def ensure_current(self):
        if file_sha(self.cache_path) != self.revision:
            raise ValueError(
                "Input revision changed; start a new run with the updated evidence"
            )

    def resolve(self, supplied, *, directory=False):
        if not isinstance(supplied, str):
            raise ValueError("Evidence path must be a string")
        roots = {
            "case": self.case,
            "run": self.run,
            "guides": Path(__file__).parent / "guides",
        }
        prefix, sep, tail = supplied.partition("/")
        if not sep or prefix not in roots:
            raise ValueError("Use case/, run/ or guides/ evidence paths")
        root = roots[prefix].resolve()
        path = (root / tail).resolve()
        if any(
            part in (".agent", ".git", ".env", "sessions", "calls")
            for part in Path(tail).parts
        ):
            raise ValueError(
                "Private histories and environment files are not scientific evidence"
            )
        if not path.is_relative_to(root):
            raise ValueError("Evidence path escapes its root")
        if not path.exists():
            raise ValueError("Evidence path does not exist: " + supplied)
        if directory and not path.is_dir():
            raise ValueError("Expected an evidence directory: " + supplied)
        if not directory and not path.is_file():
            raise ValueError(
                "Expected a file; use list_records for directories: " + supplied
            )
        return path

    def list_records(self, path, offset=0, limit=30):
        directory = self.resolve(path, directory=True)
        rows = []
        for child in sorted(directory.iterdir()):
            if child.name in (".agent", ".git", ".env", "sessions", "calls"):
                continue
            supplied = path.rstrip("/") + "/" + child.name
            try:
                self.resolve(supplied, directory=child.is_dir())
            except ValueError:
                continue
            rows.append(
                {
                    "path": supplied,
                    "kind": "directory" if child.is_dir() else "file",
                    "bytes": child.stat().st_size if child.is_file() else None,
                }
            )
        return {"path": path, **page_rows(rows, offset, limit)}

    def read_record(self, path, pointer="", offset=0, limit=30, layout="rows"):
        target = require_access(self, path, "record")
        if target.stat().st_size > 20_000_000:
            raise ValueError("Record exceeds readable size")
        value = json.loads(target.read_text())
        value, access_policy = record_view(self, path, value)
        if pointer:
            if not pointer.startswith("/"):
                raise ValueError("Use a JSON pointer beginning with /")
            for part in pointer.split("/")[1:]:
                part = part.replace("~1", "/").replace("~0", "~")
                value = value[int(part)] if isinstance(value, list) else value[part]
        result = {
            "source": {"path": path, "sha256": file_sha(target)},
            "pointer": pointer,
            "access_policy": access_policy,
        }
        if isinstance(value, list):
            # Scalars/mixed lists keep their original representation.
            if layout == "columns" and not all(isinstance(v, dict) for v in value):
                raise ValueError("columns layout requires a list of uniform objects")
            page = page_rows(value, offset, limit, layout=layout)
            if layout == "rows":
                page["value"] = page.pop("rows")
            result.update(page)
        elif isinstance(value, dict) and encoded_size(value) > 8000:
            # Pointers address the original document, not an archive of this wrapper.
            result.update(page_rows(structure(value, pointer), offset, limit))
            result.update(
                view="structure",
                document_complete=False,
                instruction="Read a listed pointer in this same source file. The structure is not the field contents.",
            )
        elif isinstance(value, str) and len(value) > 8000:
            stop = min(len(value), offset + min(limit, 8000))
            result.update(
                value=value[offset:stop],
                offset=offset,
                returned_characters=stop - offset,
                total_characters=len(value),
                next_offset=stop if stop < len(value) else None,
                complete=stop >= len(value),
            )
        else:
            result.update(value=value, total_rows=None, next_offset=None, complete=True)
        return result

    def overview(self, offset=0, limit=32, layout="columns"):
        self.ensure_current()
        rows = []
        for band in range(offset, min(self.energy.shape[1], offset + limit)):
            e = self.energy[:, band]
            weights = np.array([w[:, band] for w in self.groups.values()])
            means = weights.mean(axis=1)
            leader = int(means.argmax())
            rows.append(
                {
                    "band": band + 1,
                    "energy_eV": [float(e.min()), float(e.max())],
                    "raw_mean": {n: float(means[i]) for i, n in enumerate(self.groups)},
                    "samples_with_other_leading_channel": int(
                        (weights.max(axis=0) > weights[leader]).sum()
                    ),
                }
            )
        return {
            "source": self.source,
            "shape": list(self.energy.shape),
            "spin_mode": self.meta["spin_mode"],
            "species": self.meta["species"],
            "counts": self.meta["counts"],
            "ef_eV": self.meta["ef"],
            "sampling": self.meta.get("sampling"),
            "source_files": self.meta.get("source_files", {}),
            "available_records": [
                "case/" + str(path.relative_to(self.case))
                for folder, names in (
                    ("01_scf", ("INCAR", "POSCAR", "KPOINTS", "OSZICAR")),
                    (
                        "02_band",
                        (
                            "INCAR",
                            "POSCAR",
                            "KPOINTS",
                            "OSZICAR",
                        ),
                    ),
                )
                for name in names
                if (path := self.case / folder / name).is_file()
            ],
            "k_path": path_facts(self),
            **self._source_page(rows, self.energy.shape[1], offset, limit, layout),
            "method": "Unweighted path means; raw projections are not normalized probabilities or tracked branches.",
        }

    def orbital_character(self, band, k_lo=1, k_hi=None, offset=0, limit=24):
        self.ensure_current()
        k_hi = self.energy.shape[0] if k_hi is None else k_hi
        if (
            not 1 <= band <= self.energy.shape[1]
            or not 1 <= k_lo <= k_hi <= self.energy.shape[0]
        ):
            raise ValueError("Invalid source coordinate range")
        e = self.energy[k_lo - 1 : k_hi, band - 1]
        weights = {
            name: w[k_lo - 1 : k_hi, band - 1] for name, w in self.groups.items()
        }
        maximum = np.maximum.reduce(list(weights.values()))
        rows = [
            {
                "channel": n,
                "mean_raw": float(w.mean()),
                "peak_raw": float(w.max()),
                "peak_k": int(w.argmax()) + k_lo,
                "leading_samples": int(((w == maximum) & (w > 0)).sum()),
            }
            for n, w in weights.items()
        ]
        witness = []
        for kind, index in [
            ("energy_min", int(e.argmin())),
            ("energy_max", int(e.argmax())),
            ("nearest_ef", int(np.abs(e).argmin())),
        ]:
            witness.append(
                {
                    "kind": kind,
                    "k_band_1based": [index + k_lo, band],
                    "energy_relative_eV": float(e[index]),
                    "raw_projection": {n: float(w[index]) for n, w in weights.items()},
                }
            )
        return {
            "source": self.source,
            "band": band,
            "k_range": [k_lo, k_hi],
            "channels": rows,
            "witnesses": witness,
            **character_segments(self, band, k_lo, k_hi, offset, limit),
            "method": "Raw equal-weight source samples, not a basis inclusion criterion.",
        }

    def band_slice(
        self, band_lo, band_hi, k_lo=1, k_hi=None, offset=0, limit=48, layout="columns"
    ):
        self.ensure_current()
        k_hi = self.energy.shape[0] if k_hi is None else k_hi
        if (
            not 1 <= band_lo <= band_hi <= self.energy.shape[1]
            or not 1 <= k_lo <= k_hi <= self.energy.shape[0]
        ):
            raise ValueError("Invalid source range")
        if type(offset) is not int or offset < 0 or type(limit) is not int or limit < 1:
            raise ValueError("Invalid page offset/limit")
        total = (band_hi - band_lo + 1) * (k_hi - k_lo + 1)
        rows = []
        width = band_hi - band_lo + 1
        for index in range(offset, min(total, offset + limit)):
            k, b = k_lo - 1 + index // width, band_lo - 1 + index % width
            rows.append(
                {
                    "k_band_1based": [k + 1, b + 1],
                    "energy_relative_eV": float(self.energy[k, b]),
                    "occupation": float(self.occupations[k, b])
                    if self.occupations is not None
                    else None,
                    "raw_projection": {
                        n: float(w[k, b]) for n, w in self.groups.items()
                    },
                }
            )
        return {
            "source": self.source,
            **self._source_page(rows, total, offset, limit, layout),
        }

    @staticmethod
    def _source_page(rows, total, offset, limit, layout):
        if type(offset) is not int or offset < 0:
            raise ValueError("Invalid page offset")
        page = page_rows(rows, 0, limit, layout=layout)
        stop = offset + page["returned_rows"]
        page.update(
            offset=offset,
            total_rows=total,
            next_offset=stop if stop < total else None,
            complete=stop >= total,
        )
        return page

    def ef_crossings(self, **arguments):
        return ef_crossings(self, **arguments)

    def window_diagnostics(self, plan, energies=None, sampling=None):
        """Measure the proposed interval. A clipped top is reported, never a data request."""
        e = self.energy if energies is None else np.asarray(energies, dtype=float)
        excluded = set(plan["excluded_bands_1based"])
        retained = [b for b in range(e.shape[1]) if b + 1 not in excluded]
        e = e[:, retained]
        low, high = plan["windows_relative_eV"]["dis_eV"]
        fl, fh = plan["windows_relative_eV"]["frozen_eV"]
        outer = ((e >= low) & (e <= high)).sum(axis=1)
        frozen = ((e >= fl) & (e <= fh)).sum(axis=1)
        margin = e.max(axis=1) - high
        nw = plan["num_wann"]
        result = {
            "input_revision": self.revision,
            "windows_relative_eV": plan["windows_relative_eV"],
            "basis": plan["basis"],
            "sampling": sampling or self.meta.get("sampling", "band_path"),
            "min_nouter": int(outer.min()),
            "max_nfrozen": int(frozen.max()),
            "nouter_below_num_wann_kpoints": (np.where(outer < nw)[0] + 1).tolist(),
            "nfrozen_above_num_wann_kpoints": (np.where(frozen > nw)[0] + 1).tolist(),
            "computed_top_at_or_below_dis_upper_kpoints": (
                np.where(margin <= 0)[0] + 1
            ).tolist(),
            "minimum_computed_top_margin_eV": float(margin.min()),
            "interpretation": "Only supplied sampled eigenvalues. Top coverage is a fact for role review, not an automatic instruction to add bands.",
        }
        if energies is None:
            result["frozen_projection_audit"] = self.projection_audit(plan)
        return result

    def projection_audit(
        self, plan, region="frozen", weak_selected_max=None, *, include_states=False
    ):
        """Scan every retained state in a declared region; return facts, never a veto.

        Weakness has no hidden cutoff: a requested indicator threshold is recorded
        explicitly. Summary coverage refers to computation, not model review or BZ.
        """
        self.ensure_current()
        e = self.energy
        retained = np.ones(e.shape, dtype=bool)
        for band in plan["excluded_bands_1based"]:
            retained[:, band - 1] = False
        low, high = plan["windows_relative_eV"]["dis_eV"]
        fl, fh = plan["windows_relative_eV"]["frozen_eV"]
        masks = {
            "frozen": (e >= fl) & (e <= fh),
            "outer": (e >= low) & (e <= high),
            "above_outer": e > high,
            "all_retained": np.ones(e.shape, dtype=bool),
        }
        if region not in masks:
            raise ValueError("Unknown projection audit region")
        if weak_selected_max is not None and (
            isinstance(weak_selected_max, bool)
            or not isinstance(weak_selected_max, (int, float))
            or not np.isfinite(weak_selected_max)
            or weak_selected_max < 0
        ):
            raise ValueError("weak_selected_max must be a finite nonnegative number")
        selected = sum((self.groups[name] for name in plan["basis"]), np.zeros_like(e))
        omitted = [name for name in self.groups if name not in plan["basis"]]
        competing = (
            np.maximum.reduce([self.groups[name] for name in omitted])
            if omitted
            else np.zeros_like(e)
        )
        mask = retained & masks[region]
        coords = np.argwhere(mask)
        values = selected[mask]
        deltas = (competing - selected)[mask]
        target = np.zeros_like(mask)
        for first, last in plan["target_band_ranges_1based"]:
            target[:, first - 1 : last] = True

        def row(index):
            k, b = map(int, coords[index])
            return {
                "k_band_1based": [k + 1, b + 1],
                "energy_relative_eV": float(e[k, b]),
                "occupation": float(self.occupations[k, b])
                if self.occupations is not None
                else None,
                "raw_projection": {
                    name: float(w[k, b]) for name, w in self.groups.items()
                },
                "selected_sum": float(selected[k, b]),
                "strongest_omitted_weight": float(competing[k, b]),
                "strongest_omitted_channel": max(
                    omitted, key=lambda name: self.groups[name][k, b]
                )
                if omitted
                else None,
                "is_target": bool(target[k, b]),
            }

        witnesses = (
            sorted({int(values.argmin()), int(deltas.argmax())}) if len(coords) else []
        )
        result = {
            "source": self.source,
            "plan_sha256": digest(plan),
            "region": region,
            "windows_relative_eV": plan["windows_relative_eV"],
            "basis": plan["basis"],
            "excluded_bands_1based": plan["excluded_bands_1based"],
            "coverage": {
                "complete_computational_scan": True,
                "sampling": self.meta.get("sampling"),
                "kpoints": e.shape[0],
                "retained_source_states": int(retained.sum()),
                "region_states": len(coords),
                "target_states_in_region": int((mask & target).sum()),
                "target_states_outside_region": int((retained & target & ~mask).sum()),
            },
            "selected_raw_quantiles": dict(
                zip(
                    ("min", "q25", "median", "q75", "max"),
                    map(float, np.quantile(values, [0, 0.25, 0.5, 0.75, 1])),
                )
            )
            if len(coords)
            else None,
            "stronger_omitted_channel_count": int((deltas > 0).sum()),
            "weak_indicator": {
                "selected_sum_lte": weak_selected_max,
                "count": int((values <= weak_selected_max).sum())
                if weak_selected_max is not None
                else None,
            },
            "witnesses": [row(i) for i in witnesses],
            "scope": "Every retained state in this region on the supplied sampling was scanned. Selected sums and strongest individual omitted channels are raw atomic-sphere weights, not probabilities. No threshold is assumed when weak_indicator is null-valued. Computational coverage is not model review, full-BZ validation, or scientific approval. Empty regions have no safety implication.",
        }
        if include_states:
            result["states"] = [row(i) for i in range(len(coords))]
        return result
