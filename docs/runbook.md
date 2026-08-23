# Runbook

## Local Validation

Run the full scaffold check:

```bash
./scripts/validate.sh
```

The validator checks required docs, Python syntax, unit tests, CLI help, schema
creation, generated-file hygiene, and obvious secret-shaped strings.

## Local CLI

Install or update from GitHub:

```bash
curl -fsSL https://raw.githubusercontent.com/DarkExec/ToolBurn/main/install.sh | sh
```

Update an existing install:

```bash
toolburn update
```

Install from a checkout:

```bash
./scripts/install-local.sh
```

```bash
toolburn --help
toolburn recent --help
toolburn pass --help
```

The normal local check is:

```bash
toolburn 24h
toolburn recent --hours 23 --limit 20
toolburn pass current --format markdown
toolburn inspect '<hotspot-id>' --session '<session-id>' --turn '<turn-id>' --pretty
```

This scans the default local Codex and OpenClaw session roots, writes a reusable
SQLite database under `/tmp`, and prints top actors and top tools for the
lookback window.

Use an explicit database when you want repeatable drilldown:

```bash
toolburn scan --db /tmp/toolburn.sqlite \
  --openclaw /root/.openclaw/agents/main/agent/codex-home/sessions \
  --codex /root/.codex/sessions

toolburn top --db /tmp/toolburn.sqlite --by actor --since 2026-06-02T10:00:00.000Z
toolburn top --db /tmp/toolburn.sqlite --by tool --since 2026-06-02T10:00:00.000Z
toolburn explain --db /tmp/toolburn.sqlite <actor-or-session-id> --for-agent
```

For a Harness pass in the active Codex session, run `toolburn pass current`. For an Efficiency pass over the immediately preceding Harness turn, run `toolburn pass previous`. Use `--turn` only when an exact completed turn is already known. A pass receipt may point to factual hotspots; inspect only the selected hotspot with its exact `toolburn inspect` command. Inspection is local-private and unsafe to publish. Use `toolburn compare` for stable factual deltas; leave ratings and causal conclusions to the evaluation owner.

## Evidence Handling

- Keep raw session logs, private incident traces, and personal-agent transcripts
  outside git.
- Store only fixtures that are synthetic or explicitly sanitized.
- Prefer compact JSON summaries with counts, hashes, timestamps, and sample
  bounds instead of raw log bodies.
- Keep generated SQLite databases in `/tmp` unless there is an explicit reason
  to persist one elsewhere.

## Phase 1 Closeout Proof

Before moving a Phase 1 plan to completed, capture:

- validation command and output
- exact parser sources supported
- sample `du`, `top`, `tree`, and `explain --for-agent` outputs
- evidence that the run used no model calls and no network
