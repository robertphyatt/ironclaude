# Worktree Allocate Failure: Findings

> **Date:** 2026-10-02. Read-only investigation. All evidence below was gathered this session from a scratchpad snapshot of the workspace DB, from git, from source, and from `ps`/`sysctl`.

## Summary

Three separate defects combined to produce this incident. None of them is the orphan-worktree problem that v1.1.11 and v1.1.12 fixed.

1. **Episodic-memory sync processes pile up**, each one using CPU and memory. This is a *hypothesis* for the swap growth that led to the reboot, and the lead for the memory-leak investigation. It is not proven. Nine syncs at about 400 MB each come to roughly 3.6 GB, far short of 36.5 GB. The pre-reboot sync count was never observed, and Google Drive alone is at 6.3 GB RSS now.
2. **The spawn guard ignores the local config.** Our Sep 17 tuning (`max_swap_used_gb: 50`, `spawn_block_pressure_level: 4`) never applied, so the guard blocked at 8 GB of swap.
3. **When allocate times out, its reservation is stranded permanently.** Checking out the 121k-file, LFS-heavy repo takes longer than the 60s subprocess timeout. The timeout kills the CLI, the `reserved` row is left behind, and nothing ever reclaims it.

No work was lost. Worktree `13cf1c2c` was closed out on Oct 1 between 06:10 and 07:13, and its row was marked `cleaned` on Oct 2 at 07:28, both before the reboot. Its work is on the target branch.

## Timeline (local time, -0600)

| When | Event | Evidence |
|---|---|---|
| Oct 1 06:10–07:13 | 13cf1c2c close-out. Residual recovery ref at 06:10:56, frozen ref at 06:46:04, recovery ref at 07:13:13. | `ls -laT .git/refs/ironclaude/{recovery,finalization}` |
| Oct 2 07:28 | 13cf1c2c row becomes `cleaned`, with `recovery_ref` set. | DB `updated_at 2026-10-02 13:28:40` UTC |
| Oct 2 09:18–09:48 | Brain spawn rejected: "swap 36.5GB used (max 8.0GB)". | `brain-sessions/20261002-151820-718073.log` |
| Oct 2 ~09:55 | Operator reboots. Commander restarts at 09:57. | `/tmp/ic/daemon.log:1` |
| 10:02:02 | Allocate reserves `f9781678…` (worker `d1501-review-r1`) and times out at 60s. | DB `created_at 16:02:02` UTC; daemon log 10:03 |
| 10:06:15 | Retry under a new worker id `d1501-r1-rev` reserves `55a08d1e…` and times out again. | DB `created_at 16:06:15` UTC; daemon log 10:07 |
| 10:12 | The Brain gives up and asks the operator for help (operator_wait). | daemon log :70 |

## Current state (snapshot of `~/.claude/ironclaude-workspaces.db`)

- Nonterminal roleplaying-agents rows: exactly two, both `reserved`, both with `owner_session_id` NULL, `integration_target` `portrait-gen-structural-sclera-gate`, and base `3658cc9da`.
  - `f9781678-b968-4232-a7e7-8dc89931d12f`, worker `d1501-review-r1`.
  - `55a08d1e-9759-45f2-a22c-b079aba1ca71`, worker `d1501-r1-rev`.
- `primary_checkout_owners` and `integration_locks` are both empty.
- Git has branches `ironclaude/f9781678…` and `ironclaude/55a08d1e…`, both at `3658cc9da`, the base commit, with no commits of their own. Neither has a registered worktree, and `.ironclaude/worktrees/` is empty.
- Git worktree list contains only the primary checkout and 3 healthy Codex worktrees (`~/.codex/worktrees/{8735,8859,a535}`). Those belong to Codex and are out of scope.

## Root cause 1: allocate times out and strands its reservation

- **Timeout.** `commander/src/ironclaude/workspace_client.py:30` sets `timeout: int = 60` and passes it to `subprocess.run` at `:201-208`. On timeout, Python kills the `node cli.js allocate` child.
- **Allocate order** (`worker/mcp-servers/workspace-manager/src/workspace-service.ts`):
  1. `reserveWorkerWorktree` (`:404-449`) commits a `reserved` row inside an IMMEDIATE transaction.
  2. `materializeReservedAssignment` (`:348-375`) runs `git worktree add -b`, then links shared resources, then transitions the row to `materialized` and then `active`.
  3. On error the row is deliberately kept (`:370-373`).
- **Why it is slow.** The repo is 100 GB on disk and has to check out 121,265 tracked files. 12,091 of them go through the LFS filter, because `.gitattributes` routes every `*.txt` through LFS.

  ```
  git count-objects -vH:
    count: 21247   size: 16.85 GiB
    in-pack: 202880   packs: 486   size-pack: 83.33 GiB
    garbage: 55   size-garbage: 5.01 GiB    (tmp_pack_* from killed operations, Sep 26-28)
  tracked files: 121265   lfs-tracked: 12091 (11414 *.txt)   .git/lfs: 6.8G
  hooks: post-checkout (git lfs post-checkout), post-commit, post-merge, pre-push
  ```

  The machine is also saturated: load average 71 at 48 minutes uptime (Root cause 3). A cold-cache checkout after a reboot cannot finish in 60s.
- **Why the row is never reclaimed.**
  - A retry with the same GUID waits 10s (`:465-484`), then throws "still materializing".
  - A new GUID with the same worker throws "Worker is already reserved to a different managed workspace" (`:421-427`).
  - The Brain picks a new worker id, which hits the same slow checkout and strands another row.
  - Nothing checks whether the process that wrote a `reserved` row is still alive.
- **Leftovers.** `git worktree add` created the branch before it died. Git cleaned up the half-created worktree directory and registration, but not the branch. That leaves a branch with no worktree, plus a stranded `reserved` row.

## Root cause 2: the spawn guard ignores the local config

- `orchestrator_mcp.py:2835` reads `self._config.get("max_swap_used_gb", 8.0)`. The config comes from `load_config()` at `:8110`, and `config.py:142` resolves it as the **relative** path `config/ironclaude.json`.
- The orchestrator MCP process (pid 1616) runs with cwd **`/Users/roberthyatt/.ironclaude/brain`** (`lsof -d cwd`). That directory has no `config/`. `load_config` logs a warning and uses DEFAULTS: `max_swap_used_gb` 8.0 and `spawn_block_pressure_level` 2.
- `commander/config/ironclaude.json` does contain `"max_swap_used_gb": 50` and `"spawn_block_pressure_level": 4`. But only the daemon, whose cwd is `commander/`, reads it. The guard in the MCP never has. The Sep 17 tuning was therefore never in effect where it mattered.
- Swap-used is a weak signal on macOS. The guard already has a pressure check (`:2844-2857`, `kern.memorystatus_vm_pressure_level`), and that is the real signal. Current reading: pressure 1 (normal), swap 0.

## Root cause 3: episodic-memory sync processes pile up (swap-source hypothesis, unproven)

`ps` at 48 minutes uptime shows **9 concurrent `episodic-memory/dist/sync-cli.js` processes**:
- all orphaned (PPID 1);
- 5 from the repo and 4 from the Codex plugin cache;
- each using 330–470 MB RSS and 70–250% CPU;
- running for 3–46 minutes.

Each session start launches one ("Sync started in background…"), with no single-instance lock and no limit on how long it runs. Over days of sessions they accumulate. They explain most of the load average of 71 now. Whether they account for the 36.5 GB of swap is **unmeasured**: 9 × ~400 MB ≈ 3.6 GB, and other large consumers exist (Google Drive is at 6.3 GB RSS). This is the lead for the separate memory-leak investigation the operator asked for, not a proven cause.

## 13cf1c2c: nothing was lost

- The DB row is `cleaned`, worker `gm-core-regen-1500-r3`, owner session `df69c3a4…`, with `recovery_ref refs/ironclaude/recovery/13cf1c2c-…`.
- `refs/ironclaude/recovery/13cf1c2c-…` is 0 commits ahead of the target.
- The suffixed recovery ref and `finalization/…/frozen` each hold 6 commits.
  - `git cherry` marks 5 of the 6 `-`, meaning patch-equivalent commits are already on the target.
  - The one `+` commit in each (`5a8004ba`, `5533a648`) adds 6 files. Five are byte-identical on the target. `.claude/rules/behavioral.md` differs only because the target has a newer edit (`e60602efa`).
- Conclusion: the work landed on `portrait-gen-structural-sclera-gate` as `3658cc9da`, and the worktree was retired correctly before the reboot.

## Answering "WTF, I thought we fixed this?!"

- **What v1.1.11/v1.1.12 fixed:** reaping of orphaned worktrees and branches with no DB row. That fix is working: there are no row-less orphans here.
- **What actually happened** is a different path:
  - an allocate timeout leaves a *row* stranded in `reserved`, a state the reaper does not handle;
  - a spawn guard that never read our tuning;
  - and the sync pile-up.
- **The swap tuning** "fix" from Sep 17 was real in the config file but was never wired into the process that enforces the guard. It was never tested end to end.

## Repair plan (item 2)

The order matters.

1. **Stop Commander:** `make -C /Users/roberthyatt/Code/ironclaude/commander stop`. This stops the Brain from creating more reservations during the repair.
2. **Free the CPU and memory.** Kill the orphaned `sync-cli.js` processes by exact PID: 4329 4890 7287 8390 10928 13678 14391 18810 19539, re-listed immediately before the kill. Confirm the load average drops.
3. **Back up the DB** with `sqlite3 ~/.claude/ironclaude-workspaces.db ".backup <timestamped path>"`, not `cp`. Stopping Commander does not quiesce this DB: the Codex app and the Claude sessions each run workspace-manager servers that keep it open.
4. **Retire the two stranded reservations through the product path, with no raw SQL.** Run `node <plugin>/mcp-servers/workspace-manager/dist/cli.js reap '{"repository_path":"/Users/roberthyatt/Code/roleplaying-agents","workspace_guid":"<guid>"}'` for `f9781678…` and `55a08d1e…`.
   - `reapLeakedAssignment` (`workspace-service.ts:891`) does not require an owner session. For a `reserved` row with no worktree on disk or in git, it calls `reapReservedAssignment` (`:935`), which deletes the row.
   - Then delete the branches `ironclaude/f9781678…` and `ironclaude/55a08d1e…`. Each sits exactly at base `3658cc9da`, holds no work, and has no worktree. `reap` does not delete branches for reserved rows.
5. **Prove allocate completes before the Brain can retry.**
   - Run `df -h` first: 134 GiB is free now, and each managed worktree is a full checkout plus smudged LFS files.
   - With the CPU hogs gone, time one real allocate with no timeout for a test GUID. Record how long it takes, then reap it.
   - **If it takes longer than 60s, retiring rows alone cannot unblock the Commander.** The 60s cap is hard-coded: `orchestrator_mcp.py:505` constructs `WorkspaceClient(workspace_plugin_root, ssh_manager=...)` with no timeout argument, so `workspace_client.py:30` defaults to 60. Config cannot override it. A code change is needed, and the operator decides: a minimal timeout hotfix now in its own short PM loop, or fold it into v1.1.16.
6. **Optional interim fix:** make the orchestrator MCP load `commander/config/ironclaude.json`. Otherwise the 8 GB swap guard will block again as swap climbs.
7. **Relaunch Commander** detached, with the Claude Code environment variables unset, only after step 5 passes.
8. Do not touch the Codex worktrees or any `finalization` or `recovery` refs.

## Candidate v1.1.16 scope (item 3)

- **A. Spawn guard reads the real config.**
  - `load_config` should resolve its path relative to the package or `commander/`, or the daemon should pass the path to the orchestrator MCP explicitly through an env var or argument. Never resolve it from cwd.
  - Fail loudly when the config file is missing, instead of warning and falling back to defaults.
  - Swap guard (`orchestrator_mcp.py:2834-2857`): drop the swap-used threshold, or make it advisory, in favour of the pressure-level check.
  - Open question: is there a way to release swap without a reboot? Measure `sysctl vm.swapusage` before and after killing the top RSS consumers. The hypothesis is that swap frees as the owning processes exit; `purge` does not release it.
- **B. Allocate cannot strand a reservation, and can complete on large repos.**
  - The caller's timeout must not kill a half-done materialization. Run allocate asynchronously and poll, or give it a much longer, size-aware budget.
  - A `reserved` row whose writer process is dead should be reclaimed automatically: record the writer's pid and boot id, then reap or resume the row. Remove the branch it created only if that branch is still at its base commit.
  - Retrying with the same worker id should reattach to its reservation instead of colliding.
  - Find out why the daemon's maintenance reaper did not already reap these ownerless `reserved` rows. The product path exists (`reapLeakedAssignment` → `reapReservedAssignment`), but nothing called it for them.
  - Make the allocate timeout configurable. `orchestrator_mcp.py:505` passes none.
- **E. Make roleplaying-agents leaner** (operator request; a separate PM loop):
  - move `*.txt` out of LFS;
  - consider splitting the game runtime code from the dev tooling and experiments into separate repos;
  - prune the 5 GB of `tmp_pack_*` garbage.
- **C. Prevent the sync pile-up.** Episodic-memory sync runs as a single instance (lockfile plus PID check), with a time limit, and exits when done.
- **D. Hygiene, lower priority.**
  - Hundreds of stale `refs/ironclaude/finalization/*/frozen` and `review-receipts/*` refs, going back to August.
  - 5 GB of `tmp_pack_*` garbage left by killed git operations.
  - A pointer to the memory-leak investigation (C is the leading candidate). The pre-reboot `ps` also showed duplicate MCP server sets under one ChatGPT app-server.
