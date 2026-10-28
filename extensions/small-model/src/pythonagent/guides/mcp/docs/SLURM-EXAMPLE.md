# Optional Slurm reference workflow

This example is an execution adapter, not the canonical scientific interface. It assumes an authorized Slurm environment and existing site-specific executables. MCP can run directly there; SSH is optional. Other environments can use the file-based [interfaces](INTERFACES.md) without adopting this example. The bundled full-flow adapter currently supports Slurm only.

Use a small user-provided, nonconfidential elemental structure for the first integration test. This repository includes no structure, pseudopotential, computed result or orbital/window answer. Generate DFT inputs with the original [DFT preparation](DFT-PREPARATION.md) tool, or supply verified existing data. Keep the actual material/data outside the repository.

## Site and evidence

Copy `workflow/sites/slurm.example.json` (or the shared-WAVECAR variant) into your private workspace, set real executable/environment/resource fields and run `dft_workflow.py preflight`. Cluster-specific account, partition, launcher and limits belong only in this private profile. Do not submit the template unchanged.

Below, `CASE` is an existing prepared material directory, `SITE` the reviewed private profile, `ROOT` the authorized workspace and `POSTW90` the executable. These placeholders are not material defaults.

```bash
wannier-compass-run --workspace "$ROOT" slurm_runner.py -- configure \
  --case-dir "$CASE" --site "$SITE" --phase evidence
wannier-compass-run --workspace "$ROOT" slurm_runner.py -- start \
  --case-dir "$CASE" --phase evidence --execute
```

The detached controller collects verified bands and creates evidence, then reports `scientific_decision_required`. It calls no model. With existing evidence, the portable `prepare_evidence` interface is sufficient. Read `stage_status.json` or call `slurm_runner_status` when completion is expected; do not ask a model to poll the queue repeatedly.

## Reviewed trial

Choose and seal initial/first try through [RUNTIME](RUNTIME.md) and the scientific card. Then:

```bash
wannier-compass-run --workspace "$ROOT" slurm_runner.py -- configure \
  --case-dir "$CASE" --site "$SITE" --phase trial --trial-id trial_001 \
  --postw90 "$POSTW90"
wannier-compass-run --workspace "$ROOT" slurm_runner.py -- start \
  --case-dir "$CASE" --phase trial --trial-id trial_001 --execute
```

If needed, configure explicit `--shared-source`, `--mp-grid NX NY NZ`, `--interface-source` for a valid window-only reuse, and `--ntasks`. The original profile/physics gates remain in force. The adapter prepares/submits interface, Wannier and comparison as separate original jobs, stops at `fit_review_required`, and never chooses acceptance/retry parameters. No automatic scientific retuning occurs.

MCP equivalents are `configure_slurm_runner`, `advance_slurm_runner(background=true, execute=true)` and `slurm_runner_status`. MCP submission additionally requires `WAN_ALLOW_SUBMIT=1`. CLI execution uses explicit `--execute` under the user's compute authorization.

## Resume and errors

Configuration is pinned, including tool/profile hashes. Resume an existing job without resubmitting it. A lost successful command response can be recovered from its durable matching log. Submission response loss reports `submission_completion_unknown`; reconcile the original `submissions/*.json` and script, then `adopt-job` with the exact stage/job/record. A queue disappearance without an official output record is unknown, not success. Controller lock recovery is explicit (`recover-lock`) and only allows a recorded dead process on its recorded host.

Failed scientific gates require reviewer action. Partial technical execution retains its logs; use the existing technical-restart workflow where required. This adapter does not silently overwrite partial products or reclassify a technical restart as another scientific trial.

Optional `remote_command.py` provides argument-array SSH calls. Only explicitly side-effect-free reads may retry recognized transport failures. Mutations are tried once and uncertain completion must be reconciled. It is not needed when the agent/MCP server already runs in the computation environment.
