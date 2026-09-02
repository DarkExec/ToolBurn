from __future__ import annotations

import contextlib
import json
import io
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from toolburn.cli import build_parser, main
from toolburn.report import episode_report, tool_work_report
from toolburn.scan import bundle_from_call_payload, command_from_call_payload
from toolburn.semantics import SemanticCatalogError, load_semantic_catalog


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def git(*args: str, cwd: Path) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        text=True,
        capture_output=True,
    )
    return result.stdout.strip()


def commit_all(repo: Path, message: str) -> str:
    git("add", ".", cwd=repo)
    git("-c", "user.name=Toolburn Test", "-c", "user.email=test@example.invalid", "commit", "-m", message, cwd=repo)
    return git("rev-parse", "--short", "HEAD", cwd=repo)


def fixture_rows() -> list[dict]:
    return [
        {
            "timestamp": "2026-06-01T22:54:15.000Z",
            "type": "session_meta",
            "payload": {
                "id": "session-1",
                "timestamp": "2026-06-01T22:54:15.000Z",
                "cwd": "/root/.openclaw/workspace",
                "originator": "openclaw",
                "source": "vscode",
            },
        },
        {
            "timestamp": "2026-06-01T22:54:20.000Z",
            "type": "response_item",
            "payload": {
                "type": "function_call",
                "call_id": "call-1",
                "name": "shell_command",
                "arguments": json.dumps(
                    {
                        "command": (
                            "python3 state/ops-harness/scripts/run_watchdog_cycle.py "
                            "--dispatch-mode real"
                        )
                    }
                ),
            },
        },
        {
            "timestamp": "2026-06-01T22:54:22.000Z",
            "type": "response_item",
            "payload": {
                "type": "function_call_output",
                "call_id": "call-1",
                "output": json.dumps(
                    {
                        "name": "GOS watchdog 30m",
                        "sessionKey": "agent:main:chat:direct:0000000000",
                        "ok": True,
                    }
                ),
            },
        },
        {
            "timestamp": "2026-06-01T22:54:26.000Z",
            "type": "event_msg",
            "payload": {
                "type": "token_count",
                "info": {
                    "total_token_usage": {"total_tokens": 300},
                    "last_token_usage": {
                        "input_tokens": 100,
                        "cached_input_tokens": 80,
                        "output_tokens": 5,
                        "total_tokens": 105,
                    },
                },
            },
        },
        {
            "timestamp": "2026-06-01T22:54:29.000Z",
            "type": "event_msg",
            "payload": {
                "type": "token_count",
                "info": {
                    "total_token_usage": {"total_tokens": 1000},
                    "last_token_usage": {
                        "input_tokens": 200,
                        "cached_input_tokens": 180,
                        "output_tokens": 10,
                        "total_tokens": 210,
                    },
                },
            },
        },
    ]


def copilot_fixture_rows() -> list[dict]:
    return [
        {
            "type": "session.start",
            "data": {
                "sessionId": "copilot-session-1",
                "producer": "copilot-agent",
                "copilotVersion": "1.0.31",
                "startTime": "2026-06-01T22:54:15.000Z",
                "context": {"cwd": "/srv/dark/repos/toolburn"},
            },
            "id": "start-1",
            "timestamp": "2026-06-01T22:54:15.000Z",
        },
        {
            "type": "assistant.usage",
            "data": {
                "model": "claude-sonnet-test",
                "inputTokens": 120,
                "outputTokens": 30,
                "cacheReadTokens": 100,
                "cacheWriteTokens": 5,
                "reasoningTokens": 10,
            },
            "id": "usage-1",
            "timestamp": "2026-06-01T22:54:20.000Z",
        },
        {
            "type": "session.shutdown",
            "data": {
                "modelMetrics": {
                    "claude-opus-test": {
                        "requests": {"count": 2, "cost": 1},
                        "usage": {
                            "inputTokens": 200,
                            "outputTokens": 40,
                            "cacheReadTokens": 160,
                            "cacheWriteTokens": 0,
                            "reasoningTokens": 0,
                        },
                    }
                }
            },
            "id": "shutdown-1",
            "timestamp": "2026-06-01T22:55:20.000Z",
        },
    ]


def codex_fixture_rows() -> list[dict]:
    rows = fixture_rows()
    rows[0]["payload"] = {
        "id": "codex-session-1",
        "timestamp": "2026-06-01T22:54:15.000Z",
        "cwd": "/srv/pager",
        "originator": "codex",
        "source": "vscode",
    }
    rows[1]["payload"]["arguments"] = json.dumps({"command": "git status --short"})
    rows[2]["payload"]["output"] = "clean"
    return rows


def openclaw_cron_fixture_rows() -> list[dict]:
    rows = fixture_rows()
    rows[0]["payload"]["id"] = "cron-session-1"
    rows[1]["payload"]["arguments"] = json.dumps({"command": "tool_search exec command"})
    rows[2]["payload"]["output"] = "no tools found"
    rows.insert(
        1,
        {
            "timestamp": "2026-06-01T22:54:16.000Z",
            "type": "event_msg",
            "payload": {
                "type": "user_message",
                "message": (
                    "[cron:b2d1fd93-38c9-4c4f-bf58-926db57cf5d0 "
                    "voice-models daily maintain] Run the daily maintain cycle."
                ),
            },
        },
    )
    return rows


def openclaw_dream_fixture_rows() -> list[dict]:
    rows = [row for row in fixture_rows() if row.get("type") != "response_item"]
    rows[0]["payload"]["id"] = "dream-session-1"
    rows.insert(
        1,
        {
            "timestamp": "2026-06-01T22:54:16.000Z",
            "type": "event_msg",
            "payload": {
                "type": "user_message",
                "message": "Write a dream diary entry from these memory fragments.",
            },
        },
    )
    return rows


def openclaw_direct_fixture_rows() -> list[dict]:
    rows = fixture_rows()
    rows[0]["payload"]["id"] = "direct-session-1"
    rows[1]["payload"]["arguments"] = json.dumps({"command": "date -u"})
    key = "".join(["session", "Key"])
    value = ":".join(
        [
            "agent",
            "main",
            "tele" + "gram",
            "direct",
            "0000000000",
        ]
    )
    rows[2]["payload"]["output"] = json.dumps(
        {
            key: value,
            "ok": True,
        }
    )
    return rows


def openclaw_inbound_fixture_rows() -> list[dict]:
    rows = fixture_rows()
    rows[0]["payload"]["id"] = "inbound-session-1"
    rows[1]["payload"]["arguments"] = json.dumps(
        {"action": "send", "message": "Visible acknowledgement"}
    )
    rows[1]["payload"]["name"] = "message"
    rows[2]["payload"]["output"] = json.dumps({"ok": True, "messageId": "1"})
    rows.insert(
        1,
        {
            "timestamp": "2026-06-01T22:54:16.000Z",
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "developer",
                "content": [
                    {
                        "type": "input_text",
                        "text": "\n".join(
                            [
                                "## Inbound Context (trusted metadata)",
                                "```json",
                                "{",
                                '  "schema": "openclaw.inbound_meta.v2",',
                                '  "channel": "tele' + 'gram",',
                                '  "provider": "tele' + 'gram",',
                                '  "surface": "tele' + 'gram",',
                                '  "chat_type": "direct"',
                                "}",
                                "```",
                            ]
                        ),
                    }
                ],
            },
        },
    )
    return rows


def openclaw_inbound_mentions_heartbeat_fixture_rows() -> list[dict]:
    rows = openclaw_inbound_fixture_rows()
    rows[0]["payload"]["id"] = "inbound-mentions-heartbeat-session-1"
    rows.insert(
        2,
        {
            "timestamp": "2026-06-01T22:54:17.000Z",
            "type": "event_msg",
            "payload": {
                "type": "user_message",
                "message": (
                    "Memory says heartbeat_scan.py and spam_cleanup.py were "
                    "part of an old incident, but this is a human chat turn."
                ),
            },
        },
    )
    return rows


def openclaw_legacy_discord_fixture_rows() -> list[dict]:
    rows = fixture_rows()
    rows[0]["payload"]["id"] = "legacy-discord-session-1"
    rows[1]["payload"]["name"] = "message"
    rows[1]["payload"]["arguments"] = json.dumps(
        {
            "action": "upload-file",
            "channelId": "0000000000000000000",
            "media": "/tmp/report.pdf",
        }
    )
    rows[2]["payload"]["output"] = json.dumps({"ok": True})
    return rows


def cached_validate_attribution_rows() -> list[dict]:
    return [
        {
            "timestamp": "2026-06-01T22:54:15.000Z",
            "type": "session_meta",
            "payload": {
                "id": "cached-validate-session",
                "timestamp": "2026-06-01T22:54:15.000Z",
                "cwd": "/srv/dark/repos/toolburn",
                "originator": "codex",
            },
        },
        {
            "timestamp": "2026-06-01T22:54:20.000Z",
            "type": "response_item",
            "payload": {
                "type": "function_call",
                "call_id": "call-validate",
                "name": "shell_command",
                "arguments": json.dumps({"command": "./scripts/validate.sh"}),
            },
        },
        {
            "timestamp": "2026-06-01T22:54:21.000Z",
            "type": "response_item",
            "payload": {
                "type": "function_call_output",
                "call_id": "call-validate",
                "output": "validation passed",
            },
        },
        {
            "timestamp": "2026-06-01T22:54:24.000Z",
            "type": "event_msg",
            "payload": {
                "type": "token_count",
                "info": {
                    "last_token_usage": {
                        "input_tokens": 1000,
                        "cached_input_tokens": 990,
                        "output_tokens": 5,
                        "total_tokens": 1005,
                    },
                },
            },
        },
        {
            "timestamp": "2026-06-01T22:55:20.000Z",
            "type": "response_item",
            "payload": {
                "type": "function_call",
                "call_id": "call-heavy",
                "name": "shell_command",
                "arguments": json.dumps({"command": "python3 expensive_context.py"}),
            },
        },
        {
            "timestamp": "2026-06-01T22:55:21.000Z",
            "type": "response_item",
            "payload": {
                "type": "function_call_output",
                "call_id": "call-heavy",
                "output": "large fresh result",
            },
        },
        {
            "timestamp": "2026-06-01T22:55:24.000Z",
            "type": "event_msg",
            "payload": {
                "type": "token_count",
                "info": {
                    "last_token_usage": {
                        "input_tokens": 200,
                        "cached_input_tokens": 10,
                        "output_tokens": 10,
                        "total_tokens": 210,
                    },
                },
            },
        },
    ]


def custom_tool_attribution_rows() -> list[dict]:
    return [
        {
            "timestamp": "2026-08-31T22:54:15.000Z",
            "type": "session_meta",
            "payload": {
                "id": "custom-tool-session",
                "timestamp": "2026-08-31T22:54:15.000Z",
                "cwd": "/srv/dark/repos/toolburn",
                "originator": "codex",
            },
        },
        {
            "timestamp": "2026-08-31T22:54:20.000Z",
            "type": "response_item",
            "payload": {
                "type": "custom_tool_call",
                "call_id": "call-custom-exec",
                "name": "exec",
                "input": (
                    'const r = await tools.exec_command({"cmd":"rg -n owner .",'
                    '"workdir":"/srv/dark"}); text(r.output);'
                ),
            },
        },
        {
            "timestamp": "2026-08-31T22:54:21.000Z",
            "type": "response_item",
            "payload": {
                "type": "custom_tool_call_output",
                "call_id": "call-custom-exec",
                "output": "owner result",
            },
        },
        {
            "timestamp": "2026-08-31T22:54:24.000Z",
            "type": "event_msg",
            "payload": {
                "type": "token_count",
                "info": {
                    "last_token_usage": {
                        "input_tokens": 200,
                        "cached_input_tokens": 10,
                        "output_tokens": 10,
                        "total_tokens": 210,
                    },
                },
            },
        },
    ]


def distinct_bundle_rows() -> list[dict]:
    rows = custom_tool_attribution_rows()[:1]
    for index, commands in enumerate(
        (("rg -n owner .", "git status --short"), ("pytest -q", "git push origin main")),
        start=1,
    ):
        call_id = f"call-bundle-{index}"
        rows.extend(
            [
                {
                    "timestamp": f"2026-08-31T22:5{index}:20.000Z",
                    "type": "response_item",
                    "payload": {
                        "type": "custom_tool_call",
                        "call_id": call_id,
                        "name": "exec",
                        "input": (
                            f'const a = await tools.exec_command({{"cmd":{json.dumps(commands[0])}}}); '
                            f'const b = await tools.exec_command({{"cmd":{json.dumps(commands[1])}}});'
                        ),
                    },
                },
                {
                    "timestamp": f"2026-08-31T22:5{index}:21.000Z",
                    "type": "response_item",
                    "payload": {
                        "type": "custom_tool_call_output",
                        "call_id": call_id,
                        "output": f"bundle {index} result",
                    },
                },
                {
                    "timestamp": f"2026-08-31T22:5{index}:24.000Z",
                    "type": "event_msg",
                    "payload": {
                        "type": "token_count",
                        "info": {
                            "last_token_usage": {
                                "input_tokens": 100 * index,
                                "cached_input_tokens": 10,
                                "output_tokens": 10,
                                "total_tokens": 100 * index + 10,
                            }
                        },
                    },
                },
            ]
        )
    return rows


def process_linkage_rows() -> list[dict]:
    rows = [
        {
            "timestamp": "2026-09-02T10:00:00.000Z",
            "type": "session_meta",
            "payload": {
                "id": "process-linkage-session",
                "timestamp": "2026-09-02T10:00:00.000Z",
                "cwd": "/srv/voice",
                "originator": "codex",
            },
        },
        {
            "timestamp": "2026-09-02T10:00:01.000Z",
            "type": "response_item",
            "payload": {
                "type": "custom_tool_call",
                "call_id": "call-validation",
                "name": "exec",
                "input": (
                    'const r = await tools.exec_command({cmd:"./scripts/validate.sh",'
                    'workdir:"/srv/voice"}); text(r.output); '
                    'if(r.session_id) text(`SESSION_ID=${r.session_id}`);'
                ),
            },
        },
        {
            "timestamp": "2026-09-02T10:00:02.000Z",
            "type": "response_item",
            "payload": {
                "type": "custom_tool_call_output",
                "call_id": "call-validation",
                "output": "Script running\nSESSION_ID=42",
            },
        },
        {
            "timestamp": "2026-09-02T10:00:03.000Z",
            "type": "event_msg",
            "payload": {
                "type": "token_count",
                "info": {
                    "last_token_usage": {
                        "input_tokens": 100,
                        "cached_input_tokens": 80,
                        "output_tokens": 5,
                        "total_tokens": 105,
                    }
                },
            },
        },
    ]
    for index in (1, 2):
        rows.extend(
            [
                {
                    "timestamp": f"2026-09-02T10:00:0{index + 3}.000Z",
                    "type": "response_item",
                    "payload": {
                        "type": "custom_tool_call",
                        "call_id": f"call-poll-{index}",
                        "name": "exec",
                        "input": (
                            "const r = await tools.write_stdin({session_id:42,chars:\"\","
                            "yield_time_ms:30000}); text(r.output);"
                        ),
                    },
                },
                {
                    "timestamp": f"2026-09-02T10:00:0{index + 4}.000Z",
                    "type": "response_item",
                    "payload": {
                        "type": "custom_tool_call_output",
                        "call_id": f"call-poll-{index}",
                        "output": "still running" if index == 1 else "validation ok",
                    },
                },
                {
                    "timestamp": f"2026-09-02T10:00:0{index + 5}.000Z",
                    "type": "event_msg",
                    "payload": {
                        "type": "token_count",
                        "info": {
                            "last_token_usage": {
                                "input_tokens": 200,
                                "cached_input_tokens": 190,
                                "output_tokens": 5,
                                "total_tokens": 205,
                            }
                        },
                    },
                },
            ]
        )
    return rows


def repeated_heartbeat_rows() -> list[dict]:
    rows = [fixture_rows()[0]]
    for index, (timestamp, total) in enumerate(
        zip(
            (
                "2026-06-01T20:00:00.000Z",
                "2026-06-01T20:30:00.000Z",
                "2026-06-01T21:00:00.000Z",
                "2026-06-01T21:30:00.000Z",
            ),
            (1000, 1400, 1900, 2500),
        ),
        start=1,
    ):
        call_id = f"heartbeat-{index}"
        rows.extend(
            [
                {
                    "timestamp": timestamp,
                    "type": "response_item",
                    "payload": {
                        "type": "function_call",
                        "call_id": call_id,
                        "name": "shell_command",
                        "arguments": json.dumps(
                            {
                                "command": (
                                    "python3 state/ops-harness/scripts/run_watchdog_cycle.py "
                                    "--dispatch-mode real"
                                )
                            }
                        ),
                    },
                },
                {
                    "timestamp": timestamp.replace("00.000Z", "01.000Z"),
                    "type": "response_item",
                    "payload": {
                        "type": "function_call_output",
                        "call_id": call_id,
                        "output": '{"name":"GOS watchdog 30m","ok":true}',
                    },
                },
                {
                    "timestamp": timestamp.replace("00.000Z", "02.000Z"),
                    "type": "event_msg",
                    "payload": {
                        "type": "token_count",
                        "info": {
                            "last_token_usage": {
                                "input_tokens": total - 10,
                                "cached_input_tokens": total - 30,
                                "output_tokens": 10,
                                "total_tokens": total,
                            }
                        },
                    },
                },
            ]
        )
    return rows


class CliTests(unittest.TestCase):
    def test_custom_wrapper_combines_nested_tools_canonically(self) -> None:
        payload = {
            "name": "exec",
            "input": (
                'const write = await tools.write_stdin({"session_id": 7}); '
                'const run = await tools.exec_command({"cmd":"git status --short"});'
            ),
        }
        self.assertEqual(
            command_from_call_payload(payload),
            "mixed:exec_command+write_stdin",
        )
        bundle = bundle_from_call_payload(payload, command_from_call_payload(payload))
        self.assertEqual(bundle["wrapper"], "exec")
        self.assertEqual(bundle["tools"], ["write_stdin", "exec_command"])
        self.assertEqual(bundle["commands"], ["git status --short"])
        self.assertEqual(bundle["process_ids"], ["7"])
        self.assertEqual(
            bundle["activity"]["display"],
            "read/search git status --short",
        )

    def test_help_returns_zero(self) -> None:
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            self.assertEqual(main([]), 0)
        self.assertIn("Local-first token burn profiler", stdout.getvalue())

    def test_scan_and_du_use_last_token_usage(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            evidence = root / "rollout-2026-06-01T22-54-15-test.jsonl"
            db_path = root / "toolburn.sqlite"
            write_jsonl(evidence, fixture_rows())

            scan_out = io.StringIO()
            with contextlib.redirect_stdout(scan_out):
                self.assertEqual(
                    main(["scan", "--db", str(db_path), "--source", f"openclaw={evidence}"]),
                    0,
                )
            self.assertIn("2 token events", scan_out.getvalue())

            du_out = io.StringIO()
            with contextlib.redirect_stdout(du_out):
                self.assertEqual(main(["du", "--db", str(db_path), "--by", "actor"]), 0)
            output = du_out.getvalue()
            self.assertIn("315 raw", output)
            self.assertIn("background.openclaw.gos-watchdog-30m", output)

            tool_out = io.StringIO()
            with contextlib.redirect_stdout(tool_out):
                self.assertEqual(main(["top", "--db", str(db_path), "--by", "tool"]), 0)
            self.assertIn("run_watchdog_cycle.py", tool_out.getvalue())

    def test_explain_for_agent_exports_compact_handoff(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            evidence = root / "rollout-2026-06-01T22-54-15-test.jsonl"
            db_path = root / "toolburn.sqlite"
            write_jsonl(evidence, fixture_rows())
            main(["scan", "--db", str(db_path), "--openclaw", str(evidence)])

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                self.assertEqual(
                    main(
                        [
                            "explain",
                            "--db",
                            str(db_path),
                            "background.openclaw.gos-watchdog-30m",
                            "--for-agent",
                        ]
                    ),
                    0,
                )
            output = stdout.getvalue()
            self.assertIn("Toolburn finding", output)
            self.assertIn("context_leak", output)
            self.assertIn("Agent handoff JSON:", output)

    def test_recent_scans_defaults_and_prints_actors_and_tools(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            sessions = root / "sessions"
            evidence = sessions / "rollout-2026-06-01T22-54-15-test.jsonl"
            db_path = root / "toolburn.sqlite"
            write_jsonl(evidence, fixture_rows())

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                self.assertEqual(
                    main(
                        [
                            "recent",
                            "--hours",
                            "999999",
                            "--db",
                            str(db_path),
                            "--codex",
                            str(root / "missing-codex"),
                            "--openclaw",
                            str(sessions),
                            "--copilot",
                            str(root / "missing-copilot"),
                        ]
                    ),
                    0,
                )
            output = stdout.getvalue()
            self.assertIn("Top actors", output)
            self.assertIn("Top tool work", output)
            self.assertIn("background.openclaw.gos-watchdog-30m", output)
            self.assertIn("run_watchdog_cycle.py", output)
            self.assertNotIn("Semantic coverage", output)
            self.assertIn("uncached", output)

    def test_24h_shortcut_prints_recent_burn(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            sessions = root / "sessions"
            evidence = sessions / "rollout-2026-06-01T22-54-15-test.jsonl"
            db_path = root / "toolburn.sqlite"
            write_jsonl(evidence, fixture_rows())

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                self.assertEqual(
                    main(
                        [
                            "24h",
                            "--db",
                            str(db_path),
                            "--codex",
                            str(root / "missing-codex"),
                            "--openclaw",
                            str(sessions),
                            "--copilot",
                            str(root / "missing-copilot"),
                        ]
                    ),
                    0,
                )
            output = stdout.getvalue()
            self.assertIn("scanned", output)
            self.assertIn("since ", output)
            self.assertIn("Top actors", output)
            self.assertIn("Top tool work", output)
            self.assertNotIn("Semantic coverage", output)

    def test_recent_shortcuts_resolve_to_exact_hours(self) -> None:
        parser = build_parser()
        for shortcut, expected_hours in (("24h", 24.0), ("48h", 48.0), ("7d", 168.0)):
            with self.subTest(shortcut=shortcut):
                args = parser.parse_args([shortcut])
                self.assertEqual(args.hours, expected_hours)

    def test_recent_ignores_source_files_outside_requested_window(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            evidence = root / "sessions" / "rollout-2026-06-01T22-54-15-test.jsonl"
            db_path = root / "toolburn.sqlite"
            write_jsonl(evidence, fixture_rows())
            os.utime(evidence, (0, 0))

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                self.assertEqual(
                    main(
                        [
                            "recent",
                            "--hours",
                            "24",
                            "--db",
                            str(db_path),
                            "--codex",
                            str(evidence.parent),
                            "--openclaw",
                            str(root / "missing-openclaw"),
                            "--copilot",
                            str(root / "missing-copilot"),
                        ]
                    ),
                    0,
                )
            self.assertIn("scanned 0 changed files, ignored 1 outside window", stdout.getvalue())

    def test_tool_report_orders_by_uncached_context_not_cached_raw_tokens(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            evidence = root / "rollout-2026-06-01T22-54-15-test.jsonl"
            db_path = root / "toolburn.sqlite"
            write_jsonl(evidence, cached_validate_attribution_rows())

            main(["scan", "--db", str(db_path), "--codex", str(evidence)])

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                self.assertEqual(main(["top", "--db", str(db_path), "--by", "tool"]), 0)
            output = stdout.getvalue()
            self.assertIn("uncached", output)
            self.assertLess(
                output.index("python3 expensive_context.py"),
                output.index("./scripts/validate.sh"),
            )

    def test_scan_attributes_custom_exec_wrapper_to_nested_command(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            evidence = root / "rollout-2026-08-31T22-54-15-test.jsonl"
            db_path = root / "toolburn.sqlite"
            write_jsonl(evidence, custom_tool_attribution_rows())

            scan_out = io.StringIO()
            with contextlib.redirect_stdout(scan_out):
                self.assertEqual(main(["scan", "--db", str(db_path), "--codex", str(evidence)]), 0)
            self.assertIn("1 invocations", scan_out.getvalue())

            tool_out = io.StringIO()
            with contextlib.redirect_stdout(tool_out):
                self.assertEqual(main(["top", "--db", str(db_path), "--by", "tool"]), 0)
            output = tool_out.getvalue()
            self.assertIn("rg -n owner .", output)
            self.assertNotIn("no-tool-context", output)

    def test_same_generic_tool_label_retains_distinct_invocation_bundles(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            evidence = root / "rollout-2026-08-31T22-54-15-test.jsonl"
            db_path = root / "toolburn.sqlite"
            write_jsonl(evidence, distinct_bundle_rows())
            main(["scan", "--db", str(db_path), "--codex", str(evidence)])

            episodes = episode_report(db_path, limit=None)
            self.assertEqual(len(episodes), 2)
            self.assertEqual(
                {row["normalized_command"] for row in episodes},
                {"multiple:exec_command"},
            )
            bundles = [json.loads(row["bundle_json"])["commands"] for row in episodes]
            self.assertIn(["rg -n owner .", "git status --short"], bundles)
            self.assertIn(["pytest -q", "git push origin main"], bundles)

    def test_tool_work_links_polling_to_originating_command(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            evidence = root / "rollout-2026-09-02T10-00-00-test.jsonl"
            db_path = root / "toolburn.sqlite"
            write_jsonl(evidence, process_linkage_rows())
            main(["scan", "--db", str(db_path), "--codex", str(evidence)])

            rows = tool_work_report(db_path, limit=10)
            poll = next(row for row in rows if row["activity"]["action"] == "poll")
            self.assertEqual(poll["calls"], 2)
            self.assertEqual(poll["sessions"], 1)
            self.assertEqual(
                poll["activity"]["display"],
                "poll /srv/voice/scripts/validate.sh",
            )

    def test_repeated_background_command_is_one_cumulative_tool_work_row(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            evidence = root / "rollout-2026-06-01T20-00-00-heartbeat.jsonl"
            db_path = root / "toolburn.sqlite"
            write_jsonl(evidence, repeated_heartbeat_rows())
            main(["scan", "--db", str(db_path), "--openclaw", str(evidence)])

            rows = tool_work_report(db_path, limit=10)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["calls"], 4)
            self.assertEqual(rows[0]["raw_tokens"], 6800)
            self.assertEqual(
                rows[0]["activity"]["display"],
                "run /root/.openclaw/workspace/state/ops-harness/scripts/run_watchdog_cycle.py",
            )
            self.assertIn("background.openclaw.gos-watchdog-30m", rows[0]["label"])

    def test_apply_patch_bundle_retains_affected_target(self) -> None:
        payload = {
            "name": "exec",
            "input": (
                'const patch = "*** Begin Patch\\n'
                "*** Update File: sites/easyaivoice.com/public/run-submit.php\\n"
                "*** Update File: sites/easyaivoice.com/public/status.php\\n"
                '*** End Patch"; text(await tools.apply_patch(patch));'
            ),
        }
        bundle = bundle_from_call_payload(payload, "apply_patch", "/srv/voice")
        self.assertEqual(
            bundle["patch_paths"],
            [
                "sites/easyaivoice.com/public/run-submit.php",
                "sites/easyaivoice.com/public/status.php",
            ],
        )
        self.assertEqual(
            bundle["activity"]["display"],
            "edit /srv/voice/sites/easyaivoice.com/public/* (2 files)",
        )

    def test_non_read_command_does_not_promote_repository_slug_to_path(self) -> None:
        command = "gh pr merge 197 --repo DarkExec/app --squash && gh pr view 197 --repo DarkExec/app"
        bundle = bundle_from_call_payload(
            {
                "name": "exec_command",
                "arguments": {
                    "cmd": command,
                    "workdir": "/tmp",
                },
            },
            command,
            "/tmp",
        )
        self.assertEqual(bundle["activity"]["action"], "run")
        self.assertIn("gh pr merge 197 --repo DarkExec/app", bundle["activity"]["display"])
        self.assertNotIn("/tmp/DarkExec/app", bundle["activity"]["display"])

    def test_inline_interpreter_does_not_claim_mentioned_file_was_executed(self) -> None:
        command = 'php -r \'require "includes/auth.php"; echo "ok";\''
        bundle = bundle_from_call_payload(
            {
                "name": "exec_command",
                "arguments": {
                    "cmd": command,
                    "workdir": "/srv/site",
                },
            },
            command,
            "/srv/site",
        )
        self.assertEqual(bundle["activity"]["action"], "run")
        self.assertIn("php -r", bundle["activity"]["display"])
        self.assertNotEqual(
            bundle["activity"]["display"],
            "run /srv/site/includes/auth.php",
        )

    def test_read_only_shell_bundle_promotes_the_file_target(self) -> None:
        command = (
            'cat AGENTS.md; rg -n "ToolBurn|heartbeat" '
            "/root/.codex/memories/MEMORY.md | head -100; "
            "git status --short; git rev-parse HEAD; git log -5 --oneline"
        )
        bundle = bundle_from_call_payload(
            {"name": "exec_command"},
            command,
            "/srv/dark/repos/toolburn",
        )
        self.assertEqual(bundle["activity"]["action"], "read/search")
        self.assertIn(
            "/root/.codex/memories/MEMORY.md",
            bundle["activity"]["display"],
        )

    def test_scan_skips_unchanged_sources_and_reparses_changed_sources(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            evidence = root / "rollout-2026-08-31T22-54-15-test.jsonl"
            db_path = root / "toolburn.sqlite"
            rows = custom_tool_attribution_rows()
            write_jsonl(evidence, rows)

            main(["scan", "--db", str(db_path), "--codex", str(evidence)])

            unchanged_out = io.StringIO()
            with contextlib.redirect_stdout(unchanged_out):
                self.assertEqual(main(["scan", "--db", str(db_path), "--codex", str(evidence)]), 0)
            self.assertIn("scanned 0 changed files, skipped 1 unchanged", unchanged_out.getvalue())

            rows[-1]["payload"]["info"]["last_token_usage"]["output_tokens"] = 11
            rows[-1]["payload"]["info"]["last_token_usage"]["total_tokens"] = 211
            write_jsonl(evidence, rows)

            changed_out = io.StringIO()
            with contextlib.redirect_stdout(changed_out):
                self.assertEqual(main(["scan", "--db", str(db_path), "--codex", str(evidence)]), 0)
            self.assertIn("scanned 1 changed files", changed_out.getvalue())

            tool_out = io.StringIO()
            with contextlib.redirect_stdout(tool_out):
                self.assertEqual(main(["top", "--db", str(db_path), "--by", "tool"]), 0)
            self.assertIn("211 raw", tool_out.getvalue())

    def test_recent_applies_explicit_versioned_semantics_to_stable_episode_id(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            evidence = root / "rollout-2026-08-31T22-54-15-test.jsonl"
            db_path = root / "toolburn.sqlite"
            catalog_path = root / "semantics.json"
            write_jsonl(evidence, custom_tool_attribution_rows())
            main(["scan", "--db", str(db_path), "--codex", str(evidence)])
            episode_id = episode_report(db_path, limit=1)[0]["episode_id"]
            catalog_path.write_text(
                json.dumps(
                    {
                        "schema": "toolburn-semantics/v1",
                        "definitions": [
                            {
                                "id": "maintenance.backup-setup",
                                "version": 1,
                                "description": "Set up durable backups during maintenance.",
                            }
                        ],
                        "assignments": [
                            {
                                "episode_id": episode_id,
                                "semantic_id": "maintenance.backup-setup",
                                "semantic_version": 1,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                self.assertEqual(
                    main(
                        [
                            "recent",
                            "--hours",
                            "999999",
                            "--db",
                            str(db_path),
                            "--no-scan",
                            "--semantics",
                            str(catalog_path),
                        ]
                    ),
                    0,
                )
            output = stdout.getvalue()
            self.assertIn("1/1 episodes labeled", output)
            self.assertIn("maintenance.backup-setup@1", output)

    def test_semantic_catalog_rejects_multiple_assignments_for_one_episode(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "semantics.json"
            definitions = [
                {"id": "maintenance.one", "version": 1, "description": "One."},
                {"id": "maintenance.two", "version": 1, "description": "Two."},
            ]
            episode_id = "0123456789abcdef01234567"
            path.write_text(
                json.dumps(
                    {
                        "schema": "toolburn-semantics/v1",
                        "definitions": definitions,
                        "assignments": [
                            {
                                "episode_id": episode_id,
                                "semantic_id": "maintenance.one",
                                "semantic_version": 1,
                            },
                            {
                                "episode_id": episode_id,
                                "semantic_id": "maintenance.two",
                                "semantic_version": 1,
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(SemanticCatalogError, "multiple semantic assignments"):
                load_semantic_catalog(path)

    def test_scan_supports_github_copilot_events_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            evidence = root / "session-state" / "copilot-session-1" / "events.jsonl"
            db_path = root / "toolburn.sqlite"
            write_jsonl(evidence, copilot_fixture_rows())

            scan_out = io.StringIO()
            with contextlib.redirect_stdout(scan_out):
                self.assertEqual(main(["scan", "--db", str(db_path), "--copilot", str(root)]), 0)
            self.assertIn("2 token events", scan_out.getvalue())

            du_out = io.StringIO()
            with contextlib.redirect_stdout(du_out):
                self.assertEqual(main(["du", "--db", str(db_path), "--by", "actor"]), 0)
            output = du_out.getvalue()
            self.assertIn("400 raw", output)
            self.assertIn("260 cached", output)
            self.assertIn("human.github-copilot.workspace.toolburn", output)

    def test_sources_command_lists_support_status(self) -> None:
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            self.assertEqual(main(["sources"]), 0)
        output = stdout.getvalue()
        self.assertIn("github-copilot", output)
        self.assertIn("experimental", output)
        self.assertIn("claude-code", output)
        self.assertIn("untested", output)

    def test_update_fast_forwards_checkout_and_installs_wrapper(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            remote = root / "remote.git"
            seed = root / "seed"
            install = root / "install"
            bin_dir = root / "bin"

            git("init", "--bare", str(remote), cwd=root)
            git("init", "-b", "main", cwd=seed.mkdir() or seed)
            (seed / "toolburn").write_text("#!/usr/bin/env bash\n", encoding="utf-8")
            first = commit_all(seed, "first")
            git("remote", "add", "origin", str(remote), cwd=seed)
            git("push", "-u", "origin", "main", cwd=seed)
            git("clone", "--branch", "main", str(remote), str(install), cwd=root)

            (seed / "toolburn").write_text("#!/usr/bin/env bash\n# updated\n", encoding="utf-8")
            second = commit_all(seed, "second")
            git("push", cwd=seed)

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                self.assertEqual(
                    main(
                        [
                            "update",
                            "--install-dir",
                            str(install),
                            "--bin-dir",
                            str(bin_dir),
                        ]
                    ),
                    0,
                )
            output = stdout.getvalue()
            self.assertIn(f"commit {first} -> {second}", output)
            self.assertTrue((bin_dir / "toolburn").exists())
            self.assertEqual(second, git("rev-parse", "--short", "HEAD", cwd=install))

    def test_update_refuses_dirty_checkout_without_force(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            remote = root / "remote.git"
            seed = root / "seed"
            install = root / "install"

            git("init", "--bare", str(remote), cwd=root)
            git("init", "-b", "main", cwd=seed.mkdir() or seed)
            (seed / "toolburn").write_text("#!/usr/bin/env bash\n", encoding="utf-8")
            commit_all(seed, "first")
            git("remote", "add", "origin", str(remote), cwd=seed)
            git("push", "-u", "origin", "main", cwd=seed)
            git("clone", "--branch", "main", str(remote), str(install), cwd=root)
            (install / "local.txt").write_text("dirty\n", encoding="utf-8")

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                self.assertEqual(
                    main(["update", "--install-dir", str(install), "--skip-install"]),
                    2,
                )
            self.assertIn("has local changes", stdout.getvalue())

    def test_codex_workspace_sessions_are_labeled_human(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            evidence = root / "rollout-2026-06-01T22-54-15-test.jsonl"
            db_path = root / "toolburn.sqlite"
            write_jsonl(evidence, codex_fixture_rows())

            main(["scan", "--db", str(db_path), "--codex", str(evidence)])

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                self.assertEqual(main(["du", "--db", str(db_path), "--by", "actor"]), 0)
            self.assertIn("human.codex.workspace.pager", stdout.getvalue())

    def test_openclaw_cron_and_dream_diary_sessions_are_labeled(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "toolburn.sqlite"
            cron_evidence = root / "rollout-2026-06-01T22-54-15-cron.jsonl"
            dream_evidence = root / "rollout-2026-06-01T22-54-15-dream.jsonl"
            write_jsonl(cron_evidence, openclaw_cron_fixture_rows())
            write_jsonl(dream_evidence, openclaw_dream_fixture_rows())

            main(["scan", "--db", str(db_path), "--openclaw", str(root)])

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                self.assertEqual(main(["du", "--db", str(db_path), "--by", "actor"]), 0)
            output = stdout.getvalue()
            self.assertIn("background.openclaw.cron.voice-models-daily-maintain", output)
            self.assertIn("background.openclaw.dream-diary", output)

            tool_out = io.StringIO()
            with contextlib.redirect_stdout(tool_out):
                self.assertEqual(main(["top", "--db", str(db_path), "--by", "tool"]), 0)
            self.assertIn("no-tool-context:background.openclaw.dream-diary", tool_out.getvalue())

    def test_openclaw_direct_sessions_are_labeled_human(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "toolburn.sqlite"
            direct_evidence = root / "rollout-2026-06-01T22-54-15-direct.jsonl"
            write_jsonl(direct_evidence, openclaw_direct_fixture_rows())

            main(["scan", "--db", str(db_path), "--openclaw", str(direct_evidence)])

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                self.assertEqual(main(["du", "--db", str(db_path), "--by", "actor"]), 0)
            output = stdout.getvalue()
            self.assertIn("human.openclaw.direct-session.0000000000", output)
            self.assertNotIn("background.openclaw.direct-session", output)

    def test_openclaw_inbound_metadata_labels_human_and_message_tool_context(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "toolburn.sqlite"
            evidence = root / "rollout-2026-06-01T22-54-15-inbound.jsonl"
            write_jsonl(evidence, openclaw_inbound_fixture_rows())

            main(["scan", "--db", str(db_path), "--openclaw", str(evidence)])

            actor_out = io.StringIO()
            with contextlib.redirect_stdout(actor_out):
                self.assertEqual(main(["du", "--db", str(db_path), "--by", "actor"]), 0)
            self.assertIn("human.openclaw.tele" + "gram-direct", actor_out.getvalue())

            tool_out = io.StringIO()
            with contextlib.redirect_stdout(tool_out):
                self.assertEqual(main(["top", "--db", str(db_path), "--by", "tool"]), 0)
            output = tool_out.getvalue()
            self.assertIn("message(action=send)", output)
            self.assertNotIn("no-tool-context:human.openclaw.tele" + "gram-direct", output)

    def test_openclaw_direct_alias_merges_with_concrete_session(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "toolburn.sqlite"
            direct_evidence = root / "rollout-2026-06-01T22-54-15-direct.jsonl"
            inbound_evidence = root / "rollout-2026-06-01T22-55-15-inbound.jsonl"
            write_jsonl(direct_evidence, openclaw_direct_fixture_rows())
            write_jsonl(inbound_evidence, openclaw_inbound_fixture_rows())

            main(["scan", "--db", str(db_path), "--openclaw", str(root)])

            actor_out = io.StringIO()
            with contextlib.redirect_stdout(actor_out):
                self.assertEqual(main(["du", "--db", str(db_path), "--by", "actor"]), 0)
            output = actor_out.getvalue()
            self.assertIn("human.openclaw.direct-session.0000000000", output)
            self.assertNotIn("human.openclaw.tele" + "gram-direct", output)

            tool_out = io.StringIO()
            with contextlib.redirect_stdout(tool_out):
                self.assertEqual(main(["top", "--db", str(db_path), "--by", "tool"]), 0)
            self.assertNotIn(
                "no-tool-context:human.openclaw.tele" + "gram-direct",
                tool_out.getvalue(),
            )

    def test_openclaw_inbound_heartbeat_mentions_do_not_create_background_label(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "toolburn.sqlite"
            evidence = root / "rollout-2026-06-01T22-54-15-inbound-mention.jsonl"
            write_jsonl(evidence, openclaw_inbound_mentions_heartbeat_fixture_rows())

            main(["scan", "--db", str(db_path), "--openclaw", str(evidence)])

            actor_out = io.StringIO()
            with contextlib.redirect_stdout(actor_out):
                self.assertEqual(main(["du", "--db", str(db_path), "--by", "actor"]), 0)
            output = actor_out.getvalue()
            self.assertIn("human.openclaw.tele" + "gram-direct", output)
            self.assertNotIn("background.openclaw.heartbeat", output)

    def test_openclaw_legacy_discord_message_labels_human_channel(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "toolburn.sqlite"
            evidence = root / "rollout-2026-06-01T22-54-15-legacy-discord.jsonl"
            write_jsonl(evidence, openclaw_legacy_discord_fixture_rows())

            main(["scan", "--db", str(db_path), "--openclaw", str(evidence)])

            actor_out = io.StringIO()
            with contextlib.redirect_stdout(actor_out):
                self.assertEqual(main(["du", "--db", str(db_path), "--by", "actor"]), 0)
            self.assertIn("human.openclaw.discord-channel", actor_out.getvalue())

    def test_reports_filter_by_actor_type(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "toolburn.sqlite"
            codex_evidence = root / "codex" / "rollout-2026-06-01T22-54-15-codex.jsonl"
            openclaw_evidence = root / "openclaw" / "rollout-2026-06-01T22-54-15-openclaw.jsonl"
            write_jsonl(codex_evidence, codex_fixture_rows())
            write_jsonl(openclaw_evidence, fixture_rows())

            main(
                [
                    "scan",
                    "--db",
                    str(db_path),
                    "--codex",
                    str(codex_evidence),
                    "--openclaw",
                    str(openclaw_evidence),
                ]
            )

            human_out = io.StringIO()
            with contextlib.redirect_stdout(human_out):
                self.assertEqual(
                    main(["top", "--db", str(db_path), "--by", "actor", "--actor-type", "human"]),
                    0,
                )
            self.assertIn("human.codex.workspace.pager", human_out.getvalue())
            self.assertNotIn("background.openclaw", human_out.getvalue())

            background_out = io.StringIO()
            with contextlib.redirect_stdout(background_out):
                self.assertEqual(
                    main(
                        [
                            "top",
                            "--db",
                            str(db_path),
                            "--by",
                            "actor",
                            "--actor-type",
                            "background",
                        ]
                    ),
                    0,
                )
            self.assertIn("background.openclaw.gos-watchdog-30m", background_out.getvalue())
            self.assertNotIn("human.codex.workspace.pager", background_out.getvalue())


if __name__ == "__main__":
    unittest.main()
