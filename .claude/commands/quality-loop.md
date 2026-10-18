---
description: Run the bounded production-quality loop (audit -> plan -> parallel fixes -> verify -> re-audit, max 2 rounds)
---

Run the BorderlessGuard quality loop. You are the orchestrator; subagents never call each other.

1. Spawn `production-auditor` (round N, starting at 1). It writes docs/quality/audit_round_N.md and
   docs/quality/plan_round_N.json.
2. Read the plan. Drop tasks marked out of scope. Dispatch tasks by `owner` to the matching subagent
   (backend-engineer, ui-designer, docs-writer, security-auditor). Run at most TWO subagents at the same time, and
   only tasks listed as parallel-safe with each other (no shared files). Give each subagent only its tasks.
3. When a subagent reports, check its claims yourself (diff, tests). Resolve conflicts; you own integration.
4. Spawn `qa-runner`. If verify_all fails, send the failure back to the owning subagent once; if it still fails,
   revert that task and record it as unresolved.
5. Commit the round (never push without the user's go-ahead).
6. Spawn `production-auditor` for round N+1 to check acceptance criteria and regressions.
Stop when the plan verdict is "ship", or after round 2, or when a round fixes nothing new. Then write
docs/quality/closing_report.md: what was fixed, what remains and why, and the final verify_all table.

Arguments, if any, narrow the scope: $ARGUMENTS
