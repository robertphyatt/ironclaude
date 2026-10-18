# Unblock the roleplaying-agents Worktree: Investigation Design

> **Created:** 2026-10-12
> **Status:** Design Complete

Loop: light. This loop runs read-only queries against git and SQLite and writes one findings doc. It changes no product code and mutates nothing.

## Context

The operator ran `/reconcile` in a roleplaying-agents Codex session and it could not land the worktree into `main`. The managed assignment is `019f7d26-9faa-7e71-b7cf-ee2a2334b9f4`: active, `integration_target=portrait-gen-structural-sclera-gate`, created 2026-10-10. The worktree also holds an uncommitted `AGENTS.md` change and untracked music research.

What the code shows:

- **Target capture.** The target is captured at activation and defaults to the primary checkout's branch (`workspace-service.ts:439`).
- **Reconcile path.** `reconcile_worktree` lands into `integrationTargetRef(assignment.integration_target)` (`integration.ts:213-215`). It has no target parameter and there is no retarget operation.
- **Dirty refusal.** Finalization refuses a dirty worktree (`integration.ts:976-978`).
- **Off-ref primary.** If the primary checkout is on a different branch, landing moves only the target ref (`integration.ts:619-672`).
- **Sync.** `sync_worktree_to_target` runs `rebase --onto <target> <base_commit>` and then updates `base_commit` (`integration.ts:507-567`). It needs a clean worktree when there are local commits (`:550`).
- **Merge-base check.** Finalization refuses when `base_commit` is not the merge-base with the target (`:897`).

Episodic memory has no record of any earlier retarget or assignment-row edit.

## Requirements

- **R1. Assignment row.** For that guid, record `worktree_path`, `branch`, `base_commit`, `current_head`, `lifecycle_status`, `integration_target` and `disposition`. Also record any `integration_locks`, `integration_records` or `primary_checkout_owners` rows that reference it. Read from `~/.claude/ironclaude-workspaces.db` with `sqlite3 -readonly`.
- **R2. Worktree state.** Record `git status --porcelain=v1 --untracked-files=all` and `git log --oneline <base_commit>..HEAD` in the worktree.
- **R3. Topology.** Record:
  - `main` and sclera-gate SHAs;
  - `rev-list --count` in both directions between them;
  - `merge-base main HEAD` compared against `base_commit`;
  - whether `base_commit` is an ancestor of `main`.
- **R4. Primary checkout.** Record the current branch and whether it is dirty.
- **R5. Other assignments.** List every other non-terminal assignment whose target is sclera-gate. A Commander worker `r3` is reported to have it.
- **R6. Findings doc.** Write `docs/reviews/2026-10-12-worktree-unblock-findings.md`. It ranks two no-code unblocks, gives the exact operator steps for each, and states which facts make each one viable or not:
  - **A.** In the Codex session, run `/commit`, then `/reconcile`, which lands into sclera-gate. The operator then cherry-picks the resulting commit onto `main` from the primary checkout.
  - **B.** Retarget the one assignment row to `main`, recording the old value, after an explicit per-action go. Then run `sync_worktree_to_target`, `/commit` and `/reconcile` from the Codex session.

  The doc also notes two things:
  - the reconcile and sync tools are gated to the provider-root session, so the recipe runs in the roleplaying-agents Codex session, not here;
  - the product fix is still the separately queued loop.
- **R7. Constraints.** No mutation of the DB, the worktree, refs or the primary checkout. Don't propose hand-copying files out of the worktree. Don't check out branches that still track the database. Send stderr to `.err` files. Keep evidence in `/private/tmp/worktree-unblock/`. Stage the doc with `git add -f`.
