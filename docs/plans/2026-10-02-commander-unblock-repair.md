# Commander Unblock Repair Plan

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.
> **Execution mode:** subagent-sequential (operator choice). One Sonnet subagent executes Task 1 with max_turns set. The main session sequences, verifies, and reviews.

**Goal:** Unblock Commander's worker spawning for roleplaying-agents with no code change.
- Retire the stranded reservations.
- Clear the CPU hogs.
- Measure allocate.
- Relaunch only if allocate fits under the 60s cap.

**Requirements:** `docs/plans/2026-10-02-commander-unblock-repair-requirements.md` (R1–R8)

**Design:** `docs/plans/2026-10-02-commander-unblock-repair-design.md`

**Architecture:**
1. Stop Commander.
2. Kill the orphaned sync PIDs.
3. Back up the DB.
4. `reap` the two rows through the product CLI.
5. Delete the two empty branches with a compare-and-swap `update-ref -d`.
6. Time one real allocate in the background, so it is never killed partway through, then retire the probe.
7. Either relaunch Commander, or hand off to the timeout hotfix.

**Tech Stack:** make, ps, kill, sqlite3, node (workspace-manager CLI), git.

## Execution invariants

- **Shell state does not persist between steps.** Use absolute paths and literal values only.
- **Kill by exact PID, never by pattern.** Only kill PIDs taken from the pre-kill snapshot.
- **No fallbacks.** Never fall back to raw SQL, never force a ref deletion, and never clean up by hand. Any unexpected result stops the loop for reporting.
- **Leave unrelated state alone.** Do not touch the Codex worktrees, or any `finalization`, `recovery` or `review-receipts` refs that existed before this repair.
- **No commit or push.** Plan artifacts are force-staged with `git add -f`.
- **Fixed names:**
  - CLI: `/Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager/dist/cli.js`
  - Repo: `/Users/roberthyatt/Code/roleplaying-agents`
  - SP (scratchpad): `/private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad`
  - Daemon pid file: `/tmp/ic-daemon.pid`
  - Brain pid file: `/tmp/ic/brain.pid`

---

## Task 1: Repair and relaunch decision (R1–R8)

No tests are required. This is an operational repair with no code changes. Each step's before and after evidence is the verification, and it goes into the repair log.

**Files:**
- Create: `docs/plans/2026-10-02-commander-unblock-repair-log.md`

**Step 1 (R1): Stop Commander.**

1. Before stopping, run `cat /tmp/ic-daemon.pid` and `cat /tmp/ic/brain.pid`, and record both PIDs. If `/tmp/ic/brain.pid` is already absent, record that and check only the daemon PID below.
2. Stop Commander, using a Bash timeout of 300000:

   ```bash
   make -C /Users/roberthyatt/Code/ironclaude/commander stop
   ```

3. Check the result. `ps -p <daemon>,<brain>` must list neither process (exit 1). The printed `Daemon (PID N) stopped` must match the recorded daemon PID.

Expected: both recorded PIDs are gone.

**Step 2 (R2): Kill the orphaned `sync-cli.js` processes.**

1. Record `sysctl vm.swapusage` and `uptime`.
2. Take the pre-kill snapshot with `ps -axo pid=,ppid=,command=`. The kill set is the PIDs whose PPID is 1 and whose command contains `episodic-memory/dist/sync-cli.js`. Record the set.
3. Run `kill <set>`. Re-check only those PIDs with `ps -p <set>`, and `kill -9` any survivors from the set.
4. Verify that every PID in the set is gone. A new matching PID that appears after the snapshot is recorded, not killed.
5. Record `sysctl vm.swapusage` and `uptime` again.

Expected: every PID in the set is gone, with the before and after numbers recorded.

**Step 3 (R3): Back up the DB.**

```bash
sqlite3 /Users/roberthyatt/.claude/ironclaude-workspaces.db ".backup /private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/ironclaude-workspaces.pre-repair.db"
```

Expected: exit 0, and `SELECT count(*) FROM assignments` on the backup equals the live count.

**Step 4 (R4): Reap both stranded rows.**

1. Write `SELECT workspace_guid,lifecycle_status,updated_at FROM assignments ORDER BY workspace_guid` from the live DB to `SP/assignments.before.txt`.
2. Reap the first row:

   ```bash
   node /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager/dist/cli.js reap '{"repository_path":"/Users/roberthyatt/Code/roleplaying-agents","workspace_guid":"f9781678-b968-4232-a7e7-8dc89931d12f"}'
   ```

3. Reap the second row:

   ```bash
   node /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager/dist/cli.js reap '{"repository_path":"/Users/roberthyatt/Code/roleplaying-agents","workspace_guid":"55a08d1e-9759-45f2-a22c-b079aba1ca71"}'
   ```

4. Write the same query to `SP/assignments.after.txt`, then run `diff SP/assignments.before.txt SP/assignments.after.txt`.

Expected:
- Both reaps succeed and return `lifecycle_status` `cleaned`.
- The diff shows only the two lines for those GUIDs removed, and nothing else changed.

**Step 5 (R5): Compare-and-swap delete the two empty branches.**

```bash
git -C /Users/roberthyatt/Code/roleplaying-agents update-ref -d refs/heads/ironclaude/f9781678-b968-4232-a7e7-8dc89931d12f 3658cc9da56b0baeab9c1d00608a03bb692a4939
```

```bash
git -C /Users/roberthyatt/Code/roleplaying-agents update-ref -d refs/heads/ironclaude/55a08d1e-9759-45f2-a22c-b079aba1ca71 3658cc9da56b0baeab9c1d00608a03bb692a4939
```

Expected: both exit 0, and `git -C /Users/roberthyatt/Code/roleplaying-agents for-each-ref refs/heads/ironclaude` lists neither ref. If a ref has moved, git refuses the delete; stop and report.

**Step 6 (R6): Time one real allocate in the background, then retire the probe.**

1. Generate a UUID with `uuidgen | tr A-Z a-z`, and use that literal value below.
2. Run the allocate with `run_in_background: true` and `timeout: 7200000`, writing its output to the scratchpad so the measurement survives:

   ```bash
   /usr/bin/time -p node /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager/dist/cli.js allocate '{"repository_path":"/Users/roberthyatt/Code/roleplaying-agents","workspace_guid":"<UUID>","worker_id":"repair-timing-probe"}' > /private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/allocate-probe.log 2>&1
   ```

3. While it runs, do not run `ps`, `sqlite3` or any other check against the probe. Wait for the completion notification.
4. Then `cat` the log and record:
   - the `real` line, which is the wall time in seconds;
   - the returned `lifecycle_status`;
   - `SELECT created_at, updated_at FROM assignments WHERE workspace_guid='<UUID>'`, as an independent duration cross-check;
   - `df -h /Users/roberthyatt/Code/roleplaying-agents`.
5. If the background call was stopped at its 7200000 ms limit:
   - record ">45s, killed at 2h";
   - do not relaunch;
   - do not reap.

   STOP and report, for operator review.
6. Otherwise, retire the probe:

   ```bash
   node /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager/dist/cli.js reap '{"repository_path":"/Users/roberthyatt/Code/roleplaying-agents","workspace_guid":"<UUID>"}'
   ```

   If `reap` throws, STOP and report. Do not clean up by hand.
7. Record what remains:
   - `git -C /Users/roberthyatt/Code/roleplaying-agents worktree list --porcelain`;
   - `git -C /Users/roberthyatt/Code/roleplaying-agents for-each-ref refs/heads/ironclaude refs/ironclaude/recovery/<UUID>`;
   - the probe's DB row.

Expected: the wall time is measured and the probe's row is terminal. Any leftover worktree, branch or recovery ref is recorded, not removed by hand.

**Step 7 (R7): Relaunch decision.**

If the Step 6 wall time is 45s or less:

1. Relaunch Commander:

   ```bash
   env -u CLAUDE_CODE_BRIDGE_SESSION_ID -u CLAUDE_CODE_CHILD_SESSION -u CLAUDE_CODE_ENTRYPOINT -u CLAUDE_CODE_EXECPATH -u CLAUDE_CODE_MESSAGING_SOCKET -u CLAUDE_CODE_MESSAGING_TOKEN -u CLAUDE_CODE_SESSION_ATTENDED -u CLAUDE_CODE_SESSION_ID -u CLAUDE_EFFORT -u CLAUDE_PID -u CLAUDECODE nohup python3 -c "import os; os.setsid(); os.execvp('make',['make','-C','/Users/roberthyatt/Code/ironclaude/commander','run'])" >/tmp/ironclaude-restart-launch.log 2>&1 & disown
   ```

2. Verify the relaunch:
   - `cat /tmp/ic-daemon.pid` and `cat /tmp/ic/brain.pid`;
   - `ps -o pid=,ppid=,sess=,command= -p <daemon>,<brain>` shows both present;
   - the `make` process shows PPID 1;
   - `/tmp/ironclaude-restart-launch.log` has no `Traceback` and no `ERROR`.

If the wall time is over 45s, do not relaunch. Commander stays stopped, and the timeout hotfix loop comes next.

Expected: the decision and its evidence are recorded.

**Step 8 (R8): Write the repair log and stage it.**

Write `docs/plans/2026-10-02-commander-unblock-repair-log.md` with the evidence from Steps 1–7, then stage:

```bash
git -C /Users/roberthyatt/Code/ironclaude add -f docs/plans/2026-10-02-commander-unblock-repair-requirements.md docs/plans/2026-10-02-commander-unblock-repair-design.md docs/plans/2026-10-02-commander-unblock-repair.md docs/plans/2026-10-02-commander-unblock-repair.plan.json docs/plans/2026-10-02-commander-unblock-repair-log.md
```

Expected: exit 0.
