# Compact, file-based operation

This is the operational interface card. Read [FIRST-TRIAL-CARD](FIRST-TRIAL-CARD.md) for scientific decisions and [INTERFACES](INTERFACES.md) for exact inputs and outputs. CLI and MCP use the same scientific implementations. Neither evidence nor selection assumes SSH, Slurm, a particular AI client, or where that client is deployed.

## Decision order

1. Provide a verified projected-band cache. `prepare_evidence(cache, out_dir)` makes inspection, plots and an **unselected** `review.json` in one call. Import raw DFT outputs first if a cache is unavailable.
2. Read the current evidence and the first-trial card. Fill orbital actions, target/reference/frozen rows and physical reasons using `patch_draft(path, changes, expected_sha256)`. Keep full draft text on disk instead of regenerating it after every edit.
3. Call `review_requirements` to obtain exact gate-witness coordinates. Patch those coordinates and your own physical explanation. The helper does not select a basis, assign a state role, enlarge frozen or waive a gate.
4. Call `review_proposal` once: the original proposal, selection figure and current conduction questions are generated together. Answer the question template. If choices change, regenerate the proposal/questions; their identities must match. Save the conservative `initial`, then seal `real_first_try` using `seal_stage`. The two may have identical choices. Initial is never executed.
5. Give the sealed selected stage to your execution integration. Prepare and authenticate interface matrices, prepare disentanglement-only input, execute, inspect actual products/convergence, then compare at identical DFT path points. Current bundled full-flow execution uses the **optional [Slurm reference adapter](SLURM-EXAMPLE.md)**. External execution/import is a separate integration contract, not permission to invent run records.
6. Read compact numerical records and the figures. State observations, hypotheses and next action separately. Record the actual result before sealing a retry with its `parent_result`. Follow [WINDOW-RETRIES](WINDOW-RETRIES.md); preserve scientific trial and technical restart counts.

An execution integration can automate deterministic preparation/monitoring between these boundaries. It must stop for scientific choices, data insufficiency, rejected evidence or uncertain execution. Deployment and transport are separate concerns: MCP may run beside the data; CLI may be invoked locally, through an existing agent shell, or through a chosen remote transport.

## Compact returns and complete evidence

`wannier-compass-run --workspace WORKSPACE TOOL.py -- ARGUMENTS` wraps the existing CLI. Its default JSON envelope is bounded to 8,000 characters; complete stdout/stderr, exit code, timing and invoked-tool hash remain in `log`, bound by `log_sha256`. Direct legacy CLI invocation remains available. `--full-output` explicitly opts into the complete output.

`ok` means the invoked command exited zero. Inspect scientific `status`, `ready_for_wannier`, `ready_for_fit`, convergence, negative witnesses and data gates separately. Runtime never assigns scientific acceptance. Per-k arrays stay on disk; summaries retain numerical checks and representative witnesses. If a needed field is in `details_in_log`, read that part of the full record. `read_record_summary` reads stored JSON without asking a model to rewrite its metrics. If needed, select an exact RFC6901 field with `pointer`, for example `/numerical_checks/min_nouter` or `/stdout/numerical_checks/min_nouter` in a command log. A `details_required` envelope asks for this evidence before further decisions.

MCP workspace file tools require paths within `WAN_WORKSPACE_ROOT`. Bundled Markdown is read-only: `read_text` accepts the absolute documentation paths returned by `workflow_guide` or the equivalent `workflow/` prefix, including before a workspace is configured. This exception does not grant access to other files outside the workspace. CLI `--workspace` sets working directory and log/cache location; explicit CLI file arguments are passed through and are not sandboxed by this wrapper.

## Draft edits and execution recovery

`patch_draft` is an object merge: supplied fields replace their old values; nested objects merge; arrays replace as a whole. `null` deletes an optional key, except `task_scope_review.requested_scope_evidence`: this required schema field keeps a literal null for a primary-manifold task. Use the raw-file SHA256 returned by the template, patch or text read for `expected_sha256`; a proposal’s `review_sha256` instead binds canonical review content. The edit requires the current file hash and archives the prior draft/patch. Sealed stages and `records/` cannot be patched. A template's null placeholders are unresolved choices, not accepted defaults.

Scientific rejection is an ordinary failed command, with no automatic retry. Optional SSH read queries may retry a bounded transport failure. Mutating SSH commands are attempted once; loss of their response means **completion unknown**, requiring reconciliation before another mutation. The optional Slurm adapter records submission intent before submitting, resumes recorded jobs, and authenticates completed evidence again. Queue disappearance alone is not success.

## Optional evaluation accounting

Normal construction follows the decision order above without calling billing tools or supplying token counts, API logs or prices. Usage accounting is a separate opt-in evaluation utility; it is not a preparation, execution, retry or acceptance requirement. Scientific provenance and computation logs remain part of normal operation.

For an explicitly requested cost evaluation, supply native usage exported by the model client; this package cannot discover billing from an arbitrary AI deployment. `account_native_usage` records a request/response identity, model, provider and phase; repeats are deduplicated. Missing counters remain null. Supported native mappings are DeepSeek, OpenAI and Anthropic, or explicit normalized counters. Reasoning tokens already included in output are not added again.

`usage_report` can filter provider/model/phase and optionally reads a model/provider-matched price file with currency, source, effective date, explicit billable fields and rates per million. Without native usage or applicable rates, cost is null. There is no token estimate from text length and no hardcoded provider price. Separate evidence, initial, first try, retry, execution, assessment and engineering calls. Tool wall time and official computation wall time are different measures; native API use and physical acceptance require a matched model test.
