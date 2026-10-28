# Selection gallery and human review

Every propose saves current inspection/review, official selection.png/pdf, and selection_plot_manifest.json binding image hashes to choice. Standard case/initial, case/real_first_try, or case/retry_NNN outputs refresh the shared parent gallery by default. no-gallery skips aggregation, not the individual figures. Official initial/select stages seal figures, update 03_wan_trial/index.html, and refresh selection_gallery/index.html at the same experiment root. Galleries link files without copying large images or changing decisions.

```text
experiment/
  material_A/MATERIAL-LAYOUT.json, 01_scf/, 02_band/, 03_wan_trial/
  material_B/MATERIAL-LAYOUT.json, 01_scf/, 02_band/, 03_wan_trial/
  selection_gallery/index.html, gallery_manifest.json
```

Only materials with matching context.experiment_id enter the automatic experiment gallery. Its manifest records stage/figure hashes and visible problems; STAGE.json remains the sealed stage identity.

```bash
python tools/selection_gallery.py --root /absolute/experiment --experiment-id current-run
```

Selection-only work can aggregate an explicitly provided root. Scan only its immediate cases and initial/real_first_try/retry_NNN children, not adjacent history:

```text
test-results/
  case-0001/initial/selection.json, review.json, selection.png, selection.pdf
  case-0001/real_first_try/...
  case-0002/initial/...
```

```bash
python tools/selection_gallery.py --root /absolute/test-results --mode selection-only
```

Standard propose defaults to this root's selection_gallery/index.html; refresh explicitly for other layouts. Deliver the gallery, its manifest, and original stage records for review. New proposals verify figure manifests. Legacy records lacking one are labeled unsealed_without_figure_manifest rather than hash-bound. Official stages use STAGE.json verification. Link available current DFT/projected bands, selection PNG/PDF, review, and selection JSON. Missing/modified/inconsistent files show warnings rather than success.

The gallery visualizes choices only; it certifies neither actual-mesh exclusions, DIS convergence, HR, nor fit. Default plotting changes no frozen window and supplies no historical answers.
