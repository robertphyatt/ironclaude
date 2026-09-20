# MCP Subprocess Teardown — Investigation Findings

> **Created:** 2026-09-16
> **Loop:** investigation-only PM loop (no source changes). Deliverable = this note.
> **Question:** Does the Commander daemon's worker-reap path tear down a reaped
> worker's MCP subprocess tree, or can MCP servers leak/accumulate?
> **Context:** operator hit macOS "out of application memory" (swap 50.7/52 GB on a
> 48 GB box) with ~59 IronClaude MCP-server node processes alive despite all LLM
> inference being offloaded to a remote box. A live snapshot then found 0 orphaned
> (PPID=1) MCP servers — no confirmed leak — but the teardown path was unverified.

All line numbers below were read from current source this loop.

## Q1 — worker-teardown propagation

**What `tmux.kill_session` does.** `TmuxManager.kill_session` (`tmux_manager.py:352-361`)
runs `tmux kill-session -t <name>` (`:355`). Destroying the session hangs up the
pane's pty, delivering **SIGHUP to the pane's foreground process group**. Node's
default SIGHUP action is *terminate*; the wrapper (Q2) installs handlers for
SIGTERM/SIGINT only, not SIGHUP, so on SIGHUP both wrapper and child die by default
action — **but only if the MCP procs are in the pane's process group** (verified
empirically in Q3 via `pgid`/`tty`; "descendant of the pane pid" is not the same as
"in the pane's foreground process group").

**Per terminal path — does it kill the worker's process tree?**

| Path | Site | Kills the tree? |
|---|---|---|
| Stuck-worker kill | `main.py:4009` (`_confirm_and_kill_stuck_worker`) | **YES** — `tmux.kill_session` → SIGHUP the pane group |
| Comm-profile activation failure (during spawn) | `main.py:3629` | **YES** — `tmux.kill_session` |
| Startup orphan reap of unregistered `ic-*` sessions | `main.py:1180` (`_kill_orphan_workers`) | **YES** — `tmux.kill_session`, but only at daemon startup, only for sessions not in the registry |
| Idle done-marker completion (normal "worker finished a unit of work") | `main.py:4211-4241` → `_finalize_and_release_worker(terminal=False)` (`:4219`) | **NO** — session deliberately left alive ("A live idle worker is never completed by the daemon", comment `:4222-4225`). The worker `claude`/`codex` process keeps running. |
| Session-died / crash / OOM | `main.py:4243-4289` | **NO ACTIVE KILL** — this branch fires only when `not self.tmux.has_session(...)` (`:4244`), i.e. the session is **already gone**. The daemon reconciles the DB row + integrates work; it signals nothing. |

**`_finalize_and_release_worker` does no process teardown.** The shared integration
seam (`orchestrator_mcp.py:3522-3556`) integrates/rescues the managed worktree and
marks the DB row completed. It **never kills the process tree**, and its
"has_session defence-in-depth" (`:3534-3536`) *downgrades* a terminal request to
non-terminal behaviour when the session is still alive — so it will not even remove
a worktree against a live worker, let alone signal its processes.

**Q1 conclusion.** The daemon has **no dedicated MCP-subprocess-tree teardown**. A
worker's MCP tree is torn down only as a side effect of one of two things:
1. **`tmux kill-session`'s SIGHUP** — but that runs on ONLY the stuck-kill,
   spawn-time comm-profile-failure, and startup-orphan-reap paths. It does NOT run
   on the daemon's normal completion (idle done-marker) path, nor on the
   session-died path (where the session is already gone).
2. **The worker process exiting on its own**, after which its MCP children's
   survival is entirely a Q2 question (self-exit on stdin EOF / `CLAUDE_PPID`) — the
   daemon never signals those children.

So the leak surface is precisely: any path where the worker `claude`/`codex` process
goes away **without** `tmux kill-session` delivering SIGHUP to the MCP procs' process
group — the idle path (worker later exits by itself), a crash, an OOM-kill, or a
SIGKILL. Whether that surface actually leaks is decided by Q2 (do the servers
self-exit?) and Q3 (are any alive right now with a dead owner?).

**Observation (record, do not fix here):** `_kill_orphan_brains` (`main.py:1186-1190`)
uses `pgrep -f "claude.*stream-json.*Orchestrator"` — a pattern-kill, which conflicts
with the operator "never pattern-kill; scope to recorded PIDs" directive. Carried to
Q4/recommendations.

## Q2 — self-exit on parent death

**The wrapper forks; teardown is signal-driven.** Each of the three wrappers
(`worker/mcp-servers/{workspace-manager,state-manager,episodic-memory}/cli/mcp-server-wrapper.js`)
`spawn(process.execPath, [bundle], { stdio: 'inherit' })` — verified: workspace-manager
`:36`/`CLAUDE_PPID :37`, state-manager `:177`/`:180`, episodic-memory `:210`/`:213`.
That is a **fork, not an exec** → 2 node procs per server (wrapper + child). The
wrapper installs `process.on('SIGTERM')` / `process.on('SIGINT')` → `child.kill(...)`
and mirrors the child's exit (workspace-manager wrapper `:41-50`). It has **no stdin
watch and no parent-death detection** (macOS has no `PR_SET_PDEATHSIG`), so the
wrapper tears down only if it is itself signalled, or if the child exits first.

**No server self-exits on stdin EOF.** The MCP SDK `StdioServerTransport.start()`
(`@modelcontextprotocol/sdk/.../server/stdio.js:27-34`) registers ONLY
`stdin.on('data')` and `stdin.on('error')` — there is **no `'end'` or `'close'`
listener**, and `close()` (`:50-65`) is invoked only by an explicit `server.close()`.
None of the three servers calls `server.close()` or registers its own stdin-EOF /
`SIGTERM` handler: each is just `await server.connect(new StdioServerTransport())`
(workspace-manager `src/index.ts:582`, state-manager `src/index.ts:180`,
episodic-memory `src/mcp-server.ts:332` — note episodic-memory's transport lives in
`mcp-server.ts`, NOT `index.ts`). So when the parent closes the stdin pipe, the SDK
does nothing: there is no designed self-exit path.

**`CLAUDE_PPID` is not a watchdog.** All `CLAUDE_PPID` reads are for *session
resolution* (mapping the server to its Claude session by parent PID), never
parent-death self-termination: workspace-manager `src/index.ts:541`; state-manager
`src/index.ts:167,172` + `src/tools/read-tools.ts:651,654`. The only `stdin.on`
matches are a spawned advisor child's stdin (`advisor-review.ts:344`,
`advisor-review.test.ts:154-155`), not the server's own stdin. episodic-memory's
source has ZERO matches across all six patterns — it has no PPID handling at all.

**Q2 conclusion — the leak switch.** No server has a designed self-exit on parent
death. Teardown depends ENTIRELY on a delivered signal:
- On the `tmux kill-session` paths (Q1: stuck-kill, spawn-time comm-profile-failure,
  startup orphan reap), SIGHUP reaches the pane's foreground process group → wrapper
  + child die by node's default SIGHUP action. **These paths tear down cleanly.**
- On any path where the parent (`claude`/`codex`) goes away WITHOUT a signal to the
  wrapper — the idle done-marker path (worker later exits on its own), a crash, an
  OOM/SIGKILL — nothing in the server or wrapper code deterministically exits. Whether
  the node procs nonetheless exit is Node-runtime-incidental (a `'data'`-listener stdin
  reaching pipe-EOF *may* let the loop drain, but this is not designed and not
  guaranteed, and better-sqlite3 holds an open fd). **This is the leak surface.** Q3
  measures the ground truth: are any such survivors alive right now?

## Q3 — live survivor census + Q4 — fork multiplier

Measured 2026-09-16 (full raw snapshot: `scratchpad/mcp-census-raw.txt`, 44 node MCP
procs). Every proc was matched to its root PPID, each root confirmed live with
`ps -p`, and roots joined to tmux panes / worker DB rows.

**Per-root attribution (all 8 roots LIVE):**

| Root PID | What it is | tty | MCP node procs | Live? |
|---|---|---|---|---|
| 18746 | ChatGPT.app `codex app-server` (Codex session, 1.1.11 cache) | ?? | 3 wrappers + 3 children = 6 | YES |
| 16475 | Brain SDK `claude` subprocess (child of daemon 16165) | ttys002 | 1 wrapper + 1 child = 2 (episodic-memory only) | YES |
| 35086 | `claude` — tmux worker `ic-gm-core-regen-1500-r3` (pane_pid 35086, PPID=tmux server 35085) | ttys004 | 6 | YES |
| 22531 | `claude` — tmux worker `ic-pipeline-comments-1509` (PPID 35085) | ttys009 | 6 | YES |
| 28405 | `claude` — tmux worker `ic-pipeline-comments-1509-r2` (PPID 35085) | ttys010 | 6 | YES |
| 25998 | `claude` — operator interactive session (PPID 24593, not a tmux pane) | ttys007 | 6 | YES |
| 23573 | `claude` — operator interactive session (PPID 23377) | ttys008 | 6 | YES |
| 21724 | `claude` — operator interactive session (PPID 21394) | ttys011 | 6 | YES |

Supporting daemon: PID 16165 = `python -m ironclaude.main` (LIVE), parent of the Brain.

**Q4 — fork multiplier CONFIRMED (measured).** Every full session runs exactly
**3 wrapper node procs + 3 child (bundle) node procs = 6**; the wrapper's `pgid`
equals the child's `pgid` equals the session root pid (e.g. ttys004: wrappers
35149/35150/35151, children 35199/35203/35205, all `pgid 35086`), confirming the
child is in the pane's foreground process group (so Q1's SIGHUP reaches it on
`tmux kill-session`). Total this snapshot: **44 node MCP procs** = 7 full sessions
(3 tmux workers + 3 interactive Claude + 1 Codex) × 6 + the Brain's episodic-only
pair (2). **Half of the 44 are wrapper procs** (22 wrappers) — each a full node
runtime that only forwards signals and mirrors the child's exit. episodic-memory's
child is `dist/mcp-server.js`; workspace-manager/state-manager's are `dist/index.js`
— the census pattern matched all three (Q2 / advisor-C1).

**Q3 — LEAK DETERMINATION: NO LEAK (at this snapshot).** Every one of the 44 MCP
node procs traces to a **live** root. There are **zero PPID=1 (launchd) orphans**
and **zero MCP procs whose owning tmux session or session root is gone**. The
falsifier — "any local MCP proc whose owning tmux session/worker row is gone but the
process survives" — did **not** occur. This matches the earlier live diagnosis (0
orphaned MCP servers) and confirms the worker-teardown path is not currently leaking
MCP subprocess trees.

**The memory-pressure mechanism (not a leak).** The ~59-in-the-incident /
44-right-now MCP node procs are the arithmetic of **many concurrent full sessions ×
6 node procs each**, not accumulated orphans: 3 daemon tmux workers + 3 operator
interactive Claude sessions + 1 Codex + the Brain, each holding a V8 heap and an
open better-sqlite3 handle. The fork-doubling (Q4) means **22 of the 44 are pure
wrapper overhead**. Reducing orchestration RAM is therefore about (a) fewer
concurrent sessions and (b) the exec-not-fork wrapper (halves node proc count) —
NOT about hunting a leak that is not there.

**Observation — a stale worker DB row (reverse of an MCP leak; record, do not fix
here).** `workers` has `gm-core-regen-1500-r2` (tmux `ic-gm-core-regen-1500-r2`)
`status='running'` since 2026-09-08, but there is **no** live tmux session by that
name and **no** MCP procs for it — the process tree is gone while the DB row lingers.
That is worker-liveness bookkeeping (the session-died reconciliation of Q1 did not
clear this row), the OPPOSITE of an MCP leak, and out of this loop's scope. Likewise
the live `ic-pipeline-comments-1509`/`-r2` tmux sessions are not in the DB `running`
set, yet their MCP procs are live-rooted (not leaked) — a loose session-to-row
mapping, also out of scope.

## Verdict

**NO LEAK observed — CONDITIONAL-LEAK design gap remains.**

- **Empirically (Q3):** at census, all 44 MCP node procs trace to a live root; zero
  PPID=1 orphans; zero procs whose owning session/root is gone. The daemon's
  worker-reap path is **not currently leaking** MCP subprocess trees. The operator's
  out-of-memory episode is explained by the *arithmetic* of many concurrent full
  sessions × 6 node procs each (Q4), not by accumulated orphans — offloading LLM
  inference to a remote box does not reduce this orchestration RAM.
- **By design (Q1 + Q2):** the daemon has **no dedicated MCP-tree teardown**. Clean
  teardown happens only on the `tmux kill-session` paths (stuck-kill, spawn-time
  comm-profile-failure, startup orphan reap), where SIGHUP reaches the pane's
  foreground process group. On every other terminal path — the **idle done-marker
  path** (worker left alive, later exits on its own), a **crash/OOM**, or a
  **SIGKILL** — no signal is delivered to the wrapper, and no server self-exits on
  stdin EOF or `CLAUDE_PPID` death (Q2). On those paths teardown is
  Node-runtime-incidental, not guaranteed.
- **Therefore the exact conditional-leak condition is:** a worker (or interactive/
  Codex session) whose root process dies **without** `tmux kill-session` delivering
  SIGHUP to the MCP process group — force-quit, SIGKILL, OOM, or crash — **can** strand
  its 6 (or 2) MCP node procs as PPID=1 orphans. Q3 found none right now, but nothing
  in the code *prevents* it; the current clean state depends on how sessions happened
  to end, not on a teardown guarantee.

## Recommendations (not implemented — each its own operator-gated loop)

Prioritised by impact; each cites the finding that motivates it.

1. **Exec-instead-of-fork the MCP wrapper** — *motivated by Q4* (22 of 44 live node
   procs are pure wrapper overhead; every wrapper holds a full node runtime only to
   forward SIGTERM/SIGINT and mirror the child's exit). Replacing
   `spawn(process.execPath,[bundle])` + signal-forwarding with `process.execvp`-style
   exec (or having the wrapper re-exec into the bundle after `ensureRuntime`) halves
   the node MCP process count per session and removes the doubling entirely. Biggest
   RAM win, no behaviour change to teardown (an exec'd server IS the pane-group
   process). *Caveat:* the wrapper currently does build-on-first-run + npm-chatter
   isolation; an exec design must preserve those.
2. **Give each server a deterministic self-exit on parent death** — *motivated by Q2*
   (no server exits on stdin EOF or watches `CLAUDE_PPID`). Add either an explicit
   `process.stdin.on('end', () => process.exit(0))` or a `CLAUDE_PPID` liveness
   watchdog (`process.kill(ppid,0)` poll → exit on `ESRCH`). **episodic-memory is the
   prime candidate** (zero PPID handling at all; `src/mcp-server.ts`). This closes the
   conditional-leak surface at the source, so a signal-less parent death can no longer
   strand the tree. Combine with #1 (exec) so the single remaining proc self-exits.
3. **Replace the two `pgrep -f` pattern-kill sites with PID-scoped teardown** —
   *motivated by the operator "never pattern-kill; scope to recorded PIDs" directive*.
   `_kill_orphan_brains` (`main.py:1186-1190`, `pgrep -f "claude.*stream-json.*Orchestrator"`)
   and `_cleanup_zombie_mcp_processes` (`orchestrator_mcp.py`, runs on `restart_mcp`;
   advisor located it at `:6706`) both pattern-kill. Record the recorded child PIDs at
   spawn (the daemon already knows each worker's pane PID; `pgid`/`tty` from Q3 make the
   tree enumerable) and signal those PIDs, never a name pattern.
4. **(Lower priority, safety net) Daemon maintenance census that surfaces row-less MCP
   survivors** — *motivated by the Q1/Q2 conditional gap*, not by an observed leak.
   Mirrors the v1.1.11 row-less-worktree reaper: periodically enumerate MCP procs whose
   owning tmux session AND worker row are both gone, and surface (not auto-kill —
   respect never-pattern-kill; act only on recorded PIDs) for teardown. Only worthwhile
   if #2 is not adopted; #2 prevents the survivor, this only cleans one up after.
5. **(Separate concern, worker-liveness not MCP) Reconcile stale `running` rows** —
   the `gm-core-regen-1500-r2` row has been `running` for 8 days with no session/procs
   (Q3 observation). The session-died reconciliation (Q1, `main.py:4243+`) only fires
   while the daemon still lists the worker as running AND re-checks it; a row that
   slipped out of that loop lingers. Out of this loop's scope; noted for a future
   worker-liveness pass.

**Documentation note (not a defect).** The design doc
(`2026-09-16-mcp-teardown-investigation-design.md`) says all three servers' children
run `dist/index.js`. That is accurate for workspace-manager and state-manager, but
**episodic-memory's child runs `dist/mcp-server.js`** (its transport entrypoint is
`src/mcp-server.ts:332`, not `src/index.ts`). The census pattern in this loop was
corrected to match all three; the design prose should be read with this correction.

