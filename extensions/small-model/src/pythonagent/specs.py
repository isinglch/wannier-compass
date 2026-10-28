"""Single source for role tool descriptions and argument validation."""

from .contracts import PLAN_SCHEMA, TEXT, obj

INTEGER = {"type": "integer", "minimum": 1}
OFFSET = {"type": "integer", "minimum": 0}
LAYOUT = {"enum": ["columns", "rows"]}
ENERGY_RANGE = {
    "type": "array",
    "items": {"type": "number"},
    "minItems": 2,
    "maxItems": 2,
}
LIMIT = {"type": "integer", "minimum": 1, "maximum": 100}
TOOLS = {
    "list_records": {
        "arguments": obj({"path": TEXT, "offset": OFFSET, "limit": LIMIT}, ["path"]),
        "description": "List actual file/directory paths under a published evidence directory; paginate with next_offset. No guessed filenames are needed.",
    },
    "anchor_table": {
        "arguments": obj({"offset": OFFSET, "limit": LIMIT, "layout": LAYOUT}, []),
        "description": "Per-band energy ranges and raw channel means, local variation and authenticated k-path segments. Default columns layout: each column index denotes the same band row. Follow next_query; no scientific verdict.",
    },
    "orbital_character": {
        "arguments": obj(
            {
                "band": INTEGER,
                "k_lo": INTEGER,
                "k_hi": INTEGER,
                "offset": OFFSET,
                "limit": LIMIT,
            },
            ["band"],
        ),
        "description": "Full-range raw channel means/extrema, exact witnesses and paginated leading-channel runs. Ties/all-zero sets are explicit; runs stop at declared path boundaries. offset/limit page runs, not k samples; aggregate statistics always cover k_lo..k_hi. Follow next_query.",
    },
    "ef_crossings": {
        "arguments": obj(
            {
                "band_lo": INTEGER,
                "band_hi": INTEGER,
                "k_lo": INTEGER,
                "k_hi": INTEGER,
                "offset": OFFSET,
                "limit": LIMIT,
            },
            [],
        ),
        "description": "Sampled EF sign-change brackets and exact-zero samples within authenticated path segments. Endpoints have actual raw leaders; crossing coordinates are explicitly linear estimates, never computed projections. No claim about the full Fermi surface. Follow next_query.",
    },
    "window_diagnostics": {
        "arguments": obj({"plan_id": TEXT}, []),
        "description": "Counts, top margins and complete sampled frozen-projection scan summary for the current plan. Interface-grid counts do not certify path projections. No automatic scientific verdict.",
    },
    "projection_audit": {
        "arguments": obj(
            {
                "plan_id": TEXT,
                "region": {"enum": ["frozen", "outer", "above_outer", "all_retained"]},
                "weak_selected_max": {"type": "number", "minimum": 0},
            },
            ["plan_id"],
        ),
        "description": "Scan every retained sampled state in the specified plan/region (default frozen); return full coverage, selected-weight quantiles, stronger omitted-channel count and exact witnesses. Optional explicit weak_selected_max reports an indicator, not a veto. The complete scan returns summary statistics and bounded exact witnesses, not the bulk /states table. No scan certifies scientific acceptance.",
    },
    "read_record": {
        "arguments": obj(
            {
                "path": TEXT,
                "pointer": {"type": "string"},
                "layout": LAYOUT,
                "offset": OFFSET,
                "limit": LIMIT,
            },
            ["path"],
        ),
        "description": "Read scientific guide JSON, authored plans, trial summaries and statistical observation archives. diagnostics.json exposes only counts, matched_error_metrics and method. Raw per_k/pairs/states tables are unavailable. Pointers address the summary view; large permitted objects return structure pointers. Follow exact next_query.",
    },
    "read_text": {
        "arguments": obj({"path": TEXT, "offset": OFFSET, "limit": LIMIT}, ["path"]),
        "description": "Read scientific Markdown guides, case setup/settings (INCAR, POSCAR, KPOINTS, OSZICAR, CALCULATION.md) or trial execution logs (.wout/.stdout/.stderr) and settings. Bulk raw spectra/projections, OUTCAR, matrices and binary arrays are unavailable.",
    },
    "residual_ranking": {
        "arguments": obj(
            {
                "trial": TEXT,
                "population": {"enum": ["baseline_target", "target"]},
                "top_n": {"type": "integer", "minimum": 1, "maximum": 12},
                "k_lo": INTEGER,
                "k_hi": INTEGER,
                "band_lo": INTEGER,
                "band_hi": INTEGER,
                "energy_range_eV": ENERGY_RANGE,
                "region": {"enum": ["all", "frozen", "outer", "above_outer"]},
            },
            ["trial"],
        ),
        "description": "Filter by k/band/DFT energy/region BEFORE ranking residuals; region uses that trial's approved windows. Whole-population and filtered metrics/missing coverage are separate. baseline_target by default; explicitly select target for the current-plan population. No fallback between populations; energy matching is not branch tracking.",
    },
    "trial_compare": {
        "arguments": obj(
            {
                "left": TEXT,
                "right": TEXT,
                "population": {"enum": ["baseline_target", "target"]},
            },
            ["left", "right"],
        ),
        "description": "Compare two trials in this run on authenticated native spectra: changed plan fields, execution/DIS status, coverage and measured residuals. Default fixed baseline_target. Metric deltas only for identical target and matched-state populations. No best-plan selection, acceptance, energy shift or branch-tracking claim; unavailable for adapters without bound comparison.npz.",
    },
    "execution_facts": {
        "arguments": obj(
            {
                "trial": TEXT,
                "view": {
                    "enum": [
                        "summary",
                        "inputs",
                        "interface",
                        "constraints",
                        "commands",
                    ]
                },
            },
            ["trial"],
        ),
        "description": "Read a trial's source-bound actual execution facts, including failures: original case settings, generated input, observed dimensions, full-scan window counts, phase and command exit status. Default compact summary; other views expose metadata only. Unknown stages stay unknown; no raw state arrays or scientific remedy. Prefer this over treating a historical case INCAR as current output.",
    },
    "write_plan": {
        "arguments": obj({"plan": PLAN_SCHEMA}),
        "description": "Planner only. Save exactly your complete proposal as a new JSON version; receive all objective contract errors and source-linked facts. No default windows are supplied.",
    },
}


def tool_specs_for(role):
    return {
        name: spec
        for name, spec in TOOLS.items()
        if (name != "write_plan" or role == "planner")
        and (
            role != "scheduler"
            or name in ("read_record", "read_text", "list_records", "execution_facts")
        )
    }
