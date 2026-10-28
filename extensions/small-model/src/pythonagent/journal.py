"""Append-only, hashed audit events and provider request/response artifacts."""

from datetime import datetime, timezone
import json
from pathlib import Path
import uuid
import warnings

from .state import digest, immutable_json
from .live import public_event


class Journal:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.calls = self.directory / "calls"
        self.calls.mkdir(exist_ok=True)
        self.path = self.directory / "journal.jsonl"
        self.previous = None
        self.live_path = self.directory / "events.jsonl"
        self.live_error = False

    def publish(self, kind, values=None):
        """Best-effort viewer feed, separate from the authoritative audit chain."""
        event = {
            "ts_utc": datetime.now(timezone.utc).isoformat(),
            "kind": kind,
            **(values or {}),
        }
        try:
            with self.live_path.open("a", encoding="utf-8") as stream:
                stream.write(
                    json.dumps(event, ensure_ascii=False, allow_nan=False) + "\n"
                )
        except (OSError, ValueError, TypeError):
            if not self.live_error:
                warnings.warn(
                    "Live viewer feed unavailable; workflow audit remains authoritative"
                )
                self.live_error = True

    def record(self, kind, values=None, *, prompt=None, response=None):
        event = {
            "ts_utc": datetime.now(timezone.utc).isoformat(),
            "kind": kind,
            **(values or {}),
            "previous_event": self.previous,
        }
        if prompt is not None or response is not None:
            artifact = self.calls / (uuid.uuid4().hex + ".exchange.json")
            exchange = {"prompt": prompt, "response": response}
            immutable_json(artifact, exchange)
            event["exchange"] = str(artifact.relative_to(self.directory))
            event["exchange_sha256"] = digest(exchange)
        event["event_sha256"] = digest(event)
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event, ensure_ascii=False, allow_nan=False) + "\n")
            stream.flush()
        self.previous = event["event_sha256"]
        public = public_event(kind, values or {}, response)
        if public is not None:
            if kind == "tool":
                public["tool_record_id"] = event["event_sha256"]
            self.publish(kind, public)
        return event
