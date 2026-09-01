"""Compact SQLite reports for Toolburn."""

from __future__ import annotations

import json
from pathlib import Path

from toolburn.schema import connect


def du_report(
    db_path: Path,
    group_by: str = "actor",
    limit: int = 20,
    since: str | None = None,
    actor_type: str | None = None,
) -> list[dict]:
    column, joins = group_query_parts(group_by)
    order_column = "uncached_tokens" if group_by == "tool" else "raw_tokens"
    filters = []
    params: list[object] = []
    if since:
        filters.append("token_events.ts >= ?")
        params.append(since)
    if actor_type:
        joins = f"{joins}\njoin actors on actors.actor_id = token_events.actor_id"
        filters.append("actors.actor_type = ?")
        params.append(actor_type)
    where = f"where {' and '.join(filters)}" if filters else ""
    query = f"""
        select {column} as label,
               count(token_events.token_event_id) as events,
               sum(raw_total_tokens) as raw_tokens,
               sum(input_tokens) as input_tokens,
               sum(cached_input_tokens) as cached_input_tokens,
               sum(output_tokens) as output_tokens,
               sum(max(input_tokens - cached_input_tokens, 0) + output_tokens) as uncached_tokens
        from token_events
        {joins}
        {where}
        group by {column}
        order by {order_column} desc
        limit ?
    """
    with connect(db_path) as conn:
        rows = conn.execute(query, [*params, limit]).fetchall()
    return [dict(row) for row in rows]


def top_report(
    db_path: Path,
    group_by: str = "actor",
    limit: int = 20,
    since: str | None = None,
    actor_type: str | None = None,
) -> list[dict]:
    return du_report(
        db_path, group_by=group_by, limit=limit, since=since, actor_type=actor_type
    )


def episode_report(
    db_path: Path,
    limit: int | None = 20,
    since: str | None = None,
    actor_type: str | None = None,
) -> list[dict]:
    actor = canonical_actor_sql()
    joins = """
        left join invocations on invocations.invocation_id = token_events.invocation_id
        left join tools on tools.tool_id = invocations.tool_id
    """
    filters = []
    params: list[object] = []
    if since:
        filters.append("token_events.ts >= ?")
        params.append(since)
    if actor_type:
        joins += "\njoin actors on actors.actor_id = token_events.actor_id"
        filters.append("actors.actor_type = ?")
        params.append(actor_type)
    where = f"where {' and '.join(filters)}" if filters else ""
    limit_sql = "limit ?" if limit is not None else ""
    if limit is not None:
        params.append(limit)
    query = f"""
        select coalesce(token_events.invocation_id, token_events.token_event_id) as episode_id,
               {actor} as actor_id,
               token_events.session_id,
               min(token_events.ts) as started_at,
               count(token_events.token_event_id) as events,
               sum(raw_total_tokens) as raw_tokens,
               sum(input_tokens) as input_tokens,
               sum(cached_input_tokens) as cached_input_tokens,
               sum(output_tokens) as output_tokens,
               sum(max(input_tokens - cached_input_tokens, 0) + output_tokens) as uncached_tokens,
               tools.normalized_command,
               invocations.bundle_json,
               invocations.bundle_fingerprint
        from token_events
        {joins}
        {where}
        group by coalesce(token_events.invocation_id, token_events.token_event_id),
                 {actor}, token_events.session_id, tools.normalized_command,
                 invocations.bundle_json, invocations.bundle_fingerprint
        order by uncached_tokens desc
        {limit_sql}
    """
    with connect(db_path) as conn:
        rows = conn.execute(query, params).fetchall()
    return [dict(row) for row in rows]


def format_episode_table(rows: list[dict]) -> str:
    if not rows:
        return "no rows"
    formatted = []
    for source in rows:
        row = dict(source)
        row["label"] = episode_label(row)
        formatted.append(row)
    return format_table(formatted)


def episode_label(row: dict) -> str:
    timestamp = str(row.get("started_at") or "")
    actor = str(row.get("actor_id") or "unknown")
    episode_id = str(row.get("episode_id") or "")
    bundle = format_bundle(row.get("bundle_json"), row.get("normalized_command"))
    return f"{timestamp}  {actor}  episode:{episode_id}  {bundle}"


def format_bundle(raw_bundle: object, fallback: object, limit: int = 180) -> str:
    bundle: dict = {}
    if isinstance(raw_bundle, str) and raw_bundle:
        try:
            decoded = json.loads(raw_bundle)
        except json.JSONDecodeError:
            decoded = {}
        if isinstance(decoded, dict):
            bundle = decoded
    tools = [str(item) for item in bundle.get("tools", []) if str(item)]
    commands = [compact_command(str(item)) for item in bundle.get("commands", []) if str(item)]
    parts = []
    if tools:
        counts: dict[str, int] = {}
        for tool in tools:
            counts[tool] = counts.get(tool, 0) + 1
        parts.append("+".join(f"{tool}x{count}" if count > 1 else tool for tool, count in counts.items()))
    if commands:
        parts.append("; ".join(commands[:3]) + (f"; +{len(commands) - 3} more" if len(commands) > 3 else ""))
    text = " | ".join(parts) or str(fallback or "no-tool-context")
    return text if len(text) <= limit else text[: limit - 3] + "..."


def compact_command(command: str, limit: int = 72) -> str:
    normalized = " ".join(command.split())
    return normalized if len(normalized) <= limit else normalized[: limit - 3] + "..."


def explain_report(db_path: Path, target: str) -> dict:
    with connect(db_path) as conn:
        actor = conn.execute(
            "select * from actors where actor_id = ?", (target,)
        ).fetchone()
        if actor is None:
            session = conn.execute(
                "select actor_id from sessions where session_id = ?", (target,)
            ).fetchone()
            if session is not None:
                actor = conn.execute(
                    "select * from actors where actor_id = ?", (session["actor_id"],)
                ).fetchone()
        if actor is None:
            return {"target": target, "found": False}
        actor_id = actor["actor_id"]
        totals = conn.execute(
            """
            select count(*) events,
                   sum(raw_total_tokens) raw_tokens,
                   sum(input_tokens) input_tokens,
                   sum(cached_input_tokens) cached_input_tokens,
                   sum(output_tokens) output_tokens,
                   min(ts) first_seen,
                   max(ts) last_seen
            from token_events
            where actor_id = ?
            """,
            (actor_id,),
        ).fetchone()
        tools = conn.execute(
            """
            select tools.normalized_command, count(*) invocations, sum(invocations.output_bytes) output_bytes
            from invocations
            join tools on tools.tool_id = invocations.tool_id
            where invocations.actor_id = ?
            group by tools.tool_id
            order by output_bytes desc, invocations desc
            limit 8
            """,
            (actor_id,),
        ).fetchall()
    result = {
        "target": target,
        "found": True,
        "actor": dict(actor),
        "totals": dict(totals),
        "tools": [dict(row) for row in tools],
        "opportunities": opportunities(dict(totals), [dict(row) for row in tools]),
    }
    return result


def format_table(rows: list[dict]) -> str:
    if not rows:
        return "no rows"
    lines = []
    for row in rows:
        row = dict(row)
        row.setdefault(
            "uncached_tokens",
            max(
                int(row.get("input_tokens") or 0)
                - int(row.get("cached_input_tokens") or 0),
                0,
            )
            + int(row.get("output_tokens") or 0),
        )
        lines.append(
            "{raw_tokens:>12} raw  {cached_input_tokens:>12} cached  "
            "{uncached_tokens:>10} uncached  {output_tokens:>8} output  "
            "{events:>5} events  {label}".format(**row)
        )
    return "\n".join(lines)


def format_explain(report: dict, for_agent: bool = False) -> str:
    if not report.get("found"):
        return f"Toolburn target not found: {report['target']}"
    actor = report["actor"]
    totals = report["totals"]
    tools = report["tools"]
    cached = int(totals.get("cached_input_tokens") or 0)
    raw = int(totals.get("raw_tokens") or 0)
    ratio = cached / raw if raw else 0.0
    lines = [
        "Toolburn finding",
        "",
        f"Actor: {actor['actor_id']}",
        f"Type: {actor['actor_type']}",
        f"Confidence: {actor['label_confidence']}",
        f"Window: {totals.get('first_seen') or ''} -> {totals.get('last_seen') or ''}",
        f"Tokens: {raw} raw, {cached} cached input, {totals.get('output_tokens') or 0} output",
        f"Cached ratio: {ratio:.2f}",
    ]
    if tools:
        lines.extend(["", "Top tool-contexts:"])
        for tool in tools:
            lines.append(
                f"  {tool['output_bytes'] or 0} bytes output over "
                f"{tool['invocations']} calls: {tool['normalized_command']}"
            )
    lines.extend(["", "Opportunities:"])
    for item in report["opportunities"]:
        lines.append(f"  {item}")
    if for_agent:
        lines.extend(["", "Agent handoff JSON:", json.dumps(report, sort_keys=True)])
    return "\n".join(lines)


def export_json(db_path: Path, target: str | None = None) -> str:
    payload = {"du": du_report(db_path, "actor", 100)}
    if target:
        payload["explain"] = explain_report(db_path, target)
    return json.dumps(payload, sort_keys=True)


def group_query_parts(group_by: str) -> tuple[str, str]:
    if group_by == "actor":
        return canonical_actor_sql(), ""
    if group_by == "session":
        return "token_events.session_id", ""
    if group_by == "source":
        return "token_events.source_path", ""
    if group_by == "tool":
        return (
            "coalesce(tools.normalized_command, 'no-tool-context:' || "
            + canonical_actor_sql()
            + ")",
            """
            left join invocations on invocations.invocation_id = token_events.invocation_id
            left join tools on tools.tool_id = invocations.tool_id
            """,
        )
    raise ValueError(f"unsupported group: {group_by}")


def canonical_actor_sql() -> str:
    generic_direct = "human.openclaw." + "tele" + "gram-direct"
    concrete_direct = "human.openclaw.direct-session.[0-9]*"
    return """
        case
          when token_events.actor_id = '{generic_direct}'
           and (
             select count(distinct actor_id)
             from actors
             where actor_id glob '{concrete_direct}'
           ) = 1
          then (
             select actor_id
             from actors
             where actor_id glob '{concrete_direct}'
             limit 1
           )
          else token_events.actor_id
        end
    """.format(
        generic_direct=generic_direct,
        concrete_direct=concrete_direct,
    )


def opportunities(totals: dict, tools: list[dict]) -> list[str]:
    raw = int(totals.get("raw_tokens") or 0)
    cached = int(totals.get("cached_input_tokens") or 0)
    events = int(totals.get("events") or 0)
    items: list[str] = []
    if raw and cached / raw >= 0.7 and events >= 2:
        items.append("context_leak: cached input dominates repeated cycles")
    if events >= 6:
        items.append("recurrence_check: many token events in one actor")
    if any(int(tool.get("output_bytes") or 0) > 100000 for tool in tools):
        items.append("output_cap: large tool output reached model context")
    if not items:
        items.append("inspect: keep attribution explicit before optimizing")
    return items
