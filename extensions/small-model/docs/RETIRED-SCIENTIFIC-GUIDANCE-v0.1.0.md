# Scientific reasoning and evidence

This document guides scientific judgment by the four roles. Its examples and
starting ideas are not code-enforced criteria. Make a reasoned decision from the
actual material, task and available evidence; record uncertainty and tradeoffs.

## Define and preserve the physical task

Identify chemically relevant valence, Fermi-level crossings when present, and
related low conduction from energies, occupations and same-state projections.
Distinguish the evaluated target from the broader outer/DIS space and the
reference used for inspecting projection tails. A smaller frozen interval does
not narrow the task. Compare trials on the original evaluated target as well as
their declared targets; never hide unmatched states or claim progress by dropping
difficult states. If a target interpretation changes, explain and review it.

## Basis and raw projections

The source provides element-by-angular-shell charge projections. s/p/d/f do not
identify radial principal quantum number or an independent Wannier orbital.
Atomic-sphere weights need not sum to one and do not cover interstitial space.
Use original values; absent columns mean unavailable evidence, not zero weight.
Compare competing channels on the same (k,band) state. Mean character can hide
local role exchange. Inspect extrema, Fermi crossings and local counterexamples.

Chemical block-shell expectations can motivate inspection, but do not mandate a
basis. A deep maximum, nonzero residual, filled shell or secondary hybridization
alone cannot establish inclusion or omission. Review both risks on task states.
Empty/diffuse conduction can be relevant. A channel can contribute to both deep
and target states; basis omission and source-band exclusion are separate choices.
The current cache adapter represents complete angular shells over matching ions;
its dimension is arithmetic, not scientific endorsement of that representation.

## Four independent window edges

Choose explicit finite numerical windows relative to the supplied SCF Fermi zero.
The outer/DIS window supplies states for disentanglement, and can include unwanted
background. Frozen states are forced protection. Review the actual scalar energy
mask on all retained bands, not only a list of band indices: one band may cross
a boundary at different k points, and another band may overlap the same energy.

Choose dis_min from required lower target states; choose frozen_min separately
from mixing and unwanted lower character. An overlap can prevent a single scalar
interval from protecting every target state cleanly. A deep-band omission does
not automatically request a shallow frozen interval or removal of an orbital.

Choose dis_max from the required upper target and disentanglement space. Tail and
cumulative raw-weight diagnostics depend on the reviewed reference and sampling.
A 70% weight marker or observed decay can be a starting idea, not a universal
window rule. Adding high empty bands changes that reference distribution.

For frozen_max inspect Fermi crossings, low-conduction task roles, competing
branches and what remains unprotected. EF or CBM plus a small offset can be an
initial candidate if appropriate, not a mandatory boundary. The presence of EF
inside frozen alone does not establish a good model. A sampled path gap alone
does not establish insulation throughout the BZ. Explain protection and deferral
with actual projections and the task, including unresolved branch connectivity.

Weak selected projection or stronger competing projection is evidence to inspect.
No fixed raw-weight number or channel comparison automatically vetoes a trial,
adds an orbital or decides a window edge. Reasoned uncertain trials are allowed
after reviewer approval and objective execution checks.

## Exclusions, sampling and data sufficiency

Global gap = min_k E_next - max_k E_previous; minimum direct gap =
min_k(E_next-E_previous). They answer different questions. Neither alone proves
non-target character. Exclusions require a scientific rationale and an executable
index mapping; no universal occupation tolerance, gap threshold or shell capacity
test is imposed by this workflow. The native adapter supports a contiguous source
band prefix only; a different mapping requires a registered capable adapter.

For actual execution require N_frozen <= N_wann <= N_outer on the actual input
grid. Path samples are provisional evidence. Matrix dimensions, ordering, energy
reference and input identities must match. A top margin reports available source
coverage. It does not itself decide whether the physical task needs more bands.
Reviewer/planner choose a justified window revision or additional data request.
If requested operations are unsupported, that is a capability limit to report.

## Results and iteration

Record normal completion and actual solver convergence independently of fit
quality. Disentanglement iterations and maximal-localization iterations are
different controls; planner explicitly chooses the supported solver parameters.
Report errors with interval, coverage, missing target states and source revision.
Energy-only minimum-cost matching is not branch, wavefunction, symmetry or
off-grid certification. No universal MAE acceptance threshold is supplied.
Use failures and unconverged output as observations. Strategist should propose
testable changes grounded in the previous actual plan and its result, and compare
both gains and regressions. A changed basis/exclusion mapping needs matching
matrices. Reuse requires identified compatible inputs, never just a shared folder.
