"""Mechanical action-plus-target extraction for Toolburn invocations."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
from pathlib import Path
from typing import Any


READ_COMMANDS = {
    "cat",
    "date",
    "du",
    "find",
    "head",
    "ls",
    "nl",
    "pwd",
    "rg",
    "sed",
    "stat",
    "tail",
    "test",
    "wc",
}
READ_SUBCOMMANDS = {
    "git": {"diff", "log", "rev-parse", "show", "status"},
    "gh": {"pr:checks", "pr:diff", "pr:list", "pr:status", "pr:view", "run:list", "run:view"},
}
INTERPRETERS = {"bash", "node", "php", "python", "python3", "sh"}
PATCH_PATH_RE = re.compile(r"\*\*\* (?:Add|Delete|Update) File: ([^\\\r\n]+)")
PROCESS_ID_RE = re.compile(r"\bsession_id[\"']?\s*:\s*[\"']?([0-9]+)")
OUTPUT_PROCESS_ID_RES = (
    re.compile(r"\bSESSION_ID\s*=\s*([0-9]+)"),
    re.compile(r"[\"']session_id[\"']\s*:\s*([0-9]+)"),
)
ABSOLUTE_PATH_RE = re.compile(r"(?<![A-Za-z0-9_.-])(/[A-Za-z0-9_@%+=:,./~-]+)")
RELATIVE_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9_./-])((?:\./|[A-Za-z0-9_.-]+/)[A-Za-z0-9_@%+=:,./~-]+)"
)


def bundle_activity(bundle: dict[str, Any], cwd: str = "") -> dict[str, Any]:
    tools = [str(item) for item in bundle.get("tools", []) if str(item)]
    commands = [str(item) for item in bundle.get("commands", []) if str(item)]
    patch_paths = [str(item) for item in bundle.get("patch_paths", []) if str(item)]
    process_ids = [str(item) for item in bundle.get("process_ids", []) if str(item)]
    parent_command = str(bundle.get("parent_command") or "")
    parent_cwd = str(bundle.get("parent_cwd") or cwd)

    if tools == ["write_stdin"]:
        if parent_command:
            target = command_target(parent_command, parent_cwd)
            return activity("poll", [target], evidence={"process_ids": process_ids})
        target = f"process:{process_ids[0]}" if process_ids else "unknown process"
        return activity("poll", [target], evidence={"process_ids": process_ids})

    if "apply_patch" in tools and patch_paths:
        targets = [resolve_target(path, cwd) for path in patch_paths]
        return activity("edit", [common_patch_target(targets)], evidence={"paths": targets})

    if commands:
        action = "read/search" if all(command_is_read_only(command) for command in commands) else "run"
        targets = unique(command_target(command, cwd) for command in commands)
        return activity(action, targets, evidence={"commands": commands})

    target = "+".join(tools) if tools else "unknown tool"
    return activity("use", [target], evidence={"tools": tools})


def activity(
    action: str, targets: list[str], evidence: dict[str, Any] | None = None
) -> dict[str, Any]:
    clean_targets = [target for target in targets if target] or ["unknown target"]
    display_targets = "; ".join(clean_targets[:2])
    if len(clean_targets) > 2:
        display_targets += f"; +{len(clean_targets) - 2} more"
    key_payload = json.dumps([action, clean_targets], separators=(",", ":"))
    return {
        "action": action,
        "targets": clean_targets,
        "display": f"{action} {display_targets}",
        "key": hashlib.sha256(key_payload.encode("utf-8")).hexdigest()[:24],
        "evidence": evidence or {},
    }


def command_is_read_only(command: str) -> bool:
    segments = command_segments(command)
    return bool(segments) and all(segment_is_read_only(segment) for segment in segments)


def segment_is_read_only(segment: str) -> bool:
    tokens = segment_tokens(segment)
    if not tokens:
        return False
    executable = Path(tokens[0]).name
    if executable in READ_COMMANDS:
        return True
    if executable == "git" and len(tokens) > 1:
        return tokens[1] in READ_SUBCOMMANDS["git"]
    if executable == "gh" and len(tokens) > 2:
        return f"{tokens[1]}:{tokens[2]}" in READ_SUBCOMMANDS["gh"]
    return False


def command_target(command: str, cwd: str = "") -> str:
    normalized = " ".join(command.split())
    if not normalized:
        return "unknown command"
    if re.search(r"\b(?:python|python3)\s+-\s+<<", normalized):
        paths = command_paths(command, cwd)
        if paths:
            return "python heredoc -> " + summarize_targets(paths)
        return "python heredoc"
    segments = command_segments(normalized)
    executable_targets = unique(
        target for target in (executable_target(segment, cwd) for segment in segments) if target
    )
    if executable_targets:
        return summarize_targets(executable_targets)
    if command_is_read_only(normalized):
        paths = command_paths(normalized, cwd)
        if paths:
            return summarize_targets(paths)
    return normalized if len(normalized) <= 160 else normalized[:157] + "..."


def command_segments(command: str) -> list[str]:
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars="|;&\n")
        lexer.whitespace = " \t\r"
        lexer.whitespace_split = True
        lexer.commenters = ""
        tokens = list(lexer)
    except ValueError:
        return [command.strip()] if command.strip() else []
    segments: list[str] = []
    current: list[str] = []
    for token in tokens:
        if token in {"&&", "||", "|", ";", "\n"}:
            if current:
                segments.append(shlex.join(current))
                current = []
        else:
            current.append(token)
    if current:
        segments.append(shlex.join(current))
    return segments


def segment_executable(segment: str) -> str:
    tokens = segment_tokens(segment)
    return Path(tokens[0]).name if tokens else ""


def segment_tokens(segment: str) -> list[str]:
    try:
        tokens = shlex.split(segment)
    except ValueError:
        tokens = segment.split()
    while tokens and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", tokens[0]):
        tokens.pop(0)
    if tokens and tokens[0] in {"env", "sudo"}:
        tokens.pop(0)
    return tokens


def executable_target(segment: str, cwd: str) -> str:
    try:
        tokens = shlex.split(segment)
    except ValueError:
        return ""
    if not tokens:
        return ""
    executable = Path(tokens[0]).name
    if executable in INTERPRETERS and len(tokens) > 2 and tokens[1] == "-m":
        return tokens[2]
    if executable in INTERPRETERS and len(tokens) > 1 and looks_like_path(tokens[1]):
        return resolve_target(tokens[1], cwd)
    if looks_like_path(tokens[0]):
        return resolve_target(tokens[0], cwd)
    return ""


def command_paths(command: str, cwd: str) -> list[str]:
    paths = [match.group(1) for match in ABSOLUTE_PATH_RE.finditer(command)]
    paths.extend(match.group(1) for match in RELATIVE_PATH_RE.finditer(command))
    return unique(resolve_target(clean_path(path), cwd) for path in paths if clean_path(path))


def looks_like_path(value: str) -> bool:
    clean = clean_path(value)
    return clean.startswith(("/", "./")) or "/" in clean or bool(
        re.search(r"\.(?:js|md|php|py|sh|ts)$", clean)
    )


def clean_path(value: str) -> str:
    return value.strip("'\"`()[]{},:;")


def resolve_target(value: str, cwd: str) -> str:
    clean = clean_path(value)
    if not clean or clean.startswith(("http://", "https://")):
        return clean
    if clean.startswith("/") or not cwd:
        return os.path.normpath(clean)
    return os.path.normpath(str(Path(cwd) / clean))


def common_patch_target(paths: list[str]) -> str:
    if not paths:
        return "unknown files"
    if len(paths) == 1:
        return paths[0]
    try:
        common = os.path.commonpath(paths)
    except ValueError:
        common = ""
    if common and common not in {"/", "."}:
        if common in paths:
            return common
        return f"{common}/* ({len(paths)} files)"
    return f"{paths[0]} +{len(paths) - 1} files"


def summarize_targets(targets: list[str]) -> str:
    display = "; ".join(targets[:2])
    return display + (f"; +{len(targets) - 2} more" if len(targets) > 2 else "")


def patch_paths(raw: str) -> list[str]:
    return unique(clean_path(match.group(1)) for match in PATCH_PATH_RE.finditer(raw))


def process_ids(raw: str) -> list[str]:
    return unique(match.group(1) for match in PROCESS_ID_RE.finditer(raw))


def output_process_ids(raw: str) -> list[str]:
    found: list[str] = []
    for pattern in OUTPUT_PROCESS_ID_RES:
        found.extend(match.group(1) for match in pattern.finditer(raw))
    return unique(found)


def unique(values) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result
