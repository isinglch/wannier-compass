"""Recover truncated output within one logical role call, without applying fragments."""

import copy

from .llm import LLMError


class TruncatedOutput(LLMError):
    """Internal signal: the provider returned an explicitly length-limited response."""

    def __init__(self, meta, details):
        super().__init__("Provider output was truncated")
        self.meta = meta
        self.details = details


class LLMOutputLimitError(LLMError):
    """A bounded recovery failed; no truncated action was committed or dispatched."""

    def __init__(self, role, attempts, details, reason="length_recovery_exhausted"):
        super().__init__(
            f"Model output remained truncated for {role} after {attempts} response(s): {details}"
        )
        self.role, self.attempts, self.details, self.reason = (
            role,
            attempts,
            details,
            reason,
        )


class OutputRecovery:
    def __init__(self, raw):
        defaults = {"enabled": True, "max_retries": 2}
        if not isinstance(raw, dict) or set(raw) - set(defaults):
            raise LLMError(
                "Unknown length_recovery options or non-object configuration"
            )
        self.options = {**defaults, **raw}
        if type(self.options["enabled"]) is not bool:
            raise LLMError("length_recovery.enabled must be boolean")
        if (
            type(self.options["max_retries"]) is not int
            or self.options["max_retries"] < 0
        ):
            raise LLMError("length_recovery.max_retries must be a nonnegative integer")

    @staticmethod
    def is_length(details):
        return isinstance(details, dict) and (
            details.get("finish_reason") == "length"
            or details.get("reason") == "max_output_tokens"
        )

    @staticmethod
    def instruction(original, attempt, details):
        # Keep the original command complete. A generated fragment is neither
        # evidence nor an executed tool call, and is not replayed as prior work.
        message = copy.deepcopy(original)
        notice = {
            "attempt": attempt,
            "provider_details": details,
            "previous_response_applied": False,
            "instruction": (
                "Your previous response ended at the generation length limit and was not applied. "
                "Complete the SAME pending task using the current authoritative state and available evidence. "
                "Return one fresh, complete JSON object matching the active contract, including every required field. "
                "Keep explanations concise without dropping required facts, evidence or plan fields. "
                "Do not continue a JSON suffix, repeat already executed operations, or treat an unfinished draft as a result."
            ),
        }
        if isinstance(message, dict) and "output_length_recovery" not in message:
            message["output_length_recovery"] = notice
            return message
        return {"original_instruction": message, "output_length_recovery": notice}

    @staticmethod
    def total_usage(records):
        if not all(row.get("usage_status") == "reported" for row in records):
            return None
        result = {}
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            values = [row["usage"].get(key) for row in records]
            result[key] = sum(values) if all(type(v) is int for v in values) else None
        return result
