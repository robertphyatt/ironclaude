# Concurrent IronClaude Memory-Efficiency Fixes — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** Make IronClaude reliable and memory-efficient under many concurrent
operator sessions + Commander (Brain + workers) on a 48 GB Mac, by implementing all
eight investigation/Fable-verified fixes.

**Requirements:** docs/plans/2026-09-16-mcp-memory-efficiency-design.md (operator
chose "all eight fixes in one loop").

**Architecture:** Bound + self-clean live-root count (idle-TTL, MCP self-exit),
make the daemon SEE memory (swap/pressure gate + heartbeat), reclaim smaller terms
(dead-code delete, vitest caps, embedding backend, wrapper collapse). Heavy items
land safe-by-default (embedding defaults to local; wrapper collapse preserves
build/npm-isolation + marker cleanup and is verified by smoke).

**Tech Stack:** Python 3.11 (psutil, pytest); TypeScript + @modelcontextprotocol/sdk
+ vitest + esbuild for the three MCP servers.

**Grounded facts (verified against current source):**
- `orchestrator_mcp.py`: `import psutil` :33; `get_system_memory` :6851 returns only
  `total_gb`/`available_gb`; `_check_spawn_preconditions` :2731, memory-floor reject
  ends :2782, "passed" logger :2784 (uses `self._config`); seam terminal→non-terminal
  downgrade on a LIVE session at :3618-3627; `_MCP_CLEANUP_PATTERNS` :224-228;
  `_cleanup_zombie_mcp_processes` :6706-6772 called at :6791 in `restart_mcp`;
  `send_to_worker` :5770 does not touch the done-marker; `_routine_prompt_active` :4074.
- `main.py`: `import psutil` :24; daemon uses `self.config`; `format_heartbeat`
  imported from `notifications.py`, sole call :4836; stuck-path teardown
  `tmux.kill_session` then terminal finalize at :4009-4025; `_kill_orphan_brains`
  :1186-1203 (`_logged_kill`), caller in `_handle_restart` :1245, recorded brain PID
  `self.brain._brain_pid` :1269; idle done-marker branch :4211-4241, **marker is
  DELETED on delivery :4230-4238** (so marker presence is NOT a stable idle signal);
  `check_workers` loop over `running_ids` :4196; per-worker state dicts init
  ~:1477/:1498. **`check_stuck_workers` (:3870-3949) has NO call site** (dead in prod)
  — its prune block :3942-3964 never runs, so do NOT put new prunes there.
- `TmuxManager`: `create_session` wires `tmux pipe-pane … > /tmp/ic-logs/<session>.log`
  (`tmux_manager.py:342-346`); `get_log_mtime(name, ssh_host=, remote_log_dir=)`
  :514-534 (local + ssh) — the correct per-worker activity signal (an idle Claude TUI
  writes nothing; a working one redraws constantly; a Brain `send_to_worker` echoes to
  the pane → log grows).
- Config example: repo-root `config/ironclaude.json.example`; defaults dict `DEFAULTS`
  `config.py:27`, `min_available_memory_pct` :34.
- Servers: em transport `src/mcp-server.ts:333`, main() called UNCONDITIONALLY :338
  (→ `dist/mcp-server.js`, NOT git-tracked — `.gitignore` ignores em dist; built on
  demand by the wrapper); sm `src/index.ts:181`, main() :186 (→ `dist/index.js`,
  git-tracked); ws `src/index.ts:582` inside `startWorkspaceManagerServer` (exported
  :556; bundle re-exports it) guarded by `argv[1]===import.meta.url` :585-591 (→
  `dist/index.js`, git-tracked). Wrappers em/sm `cli/mcp-server-wrapper.js` define
  `PENDING_MARKER` :16, unlink it in `process.on('exit')` (sm :185, em :218); ws
  wrapper has no marker. Only workspace-manager has a `vitest.config.ts`. vitest v3
  installed (`poolOptions.forks.maxForks` valid).

**Execution invariants:** shell state does not persist between steps (literal
absolute paths); Bash cwd is `commander/` (use `git -C <repo-root>` + absolute
paths); quote globs; `docs/` gitignored → `git add -f`; no `2>/dev/null` on evidence;
TDD RED shown before GREEN; `No tests required: <reason>` where pure config/launch.
`<repo>` = `/Users/roberthyatt/Code/ironclaude`. `&&`-chained commands are fine in the
executing stage (only the review stage forbids chaining).

---

## Task 1: A1 — pressure-aware spawn gate (Python)

**Files:** Modify `commander/src/ironclaude/orchestrator_mcp.py`,
`commander/src/ironclaude/config.py`, `config/ironclaude.json.example`;
Test `commander/tests/test_orchestrator_mcp.py`.

**Step 1 (RED):** Add `TestSpawnMemoryPressureGate` to `test_orchestrator_mcp.py`,
mirroring its existing `tools` fixture. Because the memory-floor check runs real
psutil, each case MUST stub `tools.get_system_memory = MagicMock(return_value={
"available_gb": 30.0, "total_gb": 48.0})` (as tests at :8650/:8676 do) so the floor
passes and the swap/pressure guard is what's exercised. Cases: (a) `psutil.swap_memory`
→ used 9 GB, `max_swap_used_gb`=8 → reject dict `error` contains "swap too high";
(b) `_memory_pressure_level`→4, `spawn_block_on_pressure`=True → reject `error`
contains "memory pressure"; (c) swap 2 GB + pressure 1 → None; (d) `psutil.swap_memory`
AND the sysctl `subprocess.run` both raise → None (fail-open; the helpers swallow
internally). Run:
```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_orchestrator_mcp.py -k SpawnMemoryPressure -x
```
Expected: FAIL (helpers/gate absent).

**Step 2 (GREEN helpers):** Add to `OrchestratorTools` near `get_system_memory`
(~:6851):
```python
    def _swap_used_gb(self) -> float:
        """Swap in use, GB. Fail-open (0.0) so a probe error never blocks spawning."""
        try:
            return round(psutil.swap_memory().used / (1024**3), 1)
        except Exception as exc:  # noqa: BLE001 - telemetry, never fatal
            logger.info("swap probe failed (fail-open): %s", exc)
            return 0.0

    def _memory_pressure_level(self) -> int:
        """macOS memory pressure: 1=normal, 2=warn, 4=critical; 0 if unavailable."""
        try:
            out = subprocess.run(
                ["sysctl", "-n", "kern.memorystatus_vm_pressure_level"],
                capture_output=True, text=True, timeout=3,
            )
            if out.returncode == 0 and out.stdout.strip():
                return int(out.stdout.strip())
        except Exception as exc:  # noqa: BLE001 - telemetry, never fatal
            logger.info("memory pressure probe failed (fail-open): %s", exc)
        return 0
```

**Step 3 (GREEN gate):** In `_check_spawn_preconditions`, after the memory-floor
reject block (after :2782, before the "passed" logger :2784) insert:
```python
        # 3. Swap / macOS memory-pressure guard (additive to the floor above).
        max_swap = self._config.get("max_swap_used_gb", 8.0)
        swap_used = self._swap_used_gb()
        if max_swap and max_swap > 0 and swap_used > max_swap:
            logger.info("Spawn rejected: swap used %.1fGB > max %.1fGB", swap_used, max_swap)
            return {
                "error": (f"Spawn rejected: swap too high (used {swap_used}GB > "
                          f"max {max_swap}GB). Free memory or close sessions."),
                "swap_used_gb": swap_used, "max_swap_used_gb": max_swap,
            }
        if self._config.get("spawn_block_on_pressure", True):
            pressure = self._memory_pressure_level()
            if pressure >= 2:
                logger.info("Spawn rejected: macOS memory pressure level %d (>=2)", pressure)
                return {
                    "error": (f"Spawn rejected: macOS memory pressure elevated "
                              f"(level {pressure}; 2=warn, 4=critical)."),
                    "memory_pressure_level": pressure,
                }
```

**Step 4 (config):** `config.py` `DEFAULTS` (after :34) add `"max_swap_used_gb": 8.0,`
and `"spawn_block_on_pressure": True,`; `config/ironclaude.json.example` add both
(JSON `8.0` / `true`).

**Step 5 (verify):** `cd .../commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_orchestrator_mcp.py -k SpawnMemoryPressure -x && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_config.py -x` → PASS.

**Step 6 (stage):**
```bash
git -C /Users/roberthyatt/Code/ironclaude add -- commander/src/ironclaude/orchestrator_mcp.py commander/src/ironclaude/config.py config/ironclaude.json.example commander/tests/test_orchestrator_mcp.py
```

---

## Task 2: A2 — heartbeat memory line (Python)

**Files:** Modify `commander/src/ironclaude/notifications.py`,
`commander/src/ironclaude/main.py`; Test `commander/tests/test_notifications.py`.

**Step 1 (read):** Read `notifications.format_heartbeat` — note it has an EARLY
RETURN for the no-workers/no-waits case (~:123-131) AND the main lines path. The
`mem_line` must render in BOTH.

**Step 2 (RED):** Add a test asserting `format_heartbeat(..., mem_line="mem: 12.0 free / swap 2.0 / top X=1.0G")`
includes that string with `workers=[]` (early-return path) AND with a non-empty
worker list. Run:
```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_notifications.py -k mem -x
```
Expected: FAIL.

**Step 3 (GREEN notifications):** Add `mem_line: str | None = None` kwarg; when truthy
append it as its own line in BOTH the early-return branch and the lines branch. None
keeps existing output byte-identical.

**Step 4 (GREEN daemon):** Add module helper to `main.py`:
```python
def _format_mem_line() -> str:
    """One-line memory status for the heartbeat: available / swap / top-3 RSS."""
    try:
        vm = psutil.virtual_memory()
        sw = psutil.swap_memory()
        procs = []
        for p in psutil.process_iter(["name", "memory_info"]):
            try:
                procs.append((p.info["memory_info"].rss, p.info["name"]))
            except (psutil.NoSuchProcess, psutil.AccessDenied, TypeError):
                pass
        procs.sort(reverse=True)
        top = ", ".join(f"{n}={rss/(1024**3):.1f}G" for rss, n in procs[:3])
        return (f"mem: {vm.available/(1024**3):.1f}G free / "
                f"swap {sw.used/(1024**3):.1f}G / top {top}")
    except Exception as exc:  # noqa: BLE001 - telemetry, never fatal
        return f"mem: unavailable ({exc})"
```
At the `format_heartbeat(...)` call (:4836) add `mem_line=_format_mem_line(),`.

**Step 5 (verify):** `PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_notifications.py -x` → PASS.

**Step 6 (stage):**
```bash
git -C /Users/roberthyatt/Code/ironclaude add -- commander/src/ironclaude/notifications.py commander/src/ironclaude/main.py commander/tests/test_notifications.py
```

---

## Task 3: C(em) — episodic-memory self-exit on parent death (TS)

**Files:** Modify `worker/mcp-servers/episodic-memory/src/mcp-server.ts`; Test
`worker/mcp-servers/episodic-memory/src/__tests__/parent-death-exit.test.ts` (create).
(em `dist/mcp-server.js` is rebuilt for the test but NOT staged — it is gitignored /
untracked and rebuilt on demand by the wrapper.)

**Step 1 (RED):** Create the test (vitest): (a) build, spawn `dist/mcp-server.js`
`stdio:['pipe','ignore','ignore']`, `child.stdin.end()`, assert exit 0 within 5 s;
(b) spawn with `env.CLAUDE_PPID` = a short-lived helper pid and `env.IC_PPID_POLL_MS='500'`,
kill the helper, assert the child exits within 5 s (fast poll). Run:
```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/episodic-memory && npm run build && npx vitest run parent-death-exit
```
Expected: FAIL — case (b) times out (no PPID watchdog). (Case (a) MAY already pass on
current code; case (b) is the reliable RED.)

**Step 2 (GREEN):** In `src/mcp-server.ts` `main()` immediately after
`await server.connect(transport)` (:333) insert:
```typescript
  // Exit when the parent (Claude/Codex session) goes away so this server never
  // lingers as an orphan holding memory. macOS has no PDEATHSIG, so watch both:
  process.stdin.on('end', () => process.exit(0));
  const icPpid = Number(process.env.CLAUDE_PPID);
  if (Number.isInteger(icPpid) && icPpid > 1) {
    const pollMs = Number(process.env.IC_PPID_POLL_MS) || 30000;
    setInterval(() => {
      try { process.kill(icPpid, 0); }
      catch (err: any) { if (err && err.code === 'ESRCH') process.exit(0); }
    }, pollMs); // refed on purpose — this watchdog must keep running
  }
```

**Step 3 (verify):** Re-run Step 1 command → PASS.

**Step 4 (stage — src + test only, NOT dist):**
```bash
git -C /Users/roberthyatt/Code/ironclaude add -f -- worker/mcp-servers/episodic-memory/src/mcp-server.ts worker/mcp-servers/episodic-memory/src/__tests__/parent-death-exit.test.ts
```

---

## Task 4: C(sm) — state-manager self-exit on parent death (TS)

**Files:** Modify `worker/mcp-servers/state-manager/src/index.ts`,
`worker/mcp-servers/state-manager/dist/index.js` (git-tracked → staged); Test
`worker/mcp-servers/state-manager/src/__tests__/parent-death-exit.test.ts` (create).

**Step 1 (RED):** Same test shape as Task 3 against `dist/index.js`. The sm server
calls `initDb()` at startup (`src/index.ts:162`), so the spawned child MUST get
`env.STATE_MANAGER_DB_PATH=<a tmp path>` (`src/db.ts:31-32`) to avoid touching the
live state DB. Run:
```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/state-manager && npm run build && npx vitest run parent-death-exit
```
Expected: FAIL (case b).

**Step 2 (GREEN):** Insert the identical self-exit block from Task 3 Step 2 after
`await server.connect(transport)` (:181).

**Step 3 (verify):** Re-run Step 1 → PASS.

**Step 4 (stage):**
```bash
git -C /Users/roberthyatt/Code/ironclaude add -f -- worker/mcp-servers/state-manager/src/index.ts worker/mcp-servers/state-manager/dist/index.js worker/mcp-servers/state-manager/src/__tests__/parent-death-exit.test.ts
```

---

## Task 5: C(ws) — workspace-manager self-exit on parent death (TS)

**Files:** Modify `worker/mcp-servers/workspace-manager/src/index.ts`,
`worker/mcp-servers/workspace-manager/dist/index.js` (git-tracked → staged); Test
`worker/mcp-servers/workspace-manager/src/__tests__/parent-death-exit.test.ts` (create).

**Step 1 (RED):** Same test shape against `dist/index.js`. Run:
```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager && npm run build && npx vitest run parent-death-exit
```
Expected: FAIL (case b).

**Step 2 (GREEN):** Insert the identical self-exit block after
`await server.connect(new StdioServerTransport());` (:582), inside
`startWorkspaceManagerServer`.

**Step 3 (verify):** Re-run Step 1 → PASS; then `npx vitest run` (full ws suite) → PASS.

**Step 4 (stage):**
```bash
git -C /Users/roberthyatt/Code/ironclaude add -f -- worker/mcp-servers/workspace-manager/src/index.ts worker/mcp-servers/workspace-manager/dist/index.js worker/mcp-servers/workspace-manager/src/__tests__/parent-death-exit.test.ts
```

---

## Task 6: E — vitest fan-out caps (TS config)

**Files:** Modify `worker/mcp-servers/workspace-manager/vitest.config.ts`; Create
`worker/mcp-servers/state-manager/vitest.config.ts`,
`worker/mcp-servers/episodic-memory/vitest.config.ts`.

No tests required: build/test config only (verified by the suites running green).

**Step 1:** Extend `workspace-manager/vitest.config.ts` `test` block with
`pool: 'forks', poolOptions: { forks: { maxForks: 2, minForks: 1 } }` (preserve
existing options).

**Step 2:** Create the other two:
```typescript
import { defineConfig } from 'vitest/config';
export default defineConfig({
  test: { pool: 'forks', poolOptions: { forks: { maxForks: 2, minForks: 1 } } },
});
```

**Step 3 (verify each):**
```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/state-manager && npx vitest run --reporter=dot
```
Expected: runs (no config error) + passes; repeat cd for episodic-memory + workspace-manager.

**Step 4 (stage):**
```bash
git -C /Users/roberthyatt/Code/ironclaude add -f -- worker/mcp-servers/workspace-manager/vitest.config.ts worker/mcp-servers/state-manager/vitest.config.ts worker/mcp-servers/episodic-memory/vitest.config.ts
```

---

## Task 7: D1 — delete dead `_cleanup_zombie_mcp_processes` (Python)

**Files:** Modify `commander/src/ironclaude/orchestrator_mcp.py`,
`commander/tests/test_orchestrator_mcp.py`, `commander/tests/test_mcp_entrypoints.py`.

**Depends on:** Task 1 (same two files — order after A1).

**Step 1 (RED):** In `test_mcp_entrypoints.py` add a test asserting `OrchestratorTools`
has no attr `_cleanup_zombie_mcp_processes` and the module has no `_MCP_CLEANUP_PATTERNS`.
Run:
```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_mcp_entrypoints.py -k zombie -x
```
Expected: FAIL.

**Step 2 (GREEN — source):** Delete `_MCP_CLEANUP_PATTERNS` (:224-228), the method
(:6706-6772), and the `self._cleanup_zombie_mcp_processes()` call at :6791 (keep the
db-close + flush + execvp in `restart_mcp`).

**Step 3 (GREEN — adapt TestRestartMcp):** In `test_orchestrator_mcp.py` `TestRestartMcp`
(:7237-7358): DELETE `test_restart_mcp_calls_zombie_cleanup_before_exec` (:7270-7287),
`test_cleanup_zombie_mcp_skips_own_pid` (:7289-7305),
`test_cleanup_zombie_mcp_kills_dead_parent_process` (:7307-7332),
`test_cleanup_zombie_mcp_spares_live_parent_process` (:7334-7358); and EDIT
`test_restart_mcp_codex_refuses_before_side_effects` (drop the
`patch.object(... "_cleanup_zombie_mcp_processes")` :7242 + `cleanup.assert_not_called()`
:7251) and `test_restart_mcp_closes_db_and_execs` (drop the patch.object :7262).

**Step 4 (verify):**
```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_mcp_entrypoints.py tests/test_orchestrator_mcp.py -x
```
Expected: PASS.

**Step 5 (stage):**
```bash
git -C /Users/roberthyatt/Code/ironclaude add -- commander/src/ironclaude/orchestrator_mcp.py commander/tests/test_orchestrator_mcp.py commander/tests/test_mcp_entrypoints.py
```

---

## Task 8: D2 — `_kill_orphan_brains` → recorded PID (Python)

**Files:** Modify `commander/src/ironclaude/main.py`; Test
`commander/tests/test_kill_orphan_brains.py` (create).

**Depends on:** Task 2 (same file — order after A2).

**Step 1 (RED):** Create `test_kill_orphan_brains.py`: call
`_kill_orphan_brains(recorded_brain_pid=<int>)` with a monkeypatched `_logged_kill`
(record calls); assert it `_logged_kill`s the recorded pid; assert pgrep output is NOT
used to select a SIGTERM (patch `subprocess.run` to return two fake pids for pgrep and
assert neither is passed to `_logged_kill`). Run:
```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_kill_orphan_brains.py -x
```
Expected: FAIL (current sig takes no pid; kills pgrep output).

**Step 2 (GREEN):** Rework `_kill_orphan_brains` (:1186-1203):
```python
def _kill_orphan_brains(recorded_brain_pid: int | None = None) -> None:
    """Belt-and-suspenders: terminate a surviving Brain subprocess by its RECORDED
    PID (never by pattern). pgrep is used only to LOG residue for diagnostics."""
    if recorded_brain_pid:
        try:
            _logged_kill(recorded_brain_pid, signal.SIGTERM,
                         f"kill_orphan_brain recorded_pid={recorded_brain_pid}")
            logger.info(f"Signalled recorded brain PID {recorded_brain_pid}")
            time.sleep(2)
        except (ProcessLookupError, PermissionError) as e:
            logger.warning(f"Could not signal recorded brain PID {recorded_brain_pid}: {e}")
    try:
        result = subprocess.run(
            ["pgrep", "-f", "claude.*stream-json.*Orchestrator"],
            capture_output=True, text=True, timeout=5,
        )
        residue = [p for p in result.stdout.strip().split() if p.strip()]
        if residue:
            logger.warning("Brain pattern residue after recorded-PID kill (NOT killed): %s", residue)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Brain residue pgrep check failed: {e}")
```
Update the caller in `_handle_restart` (:1245) to pass `_daemon.brain._brain_pid` (the
recorded pid, per :1269).

**Step 3 (verify):**
```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_kill_orphan_brains.py tests/test_signal_handler.py -x
```
Expected: PASS.

**Step 4 (stage):**
```bash
git -C /Users/roberthyatt/Code/ironclaude add -- commander/src/ironclaude/main.py commander/tests/test_kill_orphan_brains.py
```

---

## Task 9: B — idle-worker TTL (Python)

**Files:** Modify `commander/src/ironclaude/main.py`,
`commander/src/ironclaude/config.py`, `config/ironclaude.json.example`,
`commander/src/ironclaude/notifications.py`; Test
`commander/tests/test_idle_worker_ttl.py` (create).

**Depends on:** Task 8 (main.py order), Task 1 (config order).

**Grounded fact correction (drives this task):** the `.done` marker is DELETED on
delivery (`main.py:4230-4238`), so marker presence is not "idle for N min". The correct
per-worker activity signal is the tmux pipe-pane log mtime via
`self.tmux.get_log_mtime(session_name, ssh_host=, remote_log_dir=)` (`tmux_manager.py:514-534`).
The seam finalize downgrades terminal→non-terminal on a LIVE session
(`orchestrator_mcp.py:3618-3627`) and signals nothing; the ONLY live-worker teardown is
`tmux.kill_session` BEFORE the terminal finalize (stuck path `main.py:4009-4025`).

**Step 1 (RED):** Create `test_idle_worker_ttl.py` using the daemon `__new__` fixture
pattern from `test_main_validate.py`, explicitly setting EVERY attr the tested path
reads (operator `__new__`-fixture rule): `_worker_idle_since={}`, `config`, `tmux`
(MagicMock with `get_log_mtime` + `kill_session`), `brain`, `slack`, `registry`,
`_finalize_drift_retry={}`, `_finalize_recovery_alerted=set()`,
`_session_died_notified=set()`, and mock `_finalize_and_release_worker`,
`_drive_finalization_recovery`, `_routine_prompt_active`. Assert:
- (a) first idle sighting (marker present) sets `_worker_idle_since[wid]` (via `setdefault`);
- (b) armed + `now-armed >= ttl` + pane-log mtime NOT after arm + `_routine_prompt_active`
  False → `_reap_idle_worker` runs the exact ordered sequence: `_finalize_and_release_worker(wid, reason="idle-ttl", terminal=False)` → `_drive_finalization_recovery` → `tmux.kill_session(session, ssh_host=…)` → `_finalize_and_release_worker(wid, reason="idle-ttl", terminal=True)` → `_drive_finalization_recovery`; assert via `attach_mock` call order;
- (c) `_drive_finalization_recovery` (first call) returns `"held"` → `kill_session.assert_not_called()` and `_worker_idle_since[wid]` still set (recovery lock not stranded);
- (d) armed but pane-log mtime > arm + `IDLE_ACTIVITY_GRACE_SECONDS` (worker active/reused) → `_worker_idle_since` popped, no reap;
- (e) `idle_worker_ttl_seconds`=0 → never reaps;
- (f) `get_log_mtime` returns None → never reaps (fail-safe).
Monkeypatch the clock. Run:
```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_idle_worker_ttl.py -x
```
Expected: FAIL.

**Step 2 (GREEN — state/config/helpers):**
- `main.py` module const `IDLE_ACTIVITY_GRACE_SECONDS = 5.0`.
- `__init__` (near :1477/:1498): `self._worker_idle_since: dict[str, float] = {}`.
- `config.py` `DEFAULTS`: `"idle_worker_ttl_seconds": 1800,`; `config/ironclaude.json.example` add the same.
- `notifications.py`: `format_worker_idle_ttl_reaped(worker_id, minutes)` one-line Slack helper.
- New daemon method `_reap_idle_worker(self, worker_id, session_name, ssh_host, idle_seconds)`:
  ```python
      pre = self._finalize_and_release_worker(worker_id, reason="idle-ttl", terminal=False)
      disp = self._drive_finalization_recovery(worker_id, pre)
      if disp in ("retrying", "held", "surfaced"):
          logger.info(f"Idle-TTL reap deferred for {worker_id} (disposition={disp}); leaving running")
          return  # do not kill mid-recovery; clock retained, re-evaluated next cycle
      self.tmux.kill_session(session_name, ssh_host=ssh_host)
      outcome = self._finalize_and_release_worker(worker_id, reason="idle-ttl", terminal=True)
      self._drive_finalization_recovery(worker_id, outcome)
      _w = self.registry.get_worker(worker_id)
      if isinstance(_w, dict) and _w.get("status") == "completed":
          self.registry.log_event("worker_finished", worker_id=worker_id)
      self.slack.post_message(format_worker_idle_ttl_reaped(worker_id, int(idle_seconds // 60)))
      self.brain.send_message(
          f"[SWEEP] Worker {worker_id} reaped after {int(idle_seconds//60)} min idle; "
          "reviewed work integrated/rescued. Spawn a replacement if the objective is unfinished."
      )
      self._worker_idle_since.pop(worker_id, None)
  ```

**Step 3 (GREEN — check_workers wiring):** In `check_workers`, per worker, BEFORE the
`if marker_exists:` branch:
```python
            ttl = self.config.get("idle_worker_ttl_seconds", 1800)
            armed = self._worker_idle_since.get(worker_id)
            if armed is not None and ttl and ttl > 0:
                mtime = self.tmux.get_log_mtime(session_name, ssh_host=ssh_host, remote_log_dir=remote_log_dir)
                if mtime is not None and mtime > armed + IDLE_ACTIVITY_GRACE_SECONDS:
                    self._worker_idle_since.pop(worker_id, None)  # active/reused → disarm
                elif (mtime is not None and time.time() - armed >= ttl
                      and not self._routine_prompt_active(worker_id)):
                    self._reap_idle_worker(worker_id, session_name, ssh_host, time.time() - armed)
                    continue
                # mtime is None → no evidence of quiet → never reap (fail-safe)
```
In the `if marker_exists:` branch, FIRST line: `self._worker_idle_since.setdefault(worker_id, time.time())` (arm once; survives the marker deletion later in the branch). Prune: after the `for worker in running_workers` loop in `check_workers` (the loop over `running_ids` context at :4196), add
`for wid in list(self._worker_idle_since):` `if wid not in running_ids: self._worker_idle_since.pop(wid, None)`.
Do NOT add the prune at :3942-3964 (dead `check_stuck_workers`).

**Step 4 (verify):**
```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_idle_worker_ttl.py tests/test_main_validate.py tests/test_notifications.py -x
```
Expected: PASS.

**Step 5 (stage):**
```bash
git -C /Users/roberthyatt/Code/ironclaude add -- commander/src/ironclaude/main.py commander/src/ironclaude/config.py config/ironclaude.json.example commander/tests/test_idle_worker_ttl.py commander/src/ironclaude/notifications.py
```

---

## Task 10: F — configurable embedding backend, local default (TS)

**Files:** Modify `worker/mcp-servers/episodic-memory/src/embeddings.ts`; Test
`worker/mcp-servers/episodic-memory/src/__tests__/embedding-backend.test.ts` (create).
(em `dist/mcp-server.js` rebuilt but NOT staged — untracked/gitignored.)

**Depends on:** Task 3 (em src edited first; both edit em, order for a clean rebuild).

**Step 1 (RED):** Create `embedding-backend.test.ts`, `vi.mock('@xenova/transformers')`
for determinism: (a) `IC_EMBEDDING_BACKEND` unset → `getEmbeddingBackend()`==='local';
(b) `'ollama'` + mocked failing `fetch` → `generateEmbedding` falls back to local,
returns a 384-length vector; (c) `'ollama'` + mocked ok `/api/embeddings` → returns the
remote vector. Run:
```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/episodic-memory && npm run build && npx vitest run embedding-backend
```
Expected: FAIL.

**Step 2 (GREEN):** Add `getEmbeddingBackend()` (`process.env.IC_EMBEDDING_BACKEND ?? 'local'`);
refactor `generateEmbedding` to dispatch: `local` → existing Xenova pipeline (unchanged);
`ollama` → POST `{model: process.env.IC_EMBEDDING_MODEL ?? 'all-minilm', prompt: truncated}`
to `${process.env.IC_EMBEDDING_BASE_URL ?? 'http://localhost:11434'}/api/embeddings`,
return `json.embedding`; on ANY error `console.error` + fall through to local. Keep
`generateExchangeEmbedding` delegating. Add a top-of-file comment: flipping to `ollama`
requires a one-time re-index (Xenova vs Ollama MiniLM vectors are not bit-identical) —
operator step, not automatic. Rebuild.

**Step 3 (verify):**
```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/episodic-memory && npm run build && npx vitest run embedding-backend && npx vitest run
```
Expected: PASS.

**Step 4 (stage — src + test only):**
```bash
git -C /Users/roberthyatt/Code/ironclaude add -f -- worker/mcp-servers/episodic-memory/src/embeddings.ts worker/mcp-servers/episodic-memory/src/__tests__/embedding-backend.test.ts
```

---

## Task 11: G — collapse the fork wrapper to in-process load + preserve cleanup (TS)

**Files:** Modify `worker/mcp-servers/episodic-memory/cli/mcp-server-wrapper.js`,
`worker/mcp-servers/state-manager/cli/mcp-server-wrapper.js`,
`worker/mcp-servers/workspace-manager/cli/mcp-server-wrapper.js`.

**Depends on:** Tasks 3, 4, 5, 10 (final bundles built first).

No tests required: launch wrapper (no unit harness); verified by build + in-process
smoke. DEPLOY to plugin caches is a separate operator-gated step.

**Step 1 (em + sm):** After `ensureBuildComplete()`/pre-spawn setup, REPLACE the
`const child = spawn(process.execPath,[bundle],{...})` + its SIGTERM/SIGINT/exit/child.on
handling with:
```javascript
  process.env.CLAUDE_PPID = String(process.ppid); // set BEFORE import (session resolution reads it)
  for (const sig of ['SIGTERM', 'SIGINT', 'SIGHUP']) {
    process.on(sig, () => { try { log(`[signal] ${sig} — exiting`); } catch {} process.exit(0); });
  }
  await import(pathToFileURL(bundle).href); // em/sm bundles call main() unconditionally
```
Keep `ensureBuildComplete` (npm-chatter isolation runs before the import) and the
existing `process.on('exit', () => { try { unlinkSync(PENDING_MARKER) } catch {} })`
(the SIGTERM/SIGINT/SIGHUP handlers call `process.exit`, which fires that `'exit'`
cleanup — this preserves the marker unlink the fork previously got). Add
`import { pathToFileURL } from 'node:url';` (or `require` per the wrapper's module
style).

**Step 2 (ws):** ws's bundle guards its own start on `argv[1]===import.meta.url`
(won't fire under import) but EXPORTS `startWorkspaceManagerServer`. So the ws wrapper:
```javascript
  process.env.CLAUDE_PPID = String(process.ppid);
  const mod = await import(pathToFileURL(bundle).href);
  if (typeof mod.startWorkspaceManagerServer !== 'function')
    throw new Error('workspace-manager bundle lacks startWorkspaceManagerServer export');
  await mod.startWorkspaceManagerServer();
```
(ws wrapper has no PENDING_MARKER, so no unlink handler needed; a SIGTERM/SIGINT/SIGHUP
→ `process.exit(0)` handler is still added for a clean shutdown.) No ws src/dist edit
and no allowed_files change — the export-call avoids widening the entry guard.

**Step 3 (verify export + unconditional starts):**
```bash
grep -n -e "export" -e "startWorkspaceManagerServer" /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager/dist/index.js
```
Expected: a line exporting `startWorkspaceManagerServer` (confirms the ws export-call
target exists). For em/sm confirm the bundle calls main() unconditionally (no argv
guard):
```bash
grep -n -e "import.meta.url" -e "argv" /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/episodic-memory/dist/mcp-server.js /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/state-manager/dist/index.js
```
Expected: NO `argv[1]===import.meta.url` start-guard around main() (em/sm start on
import). If either shows such a guard, STOP and report (would need that server's src +
dist added — not anticipated).

**Step 4 (in-process smoke, per server):**
```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/episodic-memory && node cli/mcp-server-wrapper.js < /dev/null
```
Expected: starts, logs the server-running line, exits code 0 on stdin EOF (self-exit
from Task 3), and NO child `dist/*.js` node proc was spawned (one proc, not two).
Repeat per server (sm with `STATE_MANAGER_DB_PATH=/tmp/ic-smoke-sm.db`). NOTE: live
sessions run the CACHE wrapper; DEPLOY (copy to `~/.claude` + `~/.codex` caches) is a
separate operator-gated step, recorded for later.

**Step 5 (stage):**
```bash
git -C /Users/roberthyatt/Code/ironclaude add -f -- worker/mcp-servers/episodic-memory/cli/mcp-server-wrapper.js worker/mcp-servers/state-manager/cli/mcp-server-wrapper.js worker/mcp-servers/workspace-manager/cli/mcp-server-wrapper.js
```

---

## Deferred / recorded (NOT in this loop)

- Pre-existing Boy-Scout finding (record, do not fix here): the drift/session-died
  per-worker state (`_finalize_drift_retry`, `_session_died_notified`,
  `_finalize_recovery_alerted`) is pruned ONLY at `main.py:3956-3964` inside the
  unwired `check_stuck_workers`, so it never clears in prod; and `check_stuck_workers`
  (the d1132 1 h stuck-killer) has no call site. Re-wiring it re-enables 1 h kills —
  an operator decision for a separate loop.
- DEPLOY (copy rebuilt wrappers/bundles to both plugin caches + Commander restart) and
  version bump / CHANGELOG / commit / push are all operator-gated, separate from this
  loop. Commit-sequencing vs the pending boy-scout v1.1.11 (both touch main.py) is
  surfaced at commit time.
