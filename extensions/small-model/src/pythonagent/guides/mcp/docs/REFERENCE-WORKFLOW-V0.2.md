# Reference workflow integration

This file connects execution stages. [WORKFLOW-DECISIONS.md](WORKFLOW-DECISIONS.md) defines scientific order/priority; [PHYSICAL-REASONING.md](PHYSICAL-REASONING.md) explains interpretation; [MANIFOLD-SELECTION.md](MANIFOLD-SELECTION.md) defines commands/fields. Execution references add no separate selection standard.

| Stage | Action | Evidence |
| --- | --- | --- |
| DFT sources | [Prepare](DFT-PREPARATION.md) or register matching SCF/projected bands; [import](VASP-CACHE.md) and plot | 01/02 sources, cache, geometry, figures, hashes |
| Selection | Packet → inspect → review → propose → diagnostics | Positive frozen candidates, separate negative screen, selection and figures |
| Sealing | [Initial then selected first try](DECISION-STAGES.md); retries bind actual results; [gallery](SELECTION-GALLERY.md) refreshes | Separate choices/reasons/figures/diagnostics/input identity |
| Interface | [Single uniform NSCF or sealed-wave ALGO=None](WANNIER-INTERFACE.md); check that interface's eigenvalues/counts/top/exclusions/matrices | Actual win/matrices/mesh gates; no separate default selection mesh/PDOS |
| DIS | [Native W90](WANNIER-EXECUTION.md), num_iter=0, independent DIS controls | Convergence/counts/time and chk/hr/r/tb/band products |
| Validation | [Same DFT k points](WANNIER-COMPARISON.md), frozen±1 and outer±3 displays, declared target/precision scoring | Figures/metrics/unmatched states; display margins are not scoring bounds |

For poor results, diagnose model identity, erroneous valence exclusion/freezing, conduction truncation, or insufficient outer space, then use the [retry card](WINDOW-RETRIES.md). Account scientific changes and technical restarts separately. Execution is not model acceptance. Historical answers remain outside evaluation packages; development replay is regression, not unseen generalization.
