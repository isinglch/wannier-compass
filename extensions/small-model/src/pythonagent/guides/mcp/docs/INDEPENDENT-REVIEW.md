# Independent review: evidence, judgment, and audit

Use the [first-trial card](FIRST-TRIAL-CARD.md) for selection and the [detailed sequence](WORKFLOW-DECISIONS.md) for ambiguous blocks. This document defines packets, audits, and evaluation boundaries; it contains no historical material basis/window/fit answer.

Fix the task, DFT inputs, tool version, and budget before a fresh review context. Call a test blind only if filesystem access also excludes historical answers. Empty chat context alone is insufficient. Materials used to develop rules are regression cases, not independent evidence of extrapolation.

## Input packet

Use the existing projected band-path NPZ; no preliminary projected DOS or independent NSCF mesh is required. `review_packet.py` recomputes inspection and produces complete per-band weight CSV, standard DFT/projected-band PNG/PDF, and hash manifest. It contains current DFT evidence only, without basis/window recommendations or historical Wannier fits. Inspect complete figures and state data rather than only a candidate list.

```bash
python3 tools/review_packet.py --cache /input/band_charge.npz --out-dir /new/review_packet
```

Use `plot_dft_bands.py` for official plots. Each `manifold_selection.py propose` generates standard `selection.png/pdf` and its identity manifest; use `render` for sealed choices. Sealed stages and standard selection-only layouts refresh the [gallery](SELECTION-GALLERY.md) by default. Check entries/warnings and refresh explicitly when necessary. After Wannier, use `compare_wannier.py` and `plot_fit_review.py` for same-k comparisons. See [plotting](PLOTTING.md), [DFT plots](DFT-PLOTS.md), and [fit comparison](WANNIER-COMPARISON.md). Temporary SVG files do not replace official figures.

## Required physical decisions

1. Declare `task_scope_review` before basis or target: the physical objective, independent task-anchor rows, and specifically concentrated deep-block roles. Start from main p/d/s chemical candidates. For each extra channel cite a task-state witness and its representation contribution, rather than making its deep block a target to justify inclusion. Record each channel's task role and risks of both inclusion and omission. A broader deep-bonding model needs the explicit request. Unresolved useful roles can be explored in a numerically feasible trial. Compare raw channels on the same `(k,n)`; weak atomic-sphere weight can be interstitial. Absence of a global gap does not require an extra shell; omission of a background shell does not require excluding its DFT rows.
2. Identify the lowest target-valence weight and retained unwanted/weak background. Put `dis_min` below the complete envelope, allowing background for disentanglement. Independently raise `frozen_min` past unwanted lower states; overlapping target/background energies may require protecting only a clean interior. Finally assess optional occupied non-target prefix exclusion with a global gap. Continue without exclusion when unsafe. Report global and direct gaps separately; neither forces equal lower window edges. Sharing a clean protective gap requires explicit review.
3. Review target conduction extension and tail evidence, then confirm outer coverage and candidate-state headroom. Approximately 70%/tail evidence does not replace band-top and `N_outer >= num_wann` checks. `upper_reference_audit` and conduction questions inspect all computed states above outer, inside and outside reference. High coverage within a self-selected reference cannot establish shell completeness. Target continuation requires reviewing reference/outer; diffuse tails alone need not all enter. Choose frozen independently from clean target branches, then run `frozen_state_counterexample_v1` at every included path state. Lower/upper counterexamples review their respective inner boundaries; this screen cannot change basis, target, exclusions, or outer, and zero flags do not justify expansion. Review weak/interstitial character physically. Register each future criterion's [position and allowed scope](MANIFOLD-SELECTION.md).
4. For every element-by-shell, `review.json` gives checkable band indices/energy ranges, raw projection features, same-state competition, and inclusion/omission reasons. Target, reference, and frozen band groups may differ; do not copy them to pass counts. Text must agree with `reference_upper_status`; a reference reaching the final computed band is right-censored. Explain target protection, unwanted-state avoidance, and empty-band sufficiency. Repeated generic orbital explanations are inadequate.

## Proposal and audit

After a draft, read `conduction_review.py questions`, address the strongest selected projection beyond outer and the band-edge/first positive candidate above frozen, then re-propose/check. `select` rejects missing answers, out-of-interval witnesses, or ambiguous deferrals without exploratory intent. This verifies evidence coordinates and review format, not physical role accuracy.

Write the [selection-contract](MANIFOLD-SELECTION.md) review and run `propose`, then use the read-only diagnostics below. They inspect adopted frozen states, omitted-channel competition, repeated reasons, contradictory status, and path gates. The 0.10 line is an absolute-projection negative indicator, not an orbital-deletion law or automatic boundary change. A clean report is not expansion evidence or fit acceptance.

```bash
python3 tools/review_diagnostics.py \
  --cache /input/band_charge.npz \
  --inspection /proposal/inspection.json --review /proposal/review.json \
  --selection /proposal/selection.json --out-dir /new/diagnostics
```

Review diagnostics/figures before sealing initial, and again before selecting first try. Review edits require a fresh proposal/diagnostics. Preserve both stage figures, input/tool identity, reasons, and warnings without overwrite. Numerical failure prevents submission. Explicit exploratory trials are available for scientifically uncertain but numerically feasible choices. Actual-interface mesh checks occur after interface generation; band-path counts cannot be reported as interface-mesh validation.

## Evaluation record

Record actor, model/prompt, retrieval state, input SHA256, basis/exclusions/windows, selection and fit figures, diagnostics, submission/compute times and convergence. For an explicitly requested cost evaluation, also record provider-native token usage; otherwise omit model billing. Missing requested usage is `null`, not a text-length estimate. Count technical restarts and scientific choices separately. Report each success level only after its checks: `num_iter=0` execution, convergence, complete products, and the actual fit on the predeclared region are distinct properties.
