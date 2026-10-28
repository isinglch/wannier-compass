# Projection evidence figures

[plot_dft_bands](DFT-PLOTS.md) provides mandatory per-material DFT/element-by-spdf PNG/PDF, including the complete computed spectrum and optional declared ranges/window context. Band collect generates or checks these figures even for reused caches. Propose generates selection.png/pdf; the [gallery](SELECTION-GALLERY.md) aggregates stages. plot_review supplies additional per-channel spectra/competition in a fresh directory, not a replacement for the overview.

plot_review reads current charge-only NPZ via orbital_evidence.read_cache, without importing development cases, historical bases/windows, fitted W90 curves, or running remote jobs. Requires Python/NumPy/Matplotlib and the bundled policy. Use Agg on headless servers. X/Y below are placeholders:

```bash
python tools/plot_review.py --cache current_path.npz --out-dir current_review
python tools/plot_review.py --cache current_path.npz \
  --groups X:s,X:p,Y:d --comparison-basis X:p,Y:d \
  --basis-revision B1 --basis-reason 'This run: reviewed target-manifold projections' \
  --out-dir current_review_B1
```

Default examines all computed states without a fixed seed. inspect-range is explicitly requested supplementary diagnosis. Energies are eV relative to independently sourced cache EF. Destination must be nonexistent or empty; each iteration retains its own figures/CSV/provenance.

## Outputs and interpretation

projection_*.png/pdf include full/declared raw fatbands, an explicit local view or frozen context when provided, and same-channel path-projected spectra. All channels use the same area scale, default 100 pt²×raw weight, with 0.1/0.5/1.0 references and gray zero-weight DFT background. Scatter does not claim tracked branches at crossings.

With reviewed B, competition_*.png/pdf compares candidate raw weight against strongest B channel and total B on the same state. The equality line is not an inclusion/deletion threshold. Only channels outside B and in groups generate candidate figures; B-member review requires an explicit comparison with that member removed. Preserve B revision/reason.

| File | Content |
| --- | --- |
| raw_states.csv | All raw channels, 1-based k/band, E−EF and plot coordinate, beyond display crop |
| path_projected_spectrum.csv | Gaussian-broadened projection sums |
| path_sampling.csv | Raw/normalized sampling weights and coordinate |
| same_state_competition.csv | Optional full/declared-domain competition; blank zero-denominator ratios |
| provenance.json | Input/script/output hashes, EF, smoothing, weighting, geometry, B, candidate windows, dependencies |

## Geometry and segments

Coordinates alone cannot recover reliable path length without convention, reciprocal lattice, and segments. Fall back explicitly to Stored k-point index without fabricated labels or intersegment lines. Use geometry JSON or cache path_geometry for physical distances:

```json
{
  "kcoords_type": "fractional",
  "reciprocal_lattice_2pi_per_angstrom": [[1,0,0],[0,1,0],[0,0,1]],
  "segments_1based": [[1,40],[41,80]],
  "ticks": [
    {"index_1based":1,"label":"G"},
    {"index_1based":40,"label":"X"},
    {"index_1based":41,"label":"L"},
    {"index_1based":80,"label":"G"}
  ]
}
```

Replace format-only values with actual geometry. Reciprocal row vectors include 2π in Å⁻¹; fractional conversion is k_fractional @ reciprocal. cartesian_inv_angstrom skips conversion. Inclusive segments partition every stored point in order with at least two points each; restore duplicated endpoints in adaptation when upstream deduplication removed them. Do not guess connectivity. Accumulate each segment without jump length; coincident tick labels join with |.

## Path-projected spectrum is not DOS

`S_alpha(E) = sum_k q_k sum_n w_alpha(k,n) G_sigma(E−E_kn) / sum_k q_k`.

Default sample_equal gives each stored k equal weight and retains repetitions. path_arclength requires complete physical geometry and uses segment trapezoidal weights. Zero line-mode k weights do not constitute BZ integration.

Gaussian sigma defaults 0.10 eV (standard deviation, not FWHM); points defaults 1600. Include all computed states with 5 sigma padding. energy-range crops display only, preserving numerator/denominator/bands. Wide ranges require adequate resolution and smoothing checks; discrete grids/tails introduce small integration errors. This sampled-path diagnosis does not prove full-BZ pockets absent, and smoothed tails do not establish physical manifold boundaries. Coverage/tail evidence is reviewed, never an automatic window.

## Decision overlays and scope

Explicit decision input overlays only current candidate windows and requires matching evidence.source_sha256. Do not scan history or automatically infer B. Overlay is not schema/count/fit acceptance; use check-record separately. Callers ensure current authorized provenance.

This CLI accepts no historical fitted W90 curve. Use [fit plotting](FIT-PLOTTING.md) for current same-k comparison with its energy/path/mapping boundaries. Projection plots do not establish Wannier fit. Independent agents receive the clean package/current inputs/allowed priors and deliver figures/provenance for review.
