# Independent Wannier90 disentanglement execution

[Decision stages](DECISION-STAGES.md) govern execution: standalone proposals are drafts; new runs bind a sealed `real_first_try` or retry. Initial is not executed directly. Existing results remain readable.

`tools/wannier_workflow.py` runs native Wannier90 after interface validation. It does not select orbitals/windows or run VASP. Inputs explicitly require `num_iter = 0`. Fit validation and plotting are separate steps.

## Preparation

```bash
python tools/wannier_workflow.py prepare \
  --interface-dir /absolute/interface-trial \
  --out-dir /absolute/wannier-run-001
```

The source must pass `prepare_wannier_interface.validate(outputs=True)` with `interface_files_consistent=true` and `ready_for_wannier=true`. Preparation copies `.win/.amn/.mmn/.eig` into a fresh directory, without requiring source WAVECAR. Geometry, actual interface k order, projections, and windows retain their values. Record permitted budget/convergence-control changes explicitly.

Require successful official `dft_workflow.py run-interface` evidence in `interface_run.json`: zero return code, matching manifest ID/status/current hashes. Independently check OUTCAR termination, electronic EDIFF convergence, NSW=0, ICHARG=11, and actual NBANDS. Editing success flags/hashes cannot replace these facts. External matrices without the supported execution evidence have no bypass option.

Preparation explicitly enables `write_hr=true`, `write_rmn=true`, `write_tb=true`, and `bands_plot=true`, including reuse of interfaces that had not requested them. Generate `kpoint_path` from the source manifest's band/KPOINTS and actual band/EIGENVAL through the existing adapter; any fit_path.json must agree. Preserve fractional endpoints and convert labels to stable W90 names. A legacy interface without band provenance needs explicit `--band-dir`. Verify structure and, on the current route, original band hashes. Preserve an already matching path; do not rewrite geometry from outputs to bypass validation.

Set `bands_num_points` from the DFT first-segment sample count. If path-length scaling would give another segment zero interpolation intervals, increase only the plotting budget so every original segment has an interval. This changes output sampling, not DFT/basis/windows. New preparation defaults to and caps `dis_num_iter=10000`; `--dis-num-iter N` can reduce it. Set `dis_conv_tol=1e-8`, `dis_conv_window=3`, and always `num_iter=0`. Record before/after values when reusing old interfaces; do not modify source files/matrices. Inspect older sealed results under their original settings; new execution requires fresh preparation.

`wannier_manifest.json` records source path/interface manifest ID, source/current SHA256, original settings/geometry, permitted changes, and preparation time. Existing directories are not overwritten. Bind source interface_run/OUTCAR hashes and cross-check summaries.

## Submission and execution

```bash
# Preview a reviewable Slurm script; do not call sbatch.
python tools/wannier_workflow.py submit \
  --directory /absolute/wannier-run-001 --site sites/slurm.example.json \
  --ntasks 36

# Submit explicitly within compute authorization.
python tools/wannier_workflow.py submit \
  --directory /absolute/wannier-run-001 --site sites/slurm.example.json \
  --ntasks 36 --execute

# Run only inside the required Slurm allocation.
python tools/wannier_workflow.py run \
  --directory /absolute/wannier-run-001 --site sites/slurm.example.json \
  --ntasks 36
```

Default `--ntasks=1`. Multiple ranks require an MPI-capable W90 build, must not exceed site.ntasks, and must fit actual SLURM_NTASKS. Execute the site launcher with `site.wannier90 wannier90`, initialized through environment_script/environment_args. Partition/account/time/exclude enter the submission script. There is no local run mode; allocation is mandatory.

Before execution, recheck hashes, explicit zero localization iterations, product switches, plotting path, and independent DIS budget. Existing logs/WOUT/chk/HR/run records prevent rerun; prepare a fresh directory. This entry runs native Wannier90 directly without VASP or an alternative localization driver.

## Status, accounting, and inspection

```bash
python tools/wannier_workflow.py inspect --directory /absolute/wannier-run-001
```

`wannier_run.json` retains actual argv/environment wrapper, Slurm ID, site hash, requested/available ranks, input/output hashes, return code, start/end, and `wall_seconds`. Wall time includes environment/MPI startup, excludes queuing, preparation copies, later hashing, and plotting, and is not W90's internal timer. Model-token accounting is optional for explicitly requested cost evaluations and belongs to the external client ledger; ordinary execution does not require it. Inspection rechecks inputs and recorded outputs.

DIS convergence uses adjacent-iteration relative change of gauge-invariant spread for three consecutive steps below `1e-8`; this is not a band-fit tolerance. Reaching 2000 iterations without convergence records `not_converged_by_2000=true` and `slow_disentanglement`, retained even after later convergence up to 10000. Incomplete logs before 2000 yield null, not passing. An unconverged 10000-step result remains provisional; review windows/basis/numerics without automatic budget extension or declaring construction impossible. Older logs retain their original tolerance; do not relabel an old `1e-10` failure as a new successful run.

| Status | Evidence | Permitted next step |
| --- | --- | --- |
| `completed_converged` | Explicit convergence, actual DIS records, normal exit, and all required products | Fit validation; physical acceptance still pending |
| `completed_iteration_limit` | Normal exit and explicit unconverged iteration limit | Provisional fit diagnostics only |
| `completed_no_disentanglement_required` | num_bands=num_wann, no DIS, normal product output | Fit validation; not DIS convergence |
| `execution_failed` | Execution/product/iteration inconsistency or unconfirmed DIS status | Diagnose problems/logs; not an available fit result |

All retain `accepted=false`, `fit_pass=null`. Complete iteration-limit products set `provisional_fit_ready=true` and `fit_status=provisional_unconverged`. No automatic extension, window change, or localization. `run/inspect` returns zero for complete provisional products and 2 for execution_failed; callers must inspect JSON rather than infer convergence from exit code.

Required nonempty products are `wannier90.chk`, `wannier90_hr.dat`, `wannier90_r.dat`, `wannier90_tb.dat`, `wannier90_band.dat`, and `wannier90_band.kpt`. Completeness does not establish matrix quality or fit. Adding external products afterward causes manifest/hash inconsistency rather than converting failure into success.

Native bands_plot generates its own segment density/order/connectivity, which may differ from DFT sampling and repeated endpoints. For exact comparison, evaluate postw90/geninterp at the original DFT k points and pass plot_fit_review's alignment gate.

Parsing follows explicit DIS/convergence/limit records in [Wannier90 3.1.0 disentangle](https://github.com/wannier-developers/wannier90/blob/v3.1.0/src/disentangle.F90); normal termination follows the [main program](https://github.com/wannier-developers/wannier90/blob/v3.1.0/src/wannier_prog.F90). `All done` or zero exit alone does not establish DIS convergence.

## Technical product recovery after wall-time interruption

If actual DIS completed and a stable postwann checkpoint was written, but product writing/final accounting was interrupted, prepare output-only recovery under a new `technical/restart_NNN/wannier/`:

```bash
python tools/wannier_workflow.py prepare-output-recovery \
  --source-dir TRIAL/wannier \
  --out-dir TRIAL/technical/restart_001/wannier \
  --source-job-id 123456

python tools/wannier_workflow.py submit-output-recovery \
  --directory TRIAL/technical/restart_001/wannier \
  --site sites/slurm-output-recovery.example.json --ntasks 36 --execute
```

The source ID must match any existing run record. Submission uses `--dependency=afterany:<source-job-id>`. At start, query the source: reject a still-active source; if standard inspect already certifies complete products, record `no_op_source_already_complete` and do not rerun.

Recovery records `technical_output_recovery=true`, `scientific_proposal_changed=false`, source path/job, manifest/WOUT/chk/eig hashes, inodes, and materialization method. Use a fresh destination and read-only source. Hard-link chk/eig where possible, otherwise copy. Win adds only unique `restart=plot`; reject changes to windows, basis, geometry, num_iter, DIS controls, or product switches. AMN/MMN are unnecessary: W90 3.x reads U/M from chk; HR/TB/bands also require eig. This follows the [main program](https://github.com/wannier-developers/wannier90/blob/v3.1.0/src/wannier_prog.F90) and [plot routines](https://github.com/wannier-developers/wannier90/blob/v3.1.0/src/plot.F90).

Source WOUT must contain a parseable actual DIS final state and completed checkpoint write after `Final State`. Recovery WOUT must contain plotting restart and unique final `All done`, with no new DIS. Inspect inherits source convergence and checks recovery WOUT/product hashes. An iteration-limited source remains `completed_output_recovery_iteration_limit`, provisional, and unaccepted. The recovery site template controls resources, not scientific gates or a general eight-hour runtime expectation.

## Matrix reuse for window-only retries

With official execution evidence, identical basis/projection order, original band cache, EF/spin/dimension, supply new cache/analysis/decision together. The new decision must pass current band review; preparation recomputes windows on the old interface's actual EIGENVAL mesh.

```bash
python tools/wannier_workflow.py prepare --interface-dir OLD_INTERFACE \
  --out-dir NEW_TRIAL/wannier --cache CASE/02_band/charge.npz \
  --analysis NEW_TRIAL/records/selection/analysis.json \
  --decision NEW_TRIAL/records/selection/decision.json
```

Only the four window scalars change through the new decision. Preserve matrices, geometry, k order, projections, exclusions, original-to-retained mapping, and old records. Failed/unconverged VASP, changed matrices/basis/cache, or changed exclusions cannot use this route. A new basis needs new AMN. Keep num_iter=0 and all product switches. Preparation does not execute W90 and is unnecessary for selection-only evaluation.
