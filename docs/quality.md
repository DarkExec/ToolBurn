# Quality

## Required Gate

```bash
./scripts/validate.sh
```

## Harness Expectations

- One command validates the repo.
- Docs describe only capabilities that exist or are clearly marked planned.
- Tests cover parser contracts before real incident traces are used.
- Raw private evidence is excluded from git.
- Reports are compact enough to hand to an agent without flooding context.
- Tool reports must distinguish nearby transcript context from proven tool-caused model calls.
- Recent reports must link operation context to the actor that incurred the spend and retain exact tool reports for drilldown.
- Repeated recent scans must reuse unchanged evidence without hiding changes to active session files.
- Token events without a nearby invocation must name the actor as `no-tool-context:<actor>`, not collapse into `unknown.tool`.
- Pass selection must use `$CODEX_THREAD_ID`, an exact session UUID, an exact JSONL path, or an exact turn; it must never guess the newest session.
- Pass receipts omit prompts, messages, raw arguments, and raw output while retaining enough operation structure to locate repeated cost.
- Hotspot inspection is bounded, redacted, local-private, explicitly unsafe to publish, and never persisted by Toolburn.
- Pass receipts distinguish confirmed failures, possible error-shaped output, and incomplete exit-status evidence; they never turn an output-text heuristic into a confirmed failure.
- Comparisons emit factual deltas and never score quality or claim causality.

## Phase 1 Acceptance

Toolburn is not Phase 1 complete until it can:

- parse selected OpenClaw/Codex local evidence sources
- attribute token usage by actor, tool-context, session, and time window
- preserve unknown attribution explicitly
- detect recurring and wait/poll-shaped burn paths
- export compact agent-readable drilldowns
- run offline without model calls or network access
