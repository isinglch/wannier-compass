import copy
import io
import json
from unittest.mock import patch

from pythonagent.journal import Journal
from pythonagent.llm import LLMClient
from pythonagent.roles import ROLES
from pythonagent.workflow import Workflow
from pythonagent.guidance import original, retry_guidance
from support import CaseTest, FixtureExecutor, approve, author


class TransportTests(CaseTest):
    def client(self):
        return LLMClient(
            {
                "backend": "openai",
                "base_url": "http://unit.invalid/v1",
                "model": "fixture",
                "context_tokens": 131072,
                "max_tokens": 4096,
                "proxy_mode": "direct",
            },
            Journal(self.run),
        )

    def body(self, content, identifier="fixture-response"):
        return {
            "id": identifier,
            "model": "fixture",
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {
                        "content": json.dumps(content),
                        "reasoning_content": "Private reasoning, separate from final JSON",
                    },
                }
            ],
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 20,
                "total_tokens": 120,
            },
        }

    def test_actual_session_adapter_and_four_role_workflow(self):
        client = self.client()
        wire = []
        script = [
            *author(self.plan()),
            approve(),
            (
                "evaluator",
                {
                    "action": "complete",
                    "termination_basis": "objective_satisfied",
                    "remaining_work": [],
                    "reason": "fixture outcome",
                    "observations": [],
                },
            ),
        ]

        def provider(payload):
            wire.append(copy.deepcopy(payload))
            role, answer = script.pop(0)
            self.assertIn("You are " + role, payload["messages"][0]["content"])
            head = client.sessions.heads[role]
            # Reconstruct the new user state as the provider is expected to do.
            state = copy.deepcopy(head["state"])
            packet = json.loads(payload["messages"][-1]["content"])
            if "current_state" in packet:
                state = packet["current_state"]
            else:

                def merge(a, b):
                    for k, v in b.items():
                        if isinstance(v, dict) and isinstance(a.get(k), dict):
                            merge(a[k], v)
                        else:
                            a[k] = v

                merge(state, packet.get("state_changes", {}))
                for keys in packet.get("removed_paths", []):
                    parent = state
                    for key in keys[:-1]:
                        parent = parent[key]
                    parent.pop(keys[-1], None)
            if callable(answer):
                answer = answer(state)
            return self.body(answer, "fixture-" + str(len(wire)))

        client.send_session = provider
        flow = Workflow(
            self.case,
            self.root / "flows",
            {
                "execute": True,
                "plotting": {"enabled": False},
                "scheduler": {"enabled": False},
            },
            llm=client,
            executor=FixtureExecutor(),
        )
        outcome = flow.run()
        self.assertEqual(outcome["stop"], "model_complete", outcome)
        self.assertEqual(len(wire), 4)
        self.assertTrue(
            all(sum(m["role"] == "system" for m in p["messages"]) == 1 for p in wire)
        )
        self.assertEqual(len(wire[2]["messages"]), 2)
        self.assertEqual(set(client.sessions.heads), set(ROLES))
        first_session = client.sessions.run_id
        # Reusing even a caller-injected client starts fresh sessions and a new output root.
        script[:] = [*author(self.plan()), approve()]
        second = Workflow(
            self.case,
            self.root / "flows",
            {
                "execute": False,
                "plotting": {"enabled": False},
                "scheduler": {"enabled": False},
            },
            llm=client,
            executor=FixtureExecutor(),
        )
        self.assertEqual(second.run()["stop"], "reviewed_plan_ready")
        self.assertNotEqual(first_session, client.sessions.run_id)
        self.assertEqual(len(wire[4]["messages"]), 2)

    def test_five_role_session_transport_stays_fresh_and_isolated(self):
        client = self.client()
        wire = []
        script = [
            *author(self.plan()),
            approve(),
            (
                "scheduler",
                lambda state: {
                    "action": "execute",
                    "plan_id": state["current_plan"]["plan_id"],
                    "reason": "Exact approved plan",
                },
            ),
            (
                "evaluator",
                {
                    "action": "complete",
                    "termination_basis": "objective_satisfied",
                    "remaining_work": [],
                    "reason": "fixture outcome",
                    "observations": [],
                },
            ),
        ]

        def provider(payload):
            wire.append(copy.deepcopy(payload))
            role, answer = script.pop(0)
            self.assertIn(
                "operational scheduler" if role == "scheduler" else "You are " + role,
                payload["messages"][0]["content"],
            )
            head = client.sessions.heads[role]
            # Reconstruct the new user state as the provider is expected to do.
            state = copy.deepcopy(head["state"])
            packet = json.loads(payload["messages"][-1]["content"])
            if "current_state" in packet:
                state = packet["current_state"]
            else:

                def merge(a, b):
                    for k, v in b.items():
                        if isinstance(v, dict) and isinstance(a.get(k), dict):
                            merge(a[k], v)
                        else:
                            a[k] = v

                merge(state, packet.get("state_changes", {}))
                for keys in packet.get("removed_paths", []):
                    parent = state
                    for key in keys[:-1]:
                        parent = parent[key]
                    parent.pop(keys[-1], None)
            if callable(answer):
                answer = answer(state)
            return self.body(answer, "fixture-" + str(len(wire)))

        client.send_session = provider
        flow = Workflow(
            self.case,
            self.root / "flows",
            {
                "execute": True,
                "plotting": {"enabled": False},
                "scheduler": {"enabled": True},
            },
            llm=client,
            executor=FixtureExecutor(),
        )
        outcome = flow.run()
        self.assertEqual(outcome["stop"], "model_complete", outcome)
        self.assertEqual(len(wire), 5)
        self.assertTrue(
            all(sum(m["role"] == "system" for m in p["messages"]) == 1 for p in wire)
        )
        self.assertEqual(len(wire[2]["messages"]), 2)
        self.assertEqual(set(client.sessions.heads), set(ROLES) | {"scheduler"})
        first_scheduler = client.sessions.heads["scheduler"]["session_id"]
        first_session = client.sessions.run_id
        # Reusing even a caller-injected client starts fresh sessions and a new output root.
        script[:] = [*author(self.plan()), approve()]
        second = Workflow(
            self.case,
            self.root / "flows",
            {
                "execute": False,
                "plotting": {"enabled": False},
                "scheduler": {"enabled": True},
            },
            llm=client,
            executor=FixtureExecutor(),
        )
        self.assertEqual(second.run()["stop"], "reviewed_plan_ready")
        self.assertNotEqual(first_session, client.sessions.run_id)
        self.assertNotEqual(
            first_scheduler, client.sessions.heads["scheduler"]["session_id"]
        )
        self.assertEqual(client.sessions.heads["scheduler"]["turns"], [])
        self.assertEqual(len(wire[5]["messages"]), 2)

    def test_role_session_continues_beyond_128_messages_without_count_ceiling(self):
        client = LLMClient(
            {
                "backend": "dry_run",
                "max_session_turns": 1,
                "compression": {"enabled": False},
            }
        )
        client.send_session = lambda payload: self.body({"ok": True})
        for turn in range(130):
            text, _ = client.sessions.ask(
                "scheduler" if "scheduler" in client.sessions.heads else "planner",
                {"instruction": "fixture", "sequence": turn},
                facts={},
            )
            self.assertEqual(json.loads(text), {"ok": True})
        self.assertEqual(len(client.sessions.heads["planner"]["turns"]), 130)

    def test_timeout_attempt_is_audited_with_unknown_usage(self):
        client = self.client()
        client.max_attempts = 2

        class Response(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *unused):
                self.close()

        payload = client.session_payload(
            [], [{"role": "user", "content": "fixture"}], None
        )
        with (
            patch.object(
                client.opener,
                "open",
                side_effect=[
                    TimeoutError("fixture"),
                    Response(json.dumps(self.body({"ok": True})).encode()),
                ],
            ),
            patch("pythonagent.llm.time.sleep"),
        ):
            client.send_session(payload)
        rows = [json.loads(s) for s in client.journal.path.read_text().splitlines()]
        failed = [r for r in rows if r["kind"] == "transport_attempt_failed"]
        self.assertEqual(len(failed), 1)
        self.assertIsNone(failed[0]["usage"])
        self.assertEqual(
            len([r for r in rows if r["kind"] == "transport_attempt_start"]), 2
        )
        self.assertEqual(client.proxy_mode, "direct")

    def test_model_written_compaction_preserves_latest_authoritative_state(self):
        client = self.client()
        calls = []

        def provider(payload):
            calls.append(payload)
            if "You compress the context" in payload["messages"][0]["content"]:
                return self.body(
                    {
                        "summary": "Old request was resolved. Continue current scientific work.",
                        "retain_message_ids": [],
                    }
                )
            return self.body(
                {
                    "action": "complete",
                    "termination_basis": "objective_satisfied",
                    "remaining_work": [],
                    "reason": "fixture",
                    "observations": [],
                }
            )

        client.send_session = provider
        client.sessions.ask(
            "evaluator",
            {"instruction": "old context " + ("x" * 30000)},
            facts={"current_result": {"version": 1}},
        )
        head = client.sessions.heads["evaluator"]
        head["prompt_tokens"] = 100000
        latest = {
            "current_result": {"version": 2, "metrics": {"mae_eV": 0.02}},
            "stage_guidance": retry_guidance(),
        }
        client.sessions.ask(
            "evaluator", {"instruction": "Assess current version"}, facts=latest
        )
        self.assertEqual(head["context_revision"], 1)
        self.assertEqual(head["state"]["current_result"], latest["current_result"])
        self.assertEqual(len(calls), 3)
        self.assertNotIn("x" * 10000, json.dumps(calls[-1]))
        self.assertIn("context_checkpoint", json.dumps(calls[-1]))
        self.assertIn(
            original("docs/CONSTRUCTION-OVERVIEW.md")[0]["content"],
            calls[-1]["messages"][0]["content"],
        )
        checkpoints = [
            json.loads(m["content"])
            for m in calls[-1]["messages"]
            if m["role"] == "user" and "context_checkpoint" in m["content"]
        ]
        self.assertEqual(
            checkpoints[-1]["current_state"]["stage_guidance"], latest["stage_guidance"]
        )
