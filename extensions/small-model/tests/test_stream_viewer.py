import io
import json
import threading
import hashlib
import urllib.error
import urllib.request
from unittest.mock import patch

from pythonagent.journal import Journal
from pythonagent.llm import LLMClient, LLMError
from pythonagent.output_recovery import LLMOutputLimitError
from pythonagent.streaming import assemble, StreamInterrupted
from pythonagent.viewer import RunReader, make_server
from pythonagent.workflow import Workflow
from support import CaseTest, FixtureExecutor


class Response(io.BytesIO):
    headers = {"Content-Type": "text/event-stream; charset=utf-8"}

    def __enter__(self):
        return self

    def __exit__(self, *unused):
        self.close()


def frames(*values):
    return "".join(
        "data: "
        + (value if isinstance(value, str) else json.dumps(value, ensure_ascii=False))
        + "\r\n\r\n"
        for value in values
    ).encode()


def chat(text, finish="stop", *, usage=True, done=True):
    values = [
        {
            "id": "fixture",
            "model": "fixture",
            "choices": [
                {
                    "index": 0,
                    "delta": {
                        "role": "assistant",
                        "reasoning_content": "PRIVATE_REASONING",
                    },
                    "finish_reason": None,
                }
            ],
        },
        {
            "id": "fixture",
            "choices": [
                {"index": 0, "delta": {"content": text}, "finish_reason": None}
            ],
        },
        {
            "id": "fixture",
            "choices": [{"index": 0, "delta": {}, "finish_reason": finish}],
        },
    ]
    if usage:
        values.append(
            {
                "choices": [],
                "usage": {
                    "prompt_tokens": 100,
                    "completion_tokens": 20,
                    "total_tokens": 120,
                },
            }
        )
    if done:
        values.append("[DONE]")
    return frames(*values)


class StreamingTests(CaseTest):
    def client(self, **extra):
        return LLMClient(
            {
                "backend": "openai",
                "model": "fixture",
                "base_url": "http://fixture.invalid/v1",
                "stream": True,
                "proxy_mode": "direct",
                "max_attempts": 1,
                **extra,
            },
            Journal(self.run),
        )

    def live(self, client):
        return [
            json.loads(row) for row in client.journal.live_path.read_text().splitlines()
        ]

    def test_chat_chunks_display_before_terminal_but_only_final_is_committed(self):
        client = self.client()
        displayed = []
        original = client.journal.publish

        def publish(kind, values=None):
            if kind == "model_delta":
                displayed.append(values["text"])
                self.assertEqual(client.sessions.heads["planner"]["turns"], [])
            return original(kind, values)

        with (
            patch.object(
                client.opener, "open", return_value=Response(chat('{"reason":"你好"}'))
            ),
            patch.object(client.journal, "publish", side_effect=publish),
        ):
            text, meta = client.sessions.ask(
                "planner", {"instruction": "fixture"}, facts={}
            )
        self.assertEqual(text, '{"reason":"你好"}')
        self.assertEqual("".join(displayed), text)
        self.assertEqual(meta["usage"]["total_tokens"], 120)
        self.assertEqual(len(client.sessions.heads["planner"]["turns"]), 1)
        self.assertNotIn("PRIVATE_REASONING", client.journal.live_path.read_text())
        raw = next(client.journal.calls.glob("*.stream.jsonl")).read_text()
        self.assertIn("PRIVATE_REASONING", raw)
        events = self.live(client)
        self.assertTrue(all(e["role"] == "planner" for e in events))
        self.assertEqual(events[0]["call_id"], meta["call_id"])

    def test_missing_usage_is_not_invented(self):
        client = self.client()
        with patch.object(
            client.opener,
            "open",
            return_value=Response(chat('{"ok":true}', usage=False)),
        ):
            _, meta = client.sessions.ask("reviewer", {}, facts={})
        self.assertEqual(meta["usage"], {})
        self.assertEqual(meta["usage_status"], "unavailable")

    def test_missing_done_never_commits_even_a_valid_complete_json(self):
        client = self.client()
        with patch.object(
            client.opener,
            "open",
            return_value=Response(chat('{"ok":true}', done=False)),
        ):
            with self.assertRaises(LLMError):
                client.sessions.ask("planner", {}, facts={})
        self.assertEqual(client.sessions.heads["planner"]["turns"], [])
        self.assertTrue(
            any(e["kind"] == "model_interrupted" for e in self.live(client))
        )

    def test_disconnected_attempt_retries_fresh_without_concatenating_fragments(self):
        client = self.client(max_attempts=2)
        with (
            patch.object(
                client.opener,
                "open",
                side_effect=[
                    Response(chat('{"partial":', done=False)),
                    Response(chat('{"ok":true}')),
                ],
            ),
            patch("pythonagent.llm.time.sleep"),
        ):
            text, _ = client.sessions.ask("planner", {}, facts={})
        self.assertEqual(text, '{"ok":true}')
        starts = [e for e in self.live(client) if e["kind"] == "model_start"]
        self.assertEqual(len(starts), 2)
        self.assertNotEqual(starts[0]["call_id"], starts[1]["call_id"])
        self.assertEqual(len(client.sessions.heads["planner"]["turns"]), 1)

    def test_stream_length_uses_existing_recovery_and_never_accepts_fragment(self):
        client = self.client()
        with patch.object(
            client.opener,
            "open",
            side_effect=[
                Response(chat('{"partial":', "length")),
                Response(chat('{"ok":true}')),
            ],
        ):
            text, meta = (
                client.sessions.ask("scheduler", {}, facts={})
                if "scheduler" in client.sessions.heads
                else client.sessions.ask("planner", {}, facts={})
            )
        self.assertEqual(text, '{"ok":true}')
        self.assertEqual(
            meta["output_recovery"]["recorded_response_usage"]["total_tokens"], 240
        )

    def test_stream_length_tool_json_cannot_write_a_plan(self):
        client = self.client(length_recovery={"max_retries": 0})
        body = json.dumps({"tool": "write_plan", "arguments": {"plan": self.plan()}})
        flow = Workflow(
            self.case,
            self.root / "flows",
            {"plotting": {"enabled": False}},
            llm=client,
            executor=FixtureExecutor(),
        )
        with patch.object(
            client.opener, "open", return_value=Response(chat(body, "length"))
        ):
            with self.assertRaises(LLMOutputLimitError):
                flow.decide("planner", {})
        self.assertEqual(flow.plans.counter, 0)

    def test_responses_incomplete_preserves_native_usage_and_length_details(self):
        client = self.client(api="responses", length_recovery={"max_retries": 0})
        body = {
            "id": "response-cut",
            "status": "incomplete",
            "incomplete_details": {"reason": "max_output_tokens"},
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": '{"cut":'}],
                }
            ],
            "usage": {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120},
        }
        raw = frames(
            {"type": "response.output_text.delta", "delta": '{"cut":'},
            {"type": "response.incomplete", "response": body},
        )
        with patch.object(client.opener, "open", return_value=Response(raw)):
            with self.assertRaises(LLMOutputLimitError) as error:
                client.sessions.ask("planner", {}, facts={})
        self.assertEqual(error.exception.details["reason"], "max_output_tokens")
        self.assertEqual(client.sessions.heads["planner"]["turns"], [])

    def test_responses_uses_terminal_body_not_preview_as_authoritative_answer(self):
        client = self.client(api="responses")
        body = {
            "id": "response-final",
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": '{"final":true}'}],
                }
            ],
        }
        raw = frames(
            {"type": "response.output_text.delta", "delta": "preview"},
            {"type": "response.completed", "response": body},
        )
        with patch.object(client.opener, "open", return_value=Response(raw)):
            text, _ = client.sessions.ask("planner", {}, facts={})
        self.assertEqual(text, '{"final":true}')
        self.assertEqual(
            client.sessions.heads["planner"]["response_id"], "response-final"
        )

    def test_malformed_native_tools_multi_choice_and_error_events_are_rejected(self):
        bodies = [
            frames("{bad-json}"),
            frames({"error": {"message": "bad"}}),
            frames({"choices": [{"delta": {"tool_calls": [{}]}}]}),
            frames({"choices": [{"index": 1, "delta": {"content": "bad"}}]}),
            frames({"choices": [{"delta": {}}, {"delta": {}}]}),
        ]
        for raw in bodies:
            with self.subTest(raw=raw):
                client = self.client()
                with patch.object(client.opener, "open", return_value=Response(raw)):
                    with self.assertRaises(LLMError):
                        client.sessions.ask("planner", {}, facts={})
                self.assertEqual(client.sessions.heads["planner"]["turns"], [])

    def test_crlf_multiline_sse_and_heartbeat(self):
        raw = b': heartbeat\r\ndata: {"choices":\r\ndata: [{"delta":{"content":"ok"},"finish_reason":"stop"}]}\r\n\r\ndata: [DONE]\r\n\r\n'
        body = assemble(
            Response(raw),
            "chat_completions",
            delta=lambda v: None,
            record=lambda v: None,
        )
        self.assertEqual(body["choices"][0]["message"]["content"], "ok")
        with self.assertRaises(StreamInterrupted):
            assemble(
                Response(b'data: {"choices":[]}'),
                "chat_completions",
                delta=lambda v: None,
                record=lambda v: None,
            )

    def test_stream_options_do_not_change_output_or_thinking_and_can_disable_usage_request(
        self,
    ):
        client = self.client(
            max_tokens=65536,
            chat_template_kwargs={"enable_thinking": False},
            stream_include_usage=False,
        )
        payload = client.session_payload([], [], None)
        self.assertTrue(payload["stream"])
        self.assertFalse(payload["stream_options"]["include_usage"])
        self.assertEqual(payload["max_tokens"], 65536)
        self.assertFalse(payload["chat_template_kwargs"]["enable_thinking"])

    def test_stream_config_rejects_non_booleans(self):
        for options in ({"stream": 1}, {"stream_include_usage": "yes"}):
            with self.assertRaises(LLMError):
                self.client(**options)

    def test_heartbeat_cannot_keep_stream_alive_beyond_request_budget(self):
        with self.assertRaises(TimeoutError):
            assemble(
                Response(b": heartbeat\n\n"),
                "chat_completions",
                delta=lambda v: None,
                record=lambda v: None,
                deadline=0,
            )


class ViewerTests(CaseTest):
    def setUp(self):
        super().setUp()
        self.runs = self.root / "runs"
        self.directory = self.runs / "fixture-1"
        self.journal = Journal(self.directory)
        self.reader = RunReader(self.runs)

    def test_tool_summary_and_lazy_result_preserve_actual_pagination(self):
        result = {
            "value": [{"band": i, "weight": 0.125} for i in range(50)],
            "total_rows": 100,
            "next_offset": 50,
        }
        record = self.journal.record(
            "tool",
            {
                "role": "planner",
                "tool": "read_record",
                "arguments": {"offset": 0},
                "seconds": 0.1,
                "result": result,
            },
        )
        rows, _ = self.reader.events("fixture-1")
        event = rows[0]["event"]
        self.assertEqual(event["tool_record_id"], record["event_sha256"])
        self.assertEqual(event["result"]["returned_items"], 50)
        self.assertEqual(event["result"]["next_offset"], 50)
        self.assertNotIn("value", event["result"])
        self.assertEqual(
            self.reader.tool_result("fixture-1", event["tool_record_id"])["result"],
            result,
        )

    def test_old_slim_feed_is_enriched_without_rewriting_logs_and_index_follows_new_rows(
        self,
    ):
        first = self.journal.record(
            "tool",
            {
                "role": "planner",
                "tool": "read_text",
                "arguments": {"path": "case/INCAR"},
                "seconds": 0.2,
                "result": {
                    "text": "Actual INCAR contents",
                    "total_lines": 1,
                    "next_offset": None,
                },
            },
        )
        event = json.loads(self.journal.live_path.read_text())
        event.pop("tool_record_id")
        event["result"] = {}
        self.journal.live_path.write_text(json.dumps(event) + "\n")
        paths = [self.journal.path, self.journal.live_path]
        before = [hashlib.sha256(p.read_bytes()).hexdigest() for p in paths]
        rows, cursor = self.reader.events("fixture-1")
        self.assertEqual(
            rows[0]["event"]["result"]["text_preview"], "Actual INCAR contents"
        )
        self.assertEqual(rows[0]["event"]["tool_record_id"], first["event_sha256"])
        self.assertEqual(
            before, [hashlib.sha256(p.read_bytes()).hexdigest() for p in paths]
        )
        second = self.journal.record(
            "tool",
            {
                "role": "reviewer",
                "tool": "list_records",
                "arguments": {},
                "seconds": 0.3,
                "result": {"rows": [{"path": "case/INCAR"}], "total_rows": 1},
            },
        )
        rows, _ = self.reader.events("fixture-1", cursor)
        self.assertEqual(rows[0]["event"]["tool_record_id"], second["event_sha256"])
        self.assertEqual(rows[0]["event"]["result"]["returned_items"], 1)

    def test_ambiguous_legacy_tool_reference_is_not_guessed(self):
        for value in ("old", "new"):
            self.journal.record(
                "tool",
                {
                    "role": "planner",
                    "tool": "read_text",
                    "arguments": {},
                    "seconds": 0.1,
                    "result": {"text": value},
                },
            )
        rows = [
            json.loads(line) for line in self.journal.live_path.read_text().splitlines()
        ]
        for row in rows:
            row.pop("tool_record_id")
            row["result"] = {}
        self.journal.live_path.write_text(
            "".join(json.dumps(row) + "\n" for row in rows)
        )
        events, _ = self.reader.events("fixture-1")
        self.assertTrue(all(not e["event"]["tool_result_available"] for e in events))
        self.assertTrue(all("tool_record_id" not in e["event"] for e in events))

    def test_historical_tool_with_same_arguments_has_precise_audit_reference(self):
        records = [
            self.journal.record(
                "tool",
                {
                    "role": "planner",
                    "tool": "read_text",
                    "arguments": {},
                    "seconds": 0.1,
                    "result": {"text": value},
                },
            )
            for value in ("old", "new")
        ]
        self.journal.live_path.unlink()
        rows, _ = self.reader.events("fixture-1")
        self.assertEqual(
            [r["event"]["tool_record_id"] for r in rows],
            [r["event_sha256"] for r in records],
        )
        self.assertEqual(
            [
                self.reader.tool_result("fixture-1", r["event"]["tool_record_id"])[
                    "result"
                ]["text"]
                for r in rows
            ],
            ["old", "new"],
        )

    def test_tool_result_http_is_exact_and_cannot_read_non_tool_or_other_run_records(
        self,
    ):
        result = {"text": "Full tool contents", "next_offset": None}
        tool = self.journal.record(
            "tool",
            {
                "role": "planner",
                "tool": "read_text",
                "arguments": {},
                "seconds": 0.1,
                "result": result,
            },
            prompt="SECRET_PROMPT",
        )
        non_tool = self.journal.record(
            "planner",
            {"gate": "planner"},
            prompt="SECRET_SYSTEM",
            response="Role response",
        )
        foreign = Journal(self.runs / "other").record(
            "tool",
            {
                "role": "planner",
                "tool": "read_text",
                "arguments": {},
                "seconds": 0.2,
                "result": {"text": "OTHER_RUN"},
            },
        )
        server = make_server(self.runs, 0)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.addCleanup(server.stopping.set)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        url = f"http://127.0.0.1:{server.server_port}/api/tool-result?run=fixture-1&record="
        with opener.open(url + tool["event_sha256"], timeout=2) as response:
            data = response.read().decode()
            self.assertEqual(json.loads(data)["result"], result)
            self.assertNotIn("SECRET_PROMPT", data)
        for record_id in (
            "0" * 64,
            "../journal.jsonl",
            non_tool["event_sha256"],
            foreign["event_sha256"],
        ):
            with (
                self.subTest(record_id=record_id),
                self.assertRaises(urllib.error.HTTPError) as error,
            ):
                opener.open(url + record_id, timeout=2)
            self.assertEqual(error.exception.code, 404)

    def test_projection_excludes_prompts_provider_reasoning_and_config(self):
        self.journal.record(
            "planner",
            {
                "gate": "planner",
                "api_key": "SECRET_KEY",
                "system": "SECRET_SYSTEM",
                "usage": {"total_tokens": 3},
            },
            prompt="SECRET_PROMPT",
            response='{"reason":"Actual role output"}',
        )
        rows, _ = self.reader.events("fixture-1")
        text = json.dumps(rows)
        self.assertIn("Actual role output", text)
        for secret in ("SECRET_KEY", "SECRET_SYSTEM", "SECRET_PROMPT"):
            self.assertNotIn(secret, text)

    def test_historical_journal_reads_response_only(self):
        self.journal.record(
            "reviewer",
            {"gate": "reviewer"},
            prompt="SECRET_PROMPT",
            response='{"reason":"Old role output"}',
        )
        self.journal.live_path.unlink()
        rows, _ = self.reader.events("fixture-1")
        self.assertIn("Old role output", json.dumps(rows))
        self.assertNotIn("SECRET_PROMPT", json.dumps(rows))

    def test_cursor_reconnect_and_partial_line_do_not_duplicate_messages(self):
        self.journal.publish("model_delta", {"role": "planner", "text": "first"})
        first, cursor = self.reader.events("fixture-1")
        self.assertEqual(len(first), 1)
        with self.journal.live_path.open("ab") as stream:
            stream.write(b'{"kind":"model_delta"')
        rows, after = self.reader.events("fixture-1", cursor)
        self.assertEqual((rows, after), ([], cursor))
        with self.journal.live_path.open("ab") as stream:
            stream.write(b',"text":"second"}\n')
        rows, after = self.reader.events("fixture-1", after)
        self.assertEqual([r["event"]["text"] for r in rows], ["second"])
        self.assertGreater(after, cursor)

    def test_traversal_and_run_symlinks_are_denied(self):
        for name in ("../", "/tmp", "fixture-1/../", "%2e%2e"):
            with self.assertRaises(ValueError):
                self.reader.run(name)
        (self.runs / "outside").symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.reader.run("outside")
        self.journal.live_path.symlink_to(self.root / "secret")
        with self.assertRaises(ValueError):
            self.reader.events("fixture-1")

    def test_actual_http_assets_sse_readonly_and_local_origin(self):
        self.journal.publish(
            "model_delta", {"role": "planner", "text": "Live role output"}
        )
        server = make_server(self.runs, 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.addCleanup(server.stopping.set)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        url = f"http://127.0.0.1:{server.server_port}"
        with opener.open(url) as response:
            self.assertIn("Agent 观察台", response.read().decode())
            self.assertIn(
                "default-src 'self'", response.headers["Content-Security-Policy"]
            )
        for path in ("/app.js", "/style.css", "/api/runs"):
            with opener.open(url + path) as response:
                self.assertEqual(response.status, 200)
        with opener.open(url + "/api/events?run=fixture-1", timeout=2) as response:
            lines = [response.readline().decode(), response.readline().decode()]
            self.assertTrue(lines[0].startswith("id:"))
            self.assertIn("Live role output", lines[1])
        for request, code in (
            (
                urllib.request.Request(
                    url + "/api/runs", headers={"Host": "evil.example"}
                ),
                403,
            ),
            (
                urllib.request.Request(
                    url + "/api/runs", headers={"Origin": "http://evil.example"}
                ),
                403,
            ),
            (urllib.request.Request(url + "/api/runs", data=b"{}"), 501),
            (url + "/calls/anything", 404),
            (url + "/api/events?run=..", 404),
        ):
            with self.subTest(request=request):
                with self.assertRaises(urllib.error.HTTPError) as error:
                    opener.open(request, timeout=2)
                self.assertEqual(error.exception.code, code)
