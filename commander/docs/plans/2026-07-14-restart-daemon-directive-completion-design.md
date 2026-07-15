# restart_daemon Directive-Completion Race Design

> **Created:** 2026-07-14
> **Status:** Design Complete

## Summary

`restart_daemon()` currently forks a detached watchdog that sends SIGHUP to the daemon immediately, killing the calling Brain/MCP process before it can make a follow-up `update_directive_status(directive_id, "completed")` call. When a directive's interpretation is "restart the daemon after X," the new Brain session finds that same directive still `in_progress`/`confirmed` — surfaced via the Brain's own Attention Sweep Protocol (`get_directives(status='in_progress')`) and the daemon's `check_confirmed_directives`/`check_idle_enforcement` polling — and calls `restart_daemon()` again, producing an infinite restart loop (~12 restarts observed at ~55s intervals on 2026-07-14).

The fix adds an optional `directive_id` parameter to `restart_daemon()`, mirroring the existing `kill_worker(directive_id=...)` precedent. When provided, the directive is atomically marked `completed` in the DB *before* `os.fork()` — i.e., before any possibility of SIGHUP killing the calling process. This closes the loop at its actual re-trigger surface: every downstream query that could re-surface the directive filters on `status IN ('confirmed', 'in_progress')`, so a `completed` directive is invisible to all of them.

## Architecture

No change to the existing double-fork/watchdog/self-healing architecture (guards → status dir setup → `os.fork()` → detached watchdog → SIGHUP → lock-release/reacquire monitoring). The only change is a new guard-clause-shaped step inserted between the existing guards and the fork point.

Placement matters: the directive-completion write must happen in the **parent process, before `os.fork()`** — never inside the forked child/watchdog. `self._db` is a `sqlite3.Connection` bound by `check_same_thread=True` to the thread that created it (the main thread), and both forked children call `os._exit()` on every exit path, bypassing Python cleanup. Writing from the child would be a classic sqlite-fork hazard; writing from the parent (still the main thread, pre-fork) is safe and matches the connection's ownership model.

## Components

**`OrchestratorTools.restart_daemon(self, directive_id: int | None = None) -> str`** (orchestrator_mcp.py:3553)

New guard inserted after the existing four guards (PID file exists, lock held, process alive, Slack reachable) and before the `status_dir.mkdir(...)` / `os.fork()` block:

```python
if directive_id is not None:
    row = self._db.execute(
        "SELECT id FROM directives WHERE id=?", (directive_id,)
    ).fetchone()
    if row is None:
        return json.dumps({
            "ok": False,
            "error": f"directive {directive_id} not found — refusing to restart",
        })
    self._db.execute(
        "UPDATE directives SET status='completed', updated_at=datetime('now') WHERE id=?",
        (directive_id,),
    )
    self._db.commit()
    logger.info(f"restart_daemon: pre-marked directive {directive_id} completed before SIGHUP")
```

This is a raw SQL write, not a call to the existing `update_directive_status()` helper — that helper also swaps Slack emoji reactions via a network call, which is unacceptable latency/failure risk in this timing-critical pre-SIGHUP window. Trade-off: the Slack reaction on the directive message will not flip to reflect completion; only the DB status changes.

**MCP tool wrapper** (orchestrator_mcp.py:4156-4159)

```python
@mcp.tool()
def restart_daemon(directive_id: int | None = None) -> str:
    """Send SIGHUP to the IronClaude daemon, triggering a graceful self-restart.

    Always pass directive_id when restarting the daemon as part of completing
    a directive — this is the only safe way to mark a restart directive
    complete given that the Brain dies when SIGHUP fires.
    """
    return tools.restart_daemon(directive_id)
```

**Docstring update** on the underlying method: document the same "always pass directive_id" guidance, plus the guard behavior (invalid directive_id refuses the restart rather than proceeding silently) and the pre-fork-write safety rationale.

## Data Flow

1. Brain determines a directive's interpretation requires a daemon restart (e.g., "restart daemon after X completes").
2. Brain calls `restart_daemon(directive_id=<id>)` instead of the two-step `update_directive_status` + `restart_daemon` sequence.
3. Guards run in order: PID file → lock held → process alive → Slack reachable → **directive exists** (new). Any failure returns `{"ok": false, "error": ...}` immediately, no fork, no DB write.
4. If all guards pass: DB write (`UPDATE ... SET status='completed'`, commit) happens synchronously in the parent, still pre-fork.
5. `os.fork()` proceeds exactly as today: detached double-fork watchdog, SIGHUP, lock-release/reacquire monitoring, self-heal fallback.
6. Parent returns `{"ok": true, "status": "restart_initiated", ...}` — by this point the directive is already durably `completed` in the DB (WAL-mode commit), regardless of what happens to the calling process afterward.
7. New Brain session starts; `get_directives(status='in_progress')` and the daemon's polling checks no longer surface this directive, since its status is `completed`. No re-trigger.

## Error Handling

- Invalid `directive_id` (no matching row): new guard returns `{"ok": false, "error": "directive {id} not found — refusing to restart"}` without forking or writing — same shape and same fail-closed philosophy as the existing PID/lock/liveness/Slack guards. This is a deliberate choice: an invalid `directive_id` indicates a caller bug, and the existing guard clauses in this method already treat precondition failures as hard stops, not best-effort continuations.
- `directive_id=None` (default): behavior is byte-for-byte identical to today — no new guard is evaluated, no DB write occurs.
- A restart that fails *after* the pre-fork write (e.g., watchdog can't reacquire the lock, falls back to self-heal `subprocess.Popen`) leaves the directive marked `completed` even though the restart took a degraded path. This is accepted as consistent with the watchdog's own self-healing design — if the daemon is truly unreachable, there's no live Brain session left to loop on the stale directive anyway.

## Testing Strategy

`tests/test_brain_monitor.py` is not the relevant target — verified it only covers `BrainMonitor` (tmux-liveness class), which `brain_client.py` documents as superseded; zero coverage of directives or `restart_daemon`. The relevant and already-populated test class is `tests/test_orchestrator_mcp.py`, "Tests for restart_daemon MCP tool — detached watchdog pattern" (~line 4411), which already has fixtures (`tools`, `registry`, `mock_tmux`, `db_conn` — a real file-backed sqlite DB via `init_db`) and existing tests like `test_restart_daemon_missing_pid_file`, `test_restart_daemon_forks_and_returns_immediately`.

New tests to add there, mirroring the existing `kill_worker` directive_id test pattern (`test_kill_worker_fast_path_skips_grader_when_directive_completed`):

- `test_restart_daemon_marks_directive_completed_before_fork` — insert a directive row (`status='in_progress'`), call `restart_daemon(directive_id=...)` with all guards mocked to pass, assert the DB row's status is `'completed'` after the call.
- `test_restart_daemon_invalid_directive_id_refuses_restart` — call with a `directive_id` that has no matching row; assert `{"ok": false, ...}` is returned, `os.fork` (mocked) is never called, and no DB row is modified.
- `test_restart_daemon_none_directive_id_unchanged_behavior` — call with `directive_id=None` (or omitted); assert existing behavior (from the current test suite) is unaffected — no new guard evaluated, no DB write attempted.

## Implementation Notes

- Guard ordering: the new directive-existence check goes *last* among the guards (after PID/lock/liveness/Slack), since those are cheaper and more fundamental preconditions — no reason to hit the DB before confirming the daemon is even restartable.
- The `SELECT` + `UPDATE` + `commit()` sequence must stay entirely before `pid1 = os.fork()` (currently line 3613) — this is the one placement constraint that makes the whole fix correct.
- No changes needed to `_restart_watchdog()`, the double-fork logic, self-heal fallback, or any other MCP tool.
