# Decision stages, trials, and experience interfaces

Follow the [canonical scientific sequence](WORKFLOW-DECISIONS.md) for orbitals and windows. This document defines stage sealing and retry records.

Tools provide reproducible evidence and necessary constraints; an AI or researcher proposes decisions from current projections and general physical reasoning. `initial` is a conservative baseline without case experience. `real_first_try` is the reviewed choice selected before submission. A `retry` responds to an actual preceding result. Initial and first-try parameters may be identical.

The baseline still requires basis/block review; it is not a deterministic automatic orbital selector. Record actor, model, prompt version, and inputs for replay. Numerical validity does not establish scientific accuracy, and stage sealing does not establish context isolation.

| Stage | Location | Execution | Counting |
| --- | --- | --- | --- |
| initial | `03_wan_trial/initial/` | Not executed directly | No trial number |
| real_first_try | `trial_001/records/selection/` | Prepare after explicit selection; actual-mesh checks remain required | Scientific trial 1 |
| retry | `trial_002/records/selection/`, etc. | Select with parent decision and result | New scientific trial |
| technical restart | Existing trial's `technical/restart_NNN/` | Same scientific identity; new prepared inputs | No new scientific trial; execution counted separately |

Each new stage preserves `inspection/review/analysis/decision/selection/validation/context/retrieval.json`, `review_diagnostics.json/md`, `conduction_review.json`, `selection.png/pdf`, and `STAGE.json`. Initial records retain conduction questions; first tries/retries retain checked answers. STAGE binds file/input SHA256, code identity, parent, differences, reasons, and tool time. Older stages remain read-only under their original file list. Sealing forbids in-place overwrite. The per-material page is `03_wan_trial/index.html`; the [cross-material gallery](SELECTION-GALLERY.md) defaults to `selection_gallery/index.html` under the experiment root. Rendering failure returns `gallery_warning` without changing a sealed stage's state.

## Usage sequence

Prepare the [layout and DFT sources](MATERIAL-LAYOUT.md), then use the [first-trial card](FIRST-TRIAL-CARD.md), current band cache, and `review_packet.py`. Write the review and iterate `propose`; a draft is not an executable selected stage. Before sealing a first try or retry, run `conduction_review.py questions`, address selected projection beyond the reference and band-edge/positive candidates above frozen, write answers, re-propose, and run `check`. `decision_stages.py select` recomputes this gate.

For [independent review](INDEPENDENT-REVIEW.md), the packet supplies official DFT/projected-band figures and per-band CSV. After proposal, diagnostics review the adopted frozen window and repeated/contradictory reasons. `frozen_state_counterexample_v1` only examines the independently proposed interval: lower counterexamples prompt raising the lower edge; upper counterexamples can veto a higher candidate or prompt lowering the upper edge. Passing does not justify expansion, basis changes, exclusions, or outer changes. Context records packet paths/hashes; sealing recomputes and binds diagnostics. `record_consistent` verifies consistency, not physical orbital/block identity.

Example context:

```json
{
  "experiment_id": "experiment-identifier",
  "task": "Complete selected-orbital manifold and explicitly declared precision objective",
  "evaluation_mode": "blind",
  "actor": {"kind": "ai", "id": "selector", "model": "actual-model-id", "prompt_id": "prompt-version-or-hash"},
  "guidance": [{"origin": "general_rule", "description": "General physical selection guidance from this toolkit"}],
  "external_ai_cost": null
}
```

`guidance.origin` supports task, human, general_rule, case_experience, and previous_result. Initial/blind modes forbid case_experience. Previous_result is retry-only and refers to the parent's actual result. External historical results must be declared as case_experience, not relabeled as current results or general rules. The evaluation runner is responsible for access isolation.

```bash
python tools/decision_stages.py initial --case-dir CASE \
  --review initial-review.json --context initial-context.json

# Review initial figures/metrics; retain or revise the draft. Retrieval defaults off.
python tools/decision_stages.py select --case-dir CASE \
  --parent CASE/03_wan_trial/initial --trial-id trial_001 \
  --review first-review.json --context first-context.json \
  --reason 'Specific current-projection reasons for retained or revised choices'
```

Selection creates the trial without precreating interface/wannier/comparison directories. Initial-role or unsealed legacy decisions cannot prepare/run new interface or Wannier jobs. Older outputs remain readable. Matrix reuse explicitly supplies the selected cache/analysis/decision and passes identity checks.

Interface analysis/decision paths point to `trial_001/records/selection/`. Execution remains band-path selection → interface generation → actual-mesh checks → `num_iter=0` disentanglement → same-DFT-k comparison. Interface generation uses the configured single NSCF or verified shared-WAVECAR route. No independent selection mesh/PDOS is added. All four product switches and the 10,000-step cap retain their contract.

Preserve figures/problems for an invalid initial; revise a separate draft before selecting a ready choice. Do not alter status manually to force a transition. A sealed initial is immutable. Changed DFT inputs or task require a new case.

`needs_window_review` routes to its specific cause. Counterexamples inside the adopted frozen interval require revision and re-proposal; `trial_intent` cannot waive them. If only outer-tail/role uncertainty remains and numerical/inner-window checks pass, use an [exploratory trial](MANIFOLD-SELECTION.md#exploratory-trials-under-uncertainty) within budget with a reasoned `trial_intent.mode=exploratory` and `window_override`. Keep high-energy omissions visible. Insufficient space routes to additional non-self-consistent bands; report a blocker only when data or budget constraints prohibit it. Initial may retain exploratory intent as non-executable hypothesis metadata; it cannot prepare an interface or run a solver. Explicitly select a real_first_try and pass the unchanged execution gates. An unresolved optimum alone is not a reason to abandon a retry.

## Actual results, next trials, and technical restarts

Follow the [window retry card](WINDOW-RETRIES.md). Review clean low-conduction protection when absent; with substantial high-conduction protection, test a lower frozen upper edge with basis and outer fixed. Constraint release does not require labeling legitimate targets as unwanted or using the negative screen to choose direction.

The runner records time, output identity, and actual state. After comparison, write an assessment separating `observations`, `hypotheses`, and `next_action`; optional entries include human review, task metrics/thresholds/evidence, and external model usage. Record execution failures without inventing fit results.

```bash
python tools/decision_stages.py record-result --case-dir CASE \
  --trial-id trial_001 --assessment assessment.json
python tools/decision_stages.py select --case-dir CASE \
  --parent CASE/03_wan_trial/trial_001/records/selection \
  --parent-result CASE/03_wan_trial/trial_001/records/results/result_001.json \
  --trial-id trial_002 --review next-review.json --context next-context.json \
  --reason 'Specific preceding-result evidence for a window or model change'
```

Result snapshots verify run/decision identity and retain log/output references, hashes, observations, hypotheses, and timing. `knowledge_status=candidate_not_promoted` does not certify fit or insert a case into a knowledge store. Append snapshots rather than overwriting them; outputs stay in place.

```bash
python tools/decision_stages.py technical-retry --case-dir CASE \
  --trial-id trial_001 --reason 'Specific technical fault and repair; scientific choice unchanged'
```

A technical restart returns fresh interface/wannier/comparison paths for official prepare/run with the same selection. Node/launch faults are technical restarts; changed basis/windows are scientific trials. Changed disentanglement budget/tolerance must be explicit and cannot be hidden as an equivalent restart. Directory reservation is not execution. Count distinct run records rather than calls/snapshots. Use geninterp_path/plot_fit_review in the returned comparison directory and collect via record-result.

## Retrieval and ablations

```bash
python tools/decision_stages.py retrieve --mode disabled --query query.json --out retrieval.json
python tools/decision_stages.py export-case --case-dir CASE --out trajectory.json
```

Current retrieval returns an identified empty receipt. `experience_assisted --snapshot ID` returns `backend_not_configured`, with no network/vector query or fabricated hit. A future adapter must enforce material/family holdout and snapshot filtering before returning provenance, applicability, and evidence. The current checker intentionally rejects fabricated nonempty hits; backend integration requires corresponding implementation/tests.

Suggested ablations are an explicitly selected unchanged baseline (`--baseline-control`), AI review without retrieval, and AI review with retrieval. The third arm requires a real backend. Fix task, DFT, toolkit, acceptance scope, and budget; use new cases/contexts. Commands do not automatically construct ablations or isolate model context.

Trajectory export retains stages, registered results, and deduplicated execution references. Large artifacts remain hashed references rather than vectors. Recording experience inputs/outputs is not automatic learning. The distributed toolkit includes no material answers.

## Optional evaluation usage accounting

Ordinary construction requires scientific/execution records, not model-token or billing records. Use the external-model items below only for an explicitly requested cost evaluation; omitting them does not block any scientific stage or result snapshot.

- Record stage-tool time, interface `wall_seconds`, and actual Wannier `wall_seconds` separately; deduplicate cumulative time by unique run. Queue time requires scheduler logs and is not Wannier execution time.
- Obtain `external_ai_cost` from provider usage/logs, including input/output/cache tokens, units, request IDs, and source. Unavailable usage is `null`, not zero or a text-length estimate.
- Scientific trials, technical restarts, and model requests are independent counts. Additional snapshots do not create additional executions; failures and restarts remain in costs.
