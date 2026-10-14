# Fit comparison for each trial

`tools/compare_wannier.py` is the official comparison entry for a standard material case and independent trial:

```bash
python tools/compare_wannier.py submit --case-dir /work/material \
  --trial-id trial_001 --site sites/slurm.example.json \
  --postw90 /absolute/path/postw90.x --ntasks 36 --execute
```

Submit previews by default; `--execute` calls Slurm. The script invokes official run inside the required compute allocation, never postw90 on a login node. Requested ranks must fit allocation; use site environment/launcher. Optional `--run-metrics /path/run_metrics.py --ledger /path/ledger --experiment-id ID` integrates accounting. Without it, validation_report.json still records invocation time and actual return code.

Read official execution evidence/products in `03_wan_trial/trial_001/wannier`. In a fresh comparison directory, native postw90 uses executed chk/eig at the actual DFT k points from `02_band/charge.npz` and fit_path.json, preserving repeated endpoints/disconnected segments. Subtract the same SCF EF from both spectra; do not fit an energy-zero shift.

Outputs are `fit_review.png/pdf/json`, `diagnostics.json`, `validation_report.json`, same-k interpolation data, and native logs. Update trial links afterward. Keep native W90 band.dat/kpt separately: their segment density is not necessarily DFT density.

Diagnostics use per-k one-to-one minimum-energy-difference matching within the executed frozen/outer intervals, reporting MAE/RMSE/P95/MAX and unmatched counts. They impose no matching-error cutoff and use no wavefunction/orbital/connectivity information, so they cannot certify complete manifolds or branch identity. Figures/metrics do not assign fit_pass automatically. A converged but externally unreviewed fit displays `TOOL STATUS: NOT EVALUATED`; null is not `NOT ACCEPTED`. Known blockers, such as unconverged DIS, carry not-accepted/provisional labels. Complete iteration-limited products may be plotted as PROVISIONAL. Failed execution or missing products cannot enter comparison.

Preserve basis/windows/num_iter and scientific inputs; do not rerun DIS. Existing comparison directories are immutable. Scientific retries use new trial numbers.

For a technical restart, `--trial-dir` accepts only a direct canonical-trial `technical/restart_NNN` child. Results stay there with `technical_retry_private`; original failure evidence is retained and scientific trial count does not change.

The same route supports plotting-only recovery. Inspect recognizes its recovery manifest/record, requires recovery WOUT `All done` and complete hashes, and inherits DIS convergence/count/slow-convergence markers from the sealed source WOUT. Do not invent DIS lines in the recovery log. An iteration-limited source remains PROVISIONAL and unaccepted. A no-op recovery has no independent product set and is rejected by comparison; use the already complete canonical source.

Default displays extend the actual frozen window by 1 eV on each side and outer by 3 eV. Fixed `priority_ef_pm1`/near-EF plotting parameters are removed. Outer contains background needed for disentanglement; full-outer energy matching is diagnostic rather than requiring every background branch to fit. Display margins are not acceptance regions.
