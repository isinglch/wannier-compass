import copy
import json
from unittest.mock import patch

from pythonagent.journal import Journal
from pythonagent.llm import LLMClient, LLMError, LLMHTTPError
from pythonagent.output_recovery import LLMOutputLimitError
from pythonagent.workflow import Workflow
from support import CaseTest, FixtureExecutor


def chat(content, finish="stop", *, prompt=100, completion=20, identifier="answer"):
    return {
        "id": identifier,
        "choices": [{"finish_reason": finish, "message": {"content": content}}],
        "usage": {
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": prompt + completion,
        },
    }


def response(content, identifier, *, status="completed", reason=None, prompt=100):
    return {
        "id": identifier,
        "status": status,
        "incomplete_details": {"reason": reason} if reason else None,
        "output": [
            {"type": "message", "content": [{"type": "output_text", "text": content}]}
        ],
        "usage": {
            "input_tokens": prompt,
            "output_tokens": 20,
            "total_tokens": prompt + 20,
        },
    }


class OutputRecoveryTests(CaseTest):
    def client(self, **config):
        return LLMClient(
            {
                "backend": "openai",
                "base_url": "http://fixture.invalid/v1",
                "model": "fixture",
                "max_tokens": 4096,
                "context_tokens": 131072,
                "proxy_mode": "direct",
                "chat_template_kwargs": {"enable_thinking": True},
                **config,
            },
            Journal(self.run),
        )

    def events(self, client):
        return [
            json.loads(line) for line in client.journal.path.read_text().splitlines()
        ]

    def test_partial_json_is_regenerated_in_same_session_without_fragment_replay(self):
        client = self.client()
        role = "planner"
        session = client.sessions.heads[role]["session_id"]
        wire = []
        bodies = [
            chat('{"tool":"write_plan","arguments":', "length", identifier="cut"),
            chat('{"ok":true}', identifier="complete"),
        ]

        def provider(payload):
            wire.append(copy.deepcopy(payload))
            return bodies.pop(0)

        client.send_session = provider
        state = {"input_revision": "source-1", "current_plan": None}
        text, meta = client.sessions.ask(
            role, {"instruction": "Prepare the complete original plan"}, facts=state
        )
        head = client.sessions.heads[role]
        self.assertEqual(text, '{"ok":true}')
        self.assertEqual(head["session_id"], session)
        self.assertEqual(len(head["turns"]), 1)
        self.assertEqual(head["state"], state)
        packet = json.loads(wire[-1]["messages"][-1]["content"])
        self.assertEqual(
            packet["message"]["instruction"], "Prepare the complete original plan"
        )
        self.assertFalse(
            packet["message"]["output_length_recovery"]["previous_response_applied"]
        )
        self.assertNotIn(
            '{"tool":"write_plan","arguments":',
            "\n".join(message["content"] for message in wire[-1]["messages"]),
        )
        self.assertTrue(
            all(
                p["max_tokens"] == 4096 and p["chat_template_kwargs"]["enable_thinking"]
                for p in wire
            )
        )
        self.assertTrue(
            all(sum(m["role"] == "system" for m in p["messages"]) == 1 for p in wire)
        )
        self.assertEqual(meta["output_recovery"]["retries"], 1)
        self.assertEqual(
            meta["output_recovery"]["recorded_response_usage"]["total_tokens"], 240
        )
        audited = [e for e in self.events(client) if e["kind"] == role]
        self.assertEqual(
            [e["response_status"] for e in audited], ["incomplete", "completed"]
        )
        self.assertEqual(
            len(list(client.journal.calls.glob("*.provider-response.json"))), 2
        )

    def test_reasoning_only_length_does_not_reinject_private_reasoning(self):
        client = self.client()
        cut = chat(None, "length")
        cut["choices"][0]["message"]["reasoning_content"] = "PRIVATE_INTERNAL_REASONING"
        bodies = [cut, chat('{"ok":true}')]
        wire = []

        def provider(payload):
            wire.append(copy.deepcopy(payload))
            return bodies.pop(0)

        client.send_session = provider
        text, _ = client.sessions.ask("reviewer", {"instruction": "Review"}, facts={})
        self.assertEqual(json.loads(text), {"ok": True})
        self.assertNotIn("PRIVATE_INTERNAL_REASONING", json.dumps(wire))
        self.assertEqual(len(client.sessions.heads["reviewer"]["turns"]), 1)

    def test_valid_json_marked_length_cannot_execute_write_plan(self):
        client = self.client()
        flow = Workflow(
            self.case,
            self.root / "flows",
            {"plotting": {"enabled": False}},
            llm=client,
            executor=FixtureExecutor(),
        )
        calls = 0

        def provider(payload):
            nonlocal calls
            calls += 1
            if calls <= 2:
                return chat(
                    json.dumps(
                        {"tool": "write_plan", "arguments": {"plan": self.plan()}}
                    ),
                    "length" if calls == 1 else "stop",
                    identifier=str(calls),
                )
            return chat(json.dumps({"action": "submit", "plan_id": flow.plans.latest}))

        client.send_session = provider
        answer = flow.decide("planner", {"instruction": "Author plan"})
        self.assertEqual(answer["action"], "submit")
        self.assertEqual(flow.plans.counter, 1)
        self.assertEqual(flow.steps, 1)
        self.assertEqual(len(list(flow.plans.root.glob("*/plan.json"))), 1)
        self.assertEqual(calls, 3)

    def test_exhaustion_preserves_previous_accepted_parent_and_state(self):
        client = self.client(length_recovery={"max_retries": 2})
        client.send_session = lambda payload: chat(
            '{"old":true}', identifier="accepted"
        )
        client.sessions.ask("planner", {"instruction": "old"}, facts={"version": 1})
        head = client.sessions.heads["planner"]
        before = copy.deepcopy(head)
        client.send_session = lambda payload: chat(
            '{"unfinished":', "length", identifier="cut"
        )
        with self.assertRaises(LLMOutputLimitError) as error:
            client.sessions.ask("planner", {"instruction": "new"}, facts={"version": 2})
        self.assertEqual(error.exception.attempts, 3)
        for key in ("state", "response_id", "turns", "evidence", "session_id"):
            self.assertEqual(head[key], before[key])
        events = self.events(client)
        self.assertEqual(sum(e["kind"] == "output_truncated" for e in events), 3)
        self.assertEqual(sum(e["kind"] == "length_recovery_failed" for e in events), 1)

    def test_responses_retry_uses_last_accepted_parent_not_incomplete_response(self):
        client = self.client(api="responses")
        bodies = [
            response('{"old":true}', "accepted-1"),
            response(
                '{"unfinished":',
                "cut",
                status="incomplete",
                reason="max_output_tokens",
                prompt=200,
            ),
            response('{"new":true}', "accepted-2", prompt=240),
        ]
        wire = []

        def provider(payload):
            wire.append(copy.deepcopy(payload))
            if len(wire) == 3:
                self.assertEqual(
                    client.sessions.heads["reviewer"]["response_id"], "accepted-1"
                )
            return bodies.pop(0)

        client.send_session = provider
        client.sessions.ask("reviewer", {"instruction": "old"}, facts={"version": 1})
        text, _ = client.sessions.ask(
            "reviewer", {"instruction": "new"}, facts={"version": 2}
        )
        self.assertEqual(json.loads(text), {"new": True})
        self.assertEqual(wire[1]["previous_response_id"], "accepted-1")
        self.assertEqual(wire[2]["previous_response_id"], "accepted-1")
        self.assertNotIn('"previous_response_id": "cut"', json.dumps(wire))
        self.assertEqual(len(client.sessions.heads["reviewer"]["turns"]), 2)

    def test_non_length_incomplete_is_not_retried_as_length(self):
        client = self.client(api="responses")
        with patch.object(
            client,
            "send_session",
            return_value=response(
                "", "filtered", status="incomplete", reason="content_filter"
            ),
        ) as send:
            with self.assertRaises(LLMError) as error:
                client.sessions.ask("planner", {"instruction": "task"}, facts={})
        self.assertNotIsInstance(error.exception, LLMOutputLimitError)
        self.assertEqual(send.call_count, 1)
        self.assertEqual(client.sessions.heads["planner"]["turns"], [])

    def test_length_recovery_can_be_disabled_without_accepting_fragment(self):
        client = self.client(length_recovery={"enabled": False})
        with patch.object(
            client, "send_session", return_value=chat('{"ok":true}', "length")
        ) as send:
            with self.assertRaises(LLMOutputLimitError) as error:
                client.sessions.ask("planner", {"instruction": "task"}, facts={})
        self.assertEqual(send.call_count, 1)
        self.assertEqual(error.exception.reason, "length_recovery_disabled")
        self.assertEqual(client.sessions.heads["planner"]["turns"], [])

    def test_measured_context_from_length_can_trigger_existing_model_compaction(self):
        client = self.client()
        mode = "seed"
        wire = []

        def provider(payload):
            nonlocal mode
            wire.append(copy.deepcopy(payload))
            if "You compress the context" in payload["messages"][0]["content"]:
                return chat(
                    json.dumps(
                        {
                            "summary": "Earlier work was completed; current task remains pending.",
                            "retain_message_ids": [],
                        }
                    )
                )
            if mode == "seed":
                mode = "cut"
                return chat('{"old":true}')
            if mode == "cut":
                mode = "done"
                return chat('{"unfinished":', "length", prompt=100000)
            return chat('{"new":true}')

        client.send_session = provider
        client.sessions.ask(
            "planner",
            {"instruction": "old history " + "x" * 30000},
            facts={"version": 1},
        )
        latest = {"version": 2, "current_plan": {"must_keep": "EXACT_CURRENT_STATE"}}
        text, _ = client.sessions.ask(
            "planner", {"instruction": "Complete current original task"}, facts=latest
        )
        head = client.sessions.heads["planner"]
        self.assertEqual(json.loads(text), {"new": True})
        self.assertEqual(head["context_revision"], 1)
        self.assertEqual(head["state"], latest)
        last = json.dumps(wire[-1])
        self.assertIn("EXACT_CURRENT_STATE", last)
        self.assertIn("Complete current original task", last)
        self.assertNotIn("x" * 10000, last)
        self.assertNotIn('{"unfinished":', last)
        self.assertTrue(
            any(e["kind"] == "context_summary" for e in self.events(client))
        )

    def test_unknown_usage_remains_unknown(self):
        client = self.client()
        cut = chat('{"unfinished":', "length")
        cut.pop("usage")
        bodies = [cut, chat('{"ok":true}')]
        client.send_session = lambda payload: bodies.pop(0)
        _, meta = client.sessions.ask("planner", {"instruction": "task"}, facts={})
        recovery = meta["output_recovery"]
        self.assertIsNone(recovery["recorded_response_usage"])
        self.assertEqual(
            recovery["truncated_attempts"][0]["usage_status"], "unavailable"
        )
        self.assertEqual(recovery["truncated_attempts"][0]["usage"], {})

    def test_invalid_recovery_config_fails_before_provider_use(self):
        for raw in (
            False,
            {"max_retries": True},
            {"max_retries": -1},
            {"enabled": 1},
            {"unknown": 1},
        ):
            with self.subTest(raw=raw), self.assertRaises(LLMError):
                self.client(length_recovery=raw)

    def test_partial_usage_can_inform_compression_without_inventing_output_usage(self):
        client = self.client()
        client.send_session = lambda payload: chat('{"old":true}')
        client.sessions.ask("planner", {"instruction": "old"}, facts={})
        cut = chat('{"unfinished":', "length")
        cut["usage"] = {"prompt_tokens": 12345}
        bodies = [cut, chat('{"ok":true}')]
        client.send_session = lambda payload: bodies.pop(0)
        with patch.object(client.sessions.compactor, "prepare") as prepare:
            _, meta = client.sessions.ask("planner", {"instruction": "new"}, facts={})
        self.assertEqual(prepare.call_args.kwargs["observed_prompt_tokens"], 12345)
        recovery = meta["output_recovery"]
        self.assertIsNone(recovery["recorded_response_usage"])
        self.assertEqual(recovery["truncated_attempts"][0]["usage_status"], "partial")
        self.assertEqual(
            recovery["truncated_attempts"][0]["usage"], {"prompt_tokens": 12345}
        )

    def test_protected_context_failure_preserves_last_accepted_turn(self):
        client = self.client()
        client.send_session = lambda payload: chat(
            '{"old":true}', identifier="accepted"
        )
        client.sessions.ask("planner", {"instruction": "old"}, facts={"version": 1})
        before = copy.deepcopy(client.sessions.heads["planner"])
        client.send_session = lambda payload: chat('{"unfinished":', "length")
        with patch.object(
            client.sessions.compactor,
            "prepare",
            side_effect=[
                None,
                LLMError("protected current state exceeds input budget"),
            ],
        ):
            with self.assertRaises(LLMOutputLimitError) as error:
                client.sessions.ask(
                    "planner", {"instruction": "new"}, facts={"version": 2}
                )
        self.assertEqual(error.exception.reason, "protected_context_exhausted")
        self.assertEqual(error.exception.attempts, 1)
        self.assertEqual(client.sessions.heads["planner"], before)

    def test_http_context_recovery_does_not_reset_length_retry_limit(self):
        client = self.client(length_recovery={"max_retries": 1})
        bodies = [
            chat('{"cut":', "length"),
            LLMHTTPError(400, "context length exceeded"),
            chat('{"cut":', "length"),
        ]

        def provider(payload):
            result = bodies.pop(0)
            if isinstance(result, Exception):
                raise result
            return result

        client.send_session = provider
        with patch.object(client.sessions.compactor, "prepare") as prepare:
            with self.assertRaises(LLMOutputLimitError) as error:
                client.sessions.ask("planner", {"instruction": "task"}, facts={})
        self.assertEqual(error.exception.attempts, 2)
        self.assertEqual(
            sum(bool(c.kwargs.get("force")) for c in prepare.call_args_list), 1
        )
        self.assertFalse(bodies)

    def test_workflow_reports_output_limit_as_distinct_operational_condition(self):
        client = self.client(length_recovery={"max_retries": 0})
        client.send_session = lambda payload: chat('{"cut":', "length")
        backend = FixtureExecutor()
        flow = Workflow(
            self.case,
            self.root / "flows",
            {"plotting": {"enabled": False}},
            llm=client,
            executor=backend,
        )
        result = flow.run()
        self.assertEqual(result["stop"], "model_output_limit", result)
        self.assertEqual(result["role"], "planner")
        self.assertEqual(result["response_attempts"], 1)
        self.assertEqual(result["plan_versions"], 0)
        self.assertEqual(result["role_steps"], 1)
        self.assertFalse(backend.executions)
