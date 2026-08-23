"""Compact, content-free execution receipts for Harness and Efficiency passes."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


DEFAULT_SESSION_ROOT = Path("/root/.codex/sessions")
DEFAULT_EPISODE_ROOT = Path("/var/lib/darkexec/harness-episodes")
SESSION_ID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
)
FAILURE_PATTERNS = (
    re.compile(r"^Script failed(?::|$)", re.MULTILINE),
    re.compile(r'"exit_code"\s*:\s*[1-9][0-9]*'),
    re.compile(r"^apply_patch verification failed", re.MULTILINE),
    re.compile(r"^(?:active|completed) plan (?:missing|includes)", re.MULTILINE),
    re.compile(r"^broken link in ", re.MULTILINE),
    re.compile(r"^jq: (?:compile )?error", re.MULTILINE),
)
POSSIBLE_FAILURE_PATTERNS = (
    re.compile(r"^Error(?:\[[^\]]+\])?:\s", re.MULTILINE),
    re.compile(r"^Traceback \(most recent call last\):", re.MULTILINE),
    re.compile(r"^fatal:\s", re.MULTILINE | re.IGNORECASE),
    re.compile(r"(?:^|\s)(?:command not found|MODULE_NOT_FOUND)(?:\s|$)", re.MULTILINE),
)
OUTPUT_ONLY_PROJECTION_RE = re.compile(
    r"\btext\s*\(\s*(?:JSON\.stringify\s*\(\s*)?[A-Za-z_$][A-Za-z0-9_$]*\.output\b"
)
USAGE_KEYS = (
    "rawInput",
    "cachedInput",
    "cacheWriteInput",
    "uncachedInput",
    "output",
    "reasoningOutput",
    "total",
)


class PassReceiptError(RuntimeError):
    """A pass selector or local evidence source could not be resolved safely."""


def empty_usage() -> dict[str, int]:
    return {key: 0 for key in USAGE_KEYS}


def normalize_usage(value: object) -> dict[str, int] | None:
    if not isinstance(value, dict):
        return None

    def amount(*names: str) -> int:
        for name in names:
            candidate = value.get(name)
            if isinstance(candidate, int):
                return candidate
        return 0

    raw_input = amount("input_tokens", "input", "rawInput")
    cached = amount("cached_input_tokens", "cachedInput")
    cache_write = amount("cache_write_input_tokens", "cacheWriteInput")
    output = amount("output_tokens", "output")
    reasoning = amount("reasoning_output_tokens", "reasoningOutput")
    total = amount("total_tokens", "total") or raw_input + output
    return {
        "rawInput": raw_input,
        "cachedInput": cached,
        "cacheWriteInput": cache_write,
        "uncachedInput": max(0, raw_input - cached - cache_write),
        "output": output,
        "reasoningOutput": reasoning,
        "total": total,
    }


def add_usage(left: dict[str, int], right: dict[str, int] | None) -> dict[str, int]:
    if right is None:
        return dict(left)
    return {key: int(left.get(key, 0)) + int(right.get(key, 0)) for key in USAGE_KEYS}


def subtract_usage(current: dict[str, int], baseline: dict[str, int]) -> dict[str, int]:
    result = {
        key: max(0, int(current.get(key, 0)) - int(baseline.get(key, 0)))
        for key in USAGE_KEYS
    }
    result["uncachedInput"] = max(
        0,
        result["rawInput"] - result["cachedInput"] - result["cacheWriteInput"],
    )
    return result


def resolve_selector(selector: str, session_root: Path = DEFAULT_SESSION_ROOT) -> tuple[str, list[Path]]:
    value = selector.strip()
    if value in {"current", "previous"}:
        value = os.environ.get("CODEX_THREAD_ID", "").strip()
        if not value:
            raise PassReceiptError("CODEX_THREAD_ID is unavailable; pass an exact session UUID or JSONL path")

    candidate = Path(value).expanduser()
    if candidate.is_file():
        paths = [candidate.resolve()]
    elif "/" in value or "\\" in value:
        raise PassReceiptError(f"session file not found: {candidate}")
    else:
        if not SESSION_ID_RE.fullmatch(value):
            raise PassReceiptError("session must be current, previous, an exact UUID, or an exact JSONL path")
        paths = sorted(session_root.expanduser().resolve().glob(f"**/*{value}*.jsonl"))
        if not paths:
            raise PassReceiptError(f"session {value} was not found below {session_root}")

    session_ids = {session_id_from_path(path) for path in paths if session_id_from_path(path)}
    if len(session_ids) > 1:
        raise PassReceiptError(f"session selector is ambiguous: {selector}")
    session_id = next(iter(session_ids), value if SESSION_ID_RE.fullmatch(value) else "")
    return session_id, paths


def session_id_from_path(path: Path) -> str:
    matches = SESSION_ID_RE.findall(path.name)
    return matches[-1] if matches else ""


def output_text(payload: dict[str, Any]) -> str:
    value = payload.get("output", "")
    if isinstance(value, str):
        return value.replace("\\n", "\n")
    if isinstance(value, list):
        return "\n".join(
            str(item.get("text") or item.get("content") or "")
            for item in value
            if isinstance(item, dict)
        ).replace("\\n", "\n")
    try:
        return json.dumps(value, sort_keys=True)
    except TypeError:
        return str(value)


def raw_tool_input(payload: dict[str, Any]) -> str:
    value = payload.get("input", payload.get("arguments", ""))
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, sort_keys=True)
    except TypeError:
        return str(value)


def embedded_tools(raw: str) -> list[str]:
    return list(dict.fromkeys(re.findall(r"tools\.([A-Za-z0-9_]+)\s*\(", raw)))[:8]


def command_markers(raw: str) -> list[str]:
    match = re.search(r'(?:(?:"cmd")|cmd)\s*:\s*("(?:\\.|[^"\\])*")', raw)
    if not match:
        return []
    try:
        command = json.loads(match.group(1))
        lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|")
        lexer.whitespace_split = True
        tokens = list(lexer)
    except (json.JSONDecodeError, ValueError):
        return []
    stages: list[list[str]] = [[]]
    for token in tokens:
        if token and all(character in ";&|" for character in token):
            stages.append([])
        else:
            stages[-1].append(token)
    markers: list[str] = []
    interpreters = {"bash", "php", "python", "python3", "sh"}
    for stage in stages:
        words = [word for word in stage if word and not re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", word)]
        if not words:
            continue
        executable = Path(words[0]).name
        if executable in interpreters:
            script = next((word for word in words[1:] if not word.startswith("-")), None)
            if script:
                executable = Path(script).name
        markers.append(executable)
    return list(dict.fromkeys(markers))[:8]


def tool_category(name: str, raw: str, embedded: list[str], markers: list[str]) -> str:
    joined = " ".join([name, raw, *embedded, *markers]).lower()
    if name == "wait" or "wait_agent" in joined or "write_stdin" in joined:
        return "wait"
    if "apply_patch" in joined:
        return "edit"
    if "update_plan" in joined:
        return "planning"
    if any(token in joined for token in ("pass_telemetry", "codex-session-windup", "toolburn pass")):
        return "telemetry"
    if any(token in joined for token in ("validate", "unittest", "pytest", "playwright", "php -l", "nginx -t")):
        return "validation"
    if any(token in joined for token in ("gh pr ", "git push", "git commit", "git merge", "install.sh", "deploy")):
        return "delivery"
    if any(token in joined for token in ("sed", "rg", "grep", "find", "git log", "git show", "gh pr view")):
        return "contextRecovery"
    return "execution"


def failed_output(text: str) -> bool:
    return any(pattern.search(text) for pattern in FAILURE_PATTERNS)


def possible_failed_output(text: str) -> bool:
    marker = "\nOutput:\n"
    body = text.split(marker, 1)[1] if marker in text else text
    body = body.lstrip()
    first_line = body.splitlines()[0] if body else ""
    return any(pattern.search(first_line) for pattern in POSSIBLE_FAILURE_PATTERNS)


def incomplete_failure_evidence(raw: str, embedded: list[str]) -> bool:
    """Return true when orchestration projects command output but discards its exit status."""
    return "exec_command" in embedded and bool(OUTPUT_ONLY_PROJECTION_RE.search(raw))


def parse_rollouts(paths: Iterable[Path]) -> dict[str, Any]:
    meta: dict[str, Any] = {}
    turns: list[dict[str, Any]] = []
    active: dict[str, Any] | None = None
    latest_usage = empty_usage()
    pending: dict[str, dict[str, Any]] = {}
    malformed = 0
    source_bytes = 0

    for path in sorted(paths):
        active = None
        latest_usage = empty_usage()
        pending.clear()
        source_bytes += path.stat().st_size
        with path.open(encoding="utf-8", errors="replace") as handle:
            for line_number, line in enumerate(handle, start=1):
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    malformed += 1
                    continue
                payload = record.get("payload") or {}
                record_type = record.get("type")
                timestamp = record.get("timestamp") or payload.get("timestamp")

                if record_type == "session_meta":
                    meta.update(payload)
                    continue
                if record_type == "turn_context" and active is not None:
                    active["model"] = payload.get("model") or active.get("model")
                    active["cwd"] = payload.get("cwd") or active.get("cwd")
                    continue
                if record_type == "event_msg":
                    event_type = payload.get("type")
                    if event_type == "token_count":
                        info = payload.get("info") or {}
                        normalized = normalize_usage(info.get("total_token_usage"))
                        if normalized:
                            latest_usage = normalized
                        continue
                    if event_type == "task_started":
                        if active is not None:
                            active = None
                        active = {
                            "turnId": str(payload.get("turn_id") or ""),
                            "startedAt": timestamp or payload.get("started_at"),
                            "completedAt": None,
                            "durationMs": None,
                            "baselineUsage": dict(latest_usage),
                            "usage": empty_usage(),
                            "operations": [],
                            "compactions": 0,
                            "model": None,
                            "cwd": meta.get("cwd"),
                        }
                        continue
                    if event_type == "task_complete" and active is not None:
                        completed_id = str(payload.get("turn_id") or "")
                        if active["turnId"] and completed_id and active["turnId"] != completed_id:
                            continue
                        active["completedAt"] = timestamp or payload.get("completed_at")
                        active["durationMs"] = payload.get("duration_ms")
                        active["usage"] = subtract_usage(latest_usage, active["baselineUsage"])
                        active.pop("baselineUsage", None)
                        turns.append(active)
                        active = None
                        continue
                    if event_type in {"context_compacted", "compaction"} and active is not None:
                        active["compactions"] += 1
                    continue
                if record_type != "response_item" or active is None:
                    continue

                item_type = payload.get("type")
                if item_type in {"function_call", "custom_tool_call"}:
                    call_id = str(payload.get("call_id") or payload.get("id") or f"{path.name}:{line_number}")
                    name = str(payload.get("name") or "unknown")
                    raw = raw_tool_input(payload)
                    embedded = embedded_tools(raw)
                    markers = command_markers(raw)
                    operation = {
                        "sequence": len(active["operations"]) + 1,
                        "tool": name,
                        "embeddedTools": embedded,
                        "commandMarkers": markers,
                        "category": tool_category(name, raw, embedded, markers),
                        "fingerprint": hashlib.sha256(raw.encode()).hexdigest()[:12],
                        "outputBytes": 0,
                        "failed": False,
                        "confirmedFailure": False,
                        "possibleFailure": False,
                        "failureEvidenceIncomplete": incomplete_failure_evidence(raw, embedded),
                    }
                    active["operations"].append(operation)
                    pending[call_id] = operation
                elif item_type in {"function_call_output", "custom_tool_call_output"}:
                    call_id = str(payload.get("call_id") or "")
                    operation = pending.pop(call_id, None)
                    if operation is None:
                        continue
                    text = output_text(payload)
                    operation["outputBytes"] = len(text.encode())
                    operation["confirmedFailure"] = failed_output(text)
                    operation["failed"] = operation["confirmedFailure"]
                    operation["possibleFailure"] = (
                        not operation["confirmedFailure"]
                        and operation["failureEvidenceIncomplete"]
                        and possible_failed_output(text)
                    )

    session_id = str(meta.get("session_id") or meta.get("id") or "")
    if not session_id:
        session_id = next((session_id_from_path(path) for path in paths if session_id_from_path(path)), "")
    return {
        "sessionId": session_id,
        "cwd": meta.get("cwd"),
        "source": meta.get("source"),
        "originator": meta.get("originator"),
        "threadSource": meta.get("thread_source"),
        "rolloutFiles": [str(path) for path in sorted(paths)],
        "rolloutBytes": source_bytes,
        "malformedRecords": malformed,
        "completedTurns": turns,
        "activeTurnId": active.get("turnId") if active else None,
    }


def load_darkexec_passes(session_id: str, episode_root: Path) -> list[dict[str, Any]]:
    if not session_id or not episode_root.is_dir():
        return []
    passes: list[dict[str, Any]] = []
    for path in episode_root.glob("*.json"):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        target = value.get("target") if isinstance(value.get("target"), dict) else {}
        if str(target.get("threadId") or "") != session_id:
            continue
        harness = target.get("harness") if isinstance(target.get("harness"), dict) else {}
        passes.append(
            {
                "passId": value.get("jobId") or path.stem,
                "status": value.get("status"),
                "mode": value.get("harnessMode"),
                "purpose": value.get("episodePurpose"),
                "productTurnId": target.get("turnId"),
                "harnessTurnId": harness.get("turnId"),
                "terminalAt": value.get("terminalAt"),
                "runtimeRevision": value.get("runtimeRevision"),
                "doctrineSha256": value.get("doctrineSha256"),
            }
        )
    return sorted(passes, key=lambda item: str(item.get("terminalAt") or ""))


def operation_summary(operations: list[dict[str, Any]]) -> dict[str, Any]:
    by_tool: Counter[str] = Counter()
    by_embedded: Counter[str] = Counter()
    by_category: dict[str, dict[str, int]] = {}
    markers: dict[str, dict[str, Any]] = {}
    fingerprints: dict[str, dict[str, Any]] = {}
    signatures: list[dict[str, Any]] = []
    signature_ids: dict[tuple[str, tuple[str, ...], str], int] = {}
    runs: list[list[int]] = []
    confirmed_failures = 0
    possible_failures = 0
    incomplete_evidence_calls = 0
    output_bytes = 0

    for operation in operations:
        tool = str(operation.get("tool") or "unknown")
        embedded = tuple(str(value) for value in operation.get("embeddedTools") or [])
        category = str(operation.get("category") or "execution")
        sequence = int(operation.get("sequence") or 0)
        by_tool[tool] += 1
        by_embedded.update(embedded)
        category_cost = by_category.setdefault(category, {
            "calls": 0,
            "outputBytes": 0,
            "failures": 0,
            "confirmedFailures": 0,
            "possibleFailures": 0,
            "incompleteFailureEvidenceCalls": 0,
        })
        category_cost["calls"] += 1
        category_cost["outputBytes"] += int(operation.get("outputBytes") or 0)
        output_bytes += int(operation.get("outputBytes") or 0)
        if operation.get("confirmedFailure", operation.get("failed")):
            confirmed_failures += 1
            category_cost["failures"] += 1
            category_cost["confirmedFailures"] += 1
        if operation.get("possibleFailure"):
            possible_failures += 1
            category_cost["possibleFailures"] += 1
        if operation.get("failureEvidenceIncomplete"):
            incomplete_evidence_calls += 1
            category_cost["incompleteFailureEvidenceCalls"] += 1
        for marker in operation.get("commandMarkers") or []:
            group = markers.setdefault(str(marker), {"marker": marker, "count": 0, "outputBytes": 0})
            group["count"] += 1
            group["outputBytes"] += int(operation.get("outputBytes") or 0)
        fingerprint = str(operation.get("fingerprint") or "")
        group = fingerprints.setdefault(fingerprint, {"fingerprint": fingerprint, "count": 0, "outputBytes": 0})
        group["count"] += 1
        group["outputBytes"] += int(operation.get("outputBytes") or 0)
        signature = (tool, embedded, category)
        signature_id = signature_ids.get(signature)
        if signature_id is None:
            signature_id = len(signatures) + 1
            signature_ids[signature] = signature_id
            signatures.append({"id": signature_id, "tool": tool, "embeddedTools": list(embedded), "category": category, "count": 0})
        signatures[signature_id - 1]["count"] += 1
        if runs and runs[-1][0] == signature_id:
            runs[-1][2] = sequence
            runs[-1][3] += 1
        else:
            runs.append([signature_id, sequence, sequence, 1])

    largest_category = None
    if by_category:
        largest_category = max(
            by_category,
            key=lambda key: (by_category[key]["outputBytes"], by_category[key]["calls"], key),
        )
    return {
        "calls": len(operations),
        "outputBytes": output_bytes,
        "failures": confirmed_failures,
        "confirmedFailures": confirmed_failures,
        "possibleFailures": possible_failures,
        "failureEvidenceIncomplete": incomplete_evidence_calls > 0,
        "incompleteFailureEvidenceCalls": incomplete_evidence_calls,
        "byTool": dict(sorted(by_tool.items())),
        "byEmbeddedTool": dict(sorted(by_embedded.items())),
        "byCategory": dict(sorted(by_category.items())),
        "largestCategory": largest_category,
        "signatures": signatures,
        "sequenceRunFields": ["signatureId", "sequenceStart", "sequenceEnd", "count"],
        "sequenceRuns": runs,
        "repeatedCommandMarkers": sorted(
            (group for group in markers.values() if group["count"] > 1),
            key=lambda group: (-group["count"], str(group["marker"])),
        )[:10],
        "repeatedCallFingerprints": sorted(
            (group for group in fingerprints.values() if group["count"] > 1),
            key=lambda group: (-group["count"], str(group["fingerprint"])),
        )[:10],
        "largestOutputs": [
            {
                "sequence": operation.get("sequence"),
                "tool": operation.get("tool"),
                "category": operation.get("category"),
                "outputBytes": operation.get("outputBytes"),
                "failed": bool(operation.get("failed")),
                "confirmedFailure": bool(operation.get("confirmedFailure", operation.get("failed"))),
                "possibleFailure": bool(operation.get("possibleFailure")),
                "failureEvidenceIncomplete": bool(operation.get("failureEvidenceIncomplete")),
            }
            for operation in sorted(operations, key=lambda item: -int(item.get("outputBytes") or 0))[:5]
        ],
    }


def select_turns(
    parsed: dict[str, Any],
    selector: str,
    exact_turn: str | None,
    passes: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    turns = list(parsed["completedTurns"])
    if not turns:
        raise PassReceiptError("session has no completed turns")
    if exact_turn:
        selected = [turn for turn in turns if turn["turnId"] == exact_turn]
        if not selected:
            raise PassReceiptError(f"completed turn was not found: {exact_turn}")
        return selected, {"kind": "exact_turn", "turnId": exact_turn}
    if selector == "previous":
        turn = turns[-1]
        return [turn], {"kind": "previous_completed_turn", "turnId": turn["turnId"]}

    completed_ids = {turn["turnId"] for turn in turns}
    harness_ids = {
        str(item.get("harnessTurnId") or "")
        for item in passes
        if str(item.get("harnessTurnId") or "") in completed_ids
    }
    boundary_id = next(
        (turn["turnId"] for turn in reversed(turns) if turn["turnId"] in harness_ids),
        None,
    )
    if boundary_id:
        boundary_index = next(index for index, turn in enumerate(turns) if turn["turnId"] == boundary_id)
        selected = [turn for turn in turns[boundary_index + 1 :] if turn["turnId"] not in harness_ids]
        if selected:
            return selected, {
                "kind": "ordinary_turns_since_previous_harness",
                "boundaryHarnessTurnId": boundary_id,
            }
    selected = [turn for turn in turns if turn["turnId"] not in harness_ids]
    if not selected:
        selected = [turns[-1]]
    return selected, {"kind": "completed_ordinary_session", "boundaryHarnessTurnId": None}


def compact_turn(turn: dict[str, Any]) -> dict[str, Any]:
    operations = operation_summary(turn["operations"])
    return {
        "turnId": turn["turnId"],
        "startedAt": turn.get("startedAt"),
        "completedAt": turn.get("completedAt"),
        "durationMs": turn.get("durationMs"),
        "usage": turn["usage"],
        "toolCost": {
            key: operations[key]
            for key in (
                "calls", "outputBytes", "failures", "confirmedFailures", "possibleFailures",
                "failureEvidenceIncomplete", "incompleteFailureEvidenceCalls", "largestCategory", "byCategory",
            )
        },
        "compactions": int(turn.get("compactions") or 0),
    }


def build_pass_receipt(
    selector: str,
    *,
    exact_turn: str | None = None,
    session_root: Path = DEFAULT_SESSION_ROOT,
    episode_root: Path = DEFAULT_EPISODE_ROOT,
    include_enrichment: bool = True,
) -> dict[str, Any]:
    session_id, paths = resolve_selector(selector, session_root)
    parsed = parse_rollouts(paths)
    session_id = parsed["sessionId"] or session_id
    passes = load_darkexec_passes(session_id, episode_root) if include_enrichment else []
    selected, scope = select_turns(parsed, selector, exact_turn, passes)
    usage = empty_usage()
    operations: list[dict[str, Any]] = []
    compactions = 0
    for turn in selected:
        usage = add_usage(usage, turn["usage"])
        compactions += int(turn.get("compactions") or 0)
        offset = len(operations)
        for operation in turn["operations"]:
            operations.append({**operation, "sequence": offset + int(operation["sequence"])})
    receipt = {
        "schemaVersion": 1,
        "receiptKind": "toolburn.pass/v1",
        "generatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "session": {
            "sessionId": session_id,
            "cwd": parsed.get("cwd"),
            "source": parsed.get("source"),
            "originator": parsed.get("originator"),
            "threadSource": parsed.get("threadSource"),
            "rolloutFiles": len(paths),
            "rolloutBytes": parsed["rolloutBytes"],
            "malformedRecords": parsed["malformedRecords"],
            "completedTurnCount": len(parsed["completedTurns"]),
            "activeTurnId": parsed.get("activeTurnId"),
            "scope": {**scope, "selectedTurnIds": [turn["turnId"] for turn in selected]},
            "usage": usage,
            "operations": operation_summary(operations),
            "compactions": compactions,
            "turns": [compact_turn(turn) for turn in selected],
        },
        "privacy": "Prompts, messages, raw tool arguments, raw tool output, and private trajectory content are omitted.",
    }
    if passes:
        receipt["darkexec"] = {"passes": passes}
    return receipt


def metric_view(receipt: dict[str, Any]) -> dict[str, Any]:
    session = receipt["session"]
    operations = session["operations"]
    return {
        "sessionId": session["sessionId"],
        "scope": session["scope"],
        "usage": session["usage"],
        "toolCost": {
            key: operations[key]
            for key in (
                "calls", "outputBytes", "failures", "confirmedFailures", "possibleFailures",
                "incompleteFailureEvidenceCalls", "largestCategory", "byCategory",
            )
        },
        "compactions": session["compactions"],
    }


def numeric_delta(candidate: object, baseline: object) -> object:
    if isinstance(candidate, int) and isinstance(baseline, int):
        return candidate - baseline
    if isinstance(candidate, dict) and isinstance(baseline, dict):
        return {
            key: numeric_delta(candidate.get(key, 0), baseline.get(key, 0))
            for key in sorted(set(candidate) | set(baseline))
            if isinstance(candidate.get(key, 0), (int, dict)) and isinstance(baseline.get(key, 0), (int, dict))
        }
    return None


def compare_receipts(baseline: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    baseline_view = metric_view(baseline)
    candidate_view = metric_view(candidate)
    return {
        "schemaVersion": 1,
        "receiptKind": "toolburn.compare/v1",
        "generatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "baseline": baseline_view,
        "candidate": candidate_view,
        "delta": {
            "usage": numeric_delta(candidate_view["usage"], baseline_view["usage"]),
            "toolCost": numeric_delta(candidate_view["toolCost"], baseline_view["toolCost"]),
            "compactions": candidate_view["compactions"] - baseline_view["compactions"],
        },
        "interpretation": "Factual deltas only. Toolburn does not rate intervention quality or causality.",
    }


def format_pass_markdown(receipt: dict[str, Any]) -> str:
    session = receipt["session"]
    usage = session["usage"]
    operations = session["operations"]
    lines = [
        "# Toolburn Pass Receipt",
        "",
        f"- Session: `{session['sessionId']}`",
        f"- Scope: `{session['scope']['kind']}`; turns: `{len(session['scope']['selectedTurnIds'])}`",
        f"- Tokens: `{usage['total']}` total; `{usage['uncachedInput']}` uncached input; `{usage['output']}` output",
        f"- Tools: `{operations['calls']}` calls; `{operations['outputBytes']}` output bytes; "
        f"`{operations['confirmedFailures']}` confirmed failures; `{operations['possibleFailures']}` possible failures",
        f"- Largest category: `{operations['largestCategory'] or 'none'}`; compactions: `{session['compactions']}`",
    ]
    repeated = operations["repeatedCommandMarkers"]
    if repeated:
        lines.append("- Repeated commands: " + ", ".join(f"`{item['marker']}` x{item['count']}" for item in repeated[:5]))
    if operations["failureEvidenceIncomplete"]:
        lines.append(
            f"- Failure evidence: incomplete for `{operations['incompleteFailureEvidenceCalls']}` calls whose orchestration retained output but discarded exit status"
        )
    lines.extend(["", "Content-free receipt: prompts, messages, arguments, and raw output are omitted."])
    return "\n".join(lines)
