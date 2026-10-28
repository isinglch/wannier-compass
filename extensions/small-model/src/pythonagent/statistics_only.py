"""Role read boundary: summaries and bounded witnesses, no bulk state tables.

Internal computation, validation and plotting retain the complete source data.
"""

from pathlib import Path

POLICY = """Statistics-only evidence policy: band_slice and bulk source/state/spectrum
reads are unavailable. Use anchor_table, ef_crossings, orbital_character,
window_diagnostics, projection_audit, residual_ranking and trial_compare.
Their computational coverage, methods, exceptions and bounded numerical witnesses
remain available. Scientific choices and adequacy judgments remain yours.
read_record permits guides, authored plans, trial summary records and summary
observation archives. diagnostics.json exposes counts, metrics and methods only;
per_k/pairs and projection /states archives are unavailable. read_text permits
scientific guides, case setup files (INCAR/POSCAR/KPOINTS/CALCULATION.md/OSZICAR)
and trial execution logs (.wout/.stdout/.stderr) or input settings. Raw EIGENVAL,
PROCAR, OUTCAR, charge/comparison arrays and Wannier spectrum/matrix files cannot
be read. list_records and source hashes identify files but do not grant access.
No tool-call budget or new scientific acceptance rule is introduced.
"""

CASE_TEXT = {"INCAR", "POSCAR", "KPOINTS", "OSZICAR", "CALCULATION.md"}
PLAN_RECORDS = {"plan.json", "metadata.json", "validation.json", "approval.json"}
TRIAL_RECORDS = {
    "approved-plan.json",
    "approval.json",
    "actual-input-validation.json",
    "disentanglement.json",
    "input_binding.json",
    "result.json",
    "result_binding.json",
    "diagnostics.json",
    "failure.json",
    "execution-facts.json",
}


def location(evidence, supplied):
    target = evidence.resolve(supplied)
    for root, name in (
        (evidence.case, "case"),
        (evidence.run, "run"),
        (Path(__file__).parent / "guides", "guides"),
    ):
        if target.is_relative_to(root.resolve()):
            return target, name, target.relative_to(root.resolve())
    raise ValueError("Statistics-only evidence policy: unrecognized evidence root")


def require_access(evidence, supplied, kind):
    target, root, relative = location(evidence, supplied)
    parts, name = relative.parts, relative.name
    allowed = False
    if root == "guides":
        allowed = target.suffix == (".json" if kind == "record" else ".md")
    elif root == "case":
        allowed = kind == "text" and name in CASE_TEXT
    elif root == "run":
        if kind == "record":
            allowed = (
                len(parts) == 3
                and parts[0] == "plans"
                and name in PLAN_RECORDS
                or len(parts) == 3
                and parts[0] == "trials"
                and name in TRIAL_RECORDS
                or len(parts) == 2
                and parts[0] in ("observations", "handoffs")
                and target.suffix == ".json"
                or len(parts) == 1
                and name == "final-report.json"
            )
        else:
            allowed = (
                len(parts) in (3, 4)
                and parts[0] == "trials"
                and (len(parts) == 3 or parts[2] in ("interface", "wannier"))
                and (
                    name in CASE_TEXT
                    or target.suffix in (".wout", ".stdout", ".stderr", ".win")
                )
            )
    if not allowed:
        raise ValueError(
            "Statistics-only evidence policy: bulk/raw or non-summary file access is disabled. "
            "Use the published statistical tools, bounded witnesses, guides or execution logs."
        )
    return target


def record_view(evidence, supplied, value):
    target = require_access(evidence, supplied, "record")
    omitted = []
    if target.name == "diagnostics.json":
        value = dict(value)
        for population in ("target", "baseline_target"):
            if isinstance(value.get(population), dict):
                original = value[population]
                allowed = ("counts", "matched_error_metrics", "method")
                omitted.extend(
                    "/" + population + "/" + k for k in original if k not in allowed
                )
                value[population] = {k: original[k] for k in allowed if k in original}
    # Projection audit archives are summaries in this variant; deny legacy state
    # archives defensively if a record was introduced externally.
    if (
        target.parent.name == "observations"
        and isinstance(value, dict)
        and "states" in value
    ):
        omitted.append("/states")
        value = {k: v for k, v in value.items() if k != "states"}
    return value, {
        "mode": "statistics_only",
        "omitted_pointers": omitted,
        "scope": "Source hash identifies the original file; this API exposes only its summary view.",
    }
