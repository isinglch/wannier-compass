"""Thin, bounded MCP adapter for the pinned Wannier CLI tools.

Scientific decisions remain in workflow/tools and the review records.  This
module does not infer orbitals, windows, or pass/fail physics from a material
name.  It is intended to run on a machine with access to the DFT case files.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import sys
import uuid

from mcp.server import MCPServer
from wannier_mcp import __version__

WORKFLOW = Path(__file__).resolve().parent / "workflow"
TOOLS = WORKFLOW / "tools"
MAX_INLINE = 8_000
TIMEOUT = 600

_spec = importlib.util.spec_from_file_location("wannier_runtime_adapter", TOOLS / "workflow_runtime.py")
_runtime = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_runtime)

mcp = MCPServer(
    "wannier-compass",
    version=__version__,
    instructions=(
        "Start with workflow_guide. Select basis and windows from the current "
        "projected band path; seal initial and reviewed first-try stages. "
        "Tool checks verify evidence and numerical feasibility, not physical "
        "correctness. Keep num_iter=0 and inspect selection/fit figures."
    ),
)


def _root() -> Path:
    value = os.environ.get("WAN_WORKSPACE_ROOT", "").strip()
    if not value:
        raise ValueError("Set WAN_WORKSPACE_ROOT to the case workspace before using file tools.")
    root = Path(value).expanduser().resolve()
    if not root.is_dir():
        raise ValueError("WAN_WORKSPACE_ROOT must be an existing directory.")
    return root


def _path(value: str, *, existing: bool = False) -> Path:
    root = _root()
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    candidate = candidate.resolve(strict=False)
    if not candidate.is_relative_to(root):
        raise ValueError("Every input and output path must stay within WAN_WORKSPACE_ROOT.")
    if existing and not candidate.exists():
        raise ValueError(f"Missing input: {candidate}")
    return candidate


def _run(tool: str, arguments: list[str], *, brief_keys: tuple[str, ...] = ()) -> dict:
    if tool not in {
        "dft_workflow.py", "review_packet.py", "manifold_selection.py",
        "conduction_review.py", "decision_stages.py", "prepare_wannier_interface.py",
        "wannier_workflow.py", "compare_wannier.py", "review_workflow.py",
        "slurm_runner.py", "usage_accounting.py", "evidence_pipeline.py", "selection_scope_audit.py",
    }:
        raise ValueError("Unknown fixed workflow tool.")
    result, _ = _runtime.execute(tool, arguments, _root(), timeout=TIMEOUT, brief_keys=brief_keys)
    return result


def _require_submit(execute: bool) -> None:
    if execute and os.environ.get("WAN_ALLOW_SUBMIT") != "1":
        raise ValueError("Submission disabled. Set WAN_ALLOW_SUBMIT=1 on the MCP host and request execute=true explicitly.")


def _trial_id(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", value):
        raise ValueError("trial_id must be a simple name of at most 64 letters, digits, '-' or '_'.")
    return value


@mcp.tool()
def workflow_guide() -> dict:
    """Return the physical orientation, decision order and documentation paths."""
    return {
        "version": __version__,
        "documentation_language": "en",
        "workspace": os.environ.get("WAN_WORKSPACE_ROOT"),
        "submission_enabled": os.environ.get("WAN_ALLOW_SUBMIT") == "1",
        "physical_orientation": [
            "Choose the orbitals needed to fit the required valence and low conduction bands. Keep the same required bands and energy range when comparing trials.",
            "DIS contains DFT states the model may use; frozen keeps every state inside its energy interval. Include the complete wanted valence in DIS and lower frozen_min until unwanted or weak-target states would be frozen.",
            "Give conduction enough DIS states. Try freezing low conduction when selected orbitals have strong weight there. At every path k point, check all states inside frozen for unselected-dominant or weak-selected weights; reduce the edge that includes them.",
            "Check DFT/Wannier plots at the same k points, missing or extra bands, oscillations, spreads and convergence. For a poor fit, inspect frozen first, DIS and missing DFT bands next, then the basis.",
        ],
        "sequence": [
            "read construction_overview to understand which bands to fit and how to place windows; then follow decision_card for each choice",
            "load the verified current projected-band data and draw DFT and projected-band figures",
            "choose orbitals; name the wanted bands and tail-inspection bands; set each valence/conduction DIS and frozen edge separately",
            "fill the review form and propose a draft; read scope_audit for wanted valence left below frozen, other states inside frozen and why the upper edge was chosen; revise the choice, answer its current questions and save initial/real_first_try",
            "prepare matching interface inputs; use the configured backend to run them and validate the resulting files",
            "run disentanglement with num_iter=0; check whether it converged and wrote the required files",
            "compare Wannier and DFT at the same k points over all required bands; record fit and physical review separately",
            "if the fit is not accepted, record where it failed, read the retry card and save the next trial within the allowed budget",
        ],
        "runtime_card": str(WORKFLOW / "docs/RUNTIME.md"),
        "construction_overview": str(WORKFLOW / "docs/CONSTRUCTION-OVERVIEW.md"),
        "decision_card": str(WORKFLOW / "docs/FIRST-TRIAL-CARD.md"),
        "retry_card": str(WORKFLOW / "docs/WINDOW-RETRIES.md"),
        "stage_records": str(WORKFLOW / "docs/DECISION-STAGES.md"),
        "interface_contract": str(WORKFLOW / "docs/INTERFACES.md"),
        "optional_slurm_example": str(WORKFLOW / "docs/SLURM-EXAMPLE.md"),
        "deployment": "File-based science interfaces do not require SSH or a scheduler. Current bundled full-run backend is a Slurm reference adapter; external outputs require explicit provenance integration.",
        "usage_accounting": "Optional evaluation utility. Ordinary construction does not call billing tools or require exported model usage, token counts or prices.",
        "documentation_policy": str(WORKFLOW / "docs/DOCUMENTATION.md"),
        "human_translation_root": str(WORKFLOW / "docs/zh-CN"),
        "skill": str(WORKFLOW / "SKILL.md"),
        "boundary": "Numerical checks and energy fit do not establish physical or symmetry acceptance.",
    }


@mcp.tool()
def read_text(path: str, offset: int = 0, limit: int = 10000) -> dict:
    """Read a bounded text slice from workspace records or bundled workflow docs.

    Use a bundled document path returned by workflow_guide, or prefix it with
    `workflow/`, for example `workflow/docs/FIRST-TRIAL-CARD.md`. Other paths stay inside
    WAN_WORKSPACE_ROOT. Binary DFT and matrix files are not readable here.
    """
    if not 0 <= offset or not 1 <= limit <= 20000:
        raise ValueError("offset must be nonnegative and limit must be 1..20000.")
    supplied = Path(path).expanduser()
    absolute_bundle = supplied.is_absolute() and supplied.resolve().is_relative_to(WORKFLOW.resolve())
    if path.startswith("workflow/") or absolute_bundle:
        candidate = supplied.resolve() if absolute_bundle else (WORKFLOW / path.removeprefix("workflow/")).resolve()
        if not candidate.is_relative_to(WORKFLOW.resolve()):
            raise ValueError("Bundled document path escapes the workflow.")
        if candidate.suffix != ".md":
            raise ValueError("Only bundled Markdown documents are readable.")
    else:
        candidate = _path(path, existing=True)
        if candidate.suffix not in {".json", ".md", ".csv", ".txt", ".log"}:
            raise ValueError("Only text records may be read through MCP.")
    if not candidate.is_file() or candidate.stat().st_size > 10_000_000:
        raise ValueError("Missing or oversized text record.")
    content = candidate.read_text(errors="replace")
    return {"path": str(candidate), "sha256": hashlib.sha256(candidate.read_bytes()).hexdigest(),
            "offset": offset, "next_offset": min(len(content), offset + limit),
            "total_chars": len(content), "text": content[offset:offset + limit]}


@mcp.tool()
def collect_band(case_dir: str) -> dict:
    """Import completed projected VASP band outputs into this case's band-path cache."""
    case = _path(case_dir, existing=True)
    return _run("dft_workflow.py", ["collect", "--case", str(case), "--stage", "band"])


@mcp.tool()
def create_review_packet(cache: str, out_dir: str) -> dict:
    """Make current full-path orbital evidence and plots; returns artifact locations."""
    return _run("review_packet.py", ["--cache", str(_path(cache, existing=True)),
                                      "--out-dir", str(_path(out_dir))])


@mcp.tool()
def inspect_manifold(cache: str, out_json: str) -> dict:
    """Compute baseline orbital and band evidence; no fixed EF interval is imposed."""
    return _run("manifold_selection.py", ["inspect", "--cache", str(_path(cache, existing=True)),
                                           "--out", str(_path(out_json))])


@mcp.tool()
def save_draft_json(path: str, payload: dict, expected_sha256: str = "") -> dict:
    """Write a review/context JSON draft; existing drafts require their current SHA256."""
    target = _path(path)
    if target.name not in {"review.json", "context.json", "retrieval_query.json", "assessment.json"}:
        raise ValueError("Only unsealed review, context, query, or assessment drafts may be written.")
    if any((p / "STAGE.json").exists() for p in target.parents) or "records" in target.parts:
        raise ValueError("Sealed decision records cannot be edited through MCP.")
    target.parent.mkdir(parents=True, exist_ok=True)
    with _runtime.file_lock(target.with_name(target.name + ".edit.lock")):
        if target.exists():
            actual = hashlib.sha256(target.read_bytes()).hexdigest()
            if expected_sha256 != actual:
                raise ValueError("Existing draft changed. Supply its current SHA256 for an intentional replacement.")
        elif expected_sha256:
            raise ValueError("A new draft must not supply expected_sha256.")
        target.parent.mkdir(parents=True, exist_ok=True)
        contents = json.dumps(payload, ensure_ascii=False, allow_nan=False, indent=2) + "\n"
        temp = target.with_name(target.name + ".tmp-" + uuid.uuid4().hex)
        temp.write_text(contents)
        temp.replace(target)
        return {"path": str(target), "sha256": hashlib.sha256(contents.encode()).hexdigest()}



@mcp.tool()
def propose_selection(cache: str, inspection: str, review: str, out_dir: str) -> dict:
    """Generate an auditable selection proposal and initial/first-try figure from a draft."""
    return _run("manifold_selection.py", ["propose", "--cache", str(_path(cache, existing=True)),
                                           "--inspection", str(_path(inspection, existing=True)),
                                           "--review", str(_path(review, existing=True)),
                                           "--out-dir", str(_path(out_dir))])


@mcp.tool()
def audit_selection_scope(cache: str, selection_json: str, review_json: str = "", out_json: str = "") -> dict:
    """Compute unprotected target-valence facts and actual scalar frozen membership.

    Read the decision card to interpret these facts. No basis/window is changed;
    no counterexample does not authorize expansion or establish a passed fit.
    """
    arguments = ["--cache", str(_path(cache, existing=True)),
                 "--selection", str(_path(selection_json, existing=True))]
    if review_json:
        arguments += ["--review", str(_path(review_json, existing=True))]
    if out_json:
        arguments += ["--out", str(_path(out_json))]
    return _run("selection_scope_audit.py", arguments)


@mcp.tool()
def conduction_questions(cache: str, selection_json: str, out_json: str) -> dict:
    """Return candidate-local outer and frozen review questions; does not enlarge windows."""
    return _run("conduction_review.py", ["questions", "--cache", str(_path(cache, existing=True)),
                                          "--selection", str(_path(selection_json, existing=True)),
                                          "--out", str(_path(out_json))],
                brief_keys=("selection_proposal_id", "current_outer_eV", "current_frozen_upper_eV",
                            "num_wann", "min_nouter", "min_disentanglement_headroom",
                            "near_outer_sample", "probe_censored_kpoints",
                            "selected_continuation_concern", "low_conduction_target_scope",
                            "frozen_candidates", "answer_template"))


@mcp.tool()
def check_conduction(cache: str, selection_json: str, review_json: str) -> dict:
    """Check answers against the exact current proposal before sealing a first try."""
    return _run("conduction_review.py", ["check", "--cache", str(_path(cache, existing=True)),
                                          "--selection", str(_path(selection_json, existing=True)),
                                          "--review", str(_path(review_json, existing=True))],
                brief_keys=("status", "selection_proposal_id", "scope"))


@mcp.tool()
def seal_stage(role: str, case_dir: str, review_json: str, context_json: str,
               parent_stage: str = "", trial_id: str = "", reason: str = "", parent_result: str = "") -> dict:
    """Seal an immutable initial or reviewed first-try/retry stage using official gates."""
    if role not in {"initial", "real_first_try", "retry"}:
        raise ValueError("role must be initial, real_first_try, or retry.")
    args = ["initial" if role == "initial" else "select", "--case-dir", str(_path(case_dir, existing=True)),
            "--review", str(_path(review_json, existing=True)),
            "--context", str(_path(context_json, existing=True))]
    if role != "initial":
        if not all((parent_stage, trial_id, reason)):
            raise ValueError("A first try or retry requires parent_stage, trial_id, and reason.")
        args += ["--parent", str(_path(parent_stage, existing=True)),
                 "--trial-id", _trial_id(trial_id), "--reason", reason]
    if parent_result:
        args += ["--parent-result", str(_path(parent_result, existing=True))]
    return _run("decision_stages.py", args,
                brief_keys=("stage_id", "role", "scientific_trial_index", "directory", "status"))


@mcp.tool()
def prepare_interface(band_dir: str, scf_dir: str, cache: str, analysis_json: str,
                      decision_json: str, out_dir: str, shared_source: str = "",
                      dis_num_iter: int = 10000, mp_grid: list[int] | None = None,
                      mesh_kpoints: str = "") -> dict:
    """Prepare basis-dependent VASP matrices; shared_source reuses sealed WAVECAR."""
    if not 1 <= dis_num_iter <= 10000:
        raise ValueError("dis_num_iter must be from 1 to 10000; num_iter remains 0.")
    args = ["prepare", "--band-dir", str(_path(band_dir, existing=True)),
            "--scf-dir", str(_path(scf_dir, existing=True)),
            "--cache", str(_path(cache, existing=True)),
            "--analysis", str(_path(analysis_json, existing=True)),
            "--decision", str(_path(decision_json, existing=True)),
            "--out-dir", str(_path(out_dir)), "--dis-num-iter", str(dis_num_iter)]
    if shared_source:
        args += ["--shared-source", str(_path(shared_source, existing=True))]
    if mp_grid and mesh_kpoints:
        raise ValueError("Specify at most one of mp_grid or mesh_kpoints.")
    if mp_grid:
        if len(mp_grid) != 3 or any(type(n) is not int or n <= 0 for n in mp_grid):
            raise ValueError("mp_grid must be three positive integers.")
        args += ["--mp-grid", *(str(n) for n in mp_grid)]
    if mesh_kpoints:
        args += ["--mesh-kpoints", str(_path(mesh_kpoints, existing=True))]
    return _run("prepare_wannier_interface.py", args,
                brief_keys=("mode", "out_dir", "status", "num_wann", "expected"))


@mcp.tool()
def submit_interface(interface_dir: str, site_profile: str, execute: bool = False) -> dict:
    """Optional Slurm submission for VASP interface; needs WAN_ALLOW_SUBMIT=1 and execute=true."""
    _require_submit(execute)
    args = ["submit-interface", "--trial", str(_path(interface_dir, existing=True)),
            "--site", str(_path(site_profile, existing=True))]
    if execute:
        args.append("--execute")
    return _run("dft_workflow.py", args)


@mcp.tool()
def validate_interface(interface_dir: str, outputs: bool = True) -> dict:
    """Check actual VASP interface matrices, uniform mesh, windows, and band top."""
    args = ["validate", "--trial-dir", str(_path(interface_dir, existing=True))]
    if outputs:
        args.append("--outputs")
    return _run("prepare_wannier_interface.py", args,
                brief_keys=("status", "ready_for_wannier", "interface_files_consistent",
                            "numerical_checks", "problems"))


@mcp.tool()
def prepare_wannier(interface_dir: str, out_dir: str, dis_num_iter: int = 10000,
                    cache: str = "", analysis_json: str = "", decision_json: str = "") -> dict:
    """Prepare disentanglement-only Wannier90 with num_iter=0 and HR/RMN/TB/bands."""
    if not 1 <= dis_num_iter <= 10000:
        raise ValueError("dis_num_iter must be from 1 to 10000.")
    args = ["prepare", "--interface-dir", str(_path(interface_dir, existing=True)),
            "--out-dir", str(_path(out_dir)), "--dis-num-iter", str(dis_num_iter)]
    if any((cache, analysis_json, decision_json)):
        if not all((cache, analysis_json, decision_json)):
            raise ValueError("Window-only reuse requires cache, analysis_json, and decision_json together.")
        args += ["--cache", str(_path(cache, existing=True)),
                 "--analysis", str(_path(analysis_json, existing=True)),
                 "--decision", str(_path(decision_json, existing=True))]
    return _run("wannier_workflow.py", args)


@mcp.tool()
def submit_wannier(directory: str, site_profile: str, execute: bool = False) -> dict:
    """Optional Slurm submission for Wannier90; needs WAN_ALLOW_SUBMIT=1 and execute=true."""
    _require_submit(execute)
    args = ["submit", "--directory", str(_path(directory, existing=True)),
            "--site", str(_path(site_profile, existing=True))]
    if execute:
        args.append("--execute")
    return _run("wannier_workflow.py", args)


@mcp.tool()
def inspect_wannier(directory: str) -> dict:
    """Read solver convergence and required products; this is not physical acceptance."""
    return _run("wannier_workflow.py", ["inspect", "--directory", str(_path(directory, existing=True))],
                brief_keys=("status", "converged", "dis_num_iter", "num_iter", "products", "problems"))


@mcp.tool()
def compare_fit(case_dir: str, trial_id: str, site_profile: str, postw90: str,
                execute: bool = False) -> dict:
    """Optional Slurm submission for exact-k comparison; physical review stays separate."""
    _require_submit(execute)
    executable = Path(postw90).expanduser().resolve()
    if not executable.is_file():
        raise ValueError("postw90 must be an existing executable on the MCP host.")
    args = ["submit", "--case-dir", str(_path(case_dir, existing=True)),
            "--trial-id", _trial_id(trial_id), "--site", str(_path(site_profile, existing=True)),
            "--postw90", str(executable)]
    if execute:
        args.append("--execute")
    return _run("compare_wannier.py", args)


@mcp.tool()
def record_result(case_dir: str, trial_id: str, assessment_json: str) -> dict:
    """Bind an assessment to one scientific trial without promoting numeric fit to acceptance."""
    return _run("decision_stages.py", ["record-result", "--case-dir", str(_path(case_dir, existing=True)),
                                       "--trial-id", _trial_id(trial_id),
                                       "--assessment", str(_path(assessment_json, existing=True))])


@mcp.tool()
def prepare_evidence(cache: str, out_dir: str) -> dict:
    """Portable file-only pipeline: inspection, projected-band plots and unselected review draft."""
    return _run("evidence_pipeline.py", ["--cache", str(_path(cache, existing=True)),
        "--out-dir", str(_path(out_dir)), "--workspace", str(_root())])


@mcp.tool()
def review_template(inspection: str, out_json: str) -> dict:
    """Create an unselected review skeleton; no orbital/window choice is assigned."""
    return _run("review_workflow.py", ["template", "--workspace", str(_root()),
        "--inspection", str(_path(inspection, existing=True)), "--out", str(_path(out_json))])


@mcp.tool()
def patch_draft(path: str, changes: dict, expected_sha256: str) -> dict:
    """Merge a small intentional draft edit, archive the previous version, reject stale hashes."""
    target = _path(path, existing=True)
    if target.name not in {"review.json", "context.json", "assessment.json", "retrieval_query.json"}:
        raise ValueError("Patch only an unsealed named draft.")
    change_file = _runtime.runtime_directory(_root(), "patches") / (uuid.uuid4().hex + ".json")
    _runtime.atomic_json(change_file, changes)
    return _run("review_workflow.py", ["patch", "--workspace", str(_root()),
        "--review", str(target), "--changes", str(change_file), "--expected-sha256", expected_sha256])


@mcp.tool()
def review_requirements(cache: str, inspection: str, review: str) -> dict:
    """Return exact coordinates required by existing gates; physical roles stay with the reviewer."""
    return _run("review_workflow.py", ["requirements", "--workspace", str(_root()),
        "--cache", str(_path(cache, existing=True)), "--inspection", str(_path(inspection, existing=True)),
        "--review", str(_path(review, existing=True))])


@mcp.tool()
def review_proposal(cache: str, inspection: str, review: str, out_dir: str) -> dict:
    """Run the unchanged proposal then current conduction questions in one deterministic call."""
    return _run("review_workflow.py", ["propose", "--workspace", str(_root()),
        "--cache", str(_path(cache, existing=True)), "--inspection", str(_path(inspection, existing=True)),
        "--review", str(_path(review, existing=True)), "--out", str(_path(out_dir))])


@mcp.tool()
def configure_slurm_runner(case_dir: str, site_profile: str, phase: str = "trial",
                          trial_id: str = "trial_001", postw90: str = "", shared_source: str = "",
                          mp_grid: list[int] | None = None, interface_source: str = "", ntasks: int = 1) -> dict:
    """Optional Slurm reference adapter: pin configuration without submitting or choosing physics."""
    args = ["configure", "--case-dir", str(_path(case_dir, existing=True)),
            "--site", str(_path(site_profile, existing=True)), "--phase", phase,
            "--trial-id", _trial_id(trial_id), "--ntasks", str(ntasks)]
    if postw90:
        executable = Path(postw90).expanduser().resolve()
        if not executable.is_file():
            raise ValueError("postw90 must exist on this host.")
        args += ["--postw90", str(executable)]
    for flag, value in (("--shared-source", shared_source), ("--interface-source", interface_source)):
        if value:
            args += [flag, str(_path(value, existing=True))]
    if mp_grid is not None:
        if len(mp_grid) != 3 or any(type(n) is not int or n <= 0 for n in mp_grid):
            raise ValueError("mp_grid requires three positive integers.")
        args += ["--mp-grid", *map(str, mp_grid)]
    return _run("slurm_runner.py", args)


@mcp.tool()
def advance_slurm_runner(case_dir: str, phase: str = "trial", trial_id: str = "trial_001",
                        execute: bool = False, background: bool = False) -> dict:
    """Optional Slurm reference adapter: resume/start; stop at science/reconciliation boundaries."""
    _require_submit(execute)
    args = ["start" if background else "advance", "--case-dir", str(_path(case_dir, existing=True)),
            "--phase", phase, "--trial-id", _trial_id(trial_id)]
    if execute:
        args += ["--execute"]
    return _run("slurm_runner.py", args)


@mcp.tool()
def slurm_runner_status(case_dir: str, phase: str = "trial", trial_id: str = "trial_001") -> dict:
    """Read optional Slurm reference-controller status without model-driven queue polling."""
    return _run("slurm_runner.py", ["status", "--case-dir", str(_path(case_dir, existing=True)),
        "--phase", phase, "--trial-id", _trial_id(trial_id)])


@mcp.tool()
def read_record_summary(path: str, pointer: str = "") -> dict:
    """Read bounded JSON evidence; optional RFC6901 pointer selects an exact field, including /stdout in a CLI log."""
    target = _path(path, existing=True)
    if target.suffix != ".json" or not target.is_file() or target.stat().st_size > 50_000_000:
        raise ValueError("Use a JSON record smaller than 50 MB.")
    parsed = json.loads(target.read_text())
    if pointer:
        if not pointer.startswith('/'):
            raise ValueError("JSON pointer must begin with '/'.")
        selected = parsed
        try:
            for component in pointer[1:].split('/'):
                name = component.replace('~1', '/').replace('~0', '~')
                if isinstance(selected, str):
                    selected = json.loads(selected)  # scientific JSON captured in log stdout
                if isinstance(selected, list):
                    if not re.fullmatch(r'0|[1-9][0-9]*', name):
                        raise ValueError('Invalid JSON array index.')
                    selected = selected[int(name)]
                else:
                    selected = selected[name]
        except (KeyError, IndexError, TypeError, ValueError) as error:
            raise ValueError("Missing or invalid JSON pointer: " + pointer) from error
        parsed = {'path': str(target), 'result': selected, 'scope': 'Exact requested field: ' + pointer}
    return _runtime.envelope({"exit_code": 0, "stdout": "", "stderr": ""}, target, parsed)


@mcp.tool()
def account_native_usage(source: str, provider: str, phase: str, request_id: str = "") -> dict:
    """Optional cost evaluation only: record supplied native usage; ordinary construction omits this tool."""
    args = ["record", "--workspace", str(_root()), "--source", str(_path(source, existing=True)),
            "--provider", provider, "--phase", phase]
    if request_id:
        args += ["--request-id", request_id]
    return _run("usage_accounting.py", args)


@mcp.tool()
def usage_report(pricing: str = "", out_json: str = "", provider: str = "", model: str = "", phase: str = "") -> dict:
    """Optional cost evaluation only: report supplied usage/prices; never estimate tokens from text."""
    args = ["report", "--workspace", str(_root())]
    if pricing:
        args += ["--pricing", str(_path(pricing, existing=True))]
    if out_json:
        args += ["--out", str(_path(out_json))]
    for flag, value in (("--provider", provider), ("--model", model), ("--phase", phase)):
        if value:
            args += [flag, value]
    return _run("usage_accounting.py", args)


def cli_main() -> None:
    # Console execution is a separate process; the CLI's relative imports are
    # resolved from the installed, fixed bundled tools directory.
    sys.path.insert(0, str(TOOLS))
    from workflow_cli import main as run
    run()


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
