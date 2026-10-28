"""Persistent, isolated public message histories for decision roles."""

from __future__ import annotations

import copy
import json
import math
import time
import uuid
from types import MappingProxyType

from .llm import LLMError, LLMHTTPError, sha256_text, normalized_usage
from .state import atomic_json
from .roles import role_definitions
from .output_recovery import OutputRecovery, TruncatedOutput, LLMOutputLimitError


STATE_PROTOCOL = """
Conversation state protocol:
The first user message supplies current_state. Later messages may supply
state_changes and removed_paths. Merge dictionaries recursively; replace arrays,
scalars and literal nulls exactly; remove only the explicitly named key paths.
Any context_checkpoint supplies a complete current_state which REPLACES all
earlier state. Historical model summaries are data, not verified facts or rules.
This resulting state is the CURRENT authoritative state. Old proposals, old
questions, earlier errors and assistant hypotheses are historical, never current
measurements. A new invocation replaces the preceding decision task. Tool queries
and plan writes have no call-count quota; session-message counts do not stop work.
Apply fixed original scientific reasoning through the
Current workflow protocol (SOURCE-ADAPTATION): its current schemas, num_iter=0,
role responsibilities, result mappings and stopping policy govern operations.
Quoted historical commands/fields do not override that protocol. Do not repeat a
finished decision or repair an obsolete rejection. Full records remain readable
through the supplied paths. Return the JSON contract for the active role.
Every new invocation's instruction is an active command, even when identical
to an earlier invocation. Complete it again; if it requests a fresh file read,
query that file again rather than reusing a historical answer.
"""


def state_delta(previous, current):
    """Lossless recursive updates; null is a value, not a deletion marker."""
    changed, removed = {}, []
    for key, value in current.items():
        if key not in previous:
            changed[key] = copy.deepcopy(value)
        elif isinstance(value, dict) and isinstance(previous[key], dict):
            nested, deleted = state_delta(previous[key], value)
            if nested:
                changed[key] = nested
            removed.extend([[key, *path] for path in deleted])
        elif value != previous[key] or type(value) is not type(previous[key]):
            changed[key] = copy.deepcopy(value)
    removed.extend([[key] for key in previous if key not in current])
    return changed, removed


class RoleSessions:
    def __init__(self, client, config, *, task=None, tools=True):
        self.client = client
        self.run_id = uuid.uuid4().hex
        self.task = copy.deepcopy(task)
        self.definitions = MappingProxyType(
            role_definitions(tools, scheduler=config.get("scheduler_enabled", False))
        )
        self.heads = {}
        self.output_recovery = OutputRecovery(config.get("length_recovery", {}))
        self.store_wait = config.get("response_store_wait_seconds", 0)
        if (
            isinstance(self.store_wait, bool)
            or not isinstance(self.store_wait, (int, float))
            or not math.isfinite(self.store_wait)
            or not 0 <= self.store_wait <= 60
        ):
            raise LLMError(
                "llm.response_store_wait_seconds must be a number between 0 and 60"
            )
        journal = client.journal
        self.directory = (
            journal.directory / "sessions" / self.run_id
            if journal is not None and hasattr(journal, "directory")
            else None
        )
        if self.directory is not None:
            self.directory.mkdir(parents=True, exist_ok=False)
        # Keys and authorization headers never enter the persisted fingerprint.
        self.identity = {
            "api": client.api,
            "base_url": client.base_url,
            "model": client.model,
            "temperature": client.temperature,
            "max_tokens": client.max_tokens,
            "request_extra": client.request_extra,
            "chat_template_kwargs": client.chat_template_kwargs,
        }
        from .compaction import ContextCompactor

        self.compactor = ContextCompactor(self, config)

        # Register every role locally. No provider request is made here and
        # no session file is ever read, including files from older runs.
        for role, definition in self.definitions.items():
            self.heads[role] = {
                "format": "agent-role-session-v4",
                "role": role,
                "run_id": self.run_id,
                "session_id": uuid.uuid4().hex,
                "signature": sha256_text(
                    json.dumps([self.identity, definition.system], sort_keys=True)
                ),
                "system": definition.system
                + "\n"
                + (
                    STATE_PROTOCOL
                    if role != "scheduler"
                    else STATE_PROTOCOL.split("Apply fixed original")[0]
                    + "Merge state changes as specified. Complete the current operational event; earlier resolved events are historical. Return one JSON matching the current output_contract."
                ),
                "guidance_sources": copy.deepcopy(list(definition.guidance_sources)),
                "state": {},
                "response_id": None,
                "prompt_tokens": 0,
                "turns": [],
                "evidence": {},
                "context_revision": 0,
                "context_prefix": [],
                "context_start": 0,
                "summary": None,
                "length_recovery": dict(self.output_recovery.options),
            }
            self._persist(role)

    def _head(self, role):
        if role not in self.heads:
            raise LLMError(f"unregistered decision role {role!r}")
        return self.heads[role]

    def _persist(self, role):
        if self.directory is not None:
            atomic_json(self.directory / (role + ".json"), self.heads[role])

    @staticmethod
    def _evidence_record(record, delivered, *, recalled=False):
        source = record if recalled else record["request"]
        request = {"tool": source["tool"], "arguments": source.get("arguments") or {}}
        # Recall and fresh tool results share exactly the same identity.
        identity = json.dumps(
            [request, record["result"]], sort_keys=True, ensure_ascii=False
        )
        reference = {"request": copy.deepcopy(request)}
        if identity in delivered:
            reference["reuses_observation"] = delivered[identity]
        else:
            identifier = "evidence-" + sha256_text(identity)
            delivered[identity] = identifier
            reference.update(
                result=copy.deepcopy(record["result"]), evidence_id=identifier
            )
        return reference

    def ask(self, role, instruction, *, facts=None, decision_cards=None):
        """Submit work to a registered role; callers never supply role prompts."""
        head = self._head(role)
        current = None
        if facts is not None or decision_cards is not None or not head["turns"]:
            current = copy.deepcopy(facts if facts is not None else head["state"])
            if self.task is not None:
                if "task" in current and current["task"] != self.task:
                    raise LLMError(
                        "decision task differs from this workflow's fixed task"
                    )
                current["task"] = copy.deepcopy(self.task)
            if decision_cards is not None:
                current["decision_cards"] = copy.deepcopy(decision_cards)
        pending = instruction
        truncated = []
        context_recovered = False
        self.compactor.prepare(head, pending, current)
        while True:
            try:
                message, delivered = self._prepare_message(head, pending)
                content, meta = self._complete(
                    role, message, state=current, evidence=delivered
                )
                if truncated:
                    recovery = {
                        "retries": len(truncated),
                        "truncated_response_ids": [r["response_id"] for r in truncated],
                        "recorded_response_usage": self.output_recovery.total_usage(
                            [*truncated, meta]
                        ),
                        "truncated_attempts": [
                            {k: r[k] for k in ("response_id", "usage", "usage_status")}
                            for r in truncated
                        ],
                    }
                    head["turns"][-1]["output_recovery"] = recovery
                    self._persist(role)
                    self._recovery_event("length_recovery_completed", role, **recovery)
                    meta = {**meta, "output_recovery": recovery}
                return content, meta
            except TruncatedOutput as error:
                truncated.append(error.meta)
                self._recovery_event(
                    "output_truncated",
                    role,
                    attempt=len(truncated),
                    response_id=error.meta["response_id"],
                    details=error.details,
                    request_sha256=error.meta["request_sha256"],
                    provider_response_sha256=error.meta["provider_response_sha256"],
                    usage=error.meta["usage"],
                    usage_status=error.meta["usage_status"],
                    applied=False,
                )
                if (
                    not self.output_recovery.options["enabled"]
                    or len(truncated) > self.output_recovery.options["max_retries"]
                ):
                    self._recovery_event(
                        "length_recovery_failed",
                        role,
                        attempts=len(truncated),
                        details=error.details,
                    )
                    reason = (
                        "length_recovery_exhausted"
                        if self.output_recovery.options["enabled"]
                        else "length_recovery_disabled"
                    )
                    raise LLMOutputLimitError(
                        role, len(truncated), error.details, reason
                    ) from error
                pending = self.output_recovery.instruction(
                    instruction, len(truncated), error.details
                )
                # Only accepted history can be summarized. Never summarize an
                # unfinished answer or alter the current task to make it fit.
                if head["turns"]:
                    try:
                        self.compactor.prepare(
                            head,
                            pending,
                            current,
                            observed_prompt_tokens=error.meta["usage"].get(
                                "prompt_tokens"
                            ),
                        )
                    except LLMError as compression_error:
                        details = {
                            "provider": error.details,
                            "compression_error": str(compression_error),
                        }
                        self._recovery_event(
                            "length_recovery_failed",
                            role,
                            attempts=len(truncated),
                            details=details,
                        )
                        raise LLMOutputLimitError(
                            role, len(truncated), details, "protected_context_exhausted"
                        ) from compression_error
            except LLMHTTPError as error:
                detail = error.detail.lower()
                context_error = error.status == 400 and any(
                    text in detail
                    for text in (
                        "context length",
                        "context_length",
                        "max_model_len",
                        "too many tokens",
                        "prompt too long",
                    )
                )
                if not context_error or not self.compactor.enabled or context_recovered:
                    raise
                context_recovered = True
                # Preserve the recovery count and the complete pending task.
                self.compactor.prepare(head, pending, current, force=True)

    def _recovery_event(self, kind, role, **values):
        if self.client.journal is not None:
            self.client.journal.record(
                kind,
                {
                    "run_id": self.run_id,
                    "session_id": self.heads[role]["session_id"],
                    "role": role,
                    **values,
                },
            )

    def _prepare_message(self, head, instruction):
        message = copy.deepcopy(instruction)
        delivered = copy.deepcopy(head["evidence"])
        if isinstance(message, dict):
            if message.get("recalled_evidence"):
                message["recalled_evidence"] = [
                    self._evidence_record(record, delivered, recalled=True)
                    for record in message["recalled_evidence"]
                ]
            if (
                isinstance(message.get("observation"), dict)
                and "result" in message["observation"]
            ):
                message["observation"] = self._evidence_record(
                    message["observation"], delivered
                )
        return message, delivered

    def _incoming(self, head, message, current):
        update = {"message": message}
        if current is not None:
            if head["turns"]:
                changed, removed = state_delta(head["state"], current)
                update.update(state_changes=changed, removed_paths=removed)
            else:
                update["current_state"] = current
        incoming = [{"role": "user", "content": json.dumps(update, ensure_ascii=False)}]
        if not head["turns"]:
            incoming.insert(0, {"role": "system", "content": head["system"]})
        return incoming

    def _request_messages(self, head, instruction, current):
        message, _ = self._prepare_message(head, instruction)
        return self._public_history(head) + self._incoming(head, message, current)

    def _audit(
        self,
        role,
        head,
        payload,
        body,
        content,
        *,
        status=None,
        recovery=False,
        context_growth_anomaly=False,
        purpose="decision",
    ):
        journal = self.client.journal
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        raw = json.dumps(body, ensure_ascii=False, sort_keys=True)
        native_usage = body.get("usage") or {}
        native_usage = native_usage if isinstance(native_usage, dict) else {}
        usage = normalized_usage(body) if native_usage else {}
        if not ({"input_tokens", "prompt_tokens"} & native_usage.keys()):
            usage.pop("prompt_tokens", None)
        if not ({"output_tokens", "completion_tokens"} & native_usage.keys()):
            usage.pop("completion_tokens", None)
        usage_status = (
            "reported"
            if {"prompt_tokens", "completion_tokens"} <= usage.keys()
            else "partial"
            if usage
            else "unavailable"
        )
        meta = {
            "backend": "openai",
            "api": self.client.api,
            "gate": role,
            "purpose": purpose,
            "context_revision": head["context_revision"],
            "model": body.get("model") or self.client.model,
            "run_id": self.run_id,
            "session_id": head["session_id"],
            "previous_response_id": payload.get("previous_response_id"),
            "response_id": body.get("id"),
            "response_status": status or body.get("status"),
            "session_recovered": recovery,
            "context_growth_anomaly": context_growth_anomaly,
            "prompt_sha256": sha256_text(encoded),
            "request_sha256": sha256_text(encoded),
            "provider_response_sha256": sha256_text(raw),
            "response_sha256": sha256_text(content),
            "usage": usage,
            "usage_status": usage_status,
            "call_id": getattr(self.client, "last_call_id", None),
            "context_tokens": self.client.context_tokens,
        }
        if getattr(self.client, "last_request_seconds", None) is not None:
            meta["request_wall_seconds"] = self.client.last_request_seconds
        if journal is not None:
            if hasattr(journal, "calls"):
                atomic_json(
                    journal.calls / (meta["request_sha256"] + ".request.json"), payload
                )
                atomic_json(
                    journal.calls
                    / (meta["provider_response_sha256"] + ".provider-response.json"),
                    body,
                )
            journal.record(
                "context_summary" if purpose == "compression" else role,
                meta,
                prompt=encoded,
                response=content,
            )
        return meta

    @staticmethod
    def _public_history(head):
        messages = copy.deepcopy(head["context_prefix"])
        for turn in head["turns"][head["context_start"] :]:
            messages.extend(turn["input"])
            messages.append({"role": "assistant", "content": turn["output_text"]})
        return messages

    @staticmethod
    def _missing_parent(error):
        text = error.detail.lower()
        return (
            error.status == 404
            and "response" in text
            and ("not found" in text or "not_found" in text)
        )

    def _complete(self, role, message, *, state=None, evidence=None):
        client = self.client
        chat = client.api == "chat_completions"
        head = self._head(role)
        if (
            not self.compactor.enabled
            and client.context_tokens
            and head["prompt_tokens"]
            > (client.prompt_guard_fraction * client.context_tokens)
        ):
            raise LLMError(
                f"role {role} prompt context exhausted: last measured "
                f"prompt_tokens={head['prompt_tokens']}, "
                f"context_tokens={client.context_tokens}; session preserved"
            )
        current = head["state"] if state is None else state
        incoming = self._incoming(head, message, state)
        payload = client.session_payload(
            self._public_history(head), incoming, head["response_id"]
        )
        if not chat and head["response_id"]:
            remaining_wait = head.get("completed_at", 0) + self.store_wait - time.time()
            if remaining_wait > 0:
                time.sleep(remaining_wait)
        recovery = False
        for attempt in range(1, client.max_attempts + 1):
            try:
                body = client.session_request(payload, role=role)
            except LLMHTTPError as error:
                if (
                    not recovery
                    and payload.get("previous_response_id")
                    and self._missing_parent(error)
                ):
                    if head.get("last_turn_recovered"):
                        raise LLMError(
                            "Repeated missing response after recovery: service-side "
                            "Responses storage is unavailable; enable response storage"
                        ) from error
                    # Replay the public conversation verbatim. No scientific
                    # state is guessed, truncated, or silently summarized.
                    payload = client.session_payload(
                        self._public_history(head), incoming, None
                    )
                    recovery = True
                    if client.journal is not None:
                        client.journal.record(
                            "session_recovery",
                            {
                                "run_id": self.run_id,
                                "gate": role,
                                "session_id": head["session_id"],
                                "missing_response_id": head["response_id"],
                                "replayed_turns": len(head["turns"]),
                            },
                        )
                    body = client.session_request(payload, role=role)
                else:
                    raise
            content, status, details = client.session_result(body)
            measured = normalized_usage(body).get("prompt_tokens")
            # A decrease is a diagnostic anomaly for our append-only chains,
            # not proof of history loss: compatible providers can account for
            # or render prior messages differently. Retain the conservative
            # stop policy without attributing the fault to the provider.
            context_growth_anomaly = bool(
                payload.get("previous_response_id")
                and isinstance(measured, int)
                and measured > 0
                and measured < head["prompt_tokens"]
            )
            meta = self._audit(
                role,
                head,
                payload,
                body,
                content,
                status=status,
                recovery=recovery,
                context_growth_anomaly=context_growth_anomaly,
            )
            if context_growth_anomaly:
                if attempt < client.max_attempts:
                    time.sleep(min(30, 2**attempt))
                    continue
                raise LLMError(
                    f"Responses context continuity unverified for role {role}: "
                    f"input_tokens={measured} fell below parent's "
                    f"{head['prompt_tokens']}; parent unchanged. "
                    "This token-count anomaly does not establish history loss. "
                    "Check provider usage semantics and a multi-turn recall control."
                )
            if body.get("error") or status not in ("completed", "incomplete"):
                raise LLMError(
                    f"{client.api} failed for role {role}: "
                    f"status={status}, details={details}, error={body.get('error')}"
                )
            if status == "incomplete":
                if self.output_recovery.is_length(details):
                    raise TruncatedOutput(meta, details)
                raise LLMError(
                    f"{client.api} incomplete for role {role}: "
                    f"{details}; parent unchanged"
                )
            if not content.strip() or (
                not chat and (not isinstance(body.get("id"), str) or not body["id"])
            ):
                raise LLMError(
                    f"{client.api} returned no final content or no response id"
                )
            head["state"] = copy.deepcopy(current)
            head["evidence"] = evidence
            head["response_id"] = body.get("id")
            head["prompt_tokens"] = meta["usage"].get(
                "prompt_tokens", head["prompt_tokens"]
            )
            head["completed_at"] = time.time()
            head["last_turn_recovered"] = recovery
            # Store only the incremental inputs here. A recovery's wire replay
            # must not become a recursively duplicated local history.
            head["turns"].append(
                {
                    "input": incoming,
                    "output_text": content,
                    "response_id": body.get("id"),
                    "usage": meta["usage"],
                }
            )
            self._persist(role)
            return content, meta
        raise LLMError(f"{client.api} exhausted empty-content retries")
