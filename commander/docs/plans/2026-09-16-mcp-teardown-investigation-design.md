# MCP Subprocess Teardown Verification — Investigation Design

> **Created:** 2026-09-16
> **Status:** Design Complete
> **Scope mode:** hold — investigation only, NO source changes. Deliverable is a
> findings note; any fix is a recommendation in the note, not implemented here.

## Summary

The operator hit macOS "out of application memory" (swap 50.7/52 GB on a 48 GB
box) with ~59 IronClaude MCP-server node processes alive. A live snapshot during
that incident found **0 orphaned (PPID=1) MCP servers** — every one traced to a
live root (tmux server, iTermServer, ChatGPT/Codex app, the daemon) — so there is
**no confirmed leak**. But the worker-teardown path was never verified to tear
down a reaped worker's MCP subprocess tree, and that is the exact failure mode
that would produce a slow accumulation. This loop verifies it empirically and by
code trace, and records the result (plus any hardening recommendations) in a
findings note. It changes no source.

## Prior art (already grounded, do not re-derive)

- **Wrapper FORKS, not execs.** `worker/mcp-servers/*/cli/mcp-server-wrapper.js`
  (all three: workspace-manager, state-manager, episodic-memory) calls
  `spawn(process.execPath, [bundle], { stdio: 'inherit' })` (workspace-manager
  wrapper line 36). So each MCP server = **2 node processes** (wrapper + forked
  child running `dist/index.js`), i.e. 3 servers × 2 = **6 node procs/session**.
  Verified from source this loop.
- **Wrapper signal handling:** forwards only `SIGTERM`/`SIGINT` to the child
  (lines 41-42); mirrors the child's exit / re-raises the child's terminating
  signal on itself (lines 47-50). **No stdin-EOF watch; no parent-death detection**
  (macOS has no `PR_SET_PDEATHSIG`). Sets `CLAUDE_PPID=process.ppid` in the
  child's env (line 37) — a hook the child *could* use for parent-death self-exit.
- **Worker-teardown kill sites in `commander/src/ironclaude/main.py`:** L4009
  `tmux.kill_session` (stuck-worker kill), L3629 (spawn-failure kill), L1170-1180
  `_kill_orphan_workers` (startup reap of unregistered `ic-*` tmux sessions);
  normal-completion + "session died (tmux gone)" paths around L4199-4280;
  `_finalize_and_release_worker` at main.py:1540 delegating to
  orchestrator_mcp.py:3522.
- **Daemon-restart teardown** (`_handle_restart`, main.py:210-259, per episodic
  memory 2026-04-05 directive #37): `_daemon.shutdown()` → `brain.shutdown()` →
  `_kill_orphan_brains()` → `pkill -P` → close DB/flock → `os.execvp`. Both
  `_kill_orphan_brains()` and the 2026-04-21 `_cleanup_zombie_mcp_processes()`
  (orchestrator_mcp.py, runs on `restart_mcp`) use **`pgrep -f` pattern-kill**,
  which conflicts with the later operator directive "never pattern-kill; scope to
  recorded PIDs." Record as an observation.
- **MCP servers are PPID-isolated children, one set per live session, by design**
  (validated 2026-02-17). Concurrent sessions each own a separate set. This is not
  a bug; it is the memory-pressure multiplier when many sessions run at once.

## Investigation questions (the findings note must answer each with evidence)

- **Q1 — tmux-kill propagation.** When a worker is reaped via `tmux.kill_session`,
  does the SIGHUP tmux delivers to the pane process group actually terminate the
  worker `claude`/`codex` process **and its MCP wrapper+child descendants**? The
  wrapper handles SIGTERM/SIGINT but not SIGHUP — node's default SIGHUP action is
  terminate, so both wrapper and child should die by default action; confirm the
  MCP procs are genuinely descendants of the pane PID (so they receive the pane's
  SIGHUP at all) rather than reparented elsewhere.
- **Q2 — self-exit on parent death (no signal).** On the paths where the worker
  process exits **without** the daemon sending a kill (normal completion; crash;
  "session died"), do the `dist/index.js` servers self-terminate on **stdin EOF**
  (their stdio is inherited from the pane pipe) or via `CLAUDE_PPID` watchdog — or
  do they reparent to launchd (PID 1) and linger? This is the actual leak switch.
- **Q3 — empirical survivor census.** Map every live `mcp-server-wrapper.js` and
  `dist/index.js` node process to its root (tmux pane / iTerm / Codex app / daemon
  / PID 1). Cross-check tmux `ic-*` sessions and the `workers` DB rows against the
  process set: **is there any MCP server whose owning tmux session or worker row is
  gone but the process survives?** That single fact would upgrade "no confirmed
  leak" to "confirmed leak."
- **Q4 — fork-doubling cost.** Confirm empirically the 2-procs-per-server / 6-per-
  session multiplier (count wrappers vs. children). Quantify what an exec-instead-
  of-fork wrapper would save. Recommendation only.

## Components (this is an investigation, "components" = the trace tasks)

Each task RUNS read-only in the execute stage (the stage exists solely to unblock
Bash per `ironclaude:workflow-durability`) and appends its evidence to the single
findings note `commander/docs/plans/2026-09-16-mcp-teardown-findings.md`.

1. **Code trace — worker-teardown paths (Q1).** Read the three kill sites +
   `_finalize_and_release_worker` (main.py:1540 and orchestrator_mcp.py:3522) +
   the normal-completion / session-died paths (main.py ~4199-4280). Document, per
   terminal path, whether it calls `tmux.kill_session` (→ SIGHUP the pane tree) or
   only marks the DB row / reaps the worktree, leaving the process tree alive.
2. **Code trace — wrapper + server self-exit (Q2).** Re-confirm the wrapper
   fork/signal facts, then read each `dist/index.js` for a stdin `end`/`close`
   handler or a `CLAUDE_PPID` liveness watchdog that exits the server when the
   parent dies without a signal. Record present/absent per server.
3. **Empirical process/root census (Q3, Q4).** `ps`-based full-tree snapshot of
   all node MCP procs; resolve each to its root; list tmux `ic-*` sessions and the
   `workers` rows; identify any survivor whose root/session/row is gone. Count
   wrapper vs child procs to confirm the 2× multiplier. **PID-scoped observation
   only — no kills, no `pkill`, no `pgrep -f` kill.**
4. **Findings note + recommendations.** Consolidate Q1-Q4 into a verdict
   (leak / no-leak / conditional-leak with the exact condition) and a prioritized,
   non-implemented recommendation list (e.g. exec-not-fork wrapper; stdin-EOF or
   `CLAUDE_PPID` watchdog self-exit; replace the two `pgrep -f` pattern-kill sites
   with PID-scoped teardown per the operator directive; a daemon maintenance census
   that surfaces row-less MCP survivors the way the v1.1.11 worktree reaper surfaces
   row-less worktrees).

## Data Flow / Error Handling

No runtime behavior changes. Evidence commands follow the execution invariants:
absolute paths, no `2>/dev/null` on evidence, list names not counts, each command's
empty result distinguishable from failure. Every process command is read-only
(`ps`, `pgrep -l`/`pgrep -a` for *listing only*, `tmux list-sessions`, `sqlite3`
`SELECT`). **No process is signalled or killed at any point** — this is the operator
"never pattern-kill" boundary and this loop must exemplify it.

## Testing Strategy

Investigation, not code — there is no RED/GREEN. "Verification" = each finding cites
the exact command output or file+line that establishes it (provenance rule). A
claim with no traced command or file read does not enter the note. The note states
its own verdict's falsifier: what single observation would flip no-leak → leak
(a surviving MCP proc whose root/session/row is gone).

## Implementation Notes

- Deliverable: `commander/docs/plans/2026-09-16-mcp-teardown-findings.md`
  (gitignored → `git add -f`). Design doc:
  `commander/docs/plans/2026-09-16-mcp-teardown-investigation-design.md`.
- NO source changes. The 13 staged boy-scout files (pending operator amend into
  v1.1.11) are untouched — this loop only reads source and writes the two docs.
- Out of scope: implementing any recommendation; touching the reaper, the wrapper,
  or the daemon. Those become their own operator-gated loops if the note warrants.
