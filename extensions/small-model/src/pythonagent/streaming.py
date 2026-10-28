"""SSE assembly. Only explicit terminal events produce a usable model response."""

import json
import time


class StreamError(Exception):
    pass


class StreamInterrupted(StreamError):
    pass


def sse_events(response, deadline=None):
    data, event = [], "message"
    for raw in response:
        if deadline is not None and time.monotonic() > deadline:
            raise TimeoutError("Provider stream exceeded the request time budget")
        try:
            line = raw.decode("utf-8").rstrip("\r\n")
        except UnicodeDecodeError as error:
            raise StreamError("Invalid UTF-8 in provider stream") from error
        if not line:
            if data:
                yield event, "\n".join(data)
            data, event = [], "message"
        elif not line.startswith(":"):
            field, _, value = line.partition(":")
            value = value.removeprefix(" ")
            if field == "data":
                data.append(value)
            elif field == "event":
                event = value
    # An unterminated SSE frame is never a completed model response.


def assemble(response, api, *, delta, record, deadline=None):
    text = []
    reasoning = []
    envelope, finish = {}, None
    for event, data in sse_events(response, deadline):
        if data == "[DONE]":
            record({"event": event, "data": "[DONE]"})
            if api != "chat_completions" or finish is None:
                raise StreamError("Provider ended stream without a terminal response")
            envelope.update(
                object="chat.completion",
                choices=[
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": "".join(text),
                            **(
                                {"reasoning_content": "".join(reasoning)}
                                if reasoning
                                else {}
                            ),
                        },
                        "finish_reason": finish,
                    }
                ],
            )
            return envelope
        try:
            item = json.loads(data)
        except ValueError as error:
            raise StreamError("Provider sent a non-JSON SSE event") from error
        if not isinstance(item, dict):
            raise StreamError("Provider SSE data must be an object")
        record({"event": event, "data": item})
        if item.get("error") or item.get("type", event) == "error":
            raise StreamError(
                "Provider returned an error event; inspect raw stream audit"
            )
        if api == "responses":
            kind = item.get("type", event)
            if kind == "response.output_text.delta":
                value = item.get("delta")
                if not isinstance(value, str):
                    raise StreamError("Invalid Responses text delta")
                delta(value)
            elif kind in (
                "response.completed",
                "response.incomplete",
                "response.failed",
            ):
                result = item.get("response")
                expected = kind.split(".")[1]
                if not isinstance(result, dict) or result.get("status") != expected:
                    raise StreamError("Invalid terminal Responses event")
                return result
            continue
        for key in ("id", "created", "model", "system_fingerprint", "service_tier"):
            if key in item:
                envelope[key] = item[key]
        if isinstance(item.get("usage"), dict):
            envelope["usage"] = item["usage"]
        choices = item.get("choices", [])
        if not isinstance(choices, list) or len(choices) > 1:
            raise StreamError("Invalid Chat stream choices")
        for choice in choices:
            if not isinstance(choice, dict) or choice.get("index", 0) != 0:
                raise StreamError("Only one streamed choice is supported")
            change = choice.get("delta") or {}
            if not isinstance(change, dict):
                raise StreamError("Invalid Chat delta")
            # The agent's tools use its JSON text contract, not native tool_calls.
            if change.get("tool_calls") or change.get("function_call"):
                raise StreamError(
                    "Native streamed tool calls are outside the active JSON contract"
                )
            value = change.get("content")
            if value is not None:
                if not isinstance(value, str) or finish is not None:
                    raise StreamError("Invalid or late Chat text delta")
                text.append(value)
                delta(value)
            private = change.get("reasoning_content") or change.get("reasoning")
            if isinstance(private, str):
                reasoning.append(private)  # Native audit only, never a browser event.
            if choice.get("finish_reason") is not None:
                finish = choice["finish_reason"]
    raise StreamInterrupted("Provider stream closed before its terminal event")
