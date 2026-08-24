# Reaper "release failed" diagnosis — Findings

> **Created:** 2026-08-21
> **Verdict: H1 (stale deploy of an already-shipped fix). Clean H1 — no H2b, no mixed split.**
> Strictly read-only investigation; no code, worktree, branch, ref, row, or process was changed.

## Verdict

The ~47–60 startup/hourly `Worktree reaper: release failed … Managed worktree Git identity does
not match durable assignment; reconciliation must preserve it` WARNINGs are **H1**: the running
commander shells out to a **pre-`7388ad7`** workspace-manager whose `cleanupWorkspace` validates
managed identity *unconditionally* and therefore throws on leaked assignments whose worktree dir
was already removed. Loop 3 (`7388ad7`, committed 2026-08-19) already fixes this
(`cleanupWorkspace` gates the identity check on `if (present)`), but the commander is not running
it. **No code change is needed** — the remediation is a deploy + restart (below).

## Evidence

### Primary discriminator — worktrees are GONE for every warned guid

`git -C /Users/roberthyatt/Code/roleplaying-agents worktree list --porcelain` and
`ls -la …/roleplaying-agents/.ironclaude/worktrees` register ONLY the live rows
(`active`/`ready_for_integration`: `1b0ec852, 4b294796, 5cc16cb4, 7b03ab92, 7db5cf47, beec83a8,
cbff6ba7, cef0f622, ec215505, f369bb56`), each on its own `refs/heads/ironclaude/<guid>` managed
branch (7db5cf47 detached) — i.e. NOT foreign-branch. **None** of the warned guids appears in
either listing. Per the code's `present := existsSync(worktree_path) AND path ∈ git worktree
list` (workspace-service.ts:752-753), every warned guid is **GONE** (the lone `e09dbed4` stub dir
is a 96-byte empty leftover, not git-registered).

The workspace-manager DB (`~/.claude/ironclaude-workspaces.db`; `WORKSPACE_MANAGER_DB_PATH` unset
in the commander daemon env, so the default path is authoritative) shows every warned guid is
`lifecycle_status = abandoned`, and all but two carry a `recovery_ref` (their work was already
rescue-anchored when abandoned). The two without: `7f004391`, `6105820d` (`abandoned`, no ref).

### Only pre-`7388ad7` code emits `:179` on a gone worktree

On the reaper's owned release path (`_release_leaked_assignment`, main.py:372 → workspace-manager
`cleanup`/`abandon`), current (post-`7388ad7`) code cannot throw the `:179` identity string for a
GONE worktree: `cleanupWorkspace` skips the guard via `if (present)` (:754); `abandonWorkspace`/
`rescueAbandon` never call it. A gone terminal row instead either tombstones successfully (its
`recovery_ref` is reachable) or throws a DIFFERENT message ("… lacks reachable durable recovery
evidence"). Pre-`7388ad7` `cleanupWorkspace` validated identity unconditionally → `!observed`
(worktree gone) → `:179`. So `:179`-logged + worktree-gone ⟹ pre-`7388ad7` ran.

### Exact-message discipline

Every `release failed` line in `/tmp/ironclaude-daemon.log` (the daemon's real log; not
`/tmp/ic/daemon.log`) carries the exact `:179` string — no other exception mixed in. So all
warned guids qualify for the gone⟹H1 inference.

### Corroboration — the resolved bundle is pre-`7388ad7`, and today's deploy missed it

- Commander daemon = PID 31272 (`.venv/bin/python -u -m ironclaude.main`), started
  **2026-08-21 14:10:15**; still running. Warnings fired at 14:10:21-40 (startup sweep) and again
  at 15:10 (hourly maintenance sweep) — the reaper re-attempts the same leaked rows every sweep.
- The commander resolves its workspace-manager to the **claude cache `1.1.6`** root (its sibling
  MCP wrappers run from `~/.claude/plugins/cache/ironclaude/ironclaude/1.1.6/…`), per
  `_select_discovered_root` version-match (workspace_client.py:73-88). That bundle:
  `grep -c reapLeakedAssignment …/1.1.6/mcp-servers/workspace-manager/dist/cli.js` = **0**
  (marker absent ⟹ pre-`7388ad7`), mtime **2026-08-18 21:45:58**.
- The bundle mtime (Aug 18) predates `7388ad7` (Aug 19) AND today's local deploy — this session's
  deploy updated the repo dist, the codex cache, and hooks, but NOT the claude cache's
  `mcp-servers/` (make deploy-hooks copies only hooks; the claude plugin was never reinstalled).
  So the commander has run — and still runs — the Aug 18 pre-Loop-3 build. A commander restart
  ALONE would not fix it; the claude-cache bundle must be updated first.
- Monotonicity: the abandoned worktrees were removed at abandonment time (rescueAbandon), long
  before the 14:10 startup — their `abandoned`+`recovery_ref` state proves prior removal — so
  gone-now ⟹ gone-at-warning-time holds.

## Impact

Benign (no work lost; the recovery refs preserve every abandoned row's content) but noisy: ~50+
identical WARNINGs per hourly sweep, a workspace-manager DB backlog of terminal rows that never
tombstone, and real reaper failures buried in the spam.

## Recommended remediation (H1 — operational, no code change)

1. Update the **claude plugin cache** workspace-manager to post-`7388ad7`: either
   `claude plugin install`/reinstall so `~/.claude/plugins/cache/ironclaude/ironclaude/1.1.6/
   mcp-servers/workspace-manager/dist/` matches the repo build, or copy the repo's built
   `mcp-servers/workspace-manager/dist/{cli.js,index.js,…}` into that cache path. (Confirm the
   commander's resolved root's `plugin.json` base_version still matches after any version bump.)
2. Restart the commander daemon (PID 31272) so its next reaper sweep shells out to the updated
   cli.js.
3. The gone-tolerant `cleanupWorkspace` then tombstones the `abandoned`+`recovery_ref` rows
   (work stays anchored on `refs/ironclaude/recovery/<guid>`), clearing the ~50 warnings. The two
   `no-ref` rows (`7f004391`, `6105820d`) will be surfaced as durable-proof-unreachable — correct,
   quieter behavior, not the `:179` spam.

No follow-up code loop is warranted for the warned rows (H2b did not occur). A separate, optional
cleanup — whether the reaper's OWNED branch should also route worktree-absent rows through the
owner-free `reap` verb for defence-in-depth — remains a design question, not required to resolve
this issue.
