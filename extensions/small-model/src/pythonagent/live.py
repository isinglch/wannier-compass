"""Public observation events. Never publish model prompts or provider reasoning."""

ROLES = {"planner", "reviewer", "evaluator", "strategist", "scheduler"}
KINDS = {
    "run_start",
    "run_finished",
    "tool",
    "role_decision",
    "scheduler_decision",
    "protocol_rejected",
    "scheduler_rejected",
    "handoff",
    "execution_start",
    "execution_finished",
    "command_start",
    "command_finished",
    "command_interrupted",
    "output_truncated",
    "length_recovery_completed",
    "length_recovery_failed",
    "compression_applied",
    "compression_failed",
    "compression_attempt_rejected",
    "context_summary",
    "session_recovery",
    "transport_attempt_failed",
}


def tool_summary(result):
    """Display metadata only; the original tool result stays in the audit."""
    if not isinstance(result, dict):
        return {"result_type": type(result).__name__}
    summary = {
        k: result[k]
        for k in (
            "tool_error",
            "plan_id",
            "valid",
            "full_record",
            "observation_too_large",
            "total_rows",
            "returned_rows",
            "layout",
            "byte_limited",
            "view",
            "repeated_identical_observation",
            "total_lines",
            "next_offset",
            "offset",
            "pointer",
            "complete",
        )
        if k in result
    }
    summary["result_keys"] = list(result)
    if isinstance(result.get("returned_rows"), int):
        summary["returned_items"] = result["returned_rows"]
    for key in ("value", "rows"):
        if isinstance(result.get(key), list):
            summary["returned_items"] = len(result[key])
            break
    if isinstance(result.get("text"), str):
        summary["text_preview"] = result["text"][:300]
        summary["text_preview_truncated"] = len(result["text"]) > 300
    return summary


def public_event(kind, values, response=None):
    if kind not in ROLES | KINDS:
        return None
    keep = {
        "role",
        "gate",
        "purpose",
        "call_id",
        "session_id",
        "attempt",
        "tool",
        "seconds",
        "reason",
        "decision",
        "from",
        "to",
        "message",
        "trial",
        "label",
        "returncode",
        "status",
        "response_id",
        "response_status",
        "usage",
        "usage_status",
        "context_revision",
        "stop",
        "detail",
        "error_type",
        "remaining_work",
        "termination_basis",
        "role_steps",
        "scientific_trials",
        "trial_attempts",
        "plan_versions",
        "error",
        "retries",
        "http_status",
    }
    event = {"kind": kind, **{k: v for k, v in values.items() if k in keep}}
    if kind in ROLES | {"context_summary"}:
        event["role"] = values.get("gate", kind)
        if response is not None:
            event["text"] = response
    if kind == "scheduler_decision":
        event["role"] = "scheduler"
    if kind == "tool":
        result = values.get("result") or {}
        event["arguments"] = values.get("arguments")
        event["result"] = tool_summary(result)
    return event
