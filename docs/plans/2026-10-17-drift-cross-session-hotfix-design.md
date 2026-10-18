# Drift Check Cross-Session Hotfix Design

> **Created:** 2026-10-17
> **Status:** Design Complete

Loop: full. The change is in guard code: `professional-mode-guard.sh` and `execution-drift-check.sh`, used by both clients.

## Summary

The operator reported this on 2026-10-17 at 17:52, with a screenshot: "since work trees have been flaky I have multiple agents running against main and codex just said this". Codex session "inkarnate 2", in `/Users/roberthyatt/Code/roleplaying-agents` on `main`, was blocked: "The workflow remains blocked by another session's change to docs/plans/2026-10-17-swatch-leather-category-design.md ... Its owning session must commit or revert that change before the hook permits Inkarnate work to advance."

Operator directive: "We need to prioritize fixing this in a loop and deploying it since it's blocking efforts now". The v1.2.0 completeness plan, lineage 199, is parked until this hotfix ships.

**Root cause.** Confirmed in source.
- **The gate.** It is `worker/hooks/execution-drift-check.sh`, and its block text is at lines 371-379.
- **The attribution.** Attribution is time-window based (lines 20-28 and 277-300). A "window" is one shell command of this session. Closing it charges this session's ledger with every path that turned dirty during the command, with no record of who wrote it.
- **The failure.** A file another session creates or modifies while this session's command runs is charged to this session. Long test runs make this common.
- **Persistence.** `ic_drift_violations` (lines 339-356) keeps blocking until the file is committed or reverted.

Operator's choice of fix: "A: Writer evidence (Recommended)".

## Requirements

- **R1, record file-tool writers.** `professional-mode-guard.sh` runs PreToolUse for every file-tool write on both clients: Edit, Write, NotebookEdit and MultiEdit, plus Codex `apply_patch` (`hooks.json:68`). When it allows such a write, it appends one record (epoch seconds, session id, absolute target path) to a per-user writer log under `~/.claude/` with a test override. Codex `apply_patch` records every target path in the patch.
  - **Hook trust.** The recording runs inside the existing guard invocation, so `hooks.json` is unchanged. Whether Codex re-prompts for hook trust after a script edit depends on what its `trusted_hash` covers, which is unverified. Task 1 measures it and a post-deploy check settles it.
  - **Growth.** When the log passes a size cap, it is trimmed to its newest lines.
- **R2, exclude files written by other LIVE sessions.** This matches the operator's chosen option text: "Drift ignores paths another live session wrote or has in its own ledger". Before reporting, `ic_drift_violations` drops a ledger path when either of these holds:
  - **(a) A recent writer of another session.** The path's newest writer-log record belongs to a different session, and its epoch is at or after the earliest `created_at` of this session's in-progress rows being checked. Every path in such a row's ledger turned dirty after the row existed, so an older foreign write had already been committed or reverted. If no `created_at` is available, rule (a) excludes nothing (fail closed to today's behavior).
  - **(b) Another live session's same-root ledger.** The path appears in another session's same-root drift ledger `ironclaude-drift-touched-<other>.<row>.txt`, and that `wave_tasks` row belongs to a session whose `workflow_stage` is `executing` or `reviewing`. `professional_mode` is not required, because a session the operator switched to PM off still owns its files.

  Paths still dirty that this session's own shell commands produced, with no live evidence of another writer, still block, so real side quests are still caught. That includes paths a finished or stale session once wrote.
- **R3, accepted residuals.**
  - **Overlapping shell writes (fail open).** Another LIVE session's shell command may write a path during an overlapping window. Both sessions' ledgers then hold the path, and rule (b) excludes it for each of them.
  - **Lingering live sessions.** A session left in `executing` or `reviewing` keeps its ledgers live until its next `create_plan` or reset.
  - **Finished sessions (fail closed).** A finished session's shell write during an overlapping window still blocks, which is the pre-fix behavior.
- **R4, message.** The block text points the agent at the remedy that still applies. It no longer says "It clears by itself once that session commits or reverts it" for files another session wrote, since those no longer block.
- **R5, tests (TDD).**
  - Hook tests drive the real guard and drift check with a stub repo:
    - another session's Write during this session's window does not block;
    - another session's `apply_patch` does not block;
    - a live session's own ledger path does not block;
    - this session's own shell write outside allowed_files still blocks (positive control);
    - a finished or stale session's ledger (workflow_stage idle) and an old writer record do not exclude, and this session's write still blocks;
    - a foreign writer record for a same-basename file in another directory does not exclude;
    - the writer log trims at its cap;
    - Claude and Codex record the same way.
  - The full hooks suite stays green.
  - **Amendment (2026-10-17, operator: "Amend plan + fix check (Recommended)").** `test-execution-drift-roots.sh` case 5 hard-coded exactly one `LC_ALL=C comm -23` in the hook. The R2 foreign-path subtraction adds a second `comm -23`, also under `LC_ALL=C`. Case 5 now asserts its real intent: the hook has at least one `comm` call and no `comm` call without `LC_ALL=C`.
- **R6, deploy.** Commit by the operator, then `make deploy-hooks`. Hooks are read live, so no Commander or ChatGPT restart is needed for the fix, but follow the operator's standard redeploy. No push.

- **R2 refinement (from grounding).** Drift ledgers store repo-relative paths with no root, so rule (b) could match a same-named file in another repository. Each ledger therefore gains a `<ledger>.root` file beside it, and rule (b) uses only ledgers whose root matches this session's root. Ledgers written before the fix carry no root and are ignored.
- **R2 refinement (allow sites).** Every allow path in the guard exits through `workspace_allow` in `workspace-path-adapter.sh`. That includes PM off (:416), the docs/ path (:609), the executing check (:871), the "tool not handled" fall-through (:879) and several others. The writer record hooks into `workspace_allow` and filters by the original tool name.
- **R7, Codex apply_patch in a primary checkout (folded in by the operator: "Fold into the hotfix (Recommended)").**
  - **The bypass.** With no managed-worktree assignment, `workspace-path-adapter.sh` returns at :472, before the only place it renames `apply_patch` to `MultiEdit` (:529). The guard's write gates (lines 505, 550, 623, 752, 833) match only Edit, Write, MultiEdit, Bash and NotebookEdit, so a Codex `apply_patch` falls through to `allow` at :879, in any stage and outside allowed_files.
  - **The fix.** Map `apply_patch` to `MultiEdit` before the write gates whether or not an assignment exists, and check every patch target (not only the first) against the stage rules and allowed_files.
  - **Tests.** RED tests first prove the bypass:
    - a Codex `apply_patch` of a source file in a brainstorming stage, with no assignment, is allowed today;
    - in executing, a patch whose second target is outside allowed_files is allowed today.

    GREEN tests block both, and an all-docs patch in brainstorming stays allowed as a positive control.
  - **Check `exec_command`.** Verify whether `exec_command` has the same no-assignment gap, and include it if it does.

Operator approval: "Approve, write the plan (Recommended)". Execution mode: subagent-sequential.

## Non-goals

- Changing the window model or the stage gates.
- The v1.2.0 completeness parts (parked).
- Worktree fixes.
