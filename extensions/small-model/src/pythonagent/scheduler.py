"""Operational agent and bounded event dispatcher. Scientific decisions stay private."""

from .contracts import TEXT, obj, parse_answer, schema_errors
from .specs import tool_specs_for


SCHEDULER_SCHEMA = {
    "oneOf": [
        obj({"action": {"const": "execute"}, "plan_id": TEXT, "reason": TEXT}),
        obj(
            {
                "action": {"const": "retry_execution"},
                "plan_id": TEXT,
                "reason": TEXT,
                "diagnosis_refs": {"type": "array", "items": TEXT, "minItems": 1},
            }
        ),
        obj(
            {
                "action": {"const": "resume_role"},
                "role": {"enum": ["planner", "reviewer", "evaluator", "strategist"]},
                "instruction": TEXT,
                "reason": TEXT,
            }
        ),
        obj({"action": {"const": "deliver_result"}, "reason": TEXT}),
        obj(
            {
                "action": {"const": "stop"},
                "reason": TEXT,
                "remaining_work": {"type": "array", "items": TEXT},
            }
        ),
    ]
}

SCHEDULER_PROMPT = """You are the operational scheduler of an independent workflow.
Four scientific roles own all science: planner writes full plans; reviewer approves
the exact current version or returns scientific issues; evaluator assesses actual
results; strategist proposes the next scientific experiment. You never decide
basis, windows, fit quality or scientific sufficiency, and cannot write/approve
plans, edit files/configuration, execute shell commands or override hard budgets.
The host forwards addressed scientific messages verbatim without asking you to
summarize them. Your context contains event metadata and record paths, not their
complete private conversations or the scientific experience library.
Tools are read-only. Use list_records and the supplied failure_record to find
actual logs; use read_text/read_record to read them. Missing files, denied paths,
and missing scientific data are distinct. An execution error is not a request
to invent another scientific plan. Investigate before requesting a retry.
execute requests host execution of the exact approved plan_id. retry_execution
requires the same still-approved failed plan, a concrete reason why another run
can help, and diagnosis_refs that you actually read from that failed trial.
Distinguish the observed error signature, a suspected cause and a verified cause.
A stack trace alone does not establish a specific software defect. Do not claim
reproducibility unless you inspected evidence from multiple attempts. Describe
unsupported causes as hypotheses. Base recovery on evidence and available operations.
When only runtime mechanics change and source/plan bindings remain valid, preserve
the existing scientific approval; a technical retry does not itself need a new plan.
deliver_result passes an adapter-returned assessment result to evaluator. A caught
operational exception identified by failure_record has no such result: diagnose
it, request a supported same-plan retry or stop with the precise blocker. Never
send it through scientific revision to manufacture a technical repair. Interpret
runtime artifacts within the published operation contract; interpreting scientific
plan content belongs to the scientific roles. You cannot pronounce fit quality.
resume_role continues a role after an operational request using its existing session
and the complete original handoff. There is no tool-call or plan-write count quota,
and no session-message count ceiling. Do not allocate extra tool rounds or suspend
a role because of query counts. Role decisions and experiments retain their own limits.
If a role requests an unsupported operation, first check whether it can be met by
existing read capabilities or role continuation. You cannot create new operations
or promise a repair. stop records the exact operational blocker and remaining work.
Do not repeatedly perform an unchanged failed operation. No scientific experience
documents are required for your work. Return exactly one JSON object: either
{"tool":"name","arguments":{...}} or an action in current_state.output_contract.
"""


def scheduler_options(raw):
    defaults = {
        "enabled": True,
        "max_decisions": 24,
        "max_execution_retries": 2,
    }
    if not isinstance(raw, dict) or set(raw) - set(defaults):
        raise ValueError("Unknown scheduler configuration or non-object scheduler")
    options = {**defaults, **raw}
    if type(options["enabled"]) is not bool:
        raise ValueError("scheduler.enabled must be boolean")
    for key in defaults.keys() - {"enabled"}:
        if type(options[key]) is not int or options[key] < (
            0 if key == "max_execution_retries" else 1
        ):
            raise ValueError("Invalid scheduler budget: " + key)
    return options


class Scheduler:
    def __init__(self, workflow):
        self.flow = workflow
        self.options = workflow.scheduler_config
        self.decisions = 0
        self.retries = 0
        self.read_paths = set()

    def state(self, event, allowed):
        f = self.flow
        current = f.plans.versions.get(f.plans.latest)
        result = f.result or {}
        return {
            "event": event,
            "current_plan": None
            if current is None
            else {k: current[k] for k in ("plan_id", "plan_sha256", "input_revision")},
            "approval": None
            if not f.plans.approved
            else {
                k: f.plans.approved[k]
                for k in ("plan_id", "plan_sha256", "input_revision", "reviewer")
            },
            "last_execution": {
                k: result.get(k)
                for k in (
                    "trial_id",
                    "plan_id",
                    "status",
                    "observations",
                    "failure_record",
                    "record_paths",
                    "execution_facts",
                )
            },
            "execution_capabilities": f.executor.capabilities(),
            "budget": {
                "remaining_decisions": self.options["max_decisions"] - self.decisions,
                "remaining_execution_retries": self.options["max_execution_retries"]
                - self.retries,
                "remaining_scientific_trials": f.max_trials - f.scientific_trials,
                "remaining_role_steps": f.max_steps - f.steps,
            },
            "allowed_actions": allowed,
            "output_contract": SCHEDULER_SCHEMA,
        }

    def decide(self, event, allowed):
        f = self.flow
        if self.decisions >= self.options["max_decisions"]:
            return {
                "action": "stop",
                "reason": "Scheduler decision budget exhausted",
                "remaining_work": [str(event)],
            }
        self.decisions += 1
        self.read_paths = set()
        pending = {
            "instruction": "Resolve the current operational event.",
            "begin_decision": self.decisions,
        }
        repairs = 0
        while True:
            text, _ = f.llm.sessions.ask(
                "scheduler", pending, facts=self.state(event, allowed)
            )
            try:
                answer = parse_answer(text)
                if "tool" in answer:
                    if (
                        set(answer) != {"tool", "arguments"}
                        or not isinstance(answer["arguments"], dict)
                        or answer["tool"] not in tool_specs_for("scheduler")
                    ):
                        raise ValueError("Use a published read-only scheduler tool")
                    result = f.tools.call(
                        "scheduler", answer["tool"], answer["arguments"]
                    )
                    path = answer["arguments"].get("path")
                    if answer["tool"] == "execution_facts":
                        path = result.get("source", {}).get("path")
                    if (
                        path
                        and not result.get("tool_error")
                        and not result.get("observation_too_large")
                    ):
                        self.read_paths.add(path)
                    pending = {
                        "observation": {
                            "request": answer,
                            "result": result,
                        }
                    }
                    continue
                errors = schema_errors(answer, SCHEDULER_SCHEMA)
                if errors:
                    raise ValueError(str(errors))
                if answer["action"] not in allowed:
                    raise ValueError(
                        "Action is not available for this event: " + answer["action"]
                    )
                self.validate(answer, event)
                f.journal.record(
                    "scheduler_decision", {"event": event, "decision": answer}
                )
                return answer
            except (ValueError, TypeError, KeyError) as error:
                repairs += 1
                f.journal.record(
                    "scheduler_rejected", {"reason": str(error), "attempt": repairs}
                )
                if repairs >= f.max_repairs:
                    return {
                        "action": "stop",
                        "reason": "Scheduler protocol budget exhausted: " + str(error),
                        "remaining_work": [str(event)],
                    }
                pending = {"validator_notice": {"error": str(error), "applied": False}}

    def validate(self, answer, event):
        f = self.flow
        action = answer["action"]
        if action in ("execute", "retry_execution"):
            if not f.config.get("execute", False):
                raise ValueError("Execution is disabled by operator configuration")
            _, _, approval = f.plans.execution_input()
            if answer["plan_id"] != approval["plan_id"]:
                raise ValueError(
                    "Execution must use exactly the current approved plan_id"
                )
            if action == "execute" and f.scientific_trials >= f.max_trials:
                raise ValueError("Scientific trial budget exhausted")
            if action == "retry_execution":
                if self.retries >= self.options["max_execution_retries"]:
                    raise ValueError("Technical execution retry budget exhausted")
                result = f.result or {}
                if (
                    result.get("status") != "execution_failed"
                    or result.get("plan_id") != approval["plan_id"]
                ):
                    raise ValueError(
                        "Only the same approved failed execution can be retried"
                    )
                prefix = "run/trials/" + result["trial_id"] + "/"
                for path in answer["diagnosis_refs"]:
                    if not path.startswith(prefix) or path not in self.read_paths:
                        raise ValueError(
                            "Read each diagnosis_ref from the actual failed trial before retrying"
                        )
                    f.evidence.resolve(path)
        elif action == "resume_role":
            if answer["role"] != event.get("role"):
                raise ValueError("Only the suspended role can be resumed")
            if f.steps >= f.max_steps:
                raise ValueError("Role handoff budget exhausted")
        elif action == "deliver_result":
            if f.result is None:
                raise ValueError("No actual execution result exists")
            if f.result.get("failure_record"):
                raise ValueError(
                    "A caught operational exception must be resolved operationally; no adapter-returned assessment result exists"
                )

    @staticmethod
    def blocked(answer):
        return {
            "stop": "operational_blocked",
            "detail": answer["reason"],
            "remaining_work": answer["remaining_work"],
            "numerical_acceptance": None,
        }

    def run(self):
        f = self.flow
        role = "planner"
        handoff = f.deliver(
            "workflow",
            role,
            {
                "task": "Write a complete initial scientific plan from supplied evidence."
            },
        )
        continuation = None
        while True:
            answer = f.decide(role, handoff, continuation=continuation)
            continuation = None
            action = answer["action"]
            if action in ("request_data", "request_operations"):
                record = f.deliver(role, "scheduler", answer)
                event = {
                    "kind": "role_request",
                    "role": role,
                    "request": answer["request"],
                    "reason": answer["reason"],
                    "handoff_path": record["record_path"],
                }
                choice = self.decide(event, ["resume_role", "stop"])
                if choice["action"] == "stop":
                    return self.blocked(choice)
                continuation = {
                    "pending": {"instruction": choice["instruction"]},
                }
                handoff = f.deliver(
                    "scheduler",
                    role,
                    {
                        "instruction": choice["instruction"],
                        "original_handoff": handoff,
                        "role_request": answer,
                    },
                )
                continue
            if role == "planner":
                handoff = f.deliver(role, "reviewer", answer)
                role = "reviewer"
            elif role == "reviewer":
                if action == "request_revision":
                    f.minimum_version = f.plans.counter
                    handoff = f.deliver(role, "planner", answer)
                    role = "planner"
                    continue
                f.plans.approve(role, answer["plan_id"], answer["reason"])
                if not f.config.get("execute", False):
                    return {"stop": "reviewed_plan_ready", "plan_id": answer["plan_id"]}
                if f.scientific_trials >= f.max_trials:
                    return {
                        "stop": "trial_budget_exhausted",
                        "detail": "Scientific trial budget exhausted",
                    }
                choice = self.decide(
                    {"kind": "approved_plan_ready", "plan_id": answer["plan_id"]},
                    ["execute", "stop"],
                )
                if choice["action"] == "stop":
                    return self.blocked(choice)
                while True:
                    if choice["action"] == "retry_execution":
                        self.retries += 1
                    else:
                        f.scientific_trials += 1
                    constraints = f.execute()
                    if constraints:
                        f.minimum_version = f.plans.counter
                        handoff = f.deliver(
                            "execution",
                            "planner",
                            {
                                "objective_constraint_errors": constraints,
                                "instruction": "Address the actual input constraints; scientific remedy is yours.",
                            },
                        )
                        role = "planner"
                        break
                    if f.result["status"] == "execution_failed":
                        allowed = ["retry_execution", "stop"]
                        if not f.result.get("failure_record"):
                            allowed.insert(1, "deliver_result")
                        choice = self.decide(
                            {
                                "kind": "execution_failed",
                                "trial_id": f.result["trial_id"],
                            },
                            allowed,
                        )
                        if choice["action"] == "stop":
                            return self.blocked(choice)
                        if choice["action"] == "retry_execution":
                            continue
                    handoff = f.deliver("execution", "evaluator", {"result": f.result})
                    role = "evaluator"
                    break
            elif role == "evaluator":
                if action == "complete":
                    return {
                        "stop": {
                            "objective_satisfied": "model_complete",
                            "no_justified_trial": "model_stop",
                            "data_blocker": "model_data_blocked",
                        }[answer["termination_basis"]],
                        "scientific_decision": answer,
                    }
                if f.scientific_trials >= f.max_trials:
                    return {
                        "stop": "trial_budget_exhausted",
                        "detail": "Evaluator requested another scientific trial",
                    }
                handoff = f.deliver(role, "strategist", answer)
                role = "strategist"
            else:
                if action == "stop":
                    return {
                        "stop": "model_data_blocked"
                        if answer["termination_basis"] == "data_blocker"
                        else "model_stop",
                        "scientific_decision": answer,
                    }
                f.minimum_version = f.plans.counter
                handoff = f.deliver(role, "planner", answer)
                role = "planner"
