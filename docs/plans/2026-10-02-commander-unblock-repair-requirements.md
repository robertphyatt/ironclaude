# Commander Unblock Repair — Requirements

> **Created:** 2026-10-02
> **Scope mode:** hold
> **Findings:** docs/plans/2026-10-02-worktree-allocate-findings.md

## Operator directives

- "I need you to untangle the mess it made so that it isn't block anymore"
- The operator chose "Repair + timeout hotfix (Rec.)":
  - **Repair loop now:** stop Commander; kill the 9 orphaned sync processes by exact PID; back up the DB; `reap` the 2 stranded rows and delete their 2 empty branches; time one real allocate; restart Commander.
  - **Separate hotfix, only if allocate takes more than 60s:** make the allocate timeout configurable and make the swap guard read the real config. Both are folded into v1.1.16 later.
- Standing rules:
  - Never pattern-kill processes. Kill by exact PID only.
  - Never push without an explicit go.
  - Operators never touch worktrees by hand.
  - Commander restart strips the Claude Code env vars and launches detached.

## Facts (verified in the investigation)

- **Stranded reservations.** Two `reserved` rows are stranded:
  - `f9781678-b968-4232-a7e7-8dc89931d12f` (`d1501-review-r1`)
  - `55a08d1e-9759-45f2-a22c-b079aba1ca71` (`d1501-r1-rev`)

  Both have `owner_session_id` NULL, and neither has a worktree on disk or in git.
- **Empty branches.** Branches `ironclaude/<guid>` exist for both rows, each at `3658cc9da56b0baeab9c1d00608a03bb692a4939`, which is the base commit and the current tip of the target.
- **Reaping path.** CLI `reap` → `reapLeakedAssignment` → `reapReservedAssignment` deletes a no-owner `reserved` row that has no worktree (`workspace-service.ts:891-944`). The CLI the orchestrator uses is `/Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager/dist/cli.js`, invoked as `node cli.js <command> '<json>'`.
- **Orphaned sync processes.** Nine `episodic-memory/dist/sync-cli.js` processes are orphaned (PPID 1). They were PIDs 4329, 4890, 7287, 8390, 10928, 13678, 14391, 18810 and 19539 at 10:43. The load average was 71.
- **Allocate timeout.** It is a hard 60s (`workspace_client.py:30`; `orchestrator_mcp.py:505` passes no timeout). Repo: 121,265 files, 12,091 tracked by LFS. Disk: 134 GiB free.

## Requirements

- **R1. Stop Commander** before any mutation, with `make -C /Users/roberthyatt/Code/ironclaude/commander stop`. Verify that its daemon and Brain PIDs are gone.
- **R2. Kill the orphaned `sync-cli.js` processes by exact PID.**
  - Re-list them immediately beforehand. Kill only PIDs that appear in that fresh listing, still have PPID 1, and run `episodic-memory/dist/sync-cli.js`.
  - Verify they are gone.
  - Record the load average and `vm.swapusage` before and after.
- **R3. Back up the workspace DB** with `sqlite3 ~/.claude/ironclaude-workspaces.db ".backup <scratchpad>/ironclaude-workspaces.<timestamp>.db"` before mutating it.
- **R4. Reap both stranded rows with the CLI's `reap` command.** Verify that each row is gone, that no other row changed (compare row counts and nonterminal rows before and after), and that nothing else under `roleplaying-agents` changed.
- **R5. Delete branches `ironclaude/f9781678-…` and `ironclaude/55a08d1e-…`.**
  - Use `git update-ref -d <ref> <expected-sha>` so each deletion is a compare-and-swap against the verified base commit. A ref that has moved is refused.
  - Verify both are gone. No other ref changes.
- **R6. Time one real allocate** for a fresh UUID, worker `repair-timing-probe`, against roleplaying-agents.
  - Run it with no timeout, and only after R2 has cleared the CPU hogs.
  - Record the wall time and the resulting `lifecycle_status`.
  - Then retire the probe with `reap`. If it materialized, this preserves its branch tip as a recovery ref and tombstones the row.
  - Remove the probe's worktree and branch only through the product path. Record what remains.
- **R7. Decide whether to relaunch.**
  - If R6 finished in under 60s with margin (≤45s), relaunch Commander detached with the Claude Code env vars unset, and verify the daemon and Brain are up.
  - Otherwise, leave Commander stopped and report that the timeout hotfix loop is required before relaunching.
- **R8. Record** the before/after evidence and the R7 decision in a short repair log, `docs/plans/2026-10-02-commander-unblock-repair-log.md`.

## Non-goals

- Code changes; the timeout hotfix is a separate loop.
- The Codex worktrees, any `finalization`, `recovery` or `review-receipt` refs, `tmp_pack` garbage, LFS changes, and the memory-leak fix.
