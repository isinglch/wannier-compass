# First-trial decision card

Read the [construction overview](CONSTRUCTION-OVERVIEW.md) first for the intended model, window tradeoffs and result diagnosis. Construct the main task-relevant orbital manifold and related low conduction from the current full projected band path. Declare the physical task before basis or windows; keep it fixed for fits and retries. Use [field details](MANIFOLD-SELECTION.md) when filling records and [physical reasoning](PHYSICAL-REASONING.md) for an unresolved branch.

## Decide in this order

| Step | Read these facts | Make this decision and check |
| --- | --- | --- |
| 1. Task | Full bands, occupations, composition, same-state raw projections | Identify required states, related low conduction and specifically concentrated deep blocks. Declare anchors before adding orbitals. Distinguish target, DIS background, tail reference and precision objective. A narrower frozen interval does not narrow the task. |
| 2. Basis | Chemical candidates and their task weights, competing channels | Start with block p/d/s and available f. Review extra channels with independent task-state witnesses and representation reasons. A concentrated deep-only channel may stay outside the basis; inspect the rest of the task before omitting it. A distant full-spectrum maximum does not prove absence in the task. Basis omission and band exclusion are separate. |
| 3. Valence dis_min | Lowest required target-valence states and mixed neighbours | Include the complete target envelope, keeping mixed background in DIS. Optional exclude_bands needs a clearly unwanted occupied prefix and a safe sampled global gap, followed by interface verification. Without that gap, retain the rows and continue. |
| 4. Valence frozen_min | Identifiable target valence, unwanted lower states, mixing | Protect identifiable target valence. Raise the edge where unwanted or weak lower states would be forced into frozen. Explain unprotected target states using specific evidence or a physical tradeoff. Review a deeper candidate if the shallow edge came only from choosing upper rows. Neither lower edge requires a global gap. |
| 5. Conduction dis_max | Target extension, reference-conditioned ~70%/tail hint, all computed projections above outer, counts | Choose the physical outer first. Classify continuation, separate blocks, tails and uncertainty. Then audit DFT sufficiency; retain a justified candidate and obtain required NSCF data when the computed top is insufficient. N_outer>=N_wann alone does not establish adequate DIS space. |
| 6. Conduction frozen_max | Low-conduction roles, raw selected/competing projections, actual included states | Review EF+0.5 eV for metals/semimetals or CBM+0.5 eV for insulators as a starting option. Independently review extension through supported low-conduction segments and where it stops. State the actual candidate origin. A high row-envelope maximum is a draft fact; a high adopted edge needs positive branch evidence before negative screening. |
| 7. Scalar audit | Actual energy interval on all retained states | Review lower protection, actual background inclusion, upper origin and counterexamples. Locate lower, upper or interior mixing relative to the candidate and required task states. Energy sign relative to EF does not determine the remedy. |
| 8. Seal and run | Initial/first-try records and current questions | Save initial and seal the reviewed real_first_try; identical choices are allowed. Run authorized interface and DIS with num_iter=0. Check that interface's own mesh/matrices/counts. Report fixed-task fit, missing states, convergence and products separately; use the retry card for weak results within budget. |

## Resolve the two frozen edges independently

Removing non-target deep s does not ask for a shallow frozen interval over the remaining p/d valence. Protect identifiable task valence; leave genuinely mixed states in DIS. Including s for a required role also does not request every deep s bonding state.

For a higher upper candidate, review its low-conduction task role, sustained selected-channel support and neighbouring background branches. Then apply the **Frozen-state counterexample screen**. It only vetoes/reviews frozen: a clean screen supplies no extension evidence and a negative flag supplies no basis decision. An independent basis review may use the original task/projection evidence.

N_frozen>N_wann identifies incompatible forced protection for that candidate. Review actual forced states and both edges before concluding the basis is insufficient. Small frozen coverage alone also does not establish a basis deficiency. Interior mixing may prevent one scalar interval from protecting all required states; preserve the task and report the limitation.

## Tool loop

1. Use review_packet.py / evidence_pipeline.py for full-path evidence, figures and a null-choice draft. Fill the existing review fields in the order above; use review_workflow.py patch for incremental edits.
2. review_workflow.py propose produces a selection/figure, a **selection scope audit**, and current conduction questions. Read its required_review facts and figures before answering. For a direct proposal, run:

```bash
python tools/selection_scope_audit.py --cache CASE/02_band/charge.npz \
  --selection DRAFT/selection.json --review REVIEW.json --out DRAFT/scope_audit.json
```

3. Make scientific changes first. Existing window_override supplies complete dis_eV/frozen_eV pairs and the physical reason. Re-propose after editing; preserve sealed records.
4. Answer the latest conduction_review.py questions template. A deferred higher candidate needs an interval-local counterexample or explicit exploratory uncertainty. Re-propose after answering, regenerate questions, then check that exact pair and seal with review_workflow.py seal or the stage CLI.
5. Show both sealed selection figures and actual same-k fits. Report the fixed physical task first, then frozen errors with interval/sample count. Reference rows and outer background are not automatically scoring targets. Figures use frozen±1 eV and dis±3 eV contexts; display margins do not define task or acceptance.

Audit warnings request physical review. They automatically change no variable and establish no scientific approval. Path counts are provisional; actual interface counts remain separate. No universal frozen width or material answer is supplied. For later trials read [WINDOW-RETRIES.md](WINDOW-RETRIES.md).
