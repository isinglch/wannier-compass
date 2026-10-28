"""Current numerical-result semantics, independent of scientific acceptance."""

import re

FIT_STATUSES = {
    "completed",
    "completed_converged",
    "completed_iteration_limit",
    "completed_no_disentanglement_required",
}

METRIC_CONTRACT = {
    "interpolation": "HR plus wsvec at original DFT k coordinates; both spectra relative to the same SCF EF; no fitted shift or resampling. Not a postw90 execution.",
    "target": "Current plan target rows at every supplied path k, without frozen/outer energy filtering.",
    "baseline_target": "Target rows of the first executed plan at every path k; use this fixed population to compare trials.",
    "matching": "Per-k one-to-one minimum absolute energy assignment; no error cutoff, wavefunction or branch tracking.",
    "statistics": "mae_eV and max_abs_eV over matched target pairs only; expected_states, matched_states and missing_states reported separately. RMSE/P95 and frozen/outer aggregates are not supplied by this contract.",
    "acceptance": "No automatic fit acceptance. No numeric threshold is configured by default. Image display margins are not scoring regions.",
}


def disentanglement_state(text, budget, num_bands, num_wann):
    """Read explicit W90 DIS markers and ordered iterations; never infer from exit alone.

    Marker/row conventions are those documented by the bundled MCP
    WANNIER-EXECUTION reference. Absence is unknown unless dimensions establish
    that no disentanglement was required. Unsupported dialects remain unknown.
    """
    iterations = [
        int(m[1])
        for m in re.finditer(r"^\s*(\d+)\s+[^\n]*<--\s*DIS\s*$", text, re.M | re.I)
    ]
    satisfied = bool(
        re.search(r"Disentanglement convergence criteria satisfied", text, re.I)
    )
    unsatisfied = bool(
        re.search(r"Disentanglement convergence criteria not satisfied", text, re.I)
    )
    limit = bool(
        re.search(r"Maximum number of disentanglement iterations reached", text, re.I)
    )
    normal = bool(re.search(r"All done:\s*wannier90 exiting", text, re.I))
    consistent = (
        iterations == list(range(1, len(iterations) + 1)) and len(iterations) <= budget
    )
    if num_bands < num_wann or not consistent or (satisfied and (unsatisfied or limit)):
        state = "inconsistent"
    elif num_bands == num_wann and (iterations or satisfied or unsatisfied or limit):
        state = "inconsistent"
    elif not normal:
        state = "unknown"
    elif satisfied and iterations:
        state = "converged"
    elif limit and unsatisfied and len(iterations) == budget:
        state = "iteration_limit"
    elif num_bands == num_wann and not (
        iterations or satisfied or unsatisfied or limit
    ):
        state = "not_required"
    else:
        state = "unknown"
    return {
        "state": state,
        "converged": True
        if state == "converged"
        else False
        if state == "iteration_limit"
        else None,
        "iterations": len(iterations),
        "budget": budget,
        "normal_termination": normal,
        "iteration_sequence_consistent": consistent,
        "markers": {
            "satisfied": satisfied,
            "not_satisfied": unsatisfied,
            "iteration_limit": limit,
        },
        "fit_readiness": "provisional"
        if state == "iteration_limit"
        else "available"
        if state in ("converged", "not_required")
        else "unavailable",
        "scope": "Execution facts only; not numerical or physical acceptance.",
    }
