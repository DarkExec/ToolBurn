Excellent. **ToolBurn.com** gives the project a real center of gravity.

> Ownership note: this plan began while ToolBurn was a CodePager module. The canonical repository is now `DarkExec/ToolBurn`; remaining CodePager names below describe historical evidence or possible integration targets, not repository ownership.

Here is the 3-phase plan I’d use.

# Phase 1 — Offline profiler: “`ncdu` for token burn”

## Goal

Build a local-first CLI that answers one question brutally well:

> **Where did my token usage go, and what exact tools/actors were observed near it?**

This phase should be read-only, deterministic, and useful on day one against the Voiceze/OpenClaw incident. The acceptance test is that ToolBurn can parse the raw logs, reconstruct the recurring heartbeat burn, and clearly show that the no-op Discord heartbeat was the dominant sink. The postmortem gives the perfect benchmark: the old heartbeat session added **30,782,972 raw tokens**, including **30,182,400 cached input tokens**, and burned **17,126,041 raw tokens in the last 5 hours** before the final mitigation. 

## Product shape

CLI first:

```bash
toolburn scan
toolburn du
toolburn top
toolburn tree
toolburn explain
toolburn export --for-agent
```

The UX should feel like disk cleanup:

```text
21.4M  background.openclaw.heartbeat.voiceze
  18.7M  ./tools/heartbeat_scan.py --json
   2.1M  wait/poll loop around slow shell command
 605.0K  baseline heartbeat context
```

But instead of “delete this file,” the actions are:

```text
compress this output
cap this command
cache this scan
turn this into a deterministic adapter
remove this from the model loop
only escalate non-zero candidates/errors
```

## Core objects

ToolBurn should not start with inferred categories as the primary abstraction. Session evidence is raw material. The factual abstraction should be:

```text
Factual Episode = actor + session + nearby invocation bundle + token event
```

Define these objects early:

```text
Actor
  human.codex.project.foo
  background.openclaw.heartbeat.voiceze
  cron.codex.weekly-review
  systemd.timer.incident-watchdog
  unknown.recurring.2026-06-03-a17f

Tool
  ./tools/heartbeat_scan.py --json
  tail -n 5000 logs/app.log
  rg "ERROR|WARN"
  npm test
  discord moderation scan
  MCP tool call

Invocation
  one actor calling one tool/command and producing model-visible payload

Token Event
  raw input, cached input, output, total, model, timestamp, session path

Factual Episode
  stable grouping of token events around one observed invocation, without a causal claim

Semantic Assignment
  versioned local interpretation attached to an exact factual episode ID

Opportunity
  cap, cache, compress, summarize, diff, adapter, deterministic runner, remove
```

## Deterministic attribution stack

This is the hard part, so it should be designed from the beginning.

Attribution priority should be:

```text
1. Explicit ToolBurn metadata
2. OpenClaw/Codex session metadata
3. Session path / workspace path / cwd
4. Command/tool call signature
5. systemd/cron/HEARTBEAT.md ownership
6. Recurrence clustering
7. Unknown bucket
```

The rule should be:

> **Never pretend unknown is known.**

If ToolBurn cannot deterministically label something, it should say:

```text
unknown.recurring.high-burn
unknown.human-like.codex-session
unknown.tool-output.giant-json
```

That is still useful.

## First parsers/adapters

Start with only what matters:

```text
OpenClaw session JSONL parser
Codex session/log parser
OpenClaw heartbeat/workspace detector
systemd user timer scanner
cron scanner
tool/call signature normalizer
token-count event normalizer
```

Do not build eBPF.
Do not build a cloud dashboard.
Do not intercept TLS.
Do not wrap every command yet.

Phase 1 is “read the evidence already on disk.”

## SQLite schema

Use SQLite or DuckDB locally. I’d start with SQLite because it is dead simple and easy to ship.

Minimum schema:

```sql
actors(
  actor_id text primary key,
  actor_type text,
  source text,
  label_confidence real,
  first_seen text,
  last_seen text,
  metadata_json text
);

sessions(
  session_id text primary key,
  actor_id text,
  source text,
  path text,
  workspace text,
  started_at text,
  ended_at text,
  metadata_json text
);

tools(
  tool_id text primary key,
  normalized_command text,
  executable text,
  cwd text,
  fingerprint text,
  metadata_json text
);

invocations(
  invocation_id text primary key,
  session_id text,
  actor_id text,
  tool_id text,
  started_at text,
  ended_at text,
  output_bytes integer,
  output_fingerprint text,
  output_shape_json text
);

token_events(
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
  source_path text
);

```

## Phase 1 commands

```bash
toolburn scan --sources openclaw,codex --since 7d
```

Parse local logs into the DB.

```bash
toolburn du --by actor --since 24h
```

Show usage grouped like disk usage.

```bash
toolburn top --by raw_tokens --since 24h
```

Rank the biggest burn paths.

```bash
toolburn tree background.openclaw.heartbeat.voiceze
```

Show the hierarchy under an actor.

```bash
toolburn explain ./tools/heartbeat_scan.py --for-agent
```

Emit compact drilldown for an agent.

Example output:

```text
ToolBurn finding: recurring no-op model-supervised heartbeat

Actor:
  background.openclaw.heartbeat.voiceze

Tool:
  ./tools/heartbeat_scan.py --json &&
  ./tools/spam_cleanup.py --apply --json

Pattern:
  recurring every ~30m
  high cached-input usage
  repeated wait/poll tool calls
  no candidates/errors observed

Optimization:
  move scan/cleanup into deterministic runner
  emit compact metrics
  invoke model only when candidates/errors > 0
```

That should exactly mirror the successful mitigation: native heartbeat disabled, deterministic runner active, and clean cycles showing `model_invoked=false` / `model_would_invoke=false`. 

## Phase 1 exit criteria

Phase 1 is done when ToolBurn can:

```text
1. Parse Voiceze/OpenClaw/Codex session logs.
2. Identify the heartbeat as background/recurring, not human-driven work.
3. Show raw/cached/output token totals by actor, tool, session, and time window.
4. Detect wait/poll loops.
5. Produce a compact agent-readable report.
6. Run fully offline with no model calls and no network.
```

The important win is not a pretty dashboard. The win is:

> “I can point an agent at this report and it knows exactly where to optimize.”

---

# Phase 2 — Live watcher: CodePager-ready runway alerts

## Goal

Turn the offline profiler into a deterministic live watcher that catches new token guzzlers early.

This is where ToolBurn becomes operational infrastructure for CodePager.

The live watcher should answer:

```text
Is a new actor consuming tokens?
Is it human-driven or background?
Is it recurring?
Is its burn rate dangerous?
Did a critical background actor go silent?
Which tool is causing the burn?
```

## Product shape

```bash
toolburn watch
toolburn watch --jsonl /var/log/toolburn/events.jsonl
toolburn watch --codepager
toolburn policy check
toolburn actors
toolburn alerts
```

This process should still be model-free.

## ToolBurn policy file

Create a repo-local or machine-local policy file:

```yaml
# .toolburn.yml

project: voiceze

budgets:
  weekly_raw_tokens: 100000000
  alert_at_weekly_runway_hours: 24

actors:
  - actor_id: background.openclaw.heartbeat.voiceze
    type: heartbeat
    expected_cadence: 30m
    expected_model_invocation: only_on_candidates
    max_raw_tokens_per_clean_cycle: 10000
    max_model_invocations_per_24h: 2
    criticality: high

  - actor_id: watchdog.incident.main
    type: watchdog
    expected_cadence: 5m
    liveness_required: true
    max_silent_intervals: 3
    criticality: mission_critical

tools:
  - match: "./tools/heartbeat_scan.py --json"
    owner: voiceze
    expected_output_shape: compact_json
    max_tokens_per_invocation: 5000
    optimization_hint: deterministic_noop_fast_path

unknown_actor_policy:
  recurring_background_threshold: 3
  alert_if_projected_24h_raw_tokens_gt: 1000000
```

The policy should support explicit labels, but ToolBurn should also work without them.

## Alert types

CodePager should receive compact JSONL events like:

```json
{
  "event_type": "token_guzzler_detected",
  "severity": "critical",
  "actor_id": "background.openclaw.heartbeat.voiceze",
  "actor_type": "heartbeat",
  "tool_signature": "./tools/heartbeat_scan.py --json",
  "cadence_detected": "30m",
  "tokens_per_invocation_p95": 2100000,
  "tokens_24h_projected": 100800000,
  "reason": "Recurring background actor is consuming high token volume on clean/no-op cycles.",
  "suggested_action": "Move no-op path before model invocation; invoke model only on non-zero candidates/errors."
}
```

And for liveness:

```json
{
  "event_type": "background_actor_silent",
  "severity": "warning",
  "actor_id": "watchdog.incident.main",
  "expected_cadence": "5m",
  "last_seen_age": "22m",
  "reason": "Mission-critical actor missed more than 3 expected intervals.",
  "suggested_action": "Check timer/service health and latest runner metrics."
}
```

And for high-frequency tools:

```json
{
  "event_type": "high_frequency_tool_burn",
  "severity": "warning",
  "tool_signature": "tail -n 5000 logs/app.log",
  "calls_24h": 418,
  "tokens_24h": 9200000,
  "reason": "Moderate token cost per call, but very high frequency.",
  "suggested_action": "Replace full log output with deterministic parser emitting counts, newest examples, and stable hashes."
}
```

## Detection rules

Phase 2 should ship with a small but high-value deterministic ruleset:

```text
new_recurring_actor
  same actor/tool/session shape repeats at stable cadence

background_burn_rate_spike
  projected 24h/weekly usage crosses policy threshold

no_op_model_loop
  repeated cycles with empty candidates/errors but non-trivial model usage

wait_poll_loop
  many model/tool turns waiting on slow shell command

context_leak
  cached input dominates total usage across repeated cycles

high_frequency_tool_burn
  tool not individually huge, but called constantly

background_actor_silent
  expected actor stopped emitting token/runner events

unknown_high_burn
  cannot label actor, but burn rate is dangerous
```

The Voiceze case would trigger at least:

```text
new_recurring_actor
background_burn_rate_spike
no_op_model_loop
wait_poll_loop
context_leak
```

That is exactly what should have paged you before the weekly budget was at risk.

## Live data sources

Phase 2 should follow:

```text
Codex/OpenClaw session directories
OpenClaw rollout/session JSONL
ToolBurn sidecar JSONL
systemd timer/service metadata
cron metadata
runner metrics files
HEARTBEAT.md / project manifests
```

For your deterministic runners, standardize a tiny metrics file:

```json
{
  "toolburn_actor_id": "background.openclaw.heartbeat.voiceze",
  "ts": "2026-06-03T00:00:09Z",
  "runner": "voiceze-heartbeat-runner",
  "model_invoked": false,
  "model_would_invoke": false,
  "candidate_count": 0,
  "error_count": 0,
  "duration_ms": 4132,
  "tool_outputs": [
    {
      "command": "./tools/heartbeat_scan.py --json",
      "output_bytes": 382,
      "output_sha256": "..."
    }
  ]
}
```

This lets ToolBurn monitor liveness even when a correct deterministic path uses **zero tokens**.

That matters because “no token usage” can mean either:

```text
good: deterministic no-op path is working
bad: mission-critical watchdog is dead
```

The metrics file separates those.

## Phase 2 exit criteria

Phase 2 is done when ToolBurn can:

```text
1. Run continuously as a local watcher.
2. Detect a new recurring background token consumer.
3. Emit CodePager-ready JSONL alerts.
4. Distinguish human-driven Codex work from unattended background burn.
5. Detect high-frequency tool burn.
6. Detect “silent but expected alive” actors.
7. Stay read-only/model-free/network-free by default.
```

The key product moment:

> CodePager receives: “New recurring background actor projected to burn 80% of weekly token budget in 14 hours. Top tool: `discord_scan.py --json`. Clean cycles show no candidates. Suggested fix: deterministic no-op fast path.”

---

# Phase 3 — Optimization loop: from burn map to safe removal

## Goal

Make ToolBurn not just a monitor, but the thing that hands humans or agents a precise, minimal optimization target.

This is where it becomes valuable beyond your own incident.

The product should say:

```text
Here are the top 5 burn paths.
Here is why each one is expensive.
Here is the safest likely optimization.
Here is the tiny data pack an agent needs to fix it.
Here is how to verify tokens actually dropped.
```

## Product shape

```bash
toolburn explain <burn-path-id> --for-agent
toolburn recommend
toolburn adapter new <tool>
toolburn compare --before X --after Y
toolburn guard --ci
toolburn wrap <command>
```

## Agent drill packs

The agent should not receive raw logs. It should receive just enough structured data to act.

Example:

```json
{
  "toolburn_drill_pack_version": 1,
  "burn_path_id": "bp_voiceze_heartbeat_scan",
  "summary": "Recurring no-op heartbeat burns high cached-input tokens every 30m.",
  "actor": {
    "id": "background.openclaw.heartbeat.voiceze",
    "type": "heartbeat",
    "cadence_detected": "30m"
  },
  "tool": {
    "normalized_command": "./tools/heartbeat_scan.py --json && ./tools/spam_cleanup.py --apply --json",
    "cwd": "/root/.openclaw/workspace/voiceze"
  },
  "evidence": {
    "tokens_per_invocation_p95": 2100000,
    "cached_input_ratio": 0.98,
    "candidate_counts": [0, 0, 0, 0],
    "error_counts": [0, 0, 0, 0],
    "wait_poll_calls_detected": true
  },
  "optimization_hypothesis": {
    "type": "deterministic_noop_fast_path",
    "description": "Run scan/cleanup outside the model. Only invoke model when candidates/errors are non-zero."
  },
  "constraints": [
    "Do not re-enable native OpenClaw heartbeat.",
    "Preserve Discord moderation behavior.",
    "Emit runner metrics for ToolBurn liveness."
  ],
  "verification": [
    "Clean cycle emits model_invoked=false.",
    "No new token-count events appear for clean cycles.",
    "Candidate/error escalation still invokes isolated session."
  ]
}
```

This is the handoff format.

It turns the agent from “read all logs and figure it out” into:

> “Patch this exact burn path safely.”

## Optimization recipes

Ship a small recipe library:

```text
deterministic_noop_fast_path
  When candidates/errors/counts are zero, exit before model invocation.

output_cap
  Limit giant logs or JSON payloads to bounded summaries.

stable_hash_cache
  If scan output has same hash as previous run, emit "unchanged" summary.

diff_only
  Send only changed files/errors/events since last run.

headroom_wrap
  Apply compression where lossy/lossless compression is safe.

adapter_scaffold
  Build a deterministic adapter matching harness docs.

tool_split
  Separate cheap precheck from expensive diagnostic command.

polling_elimination
  Move long-running command supervision outside the model.

escalation_payload
  Replace raw output with compact JSON: counts, examples, hashes, links.
```

Each recipe should include:

```text
when_to_apply
risk_level
expected_savings
verification_steps
rollback_steps
example_patch_shape
```

## Adapter scaffolding

This is where your harness approach becomes first-class.

```bash
toolburn adapter new discord-scan \
  --from "./tools/heartbeat_scan.py --json" \
  --actor background.openclaw.heartbeat.voiceze
```

Generated scaffold:

```text
adapters/discord-scan/
  adapter.yml
  summarize.py
  verify.py
  fixtures/
    clean-output.json
    candidate-output.json
    error-output.json
  README.md
```

The adapter contract:

```yaml
name: discord-scan
input_command: "./tools/heartbeat_scan.py --json"
safe_noop_condition:
  candidate_count: 0
  error_count: 0
model_escalation_payload:
  include:
    - candidate_count
    - error_count
    - newest_examples
    - output_hash
    - runner_timestamp
verification:
  clean_cycle_max_raw_tokens: 0
  candidate_cycle_must_escalate: true
```

This connects ToolBurn directly to the workflow you already use: identify the guzzler, build the adapter, prove the no-op path is deterministic, and remove unnecessary model supervision.

## Before/after verification

ToolBurn should make savings undeniable:

```bash
toolburn compare --before 2026-06-02T10:00Z..2026-06-02T22:48Z \
                 --after  2026-06-02T22:48Z..2026-06-03T12:00Z
```

Output:

```text
background.openclaw.heartbeat.voiceze

Before:
  raw tokens / 5h: 17,126,041
  model invocations on clean cycles: yes
  wait/poll loops: yes

After:
  raw tokens / clean cycle: 0
  model_invoked=false
  model_would_invoke=false
  candidates/errors=0

Savings:
  clean-cycle model burn eliminated
```

That lines up with the postmortem’s successful end state: deterministic runner cycles completed with `model_invoked=false`, `model_would_invoke=false`, and zero candidates/errors. 

## CI/budget guard

Once the profiler and watcher work, add budget gates:

```bash
toolburn guard --since 24h
toolburn guard --policy .toolburn.yml
toolburn guard --fail-on new-background-guzzler
```

This prevents regressions:

```text
Fail: new recurring actor detected
Fail: tool output p95 exceeds budget
Fail: no-op path invoked model
Fail: actor missing ToolBurn metadata
Warn: unknown actor used >500k raw tokens
```

This matters because the scariest failures are new things you just turned on.

## Phase 3 exit criteria

Phase 3 is done when ToolBurn can:

```text
1. Produce compact agent drill packs.
2. Recommend specific optimization recipes.
3. Generate adapter scaffolds.
4. Verify before/after token reduction.
5. Enforce token-burn budgets in CI or local guard runs.
6. Help safely remove a tool from the model loop.
```

The key product moment:

> ToolBurn does not just say “you used 20M tokens.” It says “this exact command inside this recurring actor is your burn path; here is the adapter shape; here is the verification contract; here is the before/after proof.”

---

# Security posture across all phases

Given your supply-chain concern, this needs to be part of the brand from the start.

ToolBurn defaults:

```text
local-first
read-only unless explicitly asked
no network by default
no model calls in scanner/watcher
no prompt upload
no telemetry by default
plain JSONL/SQLite
small dependency footprint
reproducible release path
redaction before export
```

Important rule:

> **ToolBurn may generate data for agents, but ToolBurn itself should not require an agent to function.**

That is the whole point. The watcher cannot be another token-burning agent.

---

# What I would build first

The first concrete target should be:

## **ToolBurn Phase 1 MVP against the Voiceze incident**

Build only enough to produce this:

```bash
toolburn scan --openclaw ~/.openclaw --since 2026-06-02
toolburn top --by actor
toolburn top --by tool
toolburn explain background.openclaw.heartbeat.voiceze --for-agent
```

Expected finding:

```text
CRITICAL BURN PATH

Actor:
  background.openclaw.heartbeat.voiceze

Pattern:
  recurring no-op heartbeat
  ~30m cadence
  high cached-input ratio
  wait/poll loop detected

Primary tool:
  ./tools/heartbeat_scan.py --json &&
  ./tools/spam_cleanup.py --apply --json

Impact:
  30.8M raw tokens over incident window
  17.1M raw tokens in last 5h

Recommended fix:
  deterministic runner
  no-op fast path
  compact escalation payload
  model only on candidates/errors
```

That proves the product. Everything else can follow.

# The 3-phase summary

```text
Phase 1: Offline Profiler
  Parse Codex/OpenClaw logs.
  Build token du/top/tree/explain.
  Attribute burn to actors, tools, invocations, and burn paths.

Phase 2: Live Watcher
  Follow logs and runner metrics.
  Detect new recurring guzzlers, high-frequency tools, silent watchdogs.
  Emit CodePager-ready JSONL alerts.

Phase 3: Optimization Loop
  Produce agent drill packs.
  Recommend Headroom/adapters/cache/cap/deterministic rewrites.
  Verify before/after savings and enforce token budgets.
```

My brutally honest implementation warning: **Phase 1 must stay boring.** If it turns into generic tracing, dashboards, eBPF, cloud sync, or automatic LLM-based analysis, it will become the same kind of system you are trying to debug. The killer wedge is a dead-simple local CLI that tells you where the token disk space went.
