from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from toolburn.cli import main
from toolburn.pass_receipt import build_pass_receipt


SESSION_ID = "11111111-2222-4333-8444-555555555555"
TURN_ONE = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
TURN_TWO = "11111111-aaaa-4bbb-8ccc-222222222222"
TURN_ACTIVE = "99999999-aaaa-4bbb-8ccc-222222222222"


def usage(total: int, raw_input: int, cached: int, output: int) -> dict:
    return {
        "total_token_usage": {
            "input_tokens": raw_input,
            "cached_input_tokens": cached,
            "cache_write_input_tokens": 0,
            "output_tokens": output,
            "reasoning_output_tokens": 0,
            "total_tokens": total,
        }
    }


def fixture_rows() -> list[dict]:
    return [
        {
            "timestamp": "2026-08-23T00:00:00Z",
            "type": "session_meta",
            "payload": {"id": SESSION_ID, "cwd": "/repo", "source": "test", "originator": "Codex"},
        },
        {"timestamp": "2026-08-23T00:00:01Z", "type": "event_msg", "payload": {"type": "token_count", "info": usage(10, 8, 2, 2)}},
        {"timestamp": "2026-08-23T00:00:02Z", "type": "event_msg", "payload": {"type": "task_started", "turn_id": TURN_ONE}},
        {"timestamp": "2026-08-23T00:00:03Z", "type": "turn_context", "payload": {"turn_id": TURN_ONE, "cwd": "/repo", "model": "test-model"}},
        {
            "timestamp": "2026-08-23T00:00:04Z",
            "type": "response_item",
            "payload": {"type": "custom_tool_call", "call_id": "one", "name": "exec", "input": 'await tools.exec_command({"cmd":"rg -n owner ."})'},
        },
        {"timestamp": "2026-08-23T00:00:05Z", "type": "response_item", "payload": {"type": "custom_tool_call_output", "call_id": "one", "output": "result"}},
        {"timestamp": "2026-08-23T00:00:06Z", "type": "event_msg", "payload": {"type": "token_count", "info": usage(110, 88, 22, 22)}},
        {"timestamp": "2026-08-23T00:00:07Z", "type": "event_msg", "payload": {"type": "task_complete", "turn_id": TURN_ONE, "duration_ms": 5000}},
        {"timestamp": "2026-08-23T00:01:00Z", "type": "event_msg", "payload": {"type": "task_started", "turn_id": TURN_TWO}},
        {"timestamp": "2026-08-23T00:01:01Z", "type": "turn_context", "payload": {"turn_id": TURN_TWO, "cwd": "/repo", "model": "test-model"}},
        {
            "timestamp": "2026-08-23T00:01:02Z",
            "type": "response_item",
            "payload": {"type": "custom_tool_call", "call_id": "two", "name": "exec", "input": 'await tools.exec_command({"cmd":"./scripts/validate.sh"})'},
        },
        {"timestamp": "2026-08-23T00:01:03Z", "type": "response_item", "payload": {"type": "custom_tool_call_output", "call_id": "two", "output": "passed"}},
        {
            "timestamp": "2026-08-23T00:01:04Z",
            "type": "response_item",
            "payload": {"type": "custom_tool_call", "call_id": "three", "name": "exec", "input": 'await tools.exec_command({"cmd":"./scripts/validate.sh"})'},
        },
        {"timestamp": "2026-08-23T00:01:05Z", "type": "response_item", "payload": {"type": "custom_tool_call_output", "call_id": "three", "output": "{\"exit_code\":1}"}},
        {"timestamp": "2026-08-23T00:01:06Z", "type": "event_msg", "payload": {"type": "token_count", "info": usage(250, 200, 50, 50)}},
        {"timestamp": "2026-08-23T00:01:07Z", "type": "event_msg", "payload": {"type": "task_complete", "turn_id": TURN_TWO, "duration_ms": 7000}},
        {"timestamp": "2026-08-23T00:02:00Z", "type": "event_msg", "payload": {"type": "task_started", "turn_id": TURN_ACTIVE}},
    ]


def write_fixture(root: Path) -> tuple[Path, Path, Path]:
    session_root = root / "sessions"
    episode_root = root / "episodes"
    session_root.mkdir()
    episode_root.mkdir()
    rollout = session_root / f"rollout-2026-08-23T00-00-00-{SESSION_ID}.jsonl"
    rollout.write_text("".join(json.dumps(row) + "\n" for row in fixture_rows()), encoding="utf-8")
    episode = {
        "jobId": "pass-one",
        "status": "completed",
        "harnessMode": "standard",
        "episodePurpose": "ordinary",
        "terminalAt": "2026-08-23T00:00:08Z",
        "target": {"threadId": SESSION_ID, "turnId": "product-before", "harness": {"turnId": TURN_ONE}},
    }
    (episode_root / "pass-one.json").write_text(json.dumps(episode), encoding="utf-8")
    return rollout, session_root, episode_root


class PassReceiptTests(unittest.TestCase):
    def test_current_uses_exact_env_session_and_previous_harness_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            _, session_root, episode_root = write_fixture(Path(directory))
            with mock.patch.dict(os.environ, {"CODEX_THREAD_ID": SESSION_ID}):
                receipt = build_pass_receipt("current", session_root=session_root, episode_root=episode_root)
        session = receipt["session"]
        self.assertEqual(session["scope"]["kind"], "ordinary_turns_since_previous_harness")
        self.assertEqual(session["scope"]["selectedTurnIds"], [TURN_TWO])
        self.assertEqual(session["usage"]["total"], 140)
        self.assertEqual(session["operations"]["calls"], 2)
        self.assertEqual(session["operations"]["failures"], 1)
        self.assertEqual(session["operations"]["largestCategory"], "validation")
        self.assertEqual(session["operations"]["repeatedCommandMarkers"][0]["marker"], "validate.sh")
        self.assertEqual(session["activeTurnId"], TURN_ACTIVE)
        self.assertIn("darkexec", receipt)

    def test_previous_and_exact_turn_are_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            rollout, session_root, episode_root = write_fixture(Path(directory))
            with mock.patch.dict(os.environ, {"CODEX_THREAD_ID": SESSION_ID}):
                previous = build_pass_receipt("previous", session_root=session_root, episode_root=episode_root)
            exact = build_pass_receipt(str(rollout), exact_turn=TURN_ONE, episode_root=episode_root)
        self.assertEqual(previous["session"]["scope"]["selectedTurnIds"], [TURN_TWO])
        self.assertEqual(exact["session"]["scope"]["selectedTurnIds"], [TURN_ONE])
        self.assertEqual(exact["session"]["usage"]["total"], 100)

    def test_cli_pass_and_compare_emit_versioned_json(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            _, session_root, episode_root = write_fixture(Path(directory))
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                status = main([
                    "pass", SESSION_ID,
                    "--turn", TURN_TWO,
                    "--session-root", str(session_root),
                    "--episodes-root", str(episode_root),
                ])
            self.assertEqual(status, 0)
            self.assertEqual(json.loads(output.getvalue())["receiptKind"], "toolburn.pass/v1")

            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                status = main([
                    "compare", SESSION_ID, SESSION_ID,
                    "--baseline-turn", TURN_ONE,
                    "--candidate-turn", TURN_TWO,
                    "--session-root", str(session_root),
                    "--episodes-root", str(episode_root),
                ])
            comparison = json.loads(output.getvalue())
            self.assertEqual(status, 0)
            self.assertEqual(comparison["receiptKind"], "toolburn.compare/v1")
            self.assertEqual(comparison["delta"]["usage"]["total"], 40)
            self.assertIn("does not rate", comparison["interpretation"])

    def test_current_fails_closed_without_session_identity(self) -> None:
        output = io.StringIO()
        with mock.patch.dict(os.environ, {}, clear=True), contextlib.redirect_stdout(output):
            status = main(["pass", "current"])
        self.assertEqual(status, 2)
        self.assertIn("CODEX_THREAD_ID is unavailable", output.getvalue())


if __name__ == "__main__":
    unittest.main()
