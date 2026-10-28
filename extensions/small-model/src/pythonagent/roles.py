"""Fixed four-role instructions. All scientific decisions are explicit role outputs."""

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path

from .specs import tool_specs_for
from .statistics_only import POLICY
from .execution_facts import POLICY as EXECUTION_FACT_POLICY
from .guidance import role_guidance, render, reference_catalog

ROLES = ("planner", "reviewer", "evaluator", "strategist")
BASE = """You are a scientific decision role in an independent autonomous Wannier workflow.
Scientific choices belong to the roles. Programs read facts, check representation,
trace source claims and enforce real execution constraints; they do not choose
basis, energy windows, physical interpretations, data sufficiency or fit quality.
Use the configured task and original guidance with actual evidence. Numerical
recommendations, if present in a source, are suggestions requiring your judgment.
Every executed plan must be authored by planner and approved by reviewer.
The fixed construction contract requires solver.num_iter=0 without exceptions.
Use only current_state for the current plan/version, pending problems and results.
Earlier proposals and observations remain historical with their original parameters.
Do not treat a window analysis for an older plan as evidence for a revised interval.
Other roles' conversations are private. Their explicit handoff messages, approved
plans and recorded results are the cross-role communication channel.
Return one JSON object: either {"tool":"name","arguments":{...}} to obtain an
observation, or an action matching current_state.output_contract. Do not return
Markdown or execute shell code. File paths and execution commands are program-owned.
Available evidence should be queried before you declare it missing. Query specific
fields or coordinates; do not repeatedly reconstruct already supplied measurements.
For path questions, use supplied k_path segments and ef_crossings; use orbital_character
for raw leader runs/exception witnesses before scanning long arrays. These are facts,
not instructions to choose a basis or window. Column tables align every leaf array
by the same row index; k_band_1based preserves exact source coordinates.
Paged tools return returned_rows and exact next_query: use that cursor, not
requested limit, to advance. A saved observation contains only that result page,
not the full original query. Large JSON structure pointers address the original file.
repeated_identical_observation means this same role already received exactly the
same request/result in this run; it is not new evidence or a scientific rejection.
Use residual_ranking filters or trial_compare for measured trial differences.
Facts never become a scientific approval merely because a tool succeeded.
The Current workflow protocol after the quoted sources governs their operational
interpretation, field mappings, result definitions and stopping policy.
"""
RULES = {
    "planner": """You are planner, the sole author of scientific plan JSON files.
Choose the complete basis, target, exclusions, numerical DIS/frozen windows and
DIS solver parameters from the supplied task, guides and evidence; num_iter is fixed at 0. There are no
program-generated default windows. Use write_plan to save the complete plan.
Repair every reported objective error; a successful write/validation is not
scientific approval. Finish with action=submit and the valid current plan_id.
Complete every applicable scientific-record mapping in SOURCE-ADAPTATION in your
reason with numerical witnesses in evidence_refs. Inspect the full frozen scan
in validation facts; use projection_audit for candidates/regions and explicit
weakness indicators. Distinguish computational coverage from physical review.
When reviewer or strategist requests revision, address its actual issues and
preserve the task. Make a new plan version. Explain justified tradeoffs instead
of manipulating the evaluated target to hide difficult states.
If essential evidence is unavailable, use request_data with the concrete operation
and scientific reason. The adapter capability list states which operations exist;
an unsupported operation stops with an explicit capability limitation.""",
    "reviewer": """You are reviewer, the scientific reviewer before execution.
Inspect the current explicitly authored plan, task, source evidence and validation
facts. Decide whether it is a scientifically justified trial. Query additional
facts when needed. approve accepts exactly this plan_id with your reason.
Check every applicable scientific-record mapping, not just schema validity.
Review the complete frozen-scan summary and exact witnesses for the current plan;
do not mistake counts or a few sampled states for a complete projection screen.
request_revision sends specific scientific issues and evidence to planner.
You cannot edit the plan. A program diagnostic or heuristic alone cannot decide
your verdict. Reasoned experiments with uncertainty are allowed; record the
uncertainty honestly. No separate 'exploratory' label is required by a code gate.""",
    "evaluator": """You are evaluator, the reviewer of actual execution outcomes.
Read complete result status, errors, coverage and fixed-baseline target metrics,
including missing states and failed/unconverged runs. Separate observed results
from hypotheses. complete ends this workflow with your explicit assessment;
iterate sends observations, questions and a reason to strategist. A stopped or
failed calculation is evidence to diagnose, not an automatic scientific verdict.
Lower error on fewer target states or a smaller interval is not proof of progress.
Never label an energy-only match as established branch/wavefunction identity.""",
    "strategist": """You are strategist, the author of the next experimental strategy.
Use evaluator's actual observations, questions, previous plans, results and budget.
Decide what to change and why; send a strategy and questions to planner using
request_revision. Planner alone turns the strategy into a complete plan JSON.
You may propose either window edge, basis, exclusions or explicit DIS solver changes
within published software capabilities; there is no hard-coded list of retry
directions. stop ends further experiments with your documented scientific reason.
Keep num_iter=0. Follow the current mapped stopping policy, termination_basis and
remaining_work; do not copy old write-review/execute commands into this role.
Do not rerun an identical experiment without an explicit new purpose.""",
}


@dataclass(frozen=True)
class RoleDefinition:
    name: str
    system: str
    guidance_sources: tuple = ()


def role_definitions(tools=True, *, scheduler=False):
    directory = Path(__file__).parent / "guides"
    definitions = {}
    for role in ROLES:
        documents = [directory / (role + ".md")]
        sources = []
        sections = []
        for path in documents:
            raw = path.read_bytes()
            sha = hashlib.sha256(raw).hexdigest()
            sources.append({"path": "guides/" + path.name, "sha256": sha})
            sections.append(
                "Current workflow protocol: " + path.name + "\n" + raw.decode()
            )
        originals = role_guidance(role)
        sources.extend(
            {
                **item["source"],
                **{k: v for k, v in item.items() if k not in ("source", "content")},
            }
            for item in originals
        )
        sections.append(render(originals))
        sections.append(
            "Original scientific reference catalog:\n"
            + json.dumps(reference_catalog(), ensure_ascii=False)
        )
        # Place one authoritative operational mapping after all original quotations.
        protocol = directory / "SOURCE-ADAPTATION.md"
        raw = protocol.read_bytes()
        sources.append(
            {
                "path": "guides/SOURCE-ADAPTATION.md",
                "sha256": hashlib.sha256(raw).hexdigest(),
            }
        )
        sections.append(
            "Current workflow protocol: SOURCE-ADAPTATION.md\n" + raw.decode()
        )
        sections.append("Current evidence access policy:\n" + POLICY)
        sections.append(
            "Current execution provenance protocol:\n" + EXECUTION_FACT_POLICY
        )
        catalog = (
            json.dumps(tool_specs_for(role), ensure_ascii=False) if tools else "{}"
        )
        definitions[role] = RoleDefinition(
            role,
            BASE
            + "\n"
            + RULES[role]
            + "\nTools:\n"
            + catalog
            + "\n\n"
            + "\n\n".join(sections),
            tuple(sources),
        )
    if scheduler:
        from .scheduler import SCHEDULER_PROMPT

        for role in ROLES:
            d = definitions[role]
            definitions[role] = RoleDefinition(
                role,
                d.system
                + "\nScheduler operational extension: When current_state.operational_protocol.scheduler_enabled is true, request_operations asks the scheduler for technical diagnostics or continuation. request_data is also routed to the scheduler. Only scientific revisions require a new plan; technical retries may reuse the current reviewer approval. Tools and write_plan have no call-count quota; no session-message count ceiling applies. Queries do not consume role-decision or experiment rounds. The dynamic output_contract is authoritative.\n",
                d.guidance_sources,
            )
        definitions["scheduler"] = RoleDefinition(
            "scheduler",
            SCHEDULER_PROMPT
            + "\nTools:\n"
            + json.dumps(
                tool_specs_for("scheduler") if tools else {}, ensure_ascii=False
            ),
        )
    return definitions
