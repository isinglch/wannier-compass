"""Four explicit role decisions, version-bound approval and deterministic delivery."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import time
import uuid

from .contracts import action_schema_for, parse_answer, schema_errors
from .evidence import Evidence
from .execution import ExecutionConstraint, make_executor, Runner
from .execution_facts import ExecutionFacts
from .journal import Journal
from .llm import LLMClient
from .plans import PlanStore
from .state import RunLock, atomic_json, file_sha, immutable_json
from .tools import ToolBox
from .guidance import retry_guidance
from .plotting import PlotManager
from .results import METRIC_CONTRACT
from .contracts import require_disentanglement_only
from .artifacts import failure_record
from .scheduler import Scheduler, scheduler_options
from .config import without_tool_limits
from .output_recovery import LLMOutputLimitError

DEFAULT_TASK = (
    "Construct and evaluate a Wannier representation of the chemically relevant valence, "
    "Fermi-level crossings when present, and related low conduction supported by source evidence. "
    "Explicitly resolve the physical task and compare all trials on that task, including missing states."
)


class WorkflowStop(RuntimeError):
    def __init__(self, reason, detail):
        super().__init__(detail)
        self.reason = reason


class Workflow:
    def __init__(self, case, runs_root, config, *, llm=None, executor=None):
        config = without_tool_limits(config)
        self.config = dict(config)
        self.scheduler_config = scheduler_options(config.get("scheduler", {}))
        self.scheduler_enabled = self.scheduler_config["enabled"]
        self.scientific_trials = 0
        self.scheduler = None
        self.task = config.get("task", DEFAULT_TASK)
        self.identifier = (
            datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            + "-"
            + uuid.uuid4().hex[:12]
        )
        self.run_dir = Path(runs_root).resolve() / self.identifier
        self.run_dir.mkdir(parents=True, exist_ok=False)
        self.journal = Journal(self.run_dir)
        self.evidence = Evidence(case, self.run_dir)
        self.plotter = PlotManager(self.evidence, self.journal, config.get("plotting"))
        self.plans = PlanStore(
            self.run_dir, self.evidence, self.journal, plotter=self.plotter
        )
        self.tools = ToolBox(self.evidence, self.plans, self.journal)
        self.executor = executor or make_executor(
            config.get("execution", {}), self.journal
        )
        self.llm = llm
        self.max_repairs = config.get("max_repair_attempts", 8)
        self.max_trials = config.get("max_trials", 3)
        self.max_steps = config.get("max_steps", 32)
        for value in (
            self.max_repairs,
            self.max_trials,
            self.max_steps,
        ):
            if type(value) is not int or value < 1:
                raise ValueError("Budgets must be positive integers")
        self.history = []
        self.result = None
        self.baseline = None
        self.started = False
        self.steps = 0
        self.minimum_version = 0

    def context(self, role, handoff):
        return {
            "task": {"objective": self.task},
            "input": self.evidence.overview(),
            "visualization": self.plotter.context(),
            "execution_capabilities": self.executor.capabilities(),
            "assessment_policy": {
                "construction": "disentanglement-only; solver.num_iter must be 0",
                "numeric_fit_threshold": None,
                "precision": "Assess any explicit precision objective in task.objective; otherwise report measured errors and coverage without inventing numerical acceptance. A missing threshold is not missing DFT evidence.",
                "termination": "Use complete/stop with termination_basis and remaining_work. Poor fit/nonconvergence alone do not justify stopping while a supported trial and budget remain. Ending is not certified acceptance.",
            },
            "native_metric_contract": METRIC_CONTRACT
            if self.executor.capabilities().get("adapter") == "vasp_wannier"
            else None,
            "current_plan": self.plans.context(),
            "current_result": self.result,
            "handoff": handoff,
            "stage_guidance": retry_guidance()
            if self.history and role in ("planner", "reviewer")
            else [],
            "history": self.history[-2:],
            "earlier_trial_records": [r["record_path"] for r in self.history[:-2]],
            "baseline_target": None
            if self.baseline is None
            else self.baseline["target_band_ranges_1based"],
            "budget": {
                "max_trials": self.max_trials,
                "trial_attempts": len(self.history),
                "remaining_trials": max(
                    0,
                    self.max_trials
                    - (
                        self.scientific_trials
                        if self.scheduler_enabled
                        else len(self.history)
                    ),
                ),
                "scientific_trials": self.scientific_trials
                if self.scheduler_enabled
                else len(self.history),
                "remaining_role_steps": max(0, self.max_steps - self.steps),
            },
            "output_contract": self.action_schema(role),
            "operational_protocol": {
                "scheduler_enabled": self.scheduler_enabled,
                "request_operations": "Ask scheduler for diagnostics or continuation; no scientific plan change is required for a technical retry.",
                "tool_policy": "No read-tool, write-plan or session-message count limit. Tools do not consume role-decision or experiment rounds.",
            }
            if self.scheduler_enabled
            else None,
        }

    def action_schema(self, role):
        return action_schema_for(role, scheduler=self.scheduler_enabled)

    def decide(self, role, handoff, *, continuation=None):
        if self.steps >= self.max_steps:
            raise WorkflowStop(
                "role_budget_exhausted", "Configured role handoff budget reached"
            )
        self.steps += 1
        invocation = uuid.uuid4().hex
        repairs = 0
        pending = {
            "instruction": {
                "task": "Complete this role decision for the current handoff.",
                "active_handoff": handoff,
            },
            "begin_decision": invocation,
        }
        if continuation:
            pending["continuation"] = continuation["pending"]
            pending["instruction"]["task"] = (
                "Continue the suspended decision using the original handoff and preserved session."
            )
        while True:
            facts = self.context(role, handoff)
            text, meta = self.llm.sessions.ask(role, pending, facts=facts)
            try:
                answer = parse_answer(text)
                if "tool" in answer:
                    if set(answer) != {"tool", "arguments"} or not isinstance(
                        answer["arguments"], dict
                    ):
                        raise ValueError(
                            "Tool request must contain exactly tool and arguments"
                        )
                    result = self.tools.call(role, answer["tool"], answer["arguments"])
                    pending = {
                        "observation": {
                            "request": answer,
                            "result": result,
                        }
                    }
                    continue
                errors = schema_errors(answer, self.action_schema(role))
                if errors:
                    raise ValueError(json.dumps(errors, ensure_ascii=False))
                if (
                    role == "evaluator"
                    and answer["action"] == "complete"
                    and answer["termination_basis"] == "objective_satisfied"
                ):
                    result = self.result or {}
                    dis = result.get("disentanglement") or {}
                    if (
                        answer["remaining_work"]
                        or result.get("status")
                        in ("execution_failed", "input_constraint_failed")
                        or (dis and dis.get("fit_readiness") != "available")
                    ):
                        raise ValueError(
                            "objective_satisfied contradicts remaining work or failed/provisional/unknown execution; iterate or report an explicit blocker/no justified trial"
                        )
                if role in ("planner", "reviewer") and "plan_id" in answer:
                    self.plans.read(answer["plan_id"])
                if (
                    role == "planner"
                    and answer["action"] == "submit"
                    and self.plans.counter <= self.minimum_version
                ):
                    raise ValueError(
                        "This revision requires a newly written plan version; historical approval cannot be reused"
                    )
                self.journal.record(
                    "role_decision",
                    {"role": role, "invocation": invocation, "decision": answer},
                )
                return answer
            except (ValueError, TypeError, KeyError) as error:
                repairs += 1
                self.journal.record(
                    "protocol_rejected",
                    {"role": role, "reason": str(error), "attempt": repairs},
                )
                if repairs >= self.max_repairs:
                    raise WorkflowStop(
                        "protocol_budget_exhausted", role + ": " + str(error)
                    ) from error
                pending = {
                    "validator_notice": {
                        "error": str(error),
                        "applied": False,
                        "instruction": "Correct the protocol or named objective error. Scientific choices remain yours.",
                    },
                }

    def deliver(self, source, target, message):
        if (
            source == "execution"
            and self.result
            and "execution_facts" in self.result
            and "result" not in message
        ):
            message = {**message, "execution_facts": self.result["execution_facts"]}
        record = {
            "from": source,
            "to": target,
            "message": message,
            "plan_id": self.plans.latest,
            "input_revision": self.evidence.revision,
        }
        path = self.run_dir / "handoffs" / (uuid.uuid4().hex + ".json")
        record["record_path"] = "run/" + str(path.relative_to(self.run_dir))
        immutable_json(path, record)
        self.journal.record(
            "handoff", {"path": str(path.relative_to(self.run_dir)), **record}
        )
        return record

    def execute(self):
        plan, path, approval = self.plans.execution_input()
        require_disentanglement_only(plan)
        if self.baseline is None:
            self.baseline = plan
        trial_id = f"trial_{len(self.history) + 1:03d}"
        trial = self.run_dir / "trials" / trial_id
        trial.mkdir(parents=True)
        immutable_json(trial / "approved-plan.json", plan)
        immutable_json(trial / "approval.json", approval)
        copied = trial / "approved-plan.json"
        if file_sha(copied) != approval["document_sha256"]:
            raise ValueError("Execution copy differs from the approved document")
        started = time.monotonic()
        self.journal.record(
            "execution_start", {"trial": trial_id, "approval": approval}
        )
        constraints = None
        recorder = ExecutionFacts(
            trial,
            plan,
            self.evidence,
            self.executor.capabilities().get("adapter", "unknown"),
        )
        previous_recorder = getattr(self.executor, "fact_recorder", None)
        if isinstance(self.executor, Runner):
            self.executor.fact_recorder = recorder
        caught_error = None
        try:
            result = self.executor.execute(
                plan, copied, trial, self.evidence, self.baseline
            )
        except ExecutionConstraint as error:
            caught_error = error
            constraints = error.issues
            validation_path = trial / "actual-input-validation.json"
            if error.facts is not None and not validation_path.exists():
                immutable_json(validation_path, error.facts)
            result = {
                "status": "input_constraint_failed",
                "observations": [],
                "constraint_errors": constraints,
            }
        except (ValueError, RuntimeError, OSError) as error:
            caught_error = error
            failure = failure_record(trial, error, self.run_dir)
            result = {
                "status": "execution_failed",
                "observations": [str(error)],
                "failure_record": failure,
                "record_paths": [failure],
            }
        except BaseException as error:
            recorder.finish(
                "interrupted"
                if isinstance(error, (KeyboardInterrupt, SystemExit))
                else "execution_failed",
                error=error,
            )
            raise
        finally:
            if isinstance(self.executor, Runner):
                self.executor.fact_recorder = previous_recorder
        try:
            self.evidence.ensure_current()
            self.plans.execution_input()
            if file_sha(copied) != approval["document_sha256"]:
                raise ValueError("Execution altered its approved plan copy")
        except BaseException as error:
            recorder.phase("post_execution_binding")
            recorder.finish("execution_failed", error=error)
            raise
        recorder.finish(result["status"], error=caught_error)
        result["execution_facts"] = recorder.summary()
        result = {
            **result,
            "trial_id": trial_id,
            "plan_id": approval["plan_id"],
            "plan_sha256": approval["plan_sha256"],
            "wall_seconds": time.monotonic() - started,
        }
        result["visualization"] = self.plotter.trial(
            plan, trial, execution_status=result["status"]
        )
        immutable_json(trial / "result.json", result)
        self.result = result
        entry = {
            "trial_id": trial_id,
            "plan_id": approval["plan_id"],
            "plan": plan,
            "result": result,
            "record_path": f"run/trials/{trial_id}/result.json",
        }
        self.history.append(entry)
        self.journal.record(
            "execution_finished",
            {
                "trial": trial_id,
                "status": result["status"],
                "seconds": result["wall_seconds"],
            },
        )
        return constraints

    def run(self):
        if self.started:
            raise RuntimeError(
                "A workflow instance runs once; create a new workflow for fresh sessions"
            )
        self.started = True
        outcome = {}
        started = time.monotonic()
        with RunLock(self.run_dir / "workflow.lock"):
            try:
                if self.config.get("execute", False):
                    self.executor.validate()
                self.plotter.dft()
                if self.llm is None:
                    llm_config = {
                        **self.config.get("llm", {}),
                        "role_sessions": True,
                        "scheduler_enabled": self.scheduler_enabled,
                    }
                    self.llm = LLMClient(
                        llm_config, self.journal, task={"objective": self.task}
                    )
                elif isinstance(self.llm, LLMClient):
                    from .sessions import RoleSessions

                    self.llm.journal = self.journal
                    self.llm.sessions = RoleSessions(
                        self.llm,
                        {
                            **self.llm.config,
                            "scheduler_enabled": self.scheduler_enabled,
                        },
                        task={"objective": self.task},
                        tools=True,
                    )
                self.journal.record(
                    "run_start",
                    {
                        "workflow_id": self.identifier,
                        "input_revision": self.evidence.revision,
                    },
                )
                if self.scheduler_enabled:
                    self.scheduler = Scheduler(self)
                    outcome = self.scheduler.run()
                else:
                    role = "planner"
                    handoff = self.deliver(
                        "workflow",
                        "planner",
                        {
                            "task": "Write a complete initial scientific plan from the supplied evidence."
                        },
                    )
                    while True:
                        answer = self.decide(role, handoff)
                        action = answer["action"]
                        if role == "planner":
                            if action == "request_data":
                                outcome = {
                                    "stop": "data_operation_unavailable",
                                    "scientific_decision": answer,
                                    "detail": "Planner requested additional data; this configured execution adapter has no data-preparation operation.",
                                }
                                break
                            handoff = self.deliver(role, "reviewer", answer)
                            role = "reviewer"
                        elif role == "reviewer":
                            if action == "request_revision":
                                self.minimum_version = self.plans.counter
                                handoff = self.deliver(role, "planner", answer)
                                role = "planner"
                                continue
                            if len(self.history) >= self.max_trials:
                                raise WorkflowStop(
                                    "trial_budget_exhausted",
                                    "Configured execution attempt budget reached",
                                )
                            self.plans.approve(
                                role, answer["plan_id"], answer["reason"]
                            )
                            if not self.config.get("execute", False):
                                outcome = {
                                    "stop": "reviewed_plan_ready",
                                    "plan_id": answer["plan_id"],
                                    "detail": "Reviewer approved this version; operator configuration has execution disabled.",
                                }
                                break
                            constraints = self.execute()
                            if constraints:
                                self.minimum_version = self.plans.counter
                                handoff = self.deliver(
                                    "execution",
                                    "planner",
                                    {
                                        "objective_constraint_errors": constraints,
                                        "instruction": "Revise the plan to satisfy actual input constraints; choose the remedy yourself.",
                                    },
                                )
                                role = "planner"
                            else:
                                handoff = self.deliver(
                                    "execution", "evaluator", {"result": self.result}
                                )
                                role = "evaluator"
                        elif role == "evaluator":
                            if action == "complete":
                                outcome = {
                                    "stop": {
                                        "objective_satisfied": "model_complete",
                                        "no_justified_trial": "model_stop",
                                        "data_blocker": "model_data_blocked",
                                    }[answer["termination_basis"]],
                                    "scientific_decision": answer,
                                }
                                break
                            if len(self.history) >= self.max_trials:
                                raise WorkflowStop(
                                    "trial_budget_exhausted",
                                    "Evaluator requested iteration beyond configured budget",
                                )
                            handoff = self.deliver(role, "strategist", answer)
                            role = "strategist"
                        else:
                            if action == "stop":
                                outcome = {
                                    "stop": "model_data_blocked"
                                    if answer["termination_basis"] == "data_blocker"
                                    else "model_stop",
                                    "scientific_decision": answer,
                                }
                                break
                            self.minimum_version = self.plans.counter
                            handoff = self.deliver(role, "planner", answer)
                            role = "planner"
            except WorkflowStop as error:
                outcome = {"stop": error.reason, "detail": str(error)}
            except LLMOutputLimitError as error:
                outcome = {
                    "stop": "model_output_limit",
                    "error_type": type(error).__name__,
                    "role": error.role,
                    "response_attempts": error.attempts,
                    "reason": error.reason,
                    "detail": str(error),
                    "details": error.details,
                }
            except Exception as error:
                # Preserve failures with an operational cause; never invent a scientific verdict.
                outcome = {
                    "stop": "operational_error",
                    "error_type": type(error).__name__,
                    "detail": str(error),
                }
            outcome.update(
                workflow_id=self.identifier,
                run_dir=str(self.run_dir),
                elapsed_seconds=time.monotonic() - started,
                trial_attempts=len(self.history),
                plan_versions=self.plans.counter,
                role_steps=self.steps,
                scheduler={
                    "enabled": self.scheduler_enabled,
                    "decisions": self.scheduler.decisions if self.scheduler else 0,
                    "execution_retries": self.scheduler.retries
                    if self.scheduler
                    else 0,
                },
                scientific_trials=self.scientific_trials
                if self.scheduler_enabled
                else len(self.history),
                numerical_acceptance=None,
                assessment_scope="Model assessment and workflow termination are distinct from program-certified numerical/physical acceptance.",
                visualization=self.plotter.context(),
                plotting_warnings=[
                    r for r in self.plotter.records if r["status"] == "failed"
                ],
            )
            self.journal.record("run_finished", outcome)
            atomic_json(self.run_dir / "final-report.json", outcome)
        return outcome
