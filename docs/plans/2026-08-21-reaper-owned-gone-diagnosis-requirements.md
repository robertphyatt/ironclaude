# Reaper "release failed" diagnosis Requirements (operator-approved)

> **Created:** 2026-08-21
> **Source:** operator directive ("figure out why the commander posts all this garbage at
> startup" → "open a PM loop" → chose "Investigation-first loop"). Read-only diagnosis; no
> code fix until the cause is proven. Human commits, no push.

## Approved scope

Prove, against live state, whether the reaper's ~47 startup WARNINGs
(`release failed … Managed worktree Git identity does not match durable assignment`) are
**H1** (the commander runs a pre-`7388ad7` workspace-manager whose `cleanupWorkspace`
validated managed identity unconditionally and throws on already-removed worktrees — a stale
deploy of an already-shipped fix) or **H2b** (current code, worktrees present on a foreign
branch — the guard firing is correct preserve-not-destroy behavior). Produce a findings note
with the evidence and the recommended remediation. Write no product code.

- **R1 — identify the running workspace-manager build.** Determine the exact
  `mcp-servers/workspace-manager/dist/cli.js` the running commander invokes (via its
  `plugin_root`), and grep that bundle for the post-`7388ad7` marker `reapLeakedAssignment`.
  Present → the commander runs current code (rules out H1's stale-build mechanism); absent →
  pre-`7388ad7` (confirms it). Enumerate candidate bundles; do not assume the repo copy is the
  one that ran.

- **R2 — dump the leaked rows' state.** Read-only `sqlite3 SELECT` against the
  workspace-manager DB (`~/.claude/ironclaude-workspaces.db` or `$WORKSPACE_MANAGER_DB_PATH`,
  per main.py:238-242) for the leaked assignments:
  `workspace_guid, repository_identity, lifecycle_status, recovery_ref, worktree_path,
  owner_session_id`. Establishes terminal-vs-nonterminal and whether a recovery anchor exists.

- **R3 — gone vs present-on-foreign-branch.** For the row `worktree_path`s, test on-disk
  existence and `git worktree list` in the owning (game) repo. Gone → H1; present-on-foreign-
  branch → H2b.

- **R4 — findings note.** Write `docs/plans/2026-08-21-reaper-owned-gone-findings.md` stating
  H1 vs H2b with the command output as evidence and the recommended remediation
  (operational restart + reaper self-clear vs a scoped follow-up fix). `git add -f`.

- **R5 — strictly read-only.** Only `grep`, `sqlite3 … SELECT`, `ls`, `git worktree list`,
  `git log`/`git show`. NO DB mutation, NO `git worktree prune`, NO worktree/branch/ref/row
  change, NO commander restart, NO redeploy. Absence must be provable (list values, not
  counts; no `2>/dev/null` on evidence; distinguish empty from failed).

## Non-goals

- Any code fix, or routing the reaper's owned branch through `reap` (a follow-up loop's call,
  only if the findings show a real residual gap — not H1).
- Clearing the 47 stale rows, restarting the commander, redeploying, or pruning worktrees
  (operational actions the operator takes after the findings; not part of this read-only loop).
- Re-litigating the pre-Loop-3 "owned-but-gone intentionally not reaped" stance; that is a
  design question for the follow-up loop if one is warranted.
