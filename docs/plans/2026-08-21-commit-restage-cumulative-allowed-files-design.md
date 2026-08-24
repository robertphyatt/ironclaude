# Commit-Time Re-Stage of Cumulative allowed_files (stash-pop fix) Design

> **Created:** 2026-08-21
> **Status:** Design Complete
> **Scope mode:** selective (single slice — the stash-pop commit-integrity bug)

## Summary

HIGH commit-integrity bug, reproduced twice ([[project_stash_pop_unstages_prior_tasks]]).
During plan execution the workflow stages each task's output with per-task `git add`,
treating the live git index as durable accumulation across the wave/loop. But a subagent's
`git stash pop` (or any index churn) drops EARLIER tasks' staged content back out of the
index. At commit time the tree is captured by `git write-tree` on the current index
(workspace-manager `git-authority.ts:320/336/358`) or by the operator's `git commit` — either
way, whatever the index holds becomes the commit. So a churned index → a PARTIAL commit with
NO error. Grounded fact: IronClaude's own code never runs `git stash` in the execution/commit
path (only the read-only guard + a "never stash the operator's work" skill note mention it),
so the stash is a subagent's own action and cannot be prevented at the IronClaude source — the
fix must re-derive the staged set at the commit boundary from the authoritative source
(the plan's cumulative allowed_files).

Operator decision (2026-08-21 brainstorm): fix in the **executing-plans skill at
`execution_complete`** (approach A), re-staging the union of every task's `allowed_files`
before the commit-message suggestion. Rejected: B (enforce in the commit MCP tool — the
tool lacks the plan's allowed_files, needs cross-server plumbing, and only covers the
/commit-tool path, not a plain `git commit`); C (per-task-boundary + commit re-stage — more
surface than the observed bug needs). The separately-considered `reset_session` slice was
dropped as YAGNI ([[project_reset_session_review_budget_laundering]]).

## Architecture

At `execution_complete` (executing-plans SKILL.md Step 7 "Plan complete", BEFORE the
commit-message suggestion), the orchestrator re-stages the union of every task's
`allowed_files` read from the plan JSON on disk (the authoritative source the skill already
holds — no new tool, no read-tool change; `read-tools.ts` exposes no allowed_files):

```
git -C <repo-root> add -- <every allowed_files path, deduped>
```

Key properties:
- **Runs in the orchestrator, after the subagents.** The orchestrator reliably follows the
  skill; the churn is caused by subagents DURING execution. Re-staging at the end, in the
  orchestrator, corrects whatever the subagents dropped — so this is robust for the observed
  bug even though it is a skill step.
- **Covers every commit path.** `execution_complete` precedes the operator's plain
  `git commit`, the `/commit` tool, and the managed lane — the index is correct before any of
  them run.
- **No over-stage, no clobber.** Only explicit allowed_files paths are staged. A legit
  unstaged working change OUTSIDE allowed_files is untouched (never `git add -A`).
- **Missing-path tolerant.** A planned allowed_file that does not exist on disk must not
  hard-fail the re-stage; stage only paths that exist (e.g. filter with a test, or add
  per-path ignoring a missing one). At `execution_complete` all authored files exist; this is
  defensive.
- **Union across ALL tasks** (at `execution_complete` every task is `review_passed`, so "all"
  == "completed"). Tracked-but-gitignored paths (e.g. `dist/*.js`) re-stage fine.

## Components

- `worker/skills/executing-plans/SKILL.md` — add the concrete re-stage step to Step 7
  (Plan complete), before "Suggest a commit message".
- `commander/tests/test_executing_plans_skill.py` — a governance test asserting the skill
  contains the `execution_complete` cumulative-allowed_files re-stage (matching the existing
  skill-content test pattern in that file, e.g. `test_executing_plans_checks_persisted_lineage_before_reviewer_dispatch`).

No state-manager/TypeScript change; no dist rebuild.

## Data Flow

Execution runs; subagents stage per-task and may churn the index →
`get_next_tasks` returns complete → workflow `execution_complete` → Step 7 re-stages the
union of the plan's allowed_files from the plan JSON → the index now holds the full intended
set → the operator's `git commit` (or `/commit`) captures a COMPLETE commit.

## Error Handling

- A path in allowed_files missing on disk → skip it (do not abort the re-stage).
- A working change outside allowed_files → left unstaged (only explicit paths added).
- The plan JSON unreadable → the step reports and the operator inspects `git status` before
  committing (the re-stage is a safety net, not a gate).

## Testing Strategy

- Commander governance test asserts the executing-plans skill's Step 7 contains the
  cumulative-allowed_files re-stage instruction and that it sits in the `execution_complete`/
  Plan-complete section before the commit-message suggestion (text-presence). Because the
  re-stage command embedded in the skill is concrete shell with two placeholders, it is ALSO
  behaviorally tested: a second test extracts the fenced command, substitutes the placeholders,
  runs it in a temp git repo from a non-root cwd (the skill's Bash-cwd invariant), and asserts
  the union of allowed_files is staged, a missing path is tolerated, and a file outside
  allowed_files stays unstaged. The step's orchestrator conduct cannot be unit-tested, but the
  concrete command it embeds can — and that behavioral guard is what fails on a re-broken
  command (frame, quoting, or JSON key) that a text-presence test would keep GREEN.
- Full regression green (state-manager vitest, workspace-manager vitest, hook suites, commander
  pytest as explicit-file foreground batches with `-m "not destructive"`).

## Implementation Notes

- The re-stage command must be authored concretely (exact `git -C <root> add --` form + how the
  union of allowed_files is gathered from the plan JSON), not "re-stage the files".
- Human commits, no push. After commit, a LOCAL deploy (stable hook dir + local marketplaces)
  so a codex+claude restart runs the newest code — the skill change only takes effect once the
  plugin is deployed.
- Selective scope: no reset_session, no enforced-tool re-stage, no per-task re-stage.
