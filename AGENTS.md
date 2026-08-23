# Toolburn

AGENTS.md is the map for this repo, not the manual. Keep durable execution
truth in docs, tests, scripts, and source files that agents can inspect.

## Start Here
- Architecture: [ARCHITECTURE.md](/srv/dark/repos/toolburn/ARCHITECTURE.md)
- Product story: [docs/product-story.md](/srv/dark/repos/toolburn/docs/product-story.md)
- Runbook: [docs/runbook.md](/srv/dark/repos/toolburn/docs/runbook.md)
- Quality gates: [docs/quality.md](/srv/dark/repos/toolburn/docs/quality.md)
- Adapter contract: [docs/adapter-contract.md](/srv/dark/repos/toolburn/docs/adapter-contract.md)
- Active plans: [docs/exec-plans/active](/srv/dark/repos/toolburn/docs/exec-plans/active)
- Completed plans: [docs/exec-plans/completed](/srv/dark/repos/toolburn/docs/exec-plans/completed)
- Harness doctrine: [/srv/harness-ops.md](/srv/harness-ops.md)

## Working Rules
- Humans steer. Agents execute.
- Phase 1 is read-only, model-free, network-free, and local-first.
- Never pretend unknown attribution is known.
- Do not add dashboards, wrappers, eBPF, cloud services, or network calls before
  the offline profiler earns them.
- Prefer deterministic parsers, compact reports, and mechanical validation over
  broad exploratory log dumps.
- Raw private incidents, session logs, token traces, and secret-shaped data stay
  out of git.

## Validate
- `./scripts/validate.sh`
