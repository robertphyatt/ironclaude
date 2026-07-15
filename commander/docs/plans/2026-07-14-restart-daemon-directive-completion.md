# restart_daemon Directive-Completion Race Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** Add an optional `directive_id` parameter to `restart_daemon()` that atomically marks the directive `completed` in the DB before the pre-SIGHUP fork, closing the Brain restart-loop bug.

**Architecture:** One new guard clause (mirrors the existing PID/lock/liveness/Slack guards) inserted between the Slack guard and `os.fork()` in `OrchestratorTools.restart_daemon()`. When `directive_id` is provided: validate the directive exists (else refuse without forking), then `UPDATE ... SET status='completed'` + `commit()` in the parent/main thread, strictly before fork. The MCP tool wrapper passes the parameter through. No changes to the fork/watchdog/self-heal logic itself.

**Tech Stack:** Python, sqlite3, pytest with `unittest.mock.patch` (matching this file's existing `os.fork`/`fcntl.flock`/`os.kill` mocking conventions — no real forking or real file locks in tests).

---

## Task 1: Add directive_id parameter to restart_daemon (method + MCP wrapper + tests)

**Files:** (paths relative to the outer `ironclaude` git repo root)
- Modify: `commander/src/ironclaude/orchestrator_mcp.py:3553-3563` (method signature + docstring)
- Modify: `commander/src/ironclaude/orchestrator_mcp.py:3600-3613` (insert guard before fork)
- Modify: `commander/src/ironclaude/orchestrator_mcp.py:4156-4159` (MCP tool wrapper)
- Test: `commander/tests/test_orchestrator_mcp.py` (insert 3 new tests into `class TestRestartDaemon`, after `test_restart_daemon_refuses_when_slack_unreachable`, before `class TestRestartWatchdog:`)

### Step 1: Write the three new tests (RED)

In `tests/test_orchestrator_mcp.py`, find this exact text (the end of `test_restart_daemon_refuses_when_slack_unreachable` followed by the start of the next class):

```python
        with patch("ironclaude.orchestrator_mcp.PID_FILE", pid_file), \
             patch("fcntl.flock", side_effect=BlockingIOError), \
             patch("os.kill"), \
             patch("os.fork") as mock_fork:
            result = tools_with_slack.restart_daemon()
        data = json.loads(result)
        assert data["ok"] is False
        assert "Slack connection required" in data["error"]
        mock_fork.assert_not_called()


class TestRestartWatchdog:
```

Replace it with (adds three tests before `TestRestartWatchdog`):

```python
        with patch("ironclaude.orchestrator_mcp.PID_FILE", pid_file), \
             patch("fcntl.flock", side_effect=BlockingIOError), \
             patch("os.kill"), \
             patch("os.fork") as mock_fork:
            result = tools_with_slack.restart_daemon()
        data = json.loads(result)
        assert data["ok"] is False
        assert "Slack connection required" in data["error"]
        mock_fork.assert_not_called()

    def test_restart_daemon_marks_directive_completed_before_fork(self, tools, tmp_path, db_conn):
        """directive_id is marked completed in the DB before the fork."""
        pid_file = tmp_path / "ic-daemon.pid"
        pid_file.write_text("12345")
        tools._slack = MagicMock(is_reachable=MagicMock(return_value=True))
        cursor = db_conn.execute(
            "INSERT INTO directives (source_ts, source_text, interpretation, status) "
            "VALUES ('1.0', 'restart daemon', 'Restart daemon after X', 'in_progress')"
        )
        db_conn.commit()
        directive_id = cursor.lastrowid
        with patch("ironclaude.orchestrator_mcp.PID_FILE", pid_file), \
             patch("fcntl.flock", side_effect=BlockingIOError), \
             patch("os.kill"), \
             patch("os.fork", return_value=123), \
             patch("os.waitpid"), \
             patch("pathlib.Path.mkdir"):
            result = tools.restart_daemon(directive_id=directive_id)
        data = json.loads(result)
        assert data["ok"] is True
        row = db_conn.execute(
            "SELECT status FROM directives WHERE id=?", (directive_id,)
        ).fetchone()
        assert row["status"] == "completed"

    def test_restart_daemon_invalid_directive_id_refuses_restart(self, tools, tmp_path):
        """Unknown directive_id refuses the restart without forking."""
        pid_file = tmp_path / "ic-daemon.pid"
        pid_file.write_text("12345")
        tools._slack = MagicMock(is_reachable=MagicMock(return_value=True))
        with patch("ironclaude.orchestrator_mcp.PID_FILE", pid_file), \
             patch("fcntl.flock", side_effect=BlockingIOError), \
             patch("os.kill"), \
             patch("os.fork") as mock_fork:
            result = tools.restart_daemon(directive_id=999999)
        data = json.loads(result)
        assert data["ok"] is False
        assert "not found" in data["error"]
        mock_fork.assert_not_called()

    def test_restart_daemon_none_directive_id_unchanged_behavior(self, tools, tmp_path):
        """Omitting directive_id preserves current behavior exactly."""
        pid_file = tmp_path / "ic-daemon.pid"
        pid_file.write_text("12345")
        tools._slack = MagicMock(is_reachable=MagicMock(return_value=True))
        with patch("ironclaude.orchestrator_mcp.PID_FILE", pid_file), \
             patch("fcntl.flock", side_effect=BlockingIOError), \
             patch("os.kill"), \
             patch("os.fork", return_value=123) as mock_fork, \
             patch("os.waitpid"), \
             patch("pathlib.Path.mkdir"):
            result = tools.restart_daemon()
        data = json.loads(result)
        assert data["ok"] is True
        assert data["status"] == "restart_initiated"
        mock_fork.assert_called_once()


class TestRestartWatchdog:
```

### Step 2: Run tests, verify they fail

Run:
```bash
PYTHONUNBUFFERED=1 python -m pytest tests/test_orchestrator_mcp.py -k "marks_directive_completed_before_fork or invalid_directive_id_refuses_restart or none_directive_id_unchanged_behavior" -v
```

Expected: All 3 new tests FAIL. The first two fail with `TypeError: restart_daemon() got an unexpected keyword argument 'directive_id'`. The third fails the same way since it still passes through the same code path being exercised (it will actually pass today since it doesn't use the kwarg — confirm it passes; if it does, that's fine, it's a baseline regression check, not required to be RED). The key RED signal is the first two tests failing with `TypeError`.

### Step 3: Implement — method signature, docstring, and pre-fork guard

In `src/ironclaude/orchestrator_mcp.py`, find this exact text:

```python
    def restart_daemon(self) -> str:
        """Send SIGHUP to restart the daemon via a detached watchdog process.

        The MCP server is a grandchild of the daemon — when the daemon kills the
        brain subprocess on SIGHUP, this process dies too.  So we fork a fully
        detached watchdog (double-fork + setsid) that handles monitoring and
        self-healing independently.

        Returns immediately with {"ok": true, "status": "restart_initiated"}.
        Guard check failures return {"ok": false, "error": "..."} without forking.
        """
```

Replace it with:

```python
    def restart_daemon(self, directive_id: int | None = None) -> str:
        """Send SIGHUP to restart the daemon via a detached watchdog process.

        The MCP server is a grandchild of the daemon — when the daemon kills the
        brain subprocess on SIGHUP, this process dies too.  So we fork a fully
        detached watchdog (double-fork + setsid) that handles monitoring and
        self-healing independently.

        Always pass directive_id when restarting the daemon as part of completing
        a directive — this is the only safe way to mark a restart directive
        complete given that the Brain dies when SIGHUP fires. When provided, the
        directive is marked 'completed' in the DB before the fork (guaranteeing
        the write survives even if SIGHUP kills this process immediately after
        returning). An unknown directive_id refuses the restart entirely.

        Returns immediately with {"ok": true, "status": "restart_initiated"}.
        Guard check failures return {"ok": false, "error": "..."} without forking.
        """
```

Then find this exact text (the Slack guard immediately followed by the status-dir setup):

```python
        # Guard: confirm Slack is reachable before restarting
        if self._slack is None or not self._slack.is_reachable():
            return json.dumps({
                "ok": False,
                "error": "Slack connection required — cannot restart without verified Slack connectivity",
            })

        # Ensure status directory exists before forking
```

Replace it with:

```python
        # Guard: confirm Slack is reachable before restarting
        if self._slack is None or not self._slack.is_reachable():
            return json.dumps({
                "ok": False,
                "error": "Slack connection required — cannot restart without verified Slack connectivity",
            })

        # Guard: confirm directive exists, then mark it completed before fork.
        # This write MUST happen in the parent, before os.fork() — self._db is
        # bound to the main thread and every forked child calls os._exit(),
        # bypassing Python cleanup, so writing from a child would be unsafe.
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

        # Ensure status directory exists before forking
```

### Step 4: Update the MCP tool wrapper

In `src/ironclaude/orchestrator_mcp.py`, find this exact text:

```python
    @mcp.tool()
    def restart_daemon() -> str:
        """Send SIGHUP to the IronClaude daemon, triggering a graceful self-restart."""
        return tools.restart_daemon()
```

Replace it with:

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

### Step 5: Run the targeted tests again, verify they pass

Run:
```bash
PYTHONUNBUFFERED=1 python -m pytest tests/test_orchestrator_mcp.py -k "restart_daemon" -v
```

Expected: All tests in `TestRestartDaemon` PASS, including the 3 new ones and all pre-existing ones (`test_restart_daemon_missing_pid_file`, `test_restart_daemon_daemon_not_running`, `test_restart_daemon_sighup_permission_error`, `test_restart_daemon_stale_pid`, `test_restart_daemon_forks_and_returns_immediately`, `test_restart_daemon_reaps_first_child`, `test_restart_daemon_logs_watchdog_fork`, `test_restart_daemon_refuses_when_no_slack`, `test_restart_daemon_refuses_when_slack_unreachable`).

### Step 6: Run the full test suite to confirm no regressions

Run:
```bash
PYTHONUNBUFFERED=1 make test
```

Expected: All tests pass, no failures introduced elsewhere (e.g. in `kill_worker` tests or anywhere else importing `restart_daemon`).

### Step 7: Stage changes

Run:
```bash
git add src/ironclaude/orchestrator_mcp.py tests/test_orchestrator_mcp.py
```

Expected: Both files staged (professional mode blocks commit).
