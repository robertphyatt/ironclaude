# Heartbeat Worker Listing Fix Design

> **Created:** 2026-05-29
> **Status:** Design Complete

## Summary

The daemon's `post_heartbeat()` only queries `status='running'` workers from the DB. When `check_workers()` incorrectly marks workers as 'completed' (transient tmux check failure, SSH resolution gaps), the heartbeat silently drops those workers. The user sees 1 worker in the heartbeat despite 6 being alive.

Additionally, `_get_worker_workflow_stage()` has a TOCTOU crash: `session_id_file.read_text()` is called without try/except after `exists()`. If the file disappears in the gap, the exception propagates through the heartbeat loop and aborts it mid-iteration — workers processed after the crash never appear in the heartbeat.

## Architecture

Use tmux_alive as ground truth instead of trusting DB status. The heartbeat queries a broader candidate set (running + recently-finished workers), then keeps only those with live tmux sessions. This is independent of whether `check_workers()` correctly maintained the status field.

## Components

### 1. `WorkerRegistry.get_recent_workers()` — new method in worker_registry.py

```python
def get_recent_workers(self, lookback_hours: int = 1) -> list[dict]:
    """Running workers + recently-finished (within lookback window)."""
    rows = self._conn.execute(
        """SELECT * FROM workers
           WHERE status = 'running'
           OR (finished_at IS NOT NULL AND finished_at > datetime('now', ?))""",
        (f"-{lookback_hours} hours",)
    ).fetchall()
    return [dict(r) for r in rows]
```

The 1-hour window catches workers incorrectly marked 'completed'/'failed'/'killed' that are still alive, without dragging in ancient history.

### 2. `post_heartbeat()` loop rewrite — main.py

Replace:
```python
running = self.registry.get_running_workers()
worker_details = []
for w in running:
    stage = self._get_worker_workflow_stage(w["tmux_session"])
    worker_details.append({...})
```

With:
```python
candidates = self.registry.get_recent_workers()
worker_details = []
for w in candidates:
    ssh_host, _ = self._resolve_worker_ssh(w)
    if not self.tmux.has_session(w["tmux_session"], ssh_host=ssh_host):
        continue
    stage = self._get_worker_workflow_stage(w["tmux_session"], ssh_host=ssh_host)
    worker_details.append({
        "id": w["id"],
        "description": w.get("description"),
        "workflow_stage": stage,
    })
```

Also fixes the ssh_host omission: current code never passes ssh_host to `_get_worker_workflow_stage` in the heartbeat, so remote workers always get stage=None.

### 3. TOCTOU hardening — `_get_worker_workflow_stage()` in main.py

Wrap the `read_text()` call (currently line 1189) in try/except:
```python
try:
    session_id = session_id_file.read_text().strip()
except OSError:
    return None
```

This prevents a file-disappeared race from aborting the heartbeat loop mid-iteration.

## Data Flow

1. Heartbeat interval elapses
2. `get_recent_workers()` returns `status='running'` + recently-finished candidates
3. For each candidate: resolve `ssh_host` via `_resolve_worker_ssh()`
4. `has_session()` with correct `ssh_host` — drop if dead
5. `_get_worker_workflow_stage()` with correct `ssh_host` — returns stage or None (never throws)
6. `format_heartbeat()` formats the alive workers list

## Error Handling

- `has_session()` failure: session is dead, worker excluded — correct behavior
- `_get_worker_workflow_stage()` OSError: returns None, worker still included with stage="unknown"
- `_get_worker_workflow_stage()` sqlite3.Error: already caught, returns None
- Workers in lookback window but truly dead: excluded by `has_session()` check

## Testing Strategy

- Unit test `get_recent_workers()`: verify returns `status='running'` workers AND recently-finished workers within window, excludes old finished workers
- Unit test heartbeat loop: mock `get_recent_workers()` returning 6 workers where `has_session()` returns True for all — verify all 6 appear in formatted message
- Unit test TOCTOU path: mock `session_id_file.read_text()` to raise `FileNotFoundError` — verify function returns None without raising
- Integration: verify heartbeat message matches tmux ls output for ic-* sessions

## Implementation Notes

- Scope: `hold` — do NOT touch `check_workers()`, do NOT add heartbeat-on-spawn triggers
- Files: `commander/src/ironclaude/worker_registry.py`, `commander/src/ironclaude/main.py`
- The `grader enforcement` block after the heartbeat post (checking `if not running`) should continue to use `get_running_workers()` — it's checking DB state for the nudge logic, not for display purposes. Change `if not running:` to `if not worker_details:` to reflect alive workers.
