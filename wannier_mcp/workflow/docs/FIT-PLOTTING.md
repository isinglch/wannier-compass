# Current-trial DFT/Wannier fit figures

plot_fit_review.py reads explicit current inputs and draws frozen/outer contexts with traceable JSON. It does not search for old win/OUTCAR/windows/models, select orbitals, or assign fit acceptance. Requires Python/NumPy/Matplotlib and bundled evidence/policy; use Agg without a desktop.

## Inputs

```bash
python tools/plot_fit_review.py \
  --cache current_projection.npz \
  --wannier-dat current_band.dat \
  --wannier-kpt current_band.kpt \
  --wannier-energy-reference absolute \
  --path current_path.json \
  --decision executed_windows_for_plot.json \
  --out-prefix figures/current_fit
```

DFT NPZ follows the [evidence contract](REFERENCE-TOOLS.md) and requires kcoords (1,nk,3) in the same fractional reciprocal basis as Wannier. Energy/path length alone cannot establish alignment. Wannier band.dat has one blank-separated two-column distance/energy block per band; band.kpt has integer count followed by kx ky kz [weight]. See the [PythTB W90 reader](https://pythtb.readthedocs.io/en/v1.8.0/_modules/pythtb.html#w90.w90_bands_consistency). Unsupported extra columns, collapsed blocks, or dialects need explicit conversion.

Mandatory energy-reference is absolute (subtract cache EF) or relative_to_ef (already uses that same EF). Do not fit an offset or estimate separate EF values. Resolve differing potential references upstream. Default displays frozen±1 eV and outer±3 eV relative to SCF EF. Padding/focus/full-range options affect explicit display only. Without windows, show supplied energies without guessing an EF interval.

## Segment validation

Path uses zero-based half-open slices and explicit uniformly sampled line endpoints/labels:

```json
{
  "coordinate_system": "fractional_reciprocal",
  "segments": [
    {
      "start": 0, "stop": 21,
      "start_fractional": [0, 0, 0], "end_fractional": [0.5, 0, 0],
      "start_label": "G", "end_label": "X"
    },
    {
      "start": 21, "stop": 42,
      "start_fractional": [0.5, 0, 0], "end_fractional": [0.5, 0.5, 0],
      "start_label": "X", "end_label": "M"
    }
  ]
}
```

Values are format examples. Adjacent segments may repeat endpoints or share one with next start=previous stop−1. Draw disconnected segments separately. Use current path provenance, not successful history.

Check exact DFT/Wannier point counts/order/coordinates, declared straight-line samples, and full segment coverage. Coordinate tolerance defaults 1e−6 and caps at 1e−4. Do not translate by reciprocal vectors, reorder, deduplicate, scale by length, or interpolate to manufacture agreement. Sampling mismatch returns 2 without metrics; evaluate Wannier at exact DFT points. Existing band.dat distances control display only and must agree across Wannier blocks. Nonuniform paths require an explicit adapter rather than relaxed gates.

## Optional mapping and metrics

Default overlay_only has metrics=null/fit_pass=null; no automatic nearest-band matching. A reviewed correspondence can supply:

```json
{
  "pairs_1based": [[3, 1], [4, 2]],
  "reason": "Current target-band review establishing fixed-index correspondence",
  "evidence_refs": ["current_mapping_review.md"]
}
```

Pairs are DFT/Wannier indices, 1-based and unique within each column. Report MAE/RMSE/max for mapped states over the full path and actual frozen/outer regions, using DFT-energy masks and equal sampled-state weights. Empty masks yield null; explicit focus is requested_detail, with no default near-EF score. Unmapped bands are not scored. Repeated endpoints remain; this is not BZ integration.

Mapping is a supplied scientific judgment. Index validity does not establish wavefunction continuity at crossings; fixed indices cannot track branch exchange. Such cases need reviewed branches or future overlap-based mapping, not nearest-energy claims. fit_pass stays null; the task defines acceptance scope/tolerances.

Explicit decision reads finite ordered nested dis/frozen windows for display only, not basis review/check-record or same-source execution validation. A path cache and actual-mesh decision can differ in sampling; callers preserve current trial/common EF provenance, with full input hashes recorded.

## Outputs and limits

Write prefix.png/pdf/json, refusing overwrite. JSON retains SHA256, EF/reference, coordinate checks, segments, displays, windows, and optional mapped metrics. Check only supplied path sampling, not off-path space, rank, wavefunctions, symmetry, or transport. There are no history tables or submission actions. Development synthetic tests are behavioral rather than scientific acceptance.

display_ranges_eV records plotting boundaries; metrics.regions_by_dft_energy records scoring. Default ±1/±3 eV padding is visual context, not error tolerance or additional required target states.
