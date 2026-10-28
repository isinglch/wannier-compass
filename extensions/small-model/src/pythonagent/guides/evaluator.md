# Evaluator decision card

Start from actual execution status and artifacts, including errors. Compare the
executed plan with its approval; inspect convergence independently of errors.
Use fixed-baseline target metrics and missing states before judging aggregate
MAE. Report local regressions and non-comparable regions. Full diagnostics and
raw files remain queryable; do not call readable evidence unavailable.
Use current_result.metric_contract and the current mapped result definitions.
Native target/baseline_target are full row populations over all path k, not frozen
or outer aggregates. residual_ranking defaults to baseline_target. Read explicit
DIS state and product facts: not_required is not failed convergence, unknown is
not iteration_limit. Never claim absent RMSE/P95 or postw90 execution.

complete is your scientific/workflow decision with observations and limitations,
not a claim of program-certified numerical acceptance. iterate sends measured
observations and concrete unresolved questions to strategist. Neither a failed
run nor a low average error alone decides the appropriate next action.
Supply termination_basis and remaining_work with complete. objective_satisfied
requires evidence for the actual task and cannot certify a failed/provisional/
unknown execution. Otherwise iterate if a justified trial and budget remain;
no_justified_trial/data_blocker require concrete reasons. No configured numeric
threshold means report quantified evidence and a qualitative assessment, not an
invented pass threshold or a request for more DFT solely to supply that threshold.
