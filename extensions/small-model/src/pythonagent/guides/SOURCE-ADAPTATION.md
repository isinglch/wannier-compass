# Current workflow protocol and explicit MCP mapping

This is the authoritative operational interpretation of the quoted MCP sources.
The verbatim originals remain scientific references; quotation does not register
a tool, change a schema, or grant a role another role's powers. Apply their
scientific evidence requirements through the mappings below, including when
reading originals through tools or receiving stage_guidance after execution.

Priority: the operator's task and fixed construction constraints, current role
responsibilities, published tools/action/plan schemas, and this mapping govern
operations. Apply original scientific reasoning within that contract. Current
numerical records govern facts; old prose/parameters/hypotheses cannot override
them. If required evidence is unavailable, name the unverified part. Never invent
a tool, measurement, successful screen or unsupported execution. Do not treat
invalid old field syntax as permission to discard its scientific evidence duty.

## Fixed construction contract

solver.num_iter MUST be explicitly 0: this workflow is disentanglement-only.
Nonzero values are rejected, not silently replaced. No scientific purpose or
exploratory intent waives this constraint. Planner must author a corrected version
and reviewer must approve it. dis_num_iter, dis_conv_tol and dis_conv_window remain
explicit planner choices. Old DIS budgets/tolerances are not current fixed values.
Localization trials are outside this workflow, including during retries.

## Scientific records and role responsibilities

Use only model-plan-v1 fields. Planner's reason must cover each applicable row
below with actual states/ranges, evidence, considered alternatives and limitations;
mark inapplicable items explicitly. Reviewer checks each item rather than accepting
a generic explanation. Numerical state witnesses belong in evidence_refs.

| Original concept | Current record or action |
| --- | --- |
| task_scope_review, task_anchor_band_ranges_1based, deep_blocks | objective states the fixed physical task. In reason, declare independent task-anchor ranges and concentrated deep blocks with target/background/outside-task roles before basis. A broader deep-bonding task needs the actual operator request. |
| orbital_decisions, additional_basis_evidence | basis contains adopted channels. In reason, record each available channel's task role, inclusion/omission risks and extra-channel task-state witnesses/representation reasons independent of an unwanted deep block. |
| target, reference, frozen row groups | target_band_ranges_1based is the scoring population. In reason identify broader reference ranges, their denominator and censoring, and intended protected states. There is no reference/frozen-row JSON field. Scalar windows define actual interval membership, including background. Target rows are not an implicit reference denominator. |
| review.json/window_override, initial/real_first_try, propose/questions/check/seal | Planner uses write_plan with a complete new plan and windows_relative_eV, then submit. Reviewer approves the exact plan_id or requests revision. Versioned plans/approvals replace old stage APIs; no old commands/stage files are required. |
| independent lower edges, positive frozen candidate | In reason explain each of the four edges separately, upper-candidate origin and positive evidence, the first unprotected task state and deferrals. EF/CBM+0.5 and reference-conditioned 70% are suggestions, not fixed rules or measurements supplied by current tools. |
| scope audit / frozen_state_counterexample_v1 | validation.facts.frozen_projection_audit scans every retained frozen state on the supplied sampling. projection_audit scans all retained states for the current plan and region and saves a summary with bounded exact witnesses. Bulk /states reads are unavailable; use orbital_character, window_diagnostics and residual_ranking for further factual queries. Explain lower/upper/interior competition and protection tradeoffs. Counts/quantiles are facts, never automatic scientific approval or a basis recommendation. |
| weak-projection screen | No weakness cutoff is assumed by default. Explicit weak_selected_max reports selected_sum <= that value; the old 0.10 indicator may be requested and needs physical interpretation. Null threshold or an empty region is not a clean screen. Complete computational coverage is not model review or full-BZ validation. |
| above-outer continuation, DFT sufficiency | Use projection_audit with above_outer/all_retained, raw witnesses and counts/top margins. Review all computed states, not only self-selected reference rows. Do not claim the old automatic tail/reference audit ran. Preserve a justified window if essential data are missing. |
| retry review/execution | Evaluator sends observations/questions; strategist sends strategy via request_revision; planner alone writes the complete changed plan; reviewer alone approves. Workflow executes only under operator execute authorization. Window-only trials preserve basis, exclusions, target and solver controls; separately declared supported changes report their confounds. |
| needs_more_data/add_bands_nscf | Planner uses request_data with the concrete missing evidence/operation. Other roles hand the issue back toward planner. additional_data_operations=[] means unavailable, not completed or scientifically unnecessary. |

There is no propose/seal tool, shell, old review patch/conduction-answer protocol,
window_override field or trial_intent gate. Do not submit those old keys. Express
uncertainty in reason and handoffs. A path projection scan is separate from actual
interface-grid count checks; do not claim mesh projections were scanned.

## Current results and metrics

Native execution interpolates executed HR+wsvec at original DFT k points, with
both spectra relative to the same SCF EF, without fitted shifts or resampling.
It does not execute the old postw90 pipeline. Use current_result.record_paths,
not historical 03_wan_trial paths. result.metric_contract defines the current
fields. Command adapters may differ: inspect their declared fields, do not invent
native metrics or assign native semantics to similarly named external results.

Native target/baseline_target match the current plan's target rows/the first
executed plan's target rows at every supplied path k. They are NOT filtered by
frozen/outer windows. MAE and maximum error use matched pairs only; missing states
are separately counted. No implicit RMSE/P95, frozen/outer aggregates or branch
identity is supplied. residual_ranking defaults to baseline_target and never
silently substitutes another population. Display margins are not scoring regions.

Native disentanglement.state distinguishes converged, iteration_limit,
not_required, unknown and inconsistent from WOUT markers, ordered DIS iterations,
the approved budget and dimensions. converged is null for not_required/unknown/
inconsistent, not a failed convergence test. Iteration-limit fits are provisional;
unknown/inconsistent states do not produce trusted fit metrics. Termination,
products, convergence and acceptance are separate. This route requires HR+wsvec
for interpolation and does not claim every old chk/r/tb/band product was validated.
Do not apply old fixed 2000/10000 iteration labels to different solver budgets.

## Assessment and termination

No numerical fit threshold is configured by default. Report measured errors,
coverage and limitations; do not invent numerical acceptance or treat a missing
threshold as missing DFT evidence. Assess an explicitly supplied precision objective
as stated, separately from complete fixed-target coverage.

complete/stop end the workflow; neither grants program-certified numerical or
physical acceptance. Evaluator complete must supply termination_basis and
remaining_work: objective_satisfied is an evidence-supported assessment of the
actual objective (qualitative when no numerical threshold was given);
no_justified_trial explains why no supported experiment addresses remaining issues;
data_blocker names essential unavailable evidence. Strategist stop allows only
the latter two bases and cannot replace evaluator's assessment. Poor fit,
nonconvergence or absent thresholds alone are not stopping reasons. If a justified
trial exists and budget remains, iterate/request_revision. Workflow reports budget
exhaustion separately. Retain failed trials, missing targets and unfinished work;
never rename a blocker as success. An objective-satisfied assessment cannot cite
a failed/provisional/unknown native execution as established fit acceptance.

## Reference and image access

Keep original sources intact. Required supplementary reading follows the question:
MANIFOLD-SELECTION for reference/tail/candidate details; INDEPENDENT-REVIEW for
unresolved review duties; WANNIER-EXECUTION for unfamiliar WOUT evidence (under
this mapping); EVALUATION-AND-KNOWLEDGE before blind/generalization/experience
claims; DFT-PREPARATION, WANNIER-INTERFACE or VASP-CACHE for source uncertainty.
The catalog is not proof of reading. Follow pagination for required sections and
disclose unverified evidence or exhausted read budget.

read_text offsets/limits count zero-based lines; read_record uses JSON pointers
and list pagination. Resolve old links via mcp-sources.json, never the old project
filesystem. This text transport has no image-view tool: paths/manifests do not
mean images were seen. Use numerical evidence and disclose visual review not
performed. Only current tools and execution_capabilities establish available
operations; no automatic matrix reuse, scheduler, new DFT or experience store is
implied. Historical automatic gates remain historical API instructions, not
current program checks; their underlying physical evidence still needs review.
