# Scientific interface contract

The canonical API operates on explicit files and records. It does not prescribe an AI client, SSH, a scheduler, account, queue, cluster layout or deployment location. The current scientific engines are VASP and Wannier90; arbitrary engine interoperability is not implemented.

## Inputs, outputs and boundaries

| Operation | MCP / canonical CLI | Required inputs | Durable outputs and boundary |
|---|---|---|---|
| Import projected bands | `collect_band` / `dft_workflow.py collect` | Prepared case, completed verified SCF/band files and official run records | `02_band/charge.npz`, fit path, DFT/projection plots; no new DFT job |
| Explicit raw-file conversion | `vasp_to_cache.py --help` | PROCAR/OUTCAR, declared sampling/spin and geometry as required by that CLI | Cache with source identity; conversion is not execution authentication |
| Evidence bundle | `prepare_evidence` / `evidence_pipeline.py` | Current band cache, new output directory | Inspection, projected-band plots, null-choice draft, pipeline/log records; stops for reviewer |
| Draft edit | `patch_draft` / `review_workflow.py patch` | Unsealed draft, small changes, current SHA256 | Updated draft and prior-version history; no sealing |
| Required witnesses | `review_requirements` / `review_workflow.py requirements` | Exact cache, inspection, current draft | Deterministically computed coordinates plus required human fields; no physical choice |
| Proposal and questions | `review_proposal` / `review_workflow.py propose` | Same cache/inspection/review, new output directory | Original selection/analysis/decision/checks, selection figures, current conduction questions; status can still require review/data |
| Stage sealing | `seal_stage` / `decision_stages.py initial/select` | Reviewed draft, context; selected stage needs parent, trial ID, reason; retry also needs actual parent result | Immutable `initial` or selected stage with hashes/ancestry; only selected numerically executable stages may run |
| Interface input preparation | `prepare_interface` / `prepare_wannier_interface.py prepare` | Band/SCF inputs, exact cache and selected analysis/decision; optional verified shared wavefunctions or explicit mesh | Basis-dependent input directory and manifest; no engine launch |
| Interface authentication | `validate_interface` / `prepare_wannier_interface.py validate --outputs` | Actual generated matrices/eigenvalues and matching records | Dimensions, actual-mesh/window/exclusion/data gates; path feasibility is not an actual-mesh guarantee |
| Wannier input preparation | `prepare_wannier` / `wannier_workflow.py prepare` | Official authenticated interface and selected stage; window reuse also needs exact cache/analysis/decision | New input/manifest; `num_iter=0`, requested products; no engine launch |
| Solver inspection | `inspect_wannier` / `wannier_workflow.py inspect` | Bound input/run records and actual outputs | Product/convergence evidence, readiness and provisional labels; no fit acceptance |
| Numerical comparison | `geninterp_path.py prepare/convert`, `plot_fit_review.py` | Exact path, DFT/Wannier eigenvalues and declared target mapping | Same-k numerical records and figures; no physical/symmetry certification |
| Actual result snapshot | `record_result` / `decision_stages.py record-result` | Actual bound run products and external assessment | Immutable result identity for subsequent retry; no automatic knowledge promotion |
| Numerical summary | `read_record_summary` | Stored JSON | Compact evidence; no invented acceptance |
| Optional evaluation usage | `account_native_usage`, `usage_report` | Explicitly requested cost evaluation, client-exported native usage and optional prices | Deduplicated usage and sourced costs; ordinary construction does not call these tools or require billing records |

CLI arguments, shapes and allowed review fields remain defined by the original tool's `--help`, policy/schema and [MANIFOLD-SELECTION](MANIFOLD-SELECTION.md). These operations retain distinct data identity, sampling, energy reference and stage ancestry. Output directories are new; immutable results are not overwritten. Read large artifacts from disk only when the next decision needs them.

## Execution integration

Execution is a separate backend concern. An integration receives prepared engine inputs plus manifest/selected-stage identities and produces real output files, exit/timing evidence and records binding the exact inputs/outputs. The scientific validator then checks those products. A scheduler success flag cannot substitute for product authentication or convergence.

**Current capability:** official Wannier execution and official end-to-end comparison retain their existing Slurm allocation requirements. DFT `run-stage`/`run-interface` also have a `local` profile path; submit tools are Slurm only. The new `slurm_runner.py` automates those existing commands as an optional adapter. Evidence, selection, preparation and file plotting can be used without that adapter. SSH is optional transport, not an execution backend or scientific input.

**External backend boundary:** bare AMN/MMN/EIG or an externally written `run.json` is not automatically an official interface. Existing gates require their authenticated records. A future PBS/direct/container/remote-service adapter needs a tested import/execution-record integration. Do not fake Slurm IDs, bypass gates or edit historical records to advertise unsupported portability.

## Return envelope

Every compact command returns `ok`, `exit_code`, `status`, `result`, `log`, `log_sha256`, and the inline budget. `result` contains the relevant checks; full arrays and text remain in the hash-bound log. Exit zero and `ok=true` report successful tool execution, including successful generation of a **needs-review** proposal. Interpret scientific status separately. Failure returns a nonzero/null exit, an error message and `automatic_retry=false`. The runtime does not create physical acceptance.

## Selection scope audit

`audit_selection_scope` / `selection_scope_audit.py` takes the current cache, exact `selection.json`, optional matching review, and an optional new output path. It returns lower target-protection facts, actual scalar membership, upper-candidate origin, representative state witnesses and `required_review`. It chooses no basis/window and grants no physical approval. `review_workflow.py propose` runs it automatically and retains its record beside the selection. Read these facts before answering conduction questions.
