# Toolburn

Toolburn is a local token burn profiler for coding agents.

The public documentation is rendered from this README at [toolburn.com](https://toolburn.com/).

Think of it as `ncdu` for token usage: it scans the session evidence already on
your machine, groups spend by actor, tool, session, and source, then points at
the burn paths worth fixing.

Toolburn is read-only. It does not call models, intercept prompts, or upload
anything. Phase 1 is an offline CLI for answering a very practical question:

> What used the tokens, and was it a human session, a background job, or a tool
> loop?

## Install

Install or update the command:

```bash
curl -fsSL https://raw.githubusercontent.com/DarkExec/ToolBurn/main/install.sh | sh
toolburn --help
```

After the command is installed, update it in place:

```bash
toolburn update
```

From a local checkout:

```bash
cd /srv/dark/repos/toolburn
./scripts/install-local.sh
toolburn --help
```

## Start Here

```bash
toolburn recent --hours 24
```

Shortcut:

```bash
toolburn 24h
```

That scans the default local session roots and prints the top actors and
tool-contexts for the lookback window.

Actor and session totals are token spend. Tool-context rows are attribution
hints: they show token events near a command in the transcript, not proof that
the command itself called a model. Tool-context reports include an `uncached`
column and rank by uncached tokens so deterministic commands with large cached
context do not look like the primary burn source.

If a token event has no nearby tool invocation, Toolburn reports it as
`no-tool-context:<actor>`. That usually means a model-only turn, final answer,
or adapter/source gap, and should be diagnosed at the actor or session level
instead of as a fake `unknown.tool`.

See supported evidence sources:

```bash
toolburn sources
```

## Commands Humans Actually Use

### Harness And Efficiency Pass Receipts

```bash
toolburn pass current
toolburn pass previous
toolburn pass <session-id> --turn <turn-id>
toolburn pass current --format json
toolburn inspect <hotspot-id> --session <session-id> --turn <turn-id>
toolburn compare <baseline-session> <candidate-session>
```

`pass current` resolves `$CODEX_THREAD_ID` and emits a compact, content-free Markdown receipt for completed ordinary turns since the previous recorded Harness turn when DarkExec episode evidence is available. `pass previous` isolates the immediately preceding completed turn for an Efficiency pass. Use `--format json` for the complete versioned machine receipt. Pass receipts separate confirmed failures from possible error-shaped output and explicitly report when orchestration retained command output but discarded its exit status. The full JSON also includes usage, compactions, tool categories, repeated command and call fingerprints, compact operation runs, and factual failure, repetition, and largest-output hotspots without prompts, messages, raw arguments, or raw output.

Each hotspot includes an exact `toolburn inspect` command. Inspection prints one bounded, redacted, local-private slice around that hotspot; it is not safe to publish and is never persisted by Toolburn.

`compare` reports factual candidate-minus-baseline deltas. It does not rate the work or claim that a Harness intervention caused the difference.

### Recent Burn

```bash
toolburn 24h
toolburn recent --hours 24 --limit 20
toolburn recent --hours 6 --limit 15
toolburn recent --hours 1 --limit 20
toolburn recent --hours 24 --no-scan
```

### Human vs Background Spend

```bash
toolburn recent --hours 24 --actor-type background
toolburn recent --hours 24 --actor-type human
toolburn recent --hours 1 --actor-type background --limit 20
```

### Scan Explicit Sources

```bash
toolburn scan --db /tmp/toolburn.sqlite --openclaw /root/.openclaw/agents/main/agent/codex-home/sessions --codex /root/.codex/sessions --copilot /root/.copilot/session-state
toolburn scan --db /tmp/toolburn.sqlite --codex /root/.codex/sessions
toolburn scan --db /tmp/toolburn.sqlite --openclaw /root/.openclaw/agents/main/agent/codex-home/sessions
toolburn scan --db /tmp/toolburn.sqlite --copilot /root/.copilot/session-state
toolburn scan --db /tmp/toolburn.sqlite --source codex=/path/to/rollout.jsonl
```

### Rank Burn By Actor, Tool-Context, Session, Or Source

```bash
toolburn top --db /tmp/toolburn.sqlite --by actor --since 2026-06-02T10:00:00.000Z
toolburn top --db /tmp/toolburn.sqlite --by tool --since 2026-06-02T10:00:00.000Z
toolburn top --db /tmp/toolburn.sqlite --by actor --actor-type background
toolburn top --db /tmp/toolburn.sqlite --by session --limit 30
toolburn top --db /tmp/toolburn.sqlite --by source --limit 30
toolburn du --db /tmp/toolburn.sqlite --by tool --actor-type human
```

### Explain A Suspicious Actor Or Session

```bash
toolburn explain --db /tmp/toolburn.sqlite <actor-or-session-id> --for-agent
toolburn tree --db /tmp/toolburn.sqlite <actor-or-session-id>
toolburn export --db /tmp/toolburn.sqlite --target <actor-or-session-id>
```

### Useful Investigation Patterns

Find background jobs burning tokens in the last hour:

```bash
toolburn recent --hours 1 --actor-type background --limit 20
```

Check whether a suspected fix actually stopped new burn after a timestamp:

```bash
toolburn top --db /tmp/toolburn-recent.sqlite --by actor --since 2026-06-03T11:29:40.000Z --actor-type background
```

Separate human coding work from agent automation:

```bash
toolburn recent --hours 24 --actor-type human
toolburn recent --hours 24 --actor-type background
```

Inspect one source file without scanning everything:

```bash
toolburn scan --db /tmp/one-session.sqlite --source codex=/path/to/rollout-2026-06-03.jsonl
toolburn top --db /tmp/one-session.sqlite --by tool
```

Run `toolburn <command> --help` for command-specific options. From this repo's
checkout, `./toolburn` also works without installation.

## Source Support

| Source | Status | Default path | Notes |
| --- | --- | --- | --- |
| Codex | Supported | `/root/.codex/sessions` | Reads `rollout-*.jsonl` token events. |
| OpenClaw | Supported | `/root/.openclaw/agents/main/agent/codex-home/sessions` | Reads OpenClaw-owned Codex transcripts and labels known background actors. |
| GitHub Copilot CLI | Experimental | `/root/.copilot/session-state` | Local Copilot installs keep session event logs at `~/.copilot/session-state/*/events.jsonl`. Toolburn reads `assistant.usage` rows when present and `session.shutdown.modelMetrics` cumulative usage when that is the only token evidence. |
| Claude Code | Untested | `~/.claude` | Claude Code support is not confirmed yet. Anthropic documents settings under `~/.claude/settings.json`, project settings under `.claude/settings.json`, and session-history controls such as `cleanupPeriodDays`, but Toolburn still needs a real Claude Code transcript sample before claiming support. |

Claude Code also uses `CLAUDE.md` instruction files, but those are context inputs,
not usage logs. Anthropic documents project/user locations such as `./CLAUDE.md`,
`./.claude/CLAUDE.md`, and `~/.claude/CLAUDE.md`.

References:

- [GitHub Copilot CLI README](https://docs.github.com/copilot/concepts/agents/about-copilot-cli)
- [Claude Code settings](https://docs.anthropic.com/en/docs/claude-code/settings)
- [Claude Code memory](https://docs.anthropic.com/en/docs/claude-code/memory)

## Current Status

Toolburn currently ships the offline profiler spine and versioned pass receipts. It can scan local Codex/OpenClaw JSONL evidence into SQLite, report recent token burn by actor, session, source, or tool, summarize the exact current or preceding Codex execution for a Harness or Efficiency pass, and compare two bounded executions without making a causal claim. GitHub Copilot CLI session-state parsing is experimental. Claude Code is listed as untested until a real local transcript is confirmed.

The active build plan is:

- [docs/exec-plans/active/toolburn-3-phase-plan.md](docs/exec-plans/active/toolburn-3-phase-plan.md)
