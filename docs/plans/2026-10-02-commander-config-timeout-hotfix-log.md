# Commander config-timeout hotfix: Task 2 log (relaunch and first spawn, H4)

## Classification: PASS

A new roleplaying-agents assignment, `d1501-r1-rev3`, reached `active`. No timeout of any kind occurred in an allocate call. No row is stuck in `reserved`.

## Step 1: relaunch

- Pre-check: `/tmp/ic-daemon.pid` was absent and no Commander process was running.
- Relaunched at 2026-10-02 19:45:48 UTC (13:45:48 MDT) with the env-stripped, detached command from the plan.
- Daemon pid 52042 (ppid 52020, `.venv/bin/python -u -m ironclaude.main`). Brain pid 52052 (ppid 52042, bundled claude CLI). Both still up at 20:02:24 UTC (etime 16:34 and 16:33).
- `grep -n -e Traceback -e ERROR /tmp/ironclaude-restart-launch.log` found nothing (rc=1).

## Step 2: first spawn

- 19:45:55 to 19:59 UTC: the Brain ran a capability-recheck loop (directive #1501, non-mutating probes). Early probes reported `workspace_write` blocked by `brain-orchestrator-guard.sh`. No spawn was attempted in that period.
- 19:58:33 UTC: the Brain reported `workspace_write` recovered, saying its t2 had earlier been "blocked on worktree allocate timeouts".
- 19:59:46 UTC: the Brain began spawning the d1501 reviewer.
- DB row (workspace_guid `1cc54dfa-7348-4e13-b004-4416ab5418c0`, worker `d1501-r1-rev3`):
  - `created_at` 2026-10-02 20:01:33 (reserved), `updated_at` 20:02:02 (active).
  - Proxy duration: about 29 s from reserve to active. This is a proxy, not allocate alone: it also covers bind and reconcile. The daemon log has no allocate-start or allocate-end lines, so the log-timestamp method was unavailable.
  - Brain log: MSG_SEND at 20:02:15 UTC: "`d1501-r1-rev3` (claude-opus) spawned in `/Users/roberthyatt/Code/roleplaying-agents`".
- Timeouts: the only "timed out" lines in the daemon log since relaunch were grader OpenAI read timeouts at http://100.76.144.47:8080/v1 (13:50:13 and 13:58:06 MDT). They are unrelated to allocate. The first watcher exited early on one of these (false positive). A second watcher with an allocate/workspace-specific pattern ran until the row reached `active`. No `TimeoutExpired` or allocate "timed out" line appeared. The 60 s timeouts the Brain narrated (10:03 to 10:12 MDT) came from the pre-relaunch daemon.
- Stuck rows: `SELECT ... WHERE lifecycle_status='reserved'` returned 0 at 20:02 UTC. The only non-terminal assignment is the active row above.

## Manual unblock of d1501 (operator-authorized, reviewed by Fable: GO-WITH-CHANGES)

**What caused the block:** the "workspace_write blocked" lines above came from a block the Brain created during the original 60 s timeouts. It misdiagnosed those timeouts as a permission problem. Its recheck probe then tried the Brain's own Write tool, which the guard always denies, so the block could never clear on its own.

**What was done:**
1. **Backup:** the database was copied to `scratchpad/ironclaude-commander.pre-d1501-unblock.db`.
2. **Race gate:** two conditions were checked before acting.
   - No recheck was in flight: `last_observed_at` was 1790970968.59, later than the claim at 1790970964.30.
   - The next recheck was 410 s away.
3. **Recovery call:** the product method `OrchestratorTools.report_directive_capability_recovery(1501, ["workspace_write"])` was run unbound on a shim connection. The row changed from `blocked` (generation 4) to `recovered`/`pending`.

**Result:**
- At 19:57:36 UTC the daemon sent `[CAPABILITY RECOVERED]`.
- At 19:57:38 the directive was `confirmed` and recovery was `accepted`.
- The Brain then confirmed recovery at 19:58:33 and spawned.

**Rollback values recorded:** generation 4, `last_observed_at` 1790970968.59322. Rollback was not needed.

## Notes

- The 29 s proxy is under the old 60 s limit, so this run does not discriminate the new 900 s budget from the old value. It shows the post-relaunch daemon completed a local allocate without a timeout.
- Remote (SSH) allocates do not use this timeout: `workspace_client.py:217` calls `self._ssh_manager.run_argv(ssh_host, argv)` directly.
