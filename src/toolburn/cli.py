"""Toolburn command line interface."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import gettempdir

from toolburn import __version__
from toolburn.pass_receipt import (
    DEFAULT_EPISODE_ROOT,
    DEFAULT_SESSION_ROOT,
    PassReceiptError,
    build_pass_receipt,
    compare_receipts,
    format_pass_markdown,
    inspect_hotspot,
)
from toolburn.report import episode_report, export_json, format_episode_table, format_explain, format_table, du_report, explain_report, top_report
from toolburn.scan import SourceSpec, scan_sources
from toolburn.schema import initialize_database, table_names
from toolburn.semantics import DEFAULT_SEMANTICS_PATH, SemanticCatalogError, format_semantic_summary, load_semantic_catalog, semantic_summary


REPORT_GROUPS = ("actor", "session", "source", "tool")
ACTOR_TYPES = ("human", "background", "unknown")
DEFAULT_CODEX_ROOT = Path("/root/.codex/sessions")
DEFAULT_OPENCLAW_ROOT = Path("/root/.openclaw/agents/main/agent/codex-home/sessions")
DEFAULT_COPILOT_ROOT = Path("/root/.copilot/session-state")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="toolburn",
        description="Local-first token burn profiler.",
    )
    parser.add_argument("--version", action="version", version=f"toolburn {__version__}")

    subparsers = parser.add_subparsers(dest="command")

    schema_parser = subparsers.add_parser("schema", help="initialize a Toolburn SQLite DB")
    schema_parser.add_argument("--db", required=True, type=Path, help="SQLite DB path")

    scan_parser = subparsers.add_parser("scan", help="scan local JSONL evidence into SQLite")
    scan_parser.add_argument("--db", required=True, type=Path, help="SQLite DB path")
    scan_parser.add_argument(
        "--source",
        action="append",
        default=[],
        metavar="LABEL=PATH",
        help="source label and file/directory path; may be repeated",
    )
    scan_parser.add_argument("--codex", type=Path, help="Codex sessions directory or JSONL file")
    scan_parser.add_argument("--openclaw", type=Path, help="OpenClaw sessions directory or JSONL file")
    scan_parser.add_argument("--copilot", type=Path, help="GitHub Copilot session-state directory or JSONL file")

    recent_parser = subparsers.add_parser(
        "recent", help="scan local defaults and show recent token burn"
    )
    recent_parser.add_argument("--hours", type=float, default=24.0, help="lookback window")
    recent_parser.add_argument("--limit", type=int, default=10)
    recent_parser.add_argument("--db", type=Path, help="SQLite DB path")
    recent_parser.add_argument("--no-scan", action="store_true", help="reuse the DB")
    recent_parser.add_argument("--actor-type", choices=ACTOR_TYPES, help="only show one actor type")
    recent_parser.add_argument("--codex", type=Path, default=DEFAULT_CODEX_ROOT)
    recent_parser.add_argument("--openclaw", type=Path, default=DEFAULT_OPENCLAW_ROOT)
    recent_parser.add_argument("--copilot", type=Path, default=DEFAULT_COPILOT_ROOT)
    recent_parser.add_argument("--semantics", type=Path, help="explicit versioned semantic catalog JSON")

    recent24_parser = subparsers.add_parser(
        "24h", help="shortcut for recent token burn over the last 24 hours"
    )
    recent24_parser.add_argument("--limit", type=int, default=10)
    recent24_parser.add_argument("--db", type=Path, help="SQLite DB path")
    recent24_parser.add_argument("--no-scan", action="store_true", help="reuse the DB")
    recent24_parser.add_argument("--actor-type", choices=ACTOR_TYPES, help="only show one actor type")
    recent24_parser.add_argument("--codex", type=Path, default=DEFAULT_CODEX_ROOT)
    recent24_parser.add_argument("--openclaw", type=Path, default=DEFAULT_OPENCLAW_ROOT)
    recent24_parser.add_argument("--copilot", type=Path, default=DEFAULT_COPILOT_ROOT)
    recent24_parser.add_argument("--semantics", type=Path, help="explicit versioned semantic catalog JSON")

    subparsers.add_parser("sources", help="show supported and planned evidence sources")

    update_parser = subparsers.add_parser("update", help="update the local Toolburn checkout")
    update_parser.add_argument(
        "--install-dir",
        type=Path,
        default=default_install_dir(),
        help="Toolburn git checkout to update",
    )
    update_parser.add_argument("--remote", default="origin", help="git remote to fetch")
    update_parser.add_argument("--ref", default="main", help="remote ref to fast-forward to")
    update_parser.add_argument(
        "--bin-dir",
        type=Path,
        default=default_bin_dir(),
        help="directory where the toolburn wrapper should be installed",
    )
    update_parser.add_argument(
        "--force",
        action="store_true",
        help="discard local checkout changes and reset to the fetched ref",
    )
    update_parser.add_argument(
        "--skip-install",
        action="store_true",
        help="update the checkout without rewriting the command wrapper",
    )

    for command in ("du", "top"):
        report_parser = subparsers.add_parser(command, help=f"show token usage by {command}")
        report_parser.add_argument("--db", required=True, type=Path, help="SQLite DB path")
        report_parser.add_argument("--by", choices=REPORT_GROUPS, default="actor")
        report_parser.add_argument("--limit", type=int, default=20)
        report_parser.add_argument("--since", help="inclusive ISO timestamp lower bound")
        report_parser.add_argument("--actor-type", choices=ACTOR_TYPES, help="only show one actor type")

    tree_parser = subparsers.add_parser("tree", help="show compact actor drilldown")
    tree_parser.add_argument("--db", required=True, type=Path, help="SQLite DB path")
    tree_parser.add_argument("target", help="actor_id or session_id")

    explain_parser = subparsers.add_parser("explain", help="explain an actor or session")
    explain_parser.add_argument("--db", required=True, type=Path, help="SQLite DB path")
    explain_parser.add_argument("target", help="actor_id or session_id")
    explain_parser.add_argument("--for-agent", action="store_true")

    export_parser = subparsers.add_parser("export", help="export compact JSON for an agent")
    export_parser.add_argument("--db", required=True, type=Path, help="SQLite DB path")
    export_parser.add_argument("--target", help="optional actor_id or session_id")

    pass_parser = subparsers.add_parser(
        "pass", help="emit a compact content-free receipt for a Harness or Efficiency pass"
    )
    pass_parser.add_argument(
        "target",
        nargs="?",
        default="current",
        help="current, previous, an exact Codex session UUID, or an exact rollout JSONL path",
    )
    pass_parser.add_argument("--turn", help="select one exact completed turn ID")
    pass_parser.add_argument("--session-root", type=Path, default=DEFAULT_SESSION_ROOT)
    pass_parser.add_argument("--episodes-root", type=Path, default=DEFAULT_EPISODE_ROOT)
    pass_parser.add_argument("--no-enrichment", action="store_true")
    pass_parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
    pass_parser.add_argument("--pretty", action="store_true", help="pretty-print JSON")

    inspect_parser = subparsers.add_parser(
        "inspect", help="inspect one bounded local-private pass hotspot"
    )
    inspect_parser.add_argument("hotspot_id", help="exact hotspot ID emitted by toolburn pass")
    inspect_parser.add_argument("--session", required=True, help="exact session UUID from the pass receipt")
    inspect_parser.add_argument("--turn", action="append", required=True, help="exact selected turn ID; may be repeated")
    inspect_parser.add_argument("--session-root", type=Path, default=DEFAULT_SESSION_ROOT)
    inspect_parser.add_argument("--pretty", action="store_true", help="pretty-print JSON")

    compare_parser = subparsers.add_parser(
        "compare", help="compare two pass receipts without rating or causal claims"
    )
    compare_parser.add_argument("baseline", help="baseline session UUID or rollout JSONL path")
    compare_parser.add_argument("candidate", help="candidate session UUID or rollout JSONL path")
    compare_parser.add_argument("--baseline-turn", help="exact completed baseline turn ID")
    compare_parser.add_argument("--candidate-turn", help="exact completed candidate turn ID")
    compare_parser.add_argument("--session-root", type=Path, default=DEFAULT_SESSION_ROOT)
    compare_parser.add_argument("--episodes-root", type=Path, default=DEFAULT_EPISODE_ROOT)
    compare_parser.add_argument("--no-enrichment", action="store_true")
    compare_parser.add_argument("--pretty", action="store_true", help="pretty-print JSON")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "schema":
        initialize_database(args.db)
        print(f"initialized {args.db}")
        print("\n".join(table_names(args.db)))
        return 0

    if args.command == "scan":
        sources = parse_sources(args)
        if not sources:
            parser.error("scan requires at least one --source, --codex, --openclaw, or --copilot path")
        counts = scan_sources(args.db, sources)
        print(format_scan_counts(counts))
        return 0

    if args.command in {"recent", "24h"}:
        db_path = args.db or default_recent_db_path()
        since = hours_ago_iso(args.hours if args.command == "recent" else 24)
        sources = existing_default_sources(args)
        if not args.no_scan:
            if not sources:
                parser.error("no default Codex/OpenClaw/Copilot session roots found")
            counts = scan_sources(db_path, sources, modified_since=since)
            print(format_scan_counts(counts))
        semantics_path = args.semantics or DEFAULT_SEMANTICS_PATH
        if args.semantics is not None and not semantics_path.exists():
            print(f"toolburn semantics failed: catalog not found: {semantics_path}")
            return 2
        try:
            catalog = load_semantic_catalog(semantics_path) if semantics_path.exists() else None
        except SemanticCatalogError as exc:
            print(f"toolburn semantics failed: {exc}")
            return 2
        episodes = episode_report(
            db_path,
            limit=None,
            since=since,
            actor_type=args.actor_type,
        )
        print(f"since {since}")
        if args.actor_type:
            print(f"actor_type {args.actor_type}")
        print("")
        print("Top actors")
        print(
            format_table(
                top_report(
                    db_path,
                    group_by="actor",
                    limit=args.limit,
                    since=since,
                    actor_type=args.actor_type,
                )
            )
        )
        print("")
        print("Top factual episodes")
        print(format_episode_table(episodes[: args.limit]))
        print("")
        print("Semantic coverage")
        print(format_semantic_summary(semantic_summary(episodes, catalog), semantics_path))
        return 0

    if args.command == "sources":
        print(format_sources())
        return 0

    if args.command == "update":
        try:
            result = update_installation(
                install_dir=args.install_dir,
                remote=args.remote,
                ref=args.ref,
                bin_dir=args.bin_dir,
                force=args.force,
                install_wrapper=not args.skip_install,
            )
        except ToolburnUpdateError as exc:
            print(f"toolburn update failed: {exc}")
            return 2
        print(format_update_result(result))
        return 0

    if args.command == "du":
        print(
            format_table(
                du_report(
                    args.db,
                    group_by=args.by,
                    limit=args.limit,
                    since=args.since,
                    actor_type=args.actor_type,
                )
            )
        )
        return 0

    if args.command == "top":
        print(
            format_table(
                top_report(
                    args.db,
                    group_by=args.by,
                    limit=args.limit,
                    since=args.since,
                    actor_type=args.actor_type,
                )
            )
        )
        return 0

    if args.command == "tree":
        print(format_explain(explain_report(args.db, args.target), for_agent=False))
        return 0

    if args.command == "explain":
        print(format_explain(explain_report(args.db, args.target), for_agent=args.for_agent))
        return 0

    if args.command == "export":
        print(export_json(args.db, args.target))
        return 0

    if args.command == "pass":
        try:
            receipt = build_pass_receipt(
                args.target,
                exact_turn=args.turn,
                session_root=args.session_root,
                episode_root=args.episodes_root,
                include_enrichment=not args.no_enrichment,
            )
        except PassReceiptError as exc:
            print(f"toolburn pass failed: {exc}")
            return 2
        if args.format == "markdown":
            print(format_pass_markdown(receipt))
        else:
            print(json.dumps(receipt, indent=2 if args.pretty else None, separators=None if args.pretty else (",", ":")))
        return 0

    if args.command == "inspect":
        try:
            receipt = inspect_hotspot(
                args.hotspot_id,
                session_id=args.session,
                turn_ids=args.turn,
                session_root=args.session_root,
            )
        except PassReceiptError as exc:
            print(f"toolburn inspect failed: {exc}")
            return 2
        print(json.dumps(receipt, indent=2 if args.pretty else None, separators=None if args.pretty else (",", ":")))
        return 0

    if args.command == "compare":
        try:
            baseline = build_pass_receipt(
                args.baseline,
                exact_turn=args.baseline_turn,
                session_root=args.session_root,
                episode_root=args.episodes_root,
                include_enrichment=not args.no_enrichment,
            )
            candidate = build_pass_receipt(
                args.candidate,
                exact_turn=args.candidate_turn,
                session_root=args.session_root,
                episode_root=args.episodes_root,
                include_enrichment=not args.no_enrichment,
            )
        except PassReceiptError as exc:
            print(f"toolburn compare failed: {exc}")
            return 2
        comparison = compare_receipts(baseline, candidate)
        print(json.dumps(comparison, indent=2 if args.pretty else None, separators=None if args.pretty else (",", ":")))
        return 0

    parser.print_help()
    return 0


def parse_sources(args: argparse.Namespace) -> list[SourceSpec]:
    sources: list[SourceSpec] = []
    if args.codex:
        sources.append(SourceSpec("codex", args.codex))
    if args.openclaw:
        sources.append(SourceSpec("openclaw", args.openclaw))
    if getattr(args, "copilot", None):
        sources.append(SourceSpec("github-copilot", args.copilot))
    for item in args.source:
        if "=" not in item:
            raise SystemExit(f"--source must use LABEL=PATH: {item}")
        label, raw_path = item.split("=", 1)
        sources.append(SourceSpec(label.strip(), Path(raw_path)))
    return sources


def existing_default_sources(args: argparse.Namespace) -> list[SourceSpec]:
    sources = []
    if args.codex and args.codex.exists():
        sources.append(SourceSpec("codex", args.codex))
    if args.openclaw and args.openclaw.exists():
        sources.append(SourceSpec("openclaw", args.openclaw))
    if args.copilot and args.copilot.exists():
        sources.append(SourceSpec("github-copilot", args.copilot))
    return sources


def hours_ago_iso(hours: float) -> str:
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    return since.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def format_scan_counts(counts: dict[str, int]) -> str:
    changed = int(counts.get("files") or 0)
    skipped = int(counts.get("files_skipped") or 0)
    outside_window = int(counts.get("files_outside_window") or 0)
    prefix = f"scanned {changed} changed files"
    if skipped:
        prefix += f", skipped {skipped} unchanged"
    if outside_window:
        prefix += f", ignored {outside_window} outside window"
    return (
        f"{prefix}, {counts['sessions']} sessions, {counts['token_events']} token events, "
        f"{counts['invocations']} invocations"
    )


def default_recent_db_path() -> Path:
    return Path(gettempdir()) / "toolburn-recent.sqlite"


class ToolburnUpdateError(RuntimeError):
    pass


def default_install_dir() -> Path:
    env = os.environ.get("TOOLBURN_INSTALL_DIR")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[2]


def default_bin_dir() -> Path:
    env = os.environ.get("TOOLBURN_BIN_DIR")
    if env:
        return Path(env)
    if os.geteuid() == 0:
        return Path("/usr/local/bin")
    return Path.home() / ".local" / "bin"


def update_installation(
    install_dir: Path,
    remote: str,
    ref: str,
    bin_dir: Path,
    force: bool = False,
    install_wrapper: bool = True,
) -> dict[str, str]:
    repo = install_dir.resolve()
    if not (repo / ".git").exists():
        raise ToolburnUpdateError(f"{repo} is not a git checkout")

    before = git_output(repo, "rev-parse", "--short", "HEAD")
    status = git_output(repo, "status", "--porcelain")
    if status and not force:
        raise ToolburnUpdateError(
            f"{repo} has local changes; commit them or rerun with --force"
        )

    git_run(repo, "fetch", "--quiet", remote, ref)
    if force:
        git_run(repo, "reset", "--quiet", "--hard", "FETCH_HEAD")
    else:
        git_run(repo, "merge", "--ff-only", "FETCH_HEAD")
    after = git_output(repo, "rev-parse", "--short", "HEAD")

    wrapper = ""
    if install_wrapper:
        wrapper = install_command_wrapper(repo, bin_dir)

    return {
        "install_dir": str(repo),
        "before": before,
        "after": after,
        "wrapper": wrapper,
    }


def git_run(repo: Path, *args: str) -> None:
    try:
        subprocess.run(
            ["git", "-C", str(repo), *args],
            check=True,
            text=True,
            capture_output=True,
        )
    except FileNotFoundError as exc:
        raise ToolburnUpdateError("git is not on PATH") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip()
        raise ToolburnUpdateError(detail or "git command failed") from exc


def git_output(repo: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), *args],
            check=True,
            text=True,
            capture_output=True,
        )
    except FileNotFoundError as exc:
        raise ToolburnUpdateError("git is not on PATH") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip()
        raise ToolburnUpdateError(detail or "git command failed") from exc
    return result.stdout.strip()


def install_command_wrapper(repo: Path, bin_dir: Path) -> str:
    target = bin_dir / "toolburn"
    bin_dir.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        f'exec "{repo / "toolburn"}" "$@"\n',
        encoding="utf-8",
    )
    target.chmod(0o755)
    return str(target)


def format_update_result(result: dict[str, str]) -> str:
    lines = [
        f"updated {result['install_dir']}",
        f"commit {result['before']} -> {result['after']}",
    ]
    if result.get("wrapper"):
        lines.append(f"installed {result['wrapper']}")
    return "\n".join(lines)


def format_sources() -> str:
    rows = [
        ("codex", "supported", str(DEFAULT_CODEX_ROOT), "Codex rollout JSONL token_count rows"),
        ("openclaw", "supported", str(DEFAULT_OPENCLAW_ROOT), "OpenClaw-owned Codex rollout JSONL"),
        (
            "github-copilot",
            "experimental",
            str(DEFAULT_COPILOT_ROOT),
            "Copilot CLI session-state events.jsonl; shutdown/model usage is cumulative",
        ),
        (
            "claude-code",
            "untested",
            "~/.claude",
            "settings and CLAUDE.md locations are documented; transcript parsing waits for a confirmed session sample",
        ),
    ]
    width = max(len(row[0]) for row in rows)
    return "\n".join(
        f"{name:<{width}}  {status:<12}  {path}  {note}" for name, status, path, note in rows
    )


if __name__ == "__main__":
    raise SystemExit(main())
