# Heartbeat Worker Listing Fix Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** Fix the heartbeat to list all workers with live tmux sessions, not just those with `status='running'` in the DB.

**Architecture:** Three changes — add `WorkerRegistry.get_recent_workers()` to query beyond `status='running'`, rewrite `post_heartbeat()` to use tmux_alive as ground truth with correct ssh_host handling, and harden `_get_worker_workflow_stage` against a TOCTOU crash that aborts the heartbeat loop mid-iteration.

**Tech Stack:** Python, SQLite (sqlite3), unittest.mock

---

## Task 1: Add `get_recent_workers()` to WorkerRegistry

**Files:**
- Modify: `commander/src/ironclaude/worker_registry.py`
- Test: `commander/tests/test_daemon.py`

**Step 1: Write tests for `get_recent_workers()`**

Add to `commander/tests/test_daemon.py` after the `TestGetWorkerWorkflowStage` class. Tests require a real SQLite connection (not a mock) to exercise the SQL:

```python
class TestGetRecentWorkers:
    def _make_registry(self, tmp_path):
        from ironclaude.db import init_db
        from ironclaude.worker_registry import WorkerRegistry
        conn = init_db(str(tmp_path / "ic.db"))
        return WorkerRegistry(conn)

    def test_returns_running_workers(self, tmp_path):
        """Returns workers with status='running'."""
        reg = self._make_registry(tmp_path)
        reg.register_worker("w1", "claude-sonnet", "ic-w1", repo="/repo")
        results = reg.get_recent_workers()
        assert len(results) == 1
        assert results[0]["id"] == "w1"

    def test_returns_recently_finished_workers(self, tmp_path):
        """Returns workers finished within lookback window."""
        import sqlite3
        reg = self._make_registry(tmp_path)
        reg.register_worker("w1", "claude-sonnet", "ic-w1", repo="/repo")
        # Mark as completed with a recent finished_at
        reg._conn.execute(
            "UPDATE workers SET status='completed', finished_at=datetime('now', '-30 minutes') WHERE id='w1'"
        )
        reg._conn.commit()
        results = reg.get_recent_workers(lookback_hours=1)
        assert len(results) == 1
        assert results[0]["id"] == "w1"

    def test_excludes_old_finished_workers(self, tmp_path):
        """Excludes workers finished outside the lookback window."""
        reg = self._make_registry(tmp_path)
        reg.register_worker("w1", "claude-sonnet", "ic-w1", repo="/repo")
        reg._conn.execute(
            "UPDATE workers SET status='completed', finished_at=datetime('now', '-2 hours') WHERE id='w1'"
        )
        reg._conn.commit()
        results = reg.get_recent_workers(lookback_hours=1)
        assert len(results) == 0

    def test_excludes_finished_null_timestamp(self, tmp_path):
        """Excludes workers with status='completed' but NULL finished_at (stale data)."""
        reg = self._make_registry(tmp_path)
        reg.register_worker("w1", "claude-sonnet", "ic-w1", repo="/repo")
        reg._conn.execute("UPDATE workers SET status='completed' WHERE id='w1'")
        reg._conn.commit()
        results = reg.get_recent_workers(lookback_hours=1)
        assert len(results) == 0

    def test_returns_both_running_and_recent_finished(self, tmp_path):
        """Returns mix of running and recently-finished workers."""
        reg = self._make_registry(tmp_path)
        reg.register_worker("w1", "claude-sonnet", "ic-w1", repo="/repo")
        reg.register_worker("w2", "claude-sonnet", "ic-w2", repo="/repo")
        reg._conn.execute(
            "UPDATE workers SET status='completed', finished_at=datetime('now', '-10 minutes') WHERE id='w2'"
        )
        reg._conn.commit()
        results = reg.get_recent_workers(lookback_hours=1)
        ids = {r["id"] for r in results}
        assert ids == {"w1", "w2"}
```

**Step 2: Run tests to verify they fail**

```bash
cd commander && .venv/bin/python -m pytest tests/test_daemon.py::TestGetRecentWorkers -v
```

Expected: `AttributeError: 'WorkerRegistry' object has no attribute 'get_recent_workers'` (or 5 failures)

**Step 3: Implement `get_recent_workers()` in worker_registry.py**

In `commander/src/ironclaude/worker_registry.py`, add after `get_running_workers_by_type` (after line 123):

```python
def get_recent_workers(self, lookback_hours: int = 1) -> list[dict]:
    """Running workers + recently-finished within lookback window.

    Includes status='running' AND workers finished within the last
    lookback_hours hours. Used by heartbeat to find workers that are
    alive even if incorrectly marked completed by check_workers().
    """
    rows = self._conn.execute(
        """SELECT * FROM workers
           WHERE status = 'running'
           OR (finished_at IS NOT NULL AND finished_at > datetime('now', ?))""",
        (f"-{lookback_hours} hours",)
    ).fetchall()
    return [dict(r) for r in rows]
```

**Step 4: Run tests to verify they pass**

```bash
cd commander && .venv/bin/python -m pytest tests/test_daemon.py::TestGetRecentWorkers -v
```

Expected: 5 passed

**Step 5: Stage changes**

```bash
git add commander/src/ironclaude/worker_registry.py commander/tests/test_daemon.py
```

Expected: changes staged

---

## Task 2: Harden `_get_worker_workflow_stage` against TOCTOU crash

**Files:**
- Modify: `commander/src/ironclaude/main.py`
- Test: `commander/tests/test_daemon.py`

**Step 1: Write test for TOCTOU crash**

Add to `TestGetWorkerWorkflowStage` class in `commander/tests/test_daemon.py`:

```python
    def test_returns_none_when_session_file_disappears_before_read(self, daemon, tmp_path):
        """Returns None (no exception) when session file exists() but read_text() raises OSError."""
        from pathlib import Path
        from unittest.mock import patch, MagicMock
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        session_id = "abcdef01-2345-6789-abcd-ef0123456789"
        session_file = claude_dir / "ironclaude-session-12345.id"
        session_file.write_text(session_id)
        daemon.tmux.list_pane_pid.return_value = "12345"

        # Simulate file disappearing between exists() and read_text()
        original_read_text = Path.read_text
        def patched_read_text(self_path, *args, **kwargs):
            if self_path == session_file:
                raise FileNotFoundError("file vanished")
            return original_read_text(self_path, *args, **kwargs)

        with patch.object(Path, "read_text", patched_read_text):
            result = daemon._get_worker_workflow_stage("ic-w1", _claude_dir=claude_dir)

        assert result is None
```

**Step 2: Run test to verify it fails**

```bash
cd commander && .venv/bin/python -m pytest tests/test_daemon.py::TestGetWorkerWorkflowStage::test_returns_none_when_session_file_disappears_before_read -v
```

Expected: FAIL — `FileNotFoundError` raised instead of returning None

**Step 3: Wrap `read_text()` in try/except in main.py**

In `commander/src/ironclaude/main.py`, find `_get_worker_workflow_stage`. The section reads:

```python
        session_id_file = claude_dir / f"ironclaude-session-{pane_pid}.id"
        if not session_id_file.exists():
            return None
        session_id = session_id_file.read_text().strip()
        if len(session_id) != 36:
            return None
```

Replace with:

```python
        session_id_file = claude_dir / f"ironclaude-session-{pane_pid}.id"
        if not session_id_file.exists():
            return None
        try:
            session_id = session_id_file.read_text().strip()
        except OSError:
            return None
        if len(session_id) != 36:
            return None
```

**Step 4: Run test to verify it passes**

```bash
cd commander && .venv/bin/python -m pytest tests/test_daemon.py::TestGetWorkerWorkflowStage -v
```

Expected: all existing + new test pass

**Step 5: Stage changes**

```bash
git add commander/src/ironclaude/main.py commander/tests/test_daemon.py
```

Expected: changes staged

---

## Task 3: Rewrite `post_heartbeat()` to use tmux_alive as ground truth

**Depends on:** Task 1 (requires `get_recent_workers()`)

**Files:**
- Modify: `commander/src/ironclaude/main.py`
- Test: `commander/tests/test_daemon.py`

**Step 1: Write tests for the new heartbeat behavior**

Add to `commander/tests/test_daemon.py`:

```python
class TestPostHeartbeat:
    def test_heartbeat_includes_all_alive_workers(self, daemon):
        """Heartbeat posts message containing all workers with live tmux sessions."""
        workers = [
            {"id": f"w{i}", "tmux_session": f"ic-w{i}", "description": f"task {i}", "machine": None}
            for i in range(1, 7)
        ]
        daemon.registry.get_recent_workers.return_value = workers
        daemon.tmux.has_session.return_value = True
        daemon._last_heartbeat = 0  # force heartbeat to fire
        daemon.post_heartbeat()
        daemon.slack.post_message.assert_called_once()
        msg = daemon.slack.post_message.call_args[0][0]
        for i in range(1, 7):
            assert f"w{i}" in msg

    def test_heartbeat_excludes_dead_sessions(self, daemon):
        """Heartbeat excludes workers where has_session returns False."""
        workers = [
            {"id": "w1", "tmux_session": "ic-w1", "description": "task 1", "machine": None},
            {"id": "w2", "tmux_session": "ic-w2", "description": "task 2", "machine": None},
        ]
        daemon.registry.get_recent_workers.return_value = workers
        daemon.tmux.has_session.side_effect = lambda name, ssh_host=None: name == "ic-w1"
        daemon._last_heartbeat = 0
        daemon.post_heartbeat()
        msg = daemon.slack.post_message.call_args[0][0]
        assert "w1" in msg
        assert "w2" not in msg

    def test_heartbeat_no_workers_posts_no_active(self, daemon):
        """Heartbeat posts 'No active workers' when all sessions are dead."""
        daemon.registry.get_recent_workers.return_value = [
            {"id": "w1", "tmux_session": "ic-w1", "description": "done", "machine": None}
        ]
        daemon.tmux.has_session.return_value = False
        daemon._last_heartbeat = 0
        daemon.post_heartbeat()
        msg = daemon.slack.post_message.call_args[0][0]
        assert "No active workers" in msg

    def test_heartbeat_respects_interval(self, daemon):
        """Heartbeat does not fire before interval elapses."""
        daemon._last_heartbeat = time.time()
        daemon.post_heartbeat()
        daemon.slack.post_message.assert_not_called()
```

**Step 2: Run tests to verify they fail**

```bash
cd commander && .venv/bin/python -m pytest tests/test_daemon.py::TestPostHeartbeat -v
```

Expected: AttributeError on `get_recent_workers` or assertion failures (4 failures)

**Step 3: Rewrite the heartbeat loop in main.py**

In `commander/src/ironclaude/main.py`, find `post_heartbeat()`. Replace the block from `running = self.registry.get_running_workers()` through the `format_heartbeat` call, and update the grader-check condition:

Current (lines 1398-1409 and 1412):
```python
        running = self.registry.get_running_workers()
        worker_details = []
        for w in running:
            stage = self._get_worker_workflow_stage(w["tmux_session"])
            worker_details.append({
                "id": w["id"],
                "description": w.get("description"),
                "workflow_stage": stage,
            })

        brain_usage = self.brain.get_token_usage() if self.brain is not None else None
        self.slack.post_message(format_heartbeat(worker_details, brain_usage=brain_usage))

        # Grader enforcement: if no workers but directives exist, nudge the Brain
        if not running and self._db is not None:
```

Replace with:
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

        brain_usage = self.brain.get_token_usage() if self.brain is not None else None
        self.slack.post_message(format_heartbeat(worker_details, brain_usage=brain_usage))

        # Grader enforcement: if no alive workers but directives exist, nudge the Brain
        if not worker_details and self._db is not None:
```

**Step 4: Run all heartbeat tests**

```bash
cd commander && .venv/bin/python -m pytest tests/test_daemon.py::TestPostHeartbeat -v
```

Expected: 4 passed

**Step 5: Run full test suite to check for regressions**

```bash
cd commander && .venv/bin/python -m pytest tests/ -v
```

Expected: all tests pass (or pre-existing failures only — no new failures)

**Step 6: Stage changes**

```bash
git add commander/src/ironclaude/main.py commander/tests/test_daemon.py
```

Expected: changes staged
