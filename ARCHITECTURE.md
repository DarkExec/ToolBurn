# Architecture

## Purpose

Toolburn is a local-first CLI for answering:

> Where did token usage go, and what exact actors, tools, payloads, and
> recurrence patterns were observed near it?

DarkExec owns Toolburn as a project-neutral execution-cost profiler. Its first
boundary is intentionally narrow: read evidence that already exists on disk,
normalize it into a local SQLite store, and emit compact reports that a human,
agent, Harness pass, or evaluation system can act on.

## Shape

```text
AGENTS.md
ARCHITECTURE.md
README.md
docs/
  adapter-contract.md
  product-story.md
  quality.md
  runbook.md
  exec-plans/
    active/
    completed/
scripts/
  validate.sh
site/
  public/
src/toolburn/
  __init__.py
  cli.py
  pass_receipt.py
  report.py
  scan.py
  schema.py
  semantics.py
tests/
```

## Product Boundary

Phase 1 includes:

- local CLI entrypoints
- SQLite schema
- parser and reporter contracts
- offline validation
- compact agent-facing output shapes
- versioned, content-free pass and comparison receipts

Phase 1 excludes:

- model calls
- network calls
- dashboard services
- command interception
- eBPF or kernel-level tracing
- background watchers

The small `site/` wrapper publishes this repository's README at toolburn.com. It is a distribution surface, not part of the profiler execution path.

## Core Model

The factual data flow is:

```text
session evidence -> actor + invocation + token event -> factual episode -> optional semantic assignment
```

The schema starts with actors, sessions, tools, invocations, and token events. Invocation rows retain the exact observed tool bundle; token events grouped by their nearby invocation form stable factual episodes. This proximity is evidence for investigation, not proof that a tool caused later token use. Optional local semantics are versioned assignments over those episode IDs and are applied at report time, never written onto globally deduplicated tools or used to rewrite the factual ledger. Adapters and policy files arrive after the offline profiler can prove value against real traces.

Pass receipts use the exact Codex session identity rather than guessing the newest file. The shared parser owns completed-turn boundaries, usage deltas, tool-cost categories, repetition signatures, failure-evidence confidence, and privacy-safe output. A structured nonzero exit or explicit tool failure is confirmed; error-shaped text is only possible, and output-only orchestration is labeled incomplete because it discarded exit status. Receipts expose factual failure, repetition, and largest-output hotspots; `toolburn inspect` resolves one exact hotspot into bounded, redacted, local-private evidence without persisting it. Optional DarkExec episode evidence may identify the preceding Harness boundary; its absence never prevents a generic receipt. Toolburn reports measurements only. Harness Ops owns pass method and Harness Gym owns comparison judgment.

## Attribution Rule

Attribution is deterministic and confidence-bearing. If a label cannot be
proven from metadata, paths, command signatures, runtime ownership, or stable
recurrence, Toolburn must keep it in an explicit unknown bucket.
