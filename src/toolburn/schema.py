"""SQLite schema for the offline Toolburn store."""

from __future__ import annotations

import sqlite3
from pathlib import Path


SCHEMA_SQL = """
create table if not exists actors(
  actor_id text primary key,
  actor_type text,
  source text,
  label_confidence real,
  first_seen text,
  last_seen text,
  metadata_json text
);

create table if not exists sessions(
  session_id text primary key,
  actor_id text,
  source text,
  path text,
  workspace text,
  started_at text,
  ended_at text,
  metadata_json text,
  foreign key(actor_id) references actors(actor_id)
);

create table if not exists tools(
  tool_id text primary key,
  normalized_command text,
  operation_context text,
  executable text,
  cwd text,
  fingerprint text,
  metadata_json text
);

create table if not exists invocations(
  invocation_id text primary key,
  session_id text,
  actor_id text,
  tool_id text,
  started_at text,
  ended_at text,
  output_bytes integer,
  output_fingerprint text,
  output_shape_json text,
  foreign key(session_id) references sessions(session_id),
  foreign key(actor_id) references actors(actor_id),
  foreign key(tool_id) references tools(tool_id)
);

create table if not exists token_events(
  token_event_id text primary key,
  session_id text,
  actor_id text,
  invocation_id text,
  ts text,
  model text,
  input_tokens integer,
  cached_input_tokens integer,
  output_tokens integer,
  raw_total_tokens integer,
  source_path text,
  foreign key(session_id) references sessions(session_id),
  foreign key(actor_id) references actors(actor_id),
  foreign key(invocation_id) references invocations(invocation_id)
);

create table if not exists burn_paths(
  burn_path_id text primary key,
  actor_id text,
  tool_id text,
  pattern text,
  cadence_seconds integer,
  tokens_24h integer,
  tokens_per_invocation_p95 integer,
  confidence real,
  metadata_json text,
  foreign key(actor_id) references actors(actor_id),
  foreign key(tool_id) references tools(tool_id)
);

create table if not exists scan_cache(
  source_path text primary key,
  source_label text,
  size_bytes integer,
  mtime_ns integer,
  parser_version integer,
  session_id text,
  scanned_at text
);
"""


REQUIRED_TABLES = (
    "actors",
    "sessions",
    "tools",
    "invocations",
    "token_events",
    "burn_paths",
    "scan_cache",
)


def initialize_database(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.executescript(SCHEMA_SQL)
        tool_columns = {
            row[1] for row in conn.execute("pragma table_info(tools)").fetchall()
        }
        if "operation_context" not in tool_columns:
            conn.execute("alter table tools add column operation_context text")
        conn.execute(
            "create index if not exists token_events_actor_ts on token_events(actor_id, ts)"
        )
        conn.execute(
            "create index if not exists token_events_session_ts on token_events(session_id, ts)"
        )
        conn.execute(
            "create index if not exists token_events_source_path on token_events(source_path)"
        )
        conn.execute(
            "create index if not exists sessions_path on sessions(path)"
        )
        conn.commit()


def table_names(path: Path) -> list[str]:
    with sqlite3.connect(path) as conn:
        rows = conn.execute(
            "select name from sqlite_master where type = 'table' order by name"
        ).fetchall()
    return [row[0] for row in rows]


def connect(path: Path) -> sqlite3.Connection:
    initialize_database(path)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn
