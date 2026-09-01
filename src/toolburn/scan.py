"""Offline parsers for local agent JSONL evidence."""

from __future__ import annotations

import hashlib
import json
import re
from bisect import bisect_right
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from toolburn.schema import connect


TOKEN_EVENT_TYPE = "token_count"
ROLLOUT_GLOB = "rollout-*.jsonl"
COPILOT_EVENTS_GLOB = "events.jsonl"
SCAN_PARSER_VERSION = 2
OPERATION_ORDER = (
    "contextRecovery",
    "execution",
    "validation",
    "delivery",
    "edit",
    "web",
    "visual",
    "planning",
    "wait",
    "telemetry",
)


@dataclass(frozen=True)
class SourceSpec:
    label: str
    path: Path


@dataclass
class ParsedSession:
    session_id: str
    actor_id: str
    actor_type: str
    source: str
    path: Path
    workspace: str
    started_at: str
    ended_at: str
    label_confidence: float
    metadata: dict[str, Any] = field(default_factory=dict)


def scan_sources(
    db_path: Path,
    sources: Iterable[SourceSpec],
    modified_since: str | None = None,
) -> dict[str, int]:
    minimum_mtime = iso_timestamp(modified_since) if modified_since else None
    with connect(db_path) as conn:
        counts = {
            "files": 0,
            "files_seen": 0,
            "files_skipped": 0,
            "files_outside_window": 0,
            "sessions": 0,
            "token_events": 0,
            "invocations": 0,
        }
        for source in sources:
            for path in iter_jsonl_paths(source.path, source.label):
                counts["files_seen"] += 1
                try:
                    stat = path.stat()
                except OSError:
                    continue
                if minimum_mtime is not None and stat.st_mtime < minimum_mtime:
                    counts["files_outside_window"] += 1
                    continue
                if scan_cache_matches(conn, path, source.label, stat.st_size, stat.st_mtime_ns):
                    counts["files_skipped"] += 1
                    continue
                parsed = parse_session_file(path, source.label)
                if parsed is None:
                    update_scan_cache(conn, path, source.label, stat.st_size, stat.st_mtime_ns, "")
                    continue
                clear_cached_source(conn, path)
                upsert_session(conn, parsed)
                counts["files"] += 1
                counts["sessions"] += 1
                counts["token_events"] += insert_token_events(conn, parsed)
                counts["invocations"] += insert_invocations(conn, parsed)
                update_scan_cache(
                    conn,
                    path,
                    source.label,
                    stat.st_size,
                    stat.st_mtime_ns,
                    parsed.session_id,
                )
        conn.commit()
    return counts


def iso_timestamp(value: str) -> float:
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    return datetime.fromisoformat(normalized).timestamp()


def scan_cache_matches(conn, path: Path, source_label: str, size_bytes: int, mtime_ns: int) -> bool:
    row = conn.execute(
        "select source_label, size_bytes, mtime_ns, parser_version from scan_cache where source_path = ?",
        (str(path),),
    ).fetchone()
    return bool(
        row
        and row["source_label"] == source_label
        and int(row["size_bytes"]) == size_bytes
        and int(row["mtime_ns"]) == mtime_ns
        and int(row["parser_version"]) == SCAN_PARSER_VERSION
    )


def clear_cached_source(conn, path: Path) -> None:
    session_rows = conn.execute(
        "select session_id from sessions where path = ?", (str(path),)
    ).fetchall()
    if not session_rows:
        return
    session_ids = [str(row["session_id"]) for row in session_rows]
    conn.execute("delete from token_events where source_path = ?", (str(path),))
    for session_id in session_ids:
        conn.execute("delete from invocations where session_id = ?", (session_id,))
        conn.execute("delete from sessions where session_id = ?", (session_id,))


def update_scan_cache(
    conn,
    path: Path,
    source_label: str,
    size_bytes: int,
    mtime_ns: int,
    session_id: str,
) -> None:
    conn.execute(
        """
        insert or replace into scan_cache(
          source_path, source_label, size_bytes, mtime_ns, parser_version, session_id, scanned_at
        ) values(?, ?, ?, ?, ?, ?, ?)
        """,
        (
            str(path),
            source_label,
            size_bytes,
            mtime_ns,
            SCAN_PARSER_VERSION,
            session_id,
            datetime.now(timezone.utc).isoformat(),
        ),
    )


def iter_jsonl_paths(path: Path, source_label: str = "") -> Iterable[Path]:
    if path.is_file():
        if path.suffix == ".jsonl":
            yield path
        return
    if not path.exists():
        return
    if is_copilot_source(source_label, path):
        yield from sorted(path.rglob(COPILOT_EVENTS_GLOB))
        return
    yield from sorted(path.rglob(ROLLOUT_GLOB))


def parse_session_file(path: Path, source_label: str) -> ParsedSession | None:
    if is_copilot_source(source_label, path):
        return parse_copilot_session_file(path, source_label)

    meta: dict[str, Any] = {}
    token_rows: list[dict[str, Any]] = []
    invocations: list[dict[str, Any]] = []
    evidence_text: list[str] = []
    pending_calls: dict[str, dict[str, Any]] = {}

    for line_no, row in iter_json_rows(path):
        payload = row.get("payload") or {}
        row_type = row.get("type")
        ts = row.get("timestamp") or payload.get("timestamp") or ""

        if row_type == "session_meta":
            meta.update(payload)
            continue

        if row_type == "event_msg":
            if payload.get("type") == TOKEN_EVENT_TYPE:
                token_rows.append({"row": row, "line_no": line_no})
                continue
            message = payload.get("message")
            if isinstance(message, str) and payload.get("type") in {
                "user_message",
                "agent_message",
                "task_started",
                "task_complete",
            }:
                evidence_text.append(evidence_excerpt(message))
            continue

        if row_type != "response_item" or not isinstance(payload, dict):
            continue

        text = compact_text(payload)
        if text:
            evidence_text.append(evidence_excerpt(text))

        if payload.get("type") in {"function_call", "custom_tool_call"}:
            call_id = payload.get("call_id") or payload.get("id") or stable_id(
                path, "call", str(line_no)
            )
            command = command_from_call_payload(payload)
            if command:
                pending_calls[call_id] = {
                    "call_id": call_id,
                    "command": command,
                    "started_at": ts,
                    "line_no": line_no,
                    "cwd": meta.get("cwd") or "",
                    "operation_context": operation_context_from_call_payload(payload, command),
                }
            continue

        if payload.get("type") in {"function_call_output", "custom_tool_call_output"}:
            call_id = payload.get("call_id") or ""
            pending = pending_calls.pop(call_id, None)
            if pending:
                output = str(payload.get("output") or "")
                pending.update(
                    {
                        "ended_at": ts,
                        "output_bytes": len(output.encode("utf-8")),
                        "output_fingerprint": sha256_text(output),
                        "output_shape": output_shape(output),
                    }
                )
                invocations.append(pending)

    session_id = str(meta.get("id") or stable_id(path, "session"))
    actor_id, actor_type, confidence = infer_actor_id(
        source_label, path, meta, evidence_text
    )
    started_at = str(meta.get("timestamp") or first_timestamp(token_rows) or "")
    ended_at = last_timestamp(token_rows) or started_at
    parsed = ParsedSession(
        session_id=session_id,
        actor_id=actor_id,
        actor_type=actor_type,
        source=source_label,
        path=path,
        workspace=str(meta.get("cwd") or ""),
        started_at=started_at,
        ended_at=ended_at,
        label_confidence=confidence,
        metadata={
            "session_meta": scrub_meta(meta),
            "token_rows": token_rows,
            "invocations": invocations,
        },
    )
    return parsed if token_rows or invocations or meta else None


def parse_copilot_session_file(path: Path, source_label: str) -> ParsedSession | None:
    meta: dict[str, Any] = {}
    token_rows: list[dict[str, Any]] = []
    invocations: list[dict[str, Any]] = []
    model = ""
    started_at = ""
    ended_at = ""

    for line_no, row in iter_json_rows(path):

        row_type = row.get("type")
        data = row.get("data") or {}
        ts = str(row.get("timestamp") or data.get("startTime") or "")
        if ts:
            if not started_at:
                started_at = ts
            ended_at = ts

        if row_type == "session.start":
            if isinstance(data, dict):
                meta.update(data)
            continue

        if row_type == "session.model_change" and isinstance(data, dict):
            model = str(data.get("newModel") or model)
            continue

        if row_type == "assistant.usage" and isinstance(data, dict):
            token_rows.append(
                {
                    "line_no": line_no,
                    "ts": ts,
                    "model": str(data.get("model") or model),
                    "usage": usage_from_copilot_data(data),
                }
            )
            continue

        if row_type == "session.shutdown" and isinstance(data, dict):
            for shutdown_model, metrics in (data.get("modelMetrics") or {}).items():
                if not isinstance(metrics, dict):
                    continue
                usage = metrics.get("usage") or {}
                if not isinstance(usage, dict):
                    continue
                token_rows.append(
                    {
                        "line_no": line_no,
                        "ts": ts,
                        "model": str(shutdown_model),
                        "usage": usage_from_copilot_data(usage),
                    }
                )
            continue

        if row_type == "tool.execution_complete" and isinstance(data, dict):
            command = copilot_command_from_tool_complete(data)
            if command:
                invocations.append(
                    {
                        "call_id": data.get("toolCallId") or stable_id(path, "tool", line_no),
                        "command": command,
                        "started_at": "",
                        "ended_at": ts,
                        "line_no": line_no,
                        "cwd": copilot_workspace(meta),
                        "operation_context": operation_context(command),
                        "output_bytes": len(str(data.get("result") or "").encode("utf-8")),
                        "output_fingerprint": sha256_text(str(data.get("result") or "")),
                        "output_shape": output_shape(str(data.get("result") or "")),
                    }
                )

    session_id = str(meta.get("sessionId") or path.parent.name or stable_id(path, "session"))
    workspace = copilot_workspace(meta)
    parsed = ParsedSession(
        session_id=session_id,
        actor_id=infer_copilot_actor_id(session_id, workspace),
        actor_type="human",
        source=source_label,
        path=path,
        workspace=workspace,
        started_at=started_at,
        ended_at=ended_at or started_at,
        label_confidence=0.55,
        metadata={
            "session_meta": scrub_copilot_meta(meta),
            "token_rows": token_rows,
            "invocations": invocations,
        },
    )
    return parsed if token_rows or invocations or meta else None


def upsert_session(conn, parsed: ParsedSession) -> None:
    conn.execute(
        """
        insert into actors(actor_id, actor_type, source, label_confidence, first_seen, last_seen, metadata_json)
        values(?, ?, ?, ?, ?, ?, ?)
        on conflict(actor_id) do update set
          first_seen = min(coalesce(first_seen, excluded.first_seen), excluded.first_seen),
          last_seen = max(coalesce(last_seen, excluded.last_seen), excluded.last_seen),
          label_confidence = max(label_confidence, excluded.label_confidence)
        """,
        (
            parsed.actor_id,
            parsed.actor_type,
            parsed.source,
            parsed.label_confidence,
            parsed.started_at,
            parsed.ended_at,
            json.dumps({"source_path": str(parsed.path)}, sort_keys=True),
        ),
    )
    conn.execute(
        """
        insert into sessions(session_id, actor_id, source, path, workspace, started_at, ended_at, metadata_json)
        values(?, ?, ?, ?, ?, ?, ?, ?)
        on conflict(session_id) do update set
          actor_id=excluded.actor_id,
          source=excluded.source,
          path=excluded.path,
          workspace=excluded.workspace,
          started_at=excluded.started_at,
          ended_at=excluded.ended_at,
          metadata_json=excluded.metadata_json
        """,
        (
            parsed.session_id,
            parsed.actor_id,
            parsed.source,
            str(parsed.path),
            parsed.workspace,
            parsed.started_at,
            parsed.ended_at,
            json.dumps(parsed.metadata["session_meta"], sort_keys=True),
        ),
    )


def insert_token_events(conn, parsed: ParsedSession) -> int:
    inserted = 0
    invocation_by_line = {
        int(invocation["line_no"]): stable_id(
            parsed.path, "invocation", str(invocation["line_no"])
        )
        for invocation in parsed.metadata["invocations"]
    }
    invocation_lines = sorted(invocation_by_line)
    for item in parsed.metadata["token_rows"]:
        if "row" in item:
            row = item["row"]
            payload = row.get("payload") or {}
            info = payload.get("info") or {}
            usage = info.get("last_token_usage") or {}
            ts = row.get("timestamp") or ""
            model = ""
        else:
            usage = item.get("usage") or {}
            ts = item.get("ts") or ""
            model = item.get("model") or ""
        event_id = stable_id(parsed.path, "token", str(item["line_no"]))
        invocation_id = nearest_invocation_id(
            invocation_by_line, invocation_lines, int(item["line_no"])
        )
        conn.execute(
            """
            insert or replace into token_events(
              token_event_id, session_id, actor_id, invocation_id, ts, model,
              input_tokens, cached_input_tokens, output_tokens, raw_total_tokens, source_path
            )
            values(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                parsed.session_id,
                parsed.actor_id,
                invocation_id,
                ts,
                model,
                int(usage.get("input_tokens") or 0),
                int(usage.get("cached_input_tokens") or 0),
                int(usage.get("output_tokens") or 0),
                int(usage.get("total_tokens") or 0),
                str(parsed.path),
            ),
        )
        inserted += 1
    return inserted


def nearest_invocation_id(
    invocation_by_line: dict[int, str], invocation_lines: list[int], token_line: int
) -> str | None:
    index = bisect_right(invocation_lines, token_line) - 1
    if index < 0:
        return None
    return invocation_by_line[invocation_lines[index]]


def insert_invocations(conn, parsed: ParsedSession) -> int:
    inserted = 0
    for invocation in parsed.metadata["invocations"]:
        command = invocation["command"]
        tool_id = stable_id("tool", normalize_command(command))
        conn.execute(
            """
            insert or replace into tools(tool_id, normalized_command, operation_context, executable, cwd, fingerprint, metadata_json)
            values(?, ?, ?, ?, ?, ?, ?)
            """,
            (
                tool_id,
                normalize_command(command),
                invocation.get("operation_context") or operation_context(command),
                executable(command),
                invocation.get("cwd") or parsed.workspace,
                stable_id("cmd", normalize_command(command)),
                "{}",
            ),
        )
        invocation_id = stable_id(parsed.path, "invocation", str(invocation["line_no"]))
        conn.execute(
            """
            insert or replace into invocations(
              invocation_id, session_id, actor_id, tool_id, started_at, ended_at,
              output_bytes, output_fingerprint, output_shape_json
            )
            values(?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                invocation_id,
                parsed.session_id,
                parsed.actor_id,
                tool_id,
                invocation.get("started_at") or "",
                invocation.get("ended_at") or "",
                int(invocation.get("output_bytes") or 0),
                invocation.get("output_fingerprint") or "",
                json.dumps(invocation.get("output_shape") or {}, sort_keys=True),
            ),
        )
        inserted += 1
    return inserted


def infer_actor_id(
    source_label: str, path: Path, meta: dict[str, Any], evidence_text: list[str]
) -> tuple[str, str, float]:
    haystack = "\n".join(evidence_text)
    session_id = str(meta.get("id") or stable_id(path, "session"))[:12]
    openclaw_source = "openclaw" in source_label.lower() or "/.openclaw/" in str(path)
    if openclaw_source and ("GOS watchdog 30m" in haystack or "run_watchdog_cycle.py" in haystack):
        return "background.openclaw.gos-watchdog-30m", "background", 0.8
    if openclaw_source:
        cron_name = openclaw_cron_name(haystack)
        if cron_name:
            return f"background.openclaw.cron.{slug(cron_name)}", "background", 0.72
    if openclaw_source and "Write a dream diary entry" in haystack:
        return "background.openclaw.dream-diary", "background", 0.68
    if openclaw_source and "sessionKey" in haystack and ":direct:" in haystack:
        match = re.search(r"agent:[^:]+:[^:]+:direct:(\d+)", haystack)
        suffix = match.group(1) if match else "unknown"
        return f"human.openclaw.direct-session.{suffix}", "human", 0.78
    if openclaw_source:
        inbound_actor = openclaw_inbound_actor(haystack)
        if inbound_actor:
            return inbound_actor, "human", 0.74
        legacy_actor = openclaw_legacy_source_actor(haystack)
        if legacy_actor:
            return legacy_actor, "human", 0.62
    if openclaw_source and openclaw_heartbeat_invoked(haystack):
        return "background.openclaw.heartbeat", "background", 0.75
    if openclaw_source:
        return f"unknown.openclaw-session.{session_id}", "unknown", 0.45
    workspace = slug(str(meta.get("cwd") or path.parent.name))
    if workspace:
        return f"human.codex.workspace.{workspace}", "human", 0.55
    return f"human.codex.session.{session_id}", "human", 0.35


def openclaw_cron_name(text: str) -> str:
    match = re.search(r"\[cron:[0-9a-f-]+\s+([^\]]+)\]", text, flags=re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return ""


def openclaw_inbound_actor(text: str) -> str:
    if "openclaw.inbound_meta" not in text:
        return ""
    values: dict[str, str] = {}
    for key in ("surface", "channel", "provider", "chat_type"):
        match = re.search(rf'"{key}"\s*:\s*"([^"]+)"', text)
        if match:
            values[key] = slug(match.group(1))
    surface = values.get("surface") or values.get("channel") or values.get("provider")
    chat_type = values.get("chat_type")
    if not surface:
        return ""
    if chat_type:
        return f"human.openclaw.{surface}-{chat_type}"
    return f"human.openclaw.{surface}"


def openclaw_legacy_source_actor(text: str) -> str:
    if '"channelId"' in text or "channelId" in text:
        return "human.openclaw.discord-channel"
    if ("Tele" + "gram direct conversation") in text:
        return "human.openclaw." + "tele" + "gram-direct"
    return ""


def openclaw_heartbeat_invoked(text: str) -> bool:
    patterns = (
        r'"cmd"\s*:\s*"[^"]*(heartbeat_scan\.py|spam_cleanup\.py)',
        r'"command"\s*:\s*"[^"]*(heartbeat_scan\.py|spam_cleanup\.py)',
        r'heartbeat_scan\.py --json && \./tools/spam_cleanup\.py',
        r'\./tools/heartbeat_scan\.py',
        r'\./tools/spam_cleanup\.py',
    )
    return any(re.search(pattern, text) for pattern in patterns)


def evidence_excerpt(text: str, limit: int = 2000) -> str:
    if len(text) <= limit * 2:
        return text
    return f"{text[:limit]}\n{text[-limit:]}"


def command_from_call_payload(payload: dict[str, Any]) -> str:
    custom_input = payload.get("input")
    if isinstance(custom_input, str) and custom_input.strip():
        return tool_context_from_custom_input(payload, custom_input)
    if isinstance(custom_input, dict):
        command = custom_input.get("command") or custom_input.get("cmd")
        if isinstance(command, str):
            return command.strip()
        return tool_context_from_payload(payload, custom_input)

    arguments = payload.get("arguments")
    if isinstance(arguments, str):
        try:
            decoded = json.loads(arguments)
        except json.JSONDecodeError:
            decoded = {}
        if isinstance(decoded, dict):
            command = decoded.get("command") or decoded.get("cmd")
            if isinstance(command, str):
                return command.strip()
            return tool_context_from_payload(payload, decoded)
        return arguments.strip()
    if isinstance(arguments, dict):
        command = arguments.get("command") or arguments.get("cmd")
        if isinstance(command, str):
            return command.strip()
        return tool_context_from_payload(payload, arguments)
    return tool_context_from_payload(payload, {})


def operation_context_from_call_payload(payload: dict[str, Any], command: str) -> str:
    custom_input = payload.get("input")
    if isinstance(custom_input, str) and custom_input.strip():
        return operation_context_from_custom_input(custom_input)
    name = str(payload.get("name") or "").strip()
    named_context = operation_context_for_tool(name)
    if name.lower() not in {"exec", "exec_command", "shell", "shell_command"} and named_context != "execution":
        return named_context
    return operation_context(command)


def operation_context_from_custom_input(raw: str) -> str:
    contexts: list[str] = []
    nested_tools = re.findall(r"\btools\.([A-Za-z0-9_]+)\s*\(", raw)
    commands = custom_commands(raw)
    for name in nested_tools:
        if name != "exec_command":
            contexts.append(operation_context_for_tool(name))
    contexts.extend(operation_context(command) for command in commands)
    if nested_tools.count("exec_command") > len(commands):
        contexts.append("execution")
    return combine_operation_contexts(contexts or ["execution"])


def custom_commands(raw: str) -> list[str]:
    commands: list[str] = []
    pattern = re.compile(r'(?:(?:"cmd")|\bcmd)\s*:\s*("(?:\\.|[^"\\])*")')
    for match in pattern.finditer(raw):
        try:
            command = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
        if isinstance(command, str) and command.strip():
            commands.append(command.strip())
    return commands


def operation_context(command: str) -> str:
    lowered = normalize_command(command).lower()
    contexts: list[str] = []
    if any(marker in lowered for marker in ("wait_agent", "write_stdin", " sleep ")):
        contexts.append("wait")
    if "apply_patch" in lowered:
        contexts.append("edit")
    if "update_plan" in lowered:
        contexts.append("planning")
    if any(marker in lowered for marker in ("pass_telemetry", "codex-session-windup", "toolburn pass")):
        contexts.append("telemetry")
    if any(marker in lowered for marker in ("validate", "unittest", "pytest", "playwright", "php -l", "nginx -t")):
        contexts.append("validation")
    if any(marker in lowered for marker in ("gh pr ", "git push", "git commit", "git merge", "install.sh", "deploy")):
        contexts.append("delivery")
    if re.search(r"(?:^|[;&|]\s*)(?:rg|sed|cat|head|tail|find)\b", lowered) or any(
        marker in lowered for marker in ("git status", "git log", "git show", "git diff", "gh pr view")
    ):
        contexts.append("contextRecovery")
    return combine_operation_contexts(contexts or ["execution"])


def operation_context_for_tool(name: str) -> str:
    lowered = name.lower()
    if lowered in {"wait", "wait_agent", "write_stdin"}:
        return "wait"
    if "apply_patch" in lowered:
        return "edit"
    if "update_plan" in lowered:
        return "planning"
    if "web" in lowered:
        return "web"
    if "image" in lowered or "screenshot" in lowered:
        return "visual"
    return "execution"


def combine_operation_contexts(values: Iterable[str]) -> str:
    contexts: set[str] = set()
    for value in values:
        contexts.update(part for part in value.split("+") if part)
    order = {name: index for index, name in enumerate(OPERATION_ORDER)}
    return "+".join(sorted(contexts, key=lambda item: (order.get(item, len(order)), item)))


def tool_context_from_custom_input(payload: dict[str, Any], raw: str) -> str:
    nested_tools = re.findall(r"\btools\.([A-Za-z0-9_]+)\s*\(", raw)
    if len(nested_tools) == 1 and nested_tools[0] == "exec_command":
        match = re.search(r'(?:(?:"cmd")|\bcmd)\s*:\s*("(?:\\.|[^"\\])*")', raw)
        if match:
            try:
                command = json.loads(match.group(1))
            except json.JSONDecodeError:
                command = ""
            if isinstance(command, str) and command.strip():
                return command.strip()
    if nested_tools:
        unique_tools = sorted(set(nested_tools))
        if len(nested_tools) == 1:
            return unique_tools[0]
        if len(unique_tools) == 1:
            return f"multiple:{unique_tools[0]}"
        return f"mixed:{'+'.join(unique_tools)}"
    return tool_context_from_payload(payload, {})


def iter_json_rows(path: Path) -> Iterable[tuple[int, dict[str, Any]]]:
    try:
        handle = path.open(encoding="utf-8", errors="replace")
    except OSError:
        return
    with handle:
        for line_no, line in enumerate(handle, start=1):
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                yield line_no, row


def tool_context_from_payload(payload: dict[str, Any], arguments: dict[str, Any]) -> str:
    name = str(payload.get("name") or "").strip()
    if not name:
        return ""
    if name in {"function_call", "custom_tool_call"}:
        return ""
    detail = ""
    for key in ("action", "fn", "tool"):
        value = arguments.get(key)
        if isinstance(value, str) and value.strip():
            detail = f"({key}={slug(value)})"
            break
    return f"{name}{detail}"


def compact_text(payload: dict[str, Any]) -> str:
    values: list[str] = []
    if isinstance(payload.get("content"), list):
        for part in payload["content"]:
            if isinstance(part, dict):
                text = part.get("text") or part.get("output_text") or part.get("input_text")
                if isinstance(text, str):
                    values.append(text)
    for key in ("output", "arguments", "name"):
        value = payload.get(key)
        if isinstance(value, str):
            values.append(value)
    return "\n".join(values)


def first_timestamp(token_rows: list[dict[str, Any]]) -> str:
    if not token_rows:
        return ""
    return str(token_rows[0]["row"].get("timestamp") or "")


def last_timestamp(token_rows: list[dict[str, Any]]) -> str:
    if not token_rows:
        return ""
    return str(token_rows[-1]["row"].get("timestamp") or "")


def scrub_meta(meta: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "id",
        "timestamp",
        "cwd",
        "originator",
        "cli_version",
        "source",
        "model_provider",
    }
    return {key: meta.get(key) for key in sorted(allowed) if key in meta}


def scrub_copilot_meta(meta: dict[str, Any]) -> dict[str, Any]:
    allowed = {"sessionId", "producer", "copilotVersion", "startTime", "alreadyInUse"}
    scrubbed = {key: meta.get(key) for key in sorted(allowed) if key in meta}
    workspace = copilot_workspace(meta)
    if workspace:
        scrubbed["cwd"] = workspace
    return scrubbed


def usage_from_copilot_data(data: dict[str, Any]) -> dict[str, int]:
    input_tokens = int(data.get("inputTokens") or 0)
    output_tokens = int(data.get("outputTokens") or 0)
    reasoning_tokens = int(data.get("reasoningTokens") or 0)
    return {
        "input_tokens": input_tokens,
        "cached_input_tokens": int(data.get("cacheReadTokens") or 0),
        "output_tokens": output_tokens,
        "raw_total_tokens": input_tokens + output_tokens + reasoning_tokens,
        "total_tokens": input_tokens + output_tokens + reasoning_tokens,
    }


def copilot_workspace(meta: dict[str, Any]) -> str:
    context = meta.get("context") or {}
    if isinstance(context, dict):
        cwd = context.get("cwd")
        if isinstance(cwd, str):
            return cwd
    return ""


def infer_copilot_actor_id(session_id: str, workspace: str) -> str:
    workspace_slug = slug(workspace)
    if workspace_slug:
        return f"human.github-copilot.workspace.{workspace_slug}"
    return f"human.github-copilot.session.{session_id[:12]}"


def copilot_command_from_tool_complete(data: dict[str, Any]) -> str:
    telemetry = data.get("toolTelemetry") or {}
    properties = telemetry.get("properties") if isinstance(telemetry, dict) else {}
    if isinstance(properties, dict):
        command = properties.get("command")
        if isinstance(command, str) and command.strip():
            return command.strip()
    result = data.get("result")
    if isinstance(result, dict):
        content = result.get("content")
        if isinstance(content, str) and content.strip():
            return str(data.get("toolName") or "tool")
    return str(data.get("toolName") or "").strip()


def is_copilot_source(source_label: str, path: Path) -> bool:
    lowered = source_label.lower()
    return "copilot" in lowered or "/.copilot/" in str(path) or path.name == COPILOT_EVENTS_GLOB


def output_shape(output: str) -> dict[str, Any]:
    stripped = output.strip()
    shape = {"bytes": len(output.encode("utf-8")), "lines": output.count("\n") + 1}
    if stripped.startswith("{") or stripped.startswith("["):
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            shape["format"] = "text"
        else:
            shape["format"] = "json"
            if isinstance(parsed, dict):
                shape["keys"] = sorted(str(key) for key in parsed.keys())[:20]
            elif isinstance(parsed, list):
                shape["items"] = len(parsed)
    else:
        shape["format"] = "text"
    return shape


def normalize_command(command: str) -> str:
    return re.sub(r"\s+", " ", command.strip())


def executable(command: str) -> str:
    normalized = normalize_command(command)
    return normalized.split(" ", 1)[0] if normalized else ""


def stable_id(*parts: object) -> str:
    joined = "\0".join(str(part) for part in parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:24]


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def slug(text: str) -> str:
    stem = Path(text).name if "/" in text else text
    return re.sub(r"[^a-z0-9]+", "-", stem.lower()).strip("-")[:48]
