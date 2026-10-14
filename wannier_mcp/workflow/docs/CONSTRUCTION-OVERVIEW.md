# Full-band Wannier overview

This is an overview of full-band Wannier construction: what we want to fit, what a good result looks like, and how orbitals and windows help us get there. Read it once in a new context, then use the [first-trial card](FIRST-TRIAL-CARD.md) for commands and checks. For later materials, keep the final reminder and reread as needed. No new thresholds or material answers are supplied.

## What are we trying to build?

Fit the required bands well with the selected orbitals. Usually the priority is near EF in a metal, or near the valence top and conduction bottom in an insulator, together with the main valence and related low conduction bands. Declare which bands and energy range matter; do not assume one fixed range for all materials.

Full-band does not request every deep shell and high empty band in DFT. An orbital mainly forming unwanted deep bands may be omitted, after checking that the other required bands do not need it.

## What should a good result look like?

Compare Wannier and DFT at the same k points. Look for close agreement in the user's important range, wanted valence and low conduction. Check missing/extra bands, wrong crossings and oscillations, as well as average error.

Valence often allows a large frozen region with a very good fit. Low conduction should also fit well. Higher up, selected weight may spread and unwanted bands mix in, worsening the fit. Show where it worsens and whether the original task is affected.

Small frozen error alone does not establish a good model. Even after shrinking frozen, compare trials on the original required bands and energy range. A frozen setting does not prove accurate path interpolation; draw and check it.

Read individual spreads. Unusually large spreads together with band oscillations deserve investigation. Different orbitals can naturally have different spreads; a large spread alone is not failure. Keep `num_iter=0`: no maximal localization is run, and these spreads are not maximally-localized minima.

## What choices do we make next?

The three choices do different jobs:

- **Orbitals / basis:** which orbitals make up the Wannier model, and how many Wannier functions it has.
- **DIS window:** the DFT states available to construct the model, also called its candidate space. Wanted and unwanted states can be included; disentanglement chooses a subspace from them.
- **Frozen window:** states that must be kept during disentanglement. At each k point, every state between `frozen_min` and `frozen_max` is frozen, including rows we did not name as wanted bands.

## How do we choose the orbitals?

Start with the elements' usual valence shells, then inspect the projected bands we need to fit. Compare weights on the same state: is the proposed orbital a major component, or only a small contribution compared with orbitals already selected?

State which wanted valence or conduction bands the orbital helps describe. That is what its role means here.

If an orbital is strong only in deep bands and contributes little to the required bands, try leaving it out. A large gap helps identify the deep group; a small gap or some mixing does not automatically require the extra orbital.

Do not add an orbital just to fit extra deep bands, or omit it just because its strongest peak is far away. It may still matter in the required bands. If unsure about an extra orbital, record why it was retained and test a smaller basis later.

Leaving out a deep orbital does not mean freezing only a narrow region around EF. The remaining valence bands still need to be fitted. Choosing orbitals and setting the frozen window are separate decisions.

## How low should the valence windows go?

Valence is often easier to judge: the projected bands may show an unwanted lower group followed by bands mainly formed by our selected orbitals.

Set **`dis_min` below the lowest point of all required valence bands** across the supplied k path. Do not cut off their lower parts. Unwanted bands also included can remain in DIS for disentanglement.

Choose **`frozen_min` as low as possible without freezing unwanted or weak-target states**. Protect valence with strong selected weight and little unwanted weight. If lowering the edge includes a state dominated by an unselected orbital, or with very little selected weight, raise the edge to leave it outside frozen. Required states below that edge stay in DIS and in the fit comparison.

Some mixing is normal. Check whether frozen forces a state the selected orbitals describe poorly. Mixing alone does not require an extra orbital or abandoning valence protection.

A lowest unwanted group separated by a clear global gap may be excluded; confirm the boundary on the actual interface mesh. Without a safe gap, retain those rows. Look further down for a gap, or exclude nothing.

Neither window requires a gap. Groups can be separated at each k point yet overlap in their overall energy ranges. Put DIS below the wanted bands and frozen above the unwanted states: `dis_min` and `frozen_min` can differ. Explain any clean wanted states left unfrozen elsewhere; keep them in the task.

## How high should the conduction DIS window go?

Conduction is harder: selected weight can spread across many empty bands and mix with other orbitals. DIS needs enough DFT states for Wannier90 to find a good model.

Look how far selected weight extends above the conduction bottom. Use the weight sum and tail plot as a first estimate. Above `dis_max`, are we cutting off important weight, or mostly leaving a diffuse tail or another group outside? This first estimate is not a fixed retry limit.

Entangled bands usually need more DIS states than Wannier functions; barely passing `N_outer >= N_wann` may leave too little choice. A truly isolated set may need no extra states. Check the bands and fit as well as counts.

After choosing `dis_max`, check DFT band sufficiency. The highest computed band may be an artificial cutoff. If DFT ends too close to the required window, add matching NSCF bands; keep the intended DIS instead of lowering it to fit the data.

## How high should conduction frozen go?

If the lowest conduction bands mainly contain selected orbitals and need accurate fitting, try freezing that part. For an insulator, look from the conduction bottom, not just EF. The first frozen window need not stop at the valence top.

Choose a proposed `frozen_max`. Inspect all states it freezes at every supplied path k point, including rows we did not intend to fit. An unwanted high band can bend into the interval at only a few k points.

Check for two problems:

1. An orbital we did not select has more weight than the selected orbitals on that state.
2. Selected weight is very small, even if every other reported weight is also small. Dividing tiny weights to obtain a large percentage does not make this state safe to freeze.

If an added conduction state fails these checks, reject the higher edge or lower `frozen_max` to leave it out. If the problem is at the valence lower edge instead, review `frozen_min`. Raising the upper edge first needs a reason: which additional wanted bands should be frozen, and do they have strong selected weight? Finding no bad state alone does not justify raising it.

Freeze more wanted conduction where possible; higher empty bands are often too mixed or weakly projected. On the interface mesh, frozen counts must not exceed `num_wann`. If they do, inspect the included states and revise the interval; the count alone does not prove a missing orbital.

## What should we change if the fit is poor?

First make sure the calculation and interface files are valid, and check whether disentanglement actually converged. Then locate where the wanted bands disagree with DFT.

**First check frozen.** Are we freezing unwanted states or states with very little selected weight? If the problem is at the lower edge, try raising `frozen_min`. If a high unwanted band comes down into the upper part, try lowering `frozen_max`. Locate the offending states before choosing which edge to move. Do not cut away good low conduction to fix a problem at the valence lower edge. Mixing inside the interval may force a compromise; record which wanted states lose frozen protection.

If many high conduction bands are already frozen, a smaller `frozen_max` can also be a useful trial: hold basis and DIS fixed and test whether releasing those constraints improves the fit. If no low conduction is frozen yet, first inspect why it was left out.

**Then check DIS.** Are we cutting off important selected weight, especially above `dis_max`? Is there too little room for disentanglement? Try a wider DIS window while keeping basis and frozen fixed. If the required DFT states were not computed, add matching NSCF bands before that trial.

**Then check the orbitals.** If important bands near EF or near the conduction bottom are still missing or badly fitted, inspect their projected weights for an orbital the model needs. Add it only with that band evidence. If an extra orbital mainly adds unnecessary deep bands, test a smaller basis. A changed basis needs matching new interface matrices. Prefer the smaller basis when it still fits the same required bands well.

For each trial, write what went wrong, what changed and whether the same required bands improved. Keep worse trials too. Follow the [retry card](WINDOW-RETRIES.md) for commands, records and the allowed trial budget.

Remember: **choose the orbitals needed for the wanted bands; include those bands fully in DIS; freeze as much of them as is safe; check the band plots and spreads; fix windows and missing DFT space before changing the basis.**

Definitions: [Wannier90 methodology](https://wannier90.readthedocs.io/en/latest/user_guide/wannier90/methodology/) and [num_iter](https://wannier90.readthedocs.io/en/latest/user_guide/wannier90/parameters/#integer-num_iter). The practical choices above follow this workflow's stated purpose.
