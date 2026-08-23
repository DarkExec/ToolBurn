# Architecture

## Purpose

Toolburn is a local-first CLI for answering:

> Where did token usage go, and what exact actors, tools, payloads, and
> recurrence patterns caused it?

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
  schema.py
tests/
```

## Product Boundary

Phase 1 includes:

- local CLI entrypoints
- SQLite schema
- parser and reporter contracts
- offline validation
- compact agent-facing output shapes

Phase 1 excludes:

- model calls
- network calls
- dashboard services
- command interception
- eBPF or kernel-level tracing
- background watchers

The small `site/` wrapper publishes this repository's README at toolburn.com. It is a distribution surface, not part of the profiler execution path.

## Core Model

The product abstraction is a burn path:

```text
actor -> tool/command -> payload/output -> model-visible context -> token event -> recurrence/rate
```

The schema starts with actors, sessions, tools, invocations, token events, and
burn paths. Adapters and policy files arrive after the offline profiler can
prove value against real traces.

## Attribution Rule

Attribution is deterministic and confidence-bearing. If a label cannot be
proven from metadata, paths, command signatures, runtime ownership, or stable
recurrence, Toolburn must keep it in an explicit unknown bucket.
