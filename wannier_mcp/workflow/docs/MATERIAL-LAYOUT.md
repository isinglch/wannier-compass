# Standard per-material workspace

Every material retains the three-stage layout, including reused DFT. The experiment [selection gallery](SELECTION-GALLERY.md) defaults to selection_gallery/index.html.

```text
material/
  README.md, MATERIAL-LAYOUT.json
  01_scf/README.md, SOURCE.json
  02_band/README.md, SOURCE.json, charge.npz, fit_path.json
    plots/dft_bands.png/pdf, projected_bands.png/pdf
    dft_bands.png/pdf, projected_bands.png/pdf -> relative plot links
  03_wan_trial/
    index.html
    initial/                  baseline records, figures, STAGE.json
    trial_001/
      README.md
      records/selection/      sealed first try or retry
      records/results/        actual run snapshots and assessments
      technical/restart_NNN/  optional same-choice restart
      interface/              actual VASP interface
      wannier/                official inputs, products, run record
      comparison/             same-k fit figures and metrics
      wannier90.win/wout/chk, *_hr/r/tb.dat, *_band.dat/kpt links
      fit_review.png/pdf -> relative comparison links
```

## Existing and new DFT

```bash
python tools/material_layout.py init --case-dir /work/material \
  --scf-source /inputs/current/scf --band-source /inputs/current/band
# Seal initial and select trial_001 through decision_stages.py.
```

Init requires a fresh case and explicit existing sources. Write stage READMEs/SOURCE.json and source-directory links, retaining core SHA256, missing files, and provenance without certifying convergence. Sources are read-only. Existing band cache/path appear through relative source links; otherwise the official adapter generates them. Reject core symlinks escaping the declared source; provide an explicit auditable source instead.

Fresh DFT preparation creates 01/02 as actual calculation directories and register_fresh records the layout without source links. SOURCE.json snapshots prepared inputs; later run.json records execution/outputs. CLI stage names stay scf/band, and legacy mapping is handled by the DFT tool without moving running jobs.

Official select creates trials; low-level new-trial only reserves a directory, not an executable choice. It creates README/records without interface/wannier/comparison, so official preparers can enforce fresh directories. IDs are trial_ plus at least three digits; old trials are immutable. Use returned paths for scf_dir/band_dir/cache/path/plots/interface/wannier/comparison/records/analysis/decision rather than inventing a layout.

```bash
python tools/plot_dft_bands.py --cache /work/material/02_band/charge.npz \
  --path /work/material/02_band/fit_path.json --out-dir /work/material/02_band/plots

python tools/compare_wannier.py submit --case-dir /work/material --trial-id trial_001 \
  --site sites/slurm.example.json --postw90 /absolute/postw90.x --ntasks 36 --execute
python tools/material_layout.py publish --case-dir /work/material --trial-id trial_001
```

Fresh band collect plots automatically; reused data also needs figures. See [interface](WANNIER-INTERFACE.md), [execution](WANNIER-EXECUTION.md), and [comparison](WANNIER-COMPARISON.md). Comparison automatically publishes links. W90 requires num_iter=0 and all four product switches. Native band sampling is not exact DFT sampling; use postw90 at actual DFT coordinates.

## Publication means local display

Publish creates relative links and managed READMEs without copying large files, moving calculations, or accepting science. It reads num_iter, DIS state, and fit state from actual records, using unknown when unavailable. Completion/convergence/products/fit remain distinct; accepted remains false.

Display requirements include win/wout/chk, hr/r/tb, band.dat/kpt, both fit figures, four DFT/projected-band figures, and wannier_run.json. Missing/empty items return incomplete with a list; display_complete means display inventory only. Idempotent publication cannot overwrite different links, regular files, or unmanaged READMEs. Product links remain confined to their declared directories; managed directories cannot themselves be symlinks. Put personal notes in records, not generated READMEs.
