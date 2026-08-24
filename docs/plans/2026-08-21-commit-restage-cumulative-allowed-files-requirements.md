# Commit-Time Re-Stage of Cumulative allowed_files Requirements (operator-approved)

> **Created:** 2026-08-21
> **Source:** operator directive (fix the stash-pop partial-commit bug) + brainstorm decision
> A (re-stage in executing-plans at execution_complete). Scope: selective, single slice.
> Human commits, no push.

## Approved scope

Guarantee the end-of-loop commit captures the full intended file set even if a subagent's
`git stash pop` (or any index churn) dropped earlier tasks' staged content out of the git
index during execution.

- **R1 — re-stage at execution_complete.** In `worker/skills/executing-plans/SKILL.md` Step 7
  (Plan complete), BEFORE the commit-message suggestion, add a concrete step that re-stages the
  UNION of every task's `allowed_files` (read from the plan JSON on disk) via
  `git -C <repo-root> add -- <paths>`. It runs in the orchestrator, after the subagents.

- **R2 — no over-stage, no clobber.** Only explicit allowed_files paths are staged (never
  `git add -A`/`.`), so an unstaged working change OUTSIDE allowed_files is never captured.

- **R3 — missing-path tolerant.** A planned allowed_file absent on disk must not abort the
  re-stage; stage only paths that exist.

- **R4 — covers every commit path.** Because it runs at `execution_complete` (before the
  operator's `git commit`, the `/commit` tool, and the managed lane), the fix needs no change
  to the workspace-manager commit tool and no cross-server plumbing.

- **R5 — governance test.** Add a test in `commander/tests/test_executing_plans_skill.py`
  asserting the skill's Step 7 contains the cumulative-allowed_files re-stage instruction in
  the execution_complete/Plan-complete section (the established skill-content test pattern).

- **R6 — no regression.** No state-manager/TypeScript change; no dist rebuild. Full regression
  green (state-manager vitest, workspace-manager vitest, hook suites, commander pytest run as
  explicit-file foreground batches with `-m "not destructive"`).

## Non-goals

- `reset_session` gating (dropped — YAGNI, [[project_reset_session_review_budget_laundering]]).
- Enforced tool-level re-stage (approach B) and per-task-boundary re-stage (approach C).
- Preventing subagents from stashing (IronClaude does not stash; the churn is external).
- The /commit skill's own re-stage (execution_complete already precedes it).
