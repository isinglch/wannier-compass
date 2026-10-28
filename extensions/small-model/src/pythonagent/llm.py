"""LLM adapter for the standalone agent.

Uses client-managed Chat Completions role histories by default, with optional
Responses chains. Native usage counters are captured verbatim and
journaled; the agent never estimates tokens from text length.

Backends:
  * ``openai``  - configured endpoint (default).
  * ``dry_run`` - no network call; returns a marker so the caller can stop
    cleanly after showing what would have been asked.

There is deliberately no backend that invents scientific answers.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.error
import urllib.request
import uuid

from .streaming import assemble, StreamError, StreamInterrupted

# 408: the iphy proxy's model service timed out under long thinking calls
# (observed on Cu/GLM); transient, same class as 5xx gateway failures.
RETRY_STATUS = {408, 429, 500, 502, 503, 504}


class LLMError(RuntimeError):
    pass


class LLMHTTPError(LLMError):
    def __init__(self, status, detail):
        super().__init__(f"LLM endpoint returned HTTP {status}: {detail}")
        self.status = status
        self.detail = detail


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def output_text(body):
    return "".join(
        part.get("text", "")
        for item in body.get("output", [])
        if item.get("type") == "message"
        for part in item.get("content", [])
        if part.get("type") == "output_text"
    )


def normalized_usage(body):
    usage = dict(body.get("usage") or {})
    usage["prompt_tokens"] = usage.get("input_tokens", usage.get("prompt_tokens", 0))
    usage["completion_tokens"] = usage.get(
        "output_tokens", usage.get("completion_tokens", 0)
    )
    if isinstance(usage.get("input_tokens_details"), dict):
        usage["prompt_tokens_details"] = usage["input_tokens_details"]
    return usage


class LLMClient:
    def __init__(self, config: dict, journal=None, *, task=None, tools=True):
        self.config = dict(config)
        self.backend = (config.get("backend") or "openai").strip().lower()
        if self.backend not in {"openai", "dry_run"}:
            raise LLMError(
                f"Unknown llm.backend {self.backend!r}; use 'openai' or 'dry_run'."
            )
        self.base_url = (config.get("base_url") or "").rstrip("/")
        if self.backend == "openai" and not self.base_url:
            raise LLMError("llm.base_url is required for the openai backend.")
        self.model = config.get("model") or ""
        if self.backend == "openai" and not self.model:
            raise LLMError("llm.model is required for the openai backend.")
        self.api_key = config.get("api_key") or ""
        if not self.api_key and config.get("api_key_env"):
            # A named environment reference keeps secrets out of model
            # profile files while profiles stay shareable.
            self.api_key = os.environ.get(config["api_key_env"], "")
        self.temperature = float(config.get("temperature", 0.2))
        self.max_tokens = int(config.get("max_tokens", 4096))
        self.timeout = float(config.get("timeout_seconds", 300))
        self.max_attempts = int(config.get("max_attempts", 4))
        self.proxy_mode = config.get("proxy_mode", "environment")
        if self.proxy_mode not in ("environment", "direct"):
            raise LLMError("llm.proxy_mode must be environment or direct")
        self.opener = urllib.request.build_opener(
            *([urllib.request.ProxyHandler({})] if self.proxy_mode == "direct" else [])
        )
        # Passthrough for server-side chat-template switches, e.g.
        # {"enable_thinking": false} on vLLM Qwen3 deployments.  Reasoning
        # models can otherwise spend the whole token budget without emitting
        # any final content.
        self.chat_template_kwargs = config.get("chat_template_kwargs")
        # Generic request-body passthrough for provider-specific knobs the
        # protocol does not name: reasoning_effort (OpenAI/DeepSeek style),
        # thinking budgets, top_p, penalty params... Merged last; role
        # sessions protect message history and protocol control fields.
        self.request_extra = config.get("request_extra") or None
        if self.request_extra is not None and not isinstance(self.request_extra, dict):
            raise LLMError(
                "llm.request_extra must be a JSON object of request "
                'body fields, e.g. {"reasoning_effort": "low"}.'
            )
        # Declared provider context limit. Sessions use the last measured
        # usage by default; provider tokenization requires explicit opt-in.
        # Context compaction reserves output capacity and never invents a
        # token count from character length.
        raw_context = config.get("context_tokens")
        self.context_tokens = raw_context if raw_context is not None else None
        if self.context_tokens is not None:
            if (
                not isinstance(self.context_tokens, int)
                or isinstance(self.context_tokens, bool)
                or self.context_tokens <= 0
            ):
                raise LLMError(
                    "llm.context_tokens must be a positive integer "
                    "token limit as declared by the provider."
                )
        self.prompt_guard_fraction = float(config.get("prompt_guard_fraction", 0.8))
        self._prompt_tokens = None
        # Audit trail of empty-content rolls (runaway thinking): what each
        # burned attempt cost, surfaced in the call meta so spirals are
        # visible in the journal instead of silently paying for them.
        self.empty_attempts = []
        self.journal = journal
        self.stream = config.get("stream", False)
        self.stream_include_usage = config.get("stream_include_usage", True)
        if type(self.stream) is not bool or type(self.stream_include_usage) is not bool:
            raise LLMError("llm.stream and stream_include_usage must be booleans")
        self.call_context = {}
        self.last_call_id = None
        self._tokenizer_unavailable = False
        self.api = config.get("api", "chat_completions")
        if self.api not in ("responses", "chat_completions"):
            raise LLMError('llm.api must be "responses" or "chat_completions"')
        self.role_sessions_enabled = config.get("role_sessions", True)
        if type(self.role_sessions_enabled) is not bool:
            raise LLMError("llm.role_sessions must be a boolean")
        if self.api == "responses" and not self.role_sessions_enabled:
            raise LLMError("llm.api=responses requires llm.role_sessions=true")
        from .sessions import RoleSessions

        self.sessions = RoleSessions(self, config, task=task, tools=tools)

    @property
    def run_id(self):
        return self.sessions.run_id

    def session_payload(self, history, incoming, parent, *, max_tokens=None):
        """Encode public messages for the configured API, protecting history."""
        if self.api == "chat_completions":
            payload = {
                "model": self.model,
                "messages": history + incoming,
                "temperature": self.temperature,
                "max_tokens": self.max_tokens,
            }
        else:
            payload = {
                "model": self.model,
                "input": incoming if parent else history + incoming,
                "store": True,
                "temperature": self.temperature,
                "max_output_tokens": self.max_tokens,
            }
            if parent:
                payload["previous_response_id"] = parent
        if self.chat_template_kwargs is not None:
            payload["chat_template_kwargs"] = self.chat_template_kwargs
        if self.request_extra:
            protected = {
                "messages",
                "input",
                "previous_response_id",
                "store",
                "model",
                "instructions",
                "conversation",
                "stream",
                "background",
                "n",
            } & self.request_extra.keys()
            if protected:
                raise LLMError(
                    f"Role session fields cannot be overridden: {sorted(protected)}"
                )
            payload.update(self.request_extra)
        if max_tokens is not None:
            # Auxiliary summary calls have their own budget; provider extras
            # must not silently replace it with the business output limit.
            payload.pop("max_completion_tokens", None)
            payload[
                "max_tokens" if self.api == "chat_completions" else "max_output_tokens"
            ] = max_tokens
        if self.stream:
            payload["stream"] = True
            if self.api == "chat_completions":
                options = payload.get("stream_options") or {}
                if not isinstance(options, dict):
                    raise LLMError("stream_options must be an object")
                payload["stream_options"] = {
                    **options,
                    "include_usage": self.stream_include_usage,
                }
        return payload

    def session_request(self, payload, *, role, purpose="decision"):
        previous = self.call_context
        self.call_context = {
            "role": role,
            "purpose": purpose,
            "session_id": self.sessions.heads[role]["session_id"],
        }
        try:
            return self.send_session(payload)
        finally:
            self.call_context = previous

    @property
    def output_limit(self):
        extra = self.request_extra or {}
        value = (
            extra.get("max_completion_tokens", extra.get("max_tokens", self.max_tokens))
            if self.api == "chat_completions"
            else extra.get("max_output_tokens", self.max_tokens)
        )
        if type(value) is not int or value < 1:
            raise LLMError("effective output token limit must be a positive integer")
        return value

    def count_messages(self, messages, *, mode="measured", url=None):
        """Optionally count with the provider template; never guess from text."""
        if mode == "measured" or (self._tokenizer_unavailable and mode != "required"):
            return None
        endpoint = url or self.base_url.rsplit("/v1", 1)[0] + "/tokenize"
        payload = {
            "model": self.model,
            "messages": messages,
            "add_generation_prompt": True,
        }
        for key in (
            "chat_template",
            "chat_template_kwargs",
            "tools",
            "add_special_tokens",
        ):
            if key in (self.request_extra or {}):
                payload[key] = self.request_extra[key]
        if (
            self.chat_template_kwargs is not None
            and "chat_template_kwargs" not in payload
        ):
            payload["chat_template_kwargs"] = self.chat_template_kwargs
        try:
            body = self.request_json(
                "/tokenize",
                payload,
                url=endpoint,
                attempts=1,
                timeout=min(self.timeout, 20),
            )
            count = body.get("count")
            if type(count) is not int or count < 0:
                raise LLMError("tokenizer returned no integer count")
            # A smaller advertised service limit cannot be ignored.
            limit = body.get("max_model_len")
            if (
                type(limit) is int
                and self.context_tokens
                and limit < self.context_tokens
            ):
                self.context_tokens = limit
            return count
        except LLMError as error:
            if mode == "required":
                raise LLMError(f"exact token counting required: {error}") from error
            self._tokenizer_unavailable = True
            if self.journal is not None:
                self.journal.record(
                    "token_counter_unavailable",
                    {
                        "run_id": self.run_id,
                        "fallback": "previous_provider_usage",
                        "error_type": type(error).__name__,
                        "status": getattr(error, "status", None),
                    },
                )
            return None

    def send_session(self, payload):
        if self.backend == "dry_run":
            raise LLMError("dry_run backend cannot send role-session requests")
        path = "/chat/completions" if self.api == "chat_completions" else "/responses"
        started = time.monotonic()
        try:
            return self.request_json(path, payload)
        finally:
            self.last_request_seconds = time.monotonic() - started

    def session_result(self, body):
        if self.api == "responses":
            return output_text(body), body.get("status"), body.get("incomplete_details")
        choices = body.get("choices")
        choice = choices[0] if isinstance(choices, list) and choices else {}
        choice = choice if isinstance(choice, dict) else {}
        returned = choice.get("message")
        returned = returned if isinstance(returned, dict) else {}
        content = returned.get("content")
        content = content if isinstance(content, str) else ""
        finish = choice.get("finish_reason")
        status = (
            "completed"
            if finish == "stop"
            else "incomplete"
            if finish == "length"
            else "failed"
        )
        return content, status, {"finish_reason": finish}

    def request_json(self, path, payload, *, url=None, attempts=None, timeout=None):
        """Retry transport faults without changing a conversation's parent."""
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        tries = self.max_attempts if attempts is None else attempts
        for attempt in range(1, tries + 1):
            attempt_started = time.monotonic()
            self.last_call_id = uuid.uuid4().hex
            context = {
                **self.call_context,
                "call_id": self.last_call_id,
                "attempt": attempt,
            }
            if self.journal is not None and self.call_context:
                self.journal.publish(
                    "model_start", {**context, "stream": bool(payload.get("stream"))}
                )
            if self.journal is not None:
                self.journal.record(
                    "transport_attempt_start",
                    {
                        "run_id": self.run_id,
                        "attempt": attempt,
                        "endpoint_path": path,
                        "payload_sha256": sha256_text(
                            json.dumps(payload, sort_keys=True)
                        ),
                        "proxy_mode": self.proxy_mode,
                    },
                )
            request = urllib.request.Request(
                url or self.base_url + path,
                data=json.dumps(payload).encode("utf-8"),
                headers=headers,
                method="POST",
            )
            try:
                with self.opener.open(
                    request, timeout=self.timeout if timeout is None else timeout
                ) as response:
                    if payload.get("stream"):
                        if (
                            "text/event-stream"
                            not in response.headers.get("Content-Type", "").lower()
                        ):
                            raise LLMError(
                                "Expected text/event-stream; disable llm.stream for an endpoint without SSE support"
                            )
                        result = self._read_stream(
                            response,
                            context,
                            deadline=attempt_started
                            + (self.timeout if timeout is None else timeout),
                        )
                    else:
                        raw = response.read().decode("utf-8")
                        try:
                            result = json.loads(raw)
                        except ValueError as error:
                            raise LLMError(
                                "LLM endpoint returned a non-JSON response"
                            ) from error
                if not isinstance(result, dict):
                    raise LLMError("LLM endpoint response must be a JSON object")
                if self.journal is not None:
                    self.journal.record(
                        "transport_attempt_completed",
                        {
                            "run_id": self.run_id,
                            "attempt": attempt,
                            "seconds": time.monotonic() - attempt_started,
                            "usage": normalized_usage(result),
                        },
                    )
                return result
            except StreamError as error:
                self._stream_failed(context, attempt_started, type(error).__name__)
                if isinstance(error, StreamInterrupted) and attempt < tries:
                    time.sleep(min(30, 2**attempt))
                    continue
                raise LLMError(str(error)) from error
            except LLMError:
                self._stream_failed(context, attempt_started, "InvalidResponse")
                raise
            except urllib.error.HTTPError as error:
                self._stream_failed(context, attempt_started, "HTTPError")
                detail = error.read().decode(errors="replace")[:2000]
                if self.journal is not None:
                    self.journal.record(
                        "transport_attempt_failed",
                        {
                            "run_id": self.run_id,
                            "attempt": attempt,
                            "http_status": error.code,
                            "seconds": time.monotonic() - attempt_started,
                            "usage": None,
                            "usage_status": "unknown_without_provider_response",
                        },
                    )
                if error.code in RETRY_STATUS and attempt < tries:
                    time.sleep(min(60, 2**attempt))
                    continue
                raise LLMHTTPError(error.code, detail) from error
            except (urllib.error.URLError, TimeoutError, OSError) as error:
                self._stream_failed(context, attempt_started, type(error).__name__)
                if self.journal is not None:
                    self.journal.record(
                        "transport_attempt_failed",
                        {
                            "run_id": self.run_id,
                            "attempt": attempt,
                            "error_type": type(error).__name__,
                            "seconds": time.monotonic() - attempt_started,
                            "usage": None,
                            "usage_status": "unknown_without_provider_response",
                        },
                    )
                if attempt < tries:
                    time.sleep(min(60, 2**attempt))
                    continue
                raise LLMError(
                    f"LLM endpoint unreachable after {attempt} attempts: {error}"
                ) from error

    def _stream_failed(self, context, started, error_type):
        if self.journal is not None and self.call_context:
            self.journal.record(
                "model_request_failed",
                {
                    **context,
                    "error_type": error_type,
                    "seconds": time.monotonic() - started,
                    "usage": None,
                    "usage_status": "unknown_without_provider_response",
                },
            )
            self.journal.publish(
                "model_interrupted",
                {
                    **context,
                    "error_type": error_type,
                    "seconds": time.monotonic() - started,
                    "usage_status": "unknown_without_provider_response",
                },
            )

    def _read_stream(self, response, context, *, deadline=None):
        pending, size = [], 0
        flushed = time.monotonic()
        count = 0
        artifact = None
        if self.journal is not None and hasattr(self.journal, "calls"):
            artifact = self.journal.calls / (context["call_id"] + ".stream.jsonl")
        stream = artifact.open("x", encoding="utf-8") if artifact is not None else None

        def flush():
            nonlocal size, flushed
            if pending and self.journal is not None:
                self.journal.publish(
                    "model_delta", {**context, "text": "".join(pending)}
                )
            pending.clear()
            size, flushed = 0, time.monotonic()

        def delta(value):
            nonlocal size
            pending.append(value)
            size += len(value)
            if size >= 1024 or time.monotonic() - flushed >= 0.1:
                flush()

        def record(value):
            nonlocal count
            count += 1
            if stream is not None:
                stream.write(json.dumps(value, ensure_ascii=False) + "\n")

        try:
            result = assemble(
                response, self.api, delta=delta, record=record, deadline=deadline
            )
            result["_stream_audit"] = {
                "path": str(artifact.relative_to(self.journal.directory))
                if artifact
                else None,
                "event_count": count,
            }
            return result
        finally:
            flush()
            if stream is not None:
                stream.close()
