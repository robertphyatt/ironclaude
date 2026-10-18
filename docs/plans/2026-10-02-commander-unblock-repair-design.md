# Commander Unblock Repair — Design

> **Created:** 2026-10-02
> **Status:** Design Complete
> **Scope mode:** hold
> **Requirements:** docs/plans/2026-10-02-commander-unblock-repair-requirements.md

## Summary

This repair unblocks Commander's worker spawning for `roleplaying-agents` without changing any code. It runs in this order:

1. Stop Commander, so the Brain cannot create new reservations mid-repair.
2. Clear the CPU hogs.
3. Back up the workspace DB.
4. Retire the two stranded reservations through the product's own `reap` path.
5. Delete their two empty branches with compare-and-swap ref deletes.
6. Measure a real allocate.
7. Relaunch only if allocate fits under the 60s cap. Otherwise defer the relaunch to the timeout hotfix loop.

## Approach

This is the only approach. The operator chose it, and the findings ruled out alternatives:
- Raw SQL is unnecessary because `reap` handles ownerless `reserved` rows.
- Relaunching without measuring allocate would just strand another reservation.

## Components

1. **Stop Commander:** `make -C /Users/roberthyatt/Code/ironclaude/commander stop`, then verify the PIDs are gone.
2. **Process cleanup:**
   - Run a fresh `ps` and filter it to PPID 1 processes running `episodic-memory/dist/sync-cli.js`.
   - `kill` those exact PIDs. Use SIGTERM first, then SIGKILL for any survivors, by PID only.
   - Record the load average and swap before and after.
3. **DB backup:** `sqlite3 .backup` into the scratchpad.
4. **Reap:** `node /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager/dist/cli.js reap '{"repository_path":"/Users/roberthyatt/Code/roleplaying-agents","workspace_guid":"<guid>"}'`, run once for each guid. Compare row counts before and after.
5. **Branch deletes:** `git -C <repo> update-ref -d refs/heads/ironclaude/<guid> 3658cc9da56b0baeab9c1d00608a03bb692a4939` for each guid.
6. **Allocate probe:**
   - Run `time node cli.js allocate '{"repository_path":...,"workspace_guid":"<fresh uuid>","worker_id":"repair-timing-probe"}'`.
   - Then `reap` the probe, and record any leftover branch or worktree.
7. **Relaunch gate:** if the probe took 45s or less, relaunch using the standard detached, env-stripped command. Otherwise leave Commander stopped and hand off to the hotfix loop.
8. **Repair log:** `docs/plans/2026-10-02-commander-unblock-repair-log.md`.

## Error Handling

Any unexpected result stops the loop for reporting. Examples: the CLI errors, a row count changes beyond the targets, or a ref has moved. No fallback to raw SQL or forced ref deletes.

## Testing Strategy

This is operational repair with no code changes, so there are no unit tests. Verification is the before/after evidence for each step, recorded in the repair log.
