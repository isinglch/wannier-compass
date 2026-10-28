# DFT and orbital-projected band figures

Every completed or reused projected-band source provides DFT and complete element-by-shell figures in 02_band. Plotting reads the existing cache without adding DFT/mesh/PDOS or changing selection rules.

```bash
python tools/plot_dft_bands.py \
  --cache /absolute/material/02_band/charge.npz \
  --out-dir /absolute/material/02_band/plots \
  --title Material
```

Optional `--path /absolute/material/02_band/fit_path.json` adds identity checking; otherwise use verified cache geometry with original KPOINTS hash. Missing/mismatched geometry is an error, not a guessed symmetry path. Destination must be new. Read legacy sources in place and write figures into the standard case, without reparsing EIGENVAL or reconstructing the path.

- dft_bands.png/pdf: actual DFT path, default complete computed energy range, with optional explicit views.
- projected_bands.png/pdf: one element-by-s/p/d/f row per channel, including zero-weight channels, at common declared display ranges.
- manifest.json: cache/path/PROCAR/SCF-EF identity, ranges, raw-weight scale, physical path/segments, output hashes, and scope.

There is no default EF±10 or fixed near-EF view. The first column preserves all computed energies. Explicit energy-range/focus-range adds a physical diagnostic view; current decision adds frozen±1 eV and outer±3 eV. Display range is not selection, target, or acceptance scope.

Sum raw charge projections over all atoms of each element and all magnetic components of each s/p/d/f shell. Default marker area is 48×raw_weight pt². All channels share a linear color scale, initially 0–1 and jointly extended if necessary. No self-normalization, per-atom averaging, clipping, or weak-channel amplification is applied.

Titles report full-path maximum raw weight; manifests additionally retain per-view maxima and raw sampled-state sums. These sums are not BZ occupations, and atomic projections are not normalized full-space probabilities.

Use stored actual k order, reciprocal row vectors including 2π, and explicit segments. Accumulate length independently per segment, excluding jumps; preserve repeated endpoints and labels. Reject source/hash/endpoint mismatches without guessed-path fallback. These figures supply review evidence, not automatic orbital selection or fit acceptance.
