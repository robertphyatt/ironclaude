# MCP Subprocess Teardown Verification — Investigation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** Verify by code trace + live process census whether the Commander
daemon's worker-reap path tears down a reaped worker's MCP subprocess tree, and
record the verdict + non-implemented hardening recommendations in a findings note.

**Requirements:** docs/plans/2026-09-16-mcp-teardown-investigation-design.md
(this investigation's operator-approved contract — the operator directive
"verify the teardown, investigation only" plus the scoped design).

**Architecture:** Investigation-only PM loop. The execute stage exists solely to
unblock read-only Bash (per `ironclaude:workflow-durability`). Four sequential
tasks each append an evidence-cited section to ONE findings note
(`commander/docs/plans/2026-09-16-mcp-teardown-findings.md`); no source is changed.

**Tech Stack:** bash (`ps`, `grep` with `-e` patterns, `tmux list-sessions` /
`list-panes`, `sqlite3 -readonly` SELECT), Read tool for code traces. macOS
(darwin, zsh). NO process is ever signalled or killed — this loop exemplifies the
"never pattern-kill" boundary.

**Global invariants (the blind reviewer checks commands against these):**
- Shell state does NOT persist between steps — every command uses literal absolute
  paths; no reliance on an earlier step's exported variable.
- Bash cwd is `commander/`; writes use absolute paths; `docs/` is gitignored so
  staging uses `git -C /Users/roberthyatt/Code/ironclaude add -f`.
- The executing-stage grep is **ugrep 7.8.4** (a Claude Code shim), and a `|`
  cannot pass the stage guard in any form; every alternation therefore uses
  repeated `-e PATTERN` (POSIX, works in BSD/GNU/ugrep, guard-safe). No `\|`.
- Every `ps | grep` pattern **bracket-escapes its first character** (`[m]cp…`,
  `[c]laude`) so the grep process's own argv does not self-match and inflate the
  census.
- Evidence commands never use `2>/dev/null`, never `head`-truncate an
  absence/completeness grep, and list NAMES not counts so an empty result is
  distinguishable from a failed command.
- Census/DB numbers are MEASURED during execution and pasted into the note — this
  plan authors NO predicted process counts as `expected:` values. Source-file grep
  `expected:` values below were measured against current source and are stable
  facts; execution re-confirms them.
- `sqlite3` is invoked `-readonly` so a wrong path errors instead of creating an
  empty DB, enforcing investigation-only.
- No tests: every task is read-only investigation producing documentation.

---

## Task 1: Q1 — worker-teardown code trace (create the findings note)

**Files:**
- Create: `commander/docs/plans/2026-09-16-mcp-teardown-findings.md`

No tests required: read-only code trace producing a documentation section.

**Step 1: Read the worker-teardown kill sites.**

Read (Read tool) each region and confirm, per terminal path, what it does to the
worker process tree:
- `commander/src/ironclaude/main.py:3966-4030` — `_confirm_and_kill_stuck_worker`
  (the `tmux.kill_session` at ~4009).
- `commander/src/ironclaude/main.py:3617-3631` — communication-profile activation
  failure → `tmux.kill_session` at ~3629.
- `commander/src/ironclaude/main.py:4190-4290` — done-marker completion (idle →
  `_finalize_and_release_worker(terminal=False)`, session NOT killed, see comment
  ~4224-4225) and "session died (tmux gone)" path (~4243-4280).
- `commander/src/ironclaude/main.py:1170-1190` — `_kill_orphan_workers` (~1180)
  and `_kill_orphan_brains` (~1186).
- `commander/src/ironclaude/main.py:1540-1557` and
  `commander/src/ironclaude/orchestrator_mcp.py:3522` — `_finalize_and_release_worker`.

**Step 2: Confirm what tmux kill-session runs and its signal semantics.**

Run:
```bash
grep -n "def kill_session" /Users/roberthyatt/Code/ironclaude/commander/src/ironclaude/tmux_manager.py
```
Expected: `352:    def kill_session` (non-empty).

Run:
```bash
grep -n "kill-session" /Users/roberthyatt/Code/ironclaude/commander/src/ironclaude/tmux_manager.py
```
Expected: `355:` line showing `tmux kill-session -t` (non-empty). Record the
operating premise precisely: `tmux kill-session` destroys the session, and the
pty hangup delivers SIGHUP to the pane's foreground process group — so whether the
MCP procs die depends on their being IN that process group (verified empirically
in Task 3 via `pgid`/`tty`), not merely on being descendants.

**Step 3: Write findings section 1.**

Create `commander/docs/plans/2026-09-16-mcp-teardown-findings.md` with a title and
a "Q1 — worker-teardown propagation" section stating, per terminal path, whether it
calls `tmux.kill_session` (→ SIGHUP the pane's foreground group → worker
`claude`/`codex` and any MCP procs in that group die by node's default SIGHUP
action) or only marks the DB row / reaps the worktree / finalizes without a kill
(tree survives — e.g. the idle done-marker path). Each claim cites `main.py:<line>`
verified in Step 1.

**Step 4: Stage the note.**

Run:
```bash
git -C /Users/roberthyatt/Code/ironclaude add -f commander/docs/plans/2026-09-16-mcp-teardown-findings.md
```
Expected: staged (professional mode blocks commit).

---

## Task 2: Q2 — wrapper + server self-exit-on-parent-death trace

**Files:**
- Modify: `commander/docs/plans/2026-09-16-mcp-teardown-findings.md`

**Depends on:** Task 1

No tests required: read-only code trace producing a documentation section.

**Step 1: Re-confirm the wrapper fork/signal facts (all three wrappers).**

Run:
```bash
grep -n -e "spawn" -e "execPath" -e "CLAUDE_PPID" /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager/cli/mcp-server-wrapper.js /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/state-manager/cli/mcp-server-wrapper.js /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/episodic-memory/cli/mcp-server-wrapper.js
```
Expected: each wrapper shows a `spawn(process.execPath, ...)` line and a
`CLAUDE_PPID` line — workspace-manager `spawn` ~:36 + `CLAUDE_PPID` :37;
state-manager `spawn` ~:177 + `CLAUDE_PPID` :180; episodic-memory `spawn` ~:210 +
`CLAUDE_PPID` :213 (non-empty for all three). Confirms fork (spawn), not exec.

**Step 2: Search each server's SOURCE for a stdin-EOF / parent-death self-exit.**

Run:
```bash
grep -rn -e "process.stdin" -e "CLAUDE_PPID" -e "stdin.on" -e "on('end')" -e 'on("end")' -e "process.ppid" /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager/src /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/state-manager/src /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/episodic-memory/src
```
Expected (measured against current source; re-confirm at run):
- workspace-manager: `CLAUDE_PPID` at `src/index.ts:541`.
- state-manager: `CLAUDE_PPID` at `src/index.ts:167,172` and `src/tools/read-tools.ts:651,654`; `process.ppid` at `read-tools.ts:651`; `stdin.on` only in `advisor-review` (child stdin, not the server's own stdin).
- episodic-memory: ZERO hits on all six patterns.
Record which servers read `CLAUDE_PPID` (candidate parent-death watchdog) and which
have no stdin/ppid handling at all (episodic-memory → relies on SDK transport close
or an external signal to die).

**Step 3: Read each server's transport-connect entrypoint.**

Read where each server constructs/connects `StdioServerTransport` and confirm
whether the process exits when the transport closes (stdin EOF) or keeps the event
loop alive, and whether the `CLAUDE_PPID` reads in Step 2 drive a self-exit
watchdog:
- workspace-manager: `src/index.ts:582` (transport), `:541` (CLAUDE_PPID use).
- state-manager: `src/index.ts:180` (transport), `:167,172` (CLAUDE_PPID use).
- episodic-memory: `src/mcp-server.ts:332` (transport). (Its `src/index.ts` is a
  library index with no transport — do NOT read that for teardown.)

**Step 4: Write findings section 2.**

Append a "Q2 — self-exit on parent death" section: per server, does it self-exit on
stdin EOF (SDK transport close) and/or via a `CLAUDE_PPID` watchdog, or depend on a
delivered SIGTERM/SIGINT? State the leak switch: a server that neither self-exits on
stdin EOF nor watches `CLAUDE_PPID` lingers if its parent dies without signalling it
(SIGKILL, or reparenting to launchd). Cite each file:line.

**Step 5: Stage the note.**

Run:
```bash
git -C /Users/roberthyatt/Code/ironclaude add -f commander/docs/plans/2026-09-16-mcp-teardown-findings.md
```
Expected: staged.

---

## Task 3: Q3 + Q4 — live process/root census (the leak/no-leak test)

**Files:**
- Modify: `commander/docs/plans/2026-09-16-mcp-teardown-findings.md`

**Depends on:** Task 2

No tests required: read-only live-process census producing a documentation
section. NO process is signalled or killed.

**Step 1: Snapshot every MCP node process (all three bundle filenames).**

The three servers' children have DIFFERENT bundle filenames: workspace-manager and
state-manager run `dist/index.js`; episodic-memory runs `dist/mcp-server.js`. Match
all three plus the wrappers. Patterns are bracket-escaped to avoid grep self-match.

Run:
```bash
ps -Ao pid,ppid,pgid,tty,stat,comm,args | grep -e "[m]cp-server-wrapper.js" -e "[m]cp-servers/.*/dist/index.js" -e "[m]cp-servers/.*/dist/mcp-server.js" > /private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/mcp-census-raw.txt; cat /private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/mcp-census-raw.txt
```
Expected: one line per MCP node process (wrappers + `index.js` children +
`mcp-server.js` children), with pid/ppid/pgid/tty, or empty if none running.
Record verbatim.

**Step 2: Resolve each distinct PPID to its root (bracket-escaped patterns).**

Run:
```bash
ps -Ao pid,ppid,pgid,tty,comm,args | grep -i -e "[c]laude" -e "[c]odex" -e "[t]mux" -e "[i]term" -e "[i]ronclaude"
```
Expected: the candidate root/parent processes. Classify each MCP proc's root as a
`claude`/`codex` worker, an interactive operator session (iTerm/tmux pane), the
Codex/ChatGPT app, the daemon, or PID 1 (launchd — an orphan).

**Step 3: Join MCP procs to tmux panes (makes Q1 measurable).**

Run:
```bash
tmux list-panes -a -F "#{session_name} #{window_index}.#{pane_index} tty=#{pane_tty} pane_pid=#{pane_pid} cmd=#{pane_current_command}"
```
Expected: one line per pane with its tty + pane_pid, or a "no server running"
message. Join to Step 1 by tty/pgid to establish which MCP procs are actually in a
worker pane's process group (the Q1 "would SIGHUP reach it" fact).

**Step 4: List live tmux sessions.**

Run:
```bash
tmux list-sessions
```
Expected: current tmux sessions (including any `ic-*` worker sessions), or a "no
server running" message. Record verbatim.

**Step 5: Locate the worker DB (discover — do not hard-code).**

Run (config default):
```bash
grep -n "db_path" /Users/roberthyatt/Code/ironclaude/commander/src/ironclaude/config.py
```
Expected: `42:    "db_path": "data/db/ironclaude.db",` and a `DB_PATH` env-override
mapping (~:96).

Run (env override actually in effect, sourced by `make run`):
```bash
grep -n "DB_PATH" /Users/roberthyatt/Code/ironclaude/commander/.env
```
Expected: `8:DB_PATH=data/db/ironclaude.db` (relative to `commander/`), or a
different value to substitute below.

Run (confirm no JSON override):
```bash
grep -n "db_path" /Users/roberthyatt/Code/ironclaude/commander/config/ironclaude.json
```
Expected: no output (no override).

Run (confirm the live DB file — non-zero, recent WAL):
```bash
ls -la /Users/roberthyatt/Code/ironclaude/commander/data/db/
```
Expected: `ironclaude.db` with non-zero size and recent `-wal`/`-shm` siblings;
`ic.db`/`tron.db` are stale and are NOT the target.

Run (confirm the schema is reachable read-only):
```bash
sqlite3 -readonly /Users/roberthyatt/Code/ironclaude/commander/data/db/ironclaude.db ".tables"
```
Expected: a table list that includes `workers`. (`-readonly` errors instead of
creating a file if the path is wrong. If the three greps above showed a different
db_path, substitute it here and below — do NOT invent one.)

**Step 6: Read the worker rows (correct columns: PK is `id`, not `worker_id`).**

Run:
```bash
sqlite3 -readonly -header /Users/roberthyatt/Code/ironclaude/commander/data/db/ironclaude.db "SELECT status, COUNT(*) FROM workers GROUP BY status;"
```
Expected: a status→count breakdown. Then:
```bash
sqlite3 -readonly -header /Users/roberthyatt/Code/ironclaude/commander/data/db/ironclaude.db "SELECT id, type, machine, client, tmux_session, status, spawned_at, finished_at, workspace_path FROM workers WHERE status='running';"
```
Expected: the running-worker rows with `id`, `machine`, `tmux_session`. `machine`
matters: a remote (ssh_host) worker's tmux session lives on another box and must NOT
be counted as a local leak. For each `ic-*` tmux session seen in Steps 1/3/4 that is
absent from the running set, look it up:
```bash
sqlite3 -readonly -header /Users/roberthyatt/Code/ironclaude/commander/data/db/ironclaude.db "SELECT id, machine, tmux_session, status, finished_at FROM workers WHERE tmux_session='<name>';"
```
Expected: the row (or none) — a `finished`/absent row whose MCP procs still live is
the leak signal.

**Step 7: The leak/no-leak determination + write findings section 3.**

Cross-check: for every MCP proc from Step 1, is its owning root (Step 2), tmux pane
(Step 3), tmux session (Step 4), and worker row (Step 6) still present? Identify ANY
local MCP proc whose owning tmux session or worker row is gone but the process
survives — that single fact upgrades "no confirmed leak" to "confirmed leak". Count
wrapper procs vs child procs (both `index.js` and `mcp-server.js` children) to
confirm the 2×/server, 6×/session multiplier (Q4). Append a "Q3 — live survivor
census + Q4 — fork multiplier" section with the verbatim snapshots, the per-root
attribution table, MEASURED wrapper/child counts, and the explicit leak/no-leak
determination with its falsifier. Stage:
```bash
git -C /Users/roberthyatt/Code/ironclaude add -f commander/docs/plans/2026-09-16-mcp-teardown-findings.md
```
Expected: staged.

---

## Task 4: Verdict + recommendations (consolidate)

**Files:**
- Modify: `commander/docs/plans/2026-09-16-mcp-teardown-findings.md`

**Depends on:** Task 3

No tests required: documentation consolidation.

**Step 1: Write the verdict.**

Append a "Verdict" section stating one of: LEAK CONFIRMED (with the exact surviving
proc + missing root/session/row), NO LEAK (teardown verified across all terminal
paths + self-exit confirmed), or CONDITIONAL LEAK (the exact condition, e.g. "a
worker killed with SIGKILL, or exiting on the idle done-marker path that does NOT
kill the session, whose episodic-memory server lacks stdin-EOF self-exit"). The
verdict follows mechanically from Q1-Q3 evidence.

**Step 2: Write recommendations (NOT implemented here).**

Append a prioritized "Recommendations (not implemented — each its own operator-gated
loop)" list drawn only from confirmed findings, candidates including:
- exec-instead-of-fork wrapper to halve node process count (if Q4 confirms 2×);
- stdin-EOF close and/or `CLAUDE_PPID` watchdog self-exit for any server Q2 found
  lacking one (episodic-memory is the prime candidate);
- replace the two `pgrep -f` pattern-kill sites (`_kill_orphan_brains` main.py:~1186,
  `_cleanup_zombie_mcp_processes` orchestrator_mcp.py:~6706) with PID-scoped teardown
  per the operator "never pattern-kill" directive;
- a daemon maintenance census that surfaces row-less MCP survivors (mirrors the
  v1.1.11 row-less worktree reaper) — only if Q3 shows survivors are possible.
Also record the design-doc prose inaccuracy: the design says all three children run
`dist/index.js`, but episodic-memory runs `dist/mcp-server.js` — a documentation
note, not a defect. Each recommendation cites the finding that motivates it;
unmotivated ones are omitted.

**Step 3: Stage the completed note.**

Run:
```bash
git -C /Users/roberthyatt/Code/ironclaude add -f commander/docs/plans/2026-09-16-mcp-teardown-findings.md
```
Expected: staged. The findings note is the loop deliverable; no source changed.
