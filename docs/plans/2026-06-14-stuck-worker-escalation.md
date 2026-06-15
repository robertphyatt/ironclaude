# Stuck Worker Escalation Fix — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** Fix three compounding bugs that allowed worker d1127 to sit at plan_ready for 499 minutes with no escalation — hash dedup suppressing check-ins, infinite liveness deferral, and missing Slack notifications.

**Architecture:** Three targeted changes to existing methods in `commander/src/ironclaude/main.py` plus one new formatter in `commander/src/ironclaude/notifications.py`. No new systems. Constants, state variables, and cleanup scaffolding go in first, then each bug fix applies independently.

**Tech Stack:** Python, pytest, unittest.mock

**Correction from design:** Edge case 5 in the design doc claims ~12 check-ins/hour with the hash bypass. The actual rate is ~4/hour because the dedup gate (heartbeat_interval=900s) is the bottleneck, not cadence. This doesn't change correctness — 15-min intervals are adequate.

---

## Task 1: Add constants, state variables, and cleanup scaffolding

**Files:**
- Modify: `commander/src/ironclaude/main.py:100-106` (constants), `commander/src/ironclaude/main.py:560-568` (state vars), `commander/src/ironclaude/main.py:1582-1588` (stuck cleanup), `commander/src/ironclaude/main.py:1662-1666` (kill cleanup), `commander/src/ironclaude/main.py:1736-1738` (stage change)

No tests required: pure constant definitions, state variable initialization, and empty cleanup entries. Behavioral tests come in tasks 3-5.

**Step 1: Add constants at module scope**

After line 106 (`STALENESS_LIVENESS_EXTENSION = 900`), add:

```python
PM_GATE_STAGES = frozenset({"plan_ready", "design_ready"})
PM_GATE_SLACK_SECONDS = 1800
MAX_LIVENESS_DEFERRALS = 2
```

**Step 2: Add state variables in __init__**

After line 567 (`self._prompt_waiting_cache: dict[int, tuple[float, bool]] = {}`), add:

```python
self._stuck_liveness_count: dict[str, int] = {}
self._pm_gate_slack_sent: dict[str, bool] = {}
```

**Step 3: Add cleanup in check_stuck_workers cleanup loop**

In the cleanup loop at lines 1582-1588, after `self._stuck_kill_deferred.pop(wid, None)`, add:

```python
self._stuck_liveness_count.pop(wid, None)
```

**Step 4: Add cleanup in _confirm_and_kill_stuck_worker final block**

In the final cleanup at lines 1662-1666, after `self._stuck_kill_deferred.pop(worker_id, None)`, add:

```python
self._stuck_liveness_count.pop(worker_id, None)
```

**Step 5: Clear _pm_gate_slack_sent on stage change**

In the stage transition tracking at lines 1736-1738, inside the `if stage != last_seen:` block, after `self._last_stage_seen[worker_id] = stage`, add:

```python
self._pm_gate_slack_sent.pop(worker_id, None)
```

**Step 6: Stage changes**

```bash
git add commander/src/ironclaude/main.py
```

---

## Task 2: Add format_worker_gate_stuck_slack formatter

**Files:**
- Modify: `commander/src/ironclaude/notifications.py:150` (after format_worker_checkin_slack)
- Modify: `commander/tests/test_notifications.py`

**Step 1: Write test**

Add to `commander/tests/test_notifications.py`, after the existing imports at line 16 (add `format_worker_gate_stuck_slack` to the import list), then add a test class:

```python
class TestWorkerGateStuckSlack:
    def test_format_includes_worker_id_and_stage(self):
        msg = format_worker_gate_stuck_slack("w-1", 30, "plan_ready")
        assert "w-1" in msg
        assert "plan_ready" in msg
        assert "30" in msg

    def test_format_includes_alert_prefix(self):
        msg = format_worker_gate_stuck_slack("w-1", 15, "design_ready")
        assert "[ALERT]" in msg
```

**Step 2: Run test — verify it fails**

```bash
cd commander && python -m pytest tests/test_notifications.py::TestWorkerGateStuckSlack -v
```

Expected: ImportError or NameError — function doesn't exist yet.

**Step 3: Implement formatter**

Add after `format_worker_checkin_slack` (line 149) in `commander/src/ironclaude/notifications.py`:

```python
def format_worker_gate_stuck_slack(
    worker_id: str, minutes: int, stage: str,
) -> str:
    return (
        f"[ALERT] Worker {worker_id} stuck at {stage} for {minutes}min — "
        f"waiting for input. Brain may be unresponsive."
    )
```

**Step 4: Run test — verify it passes**

```bash
cd commander && python -m pytest tests/test_notifications.py::TestWorkerGateStuckSlack -v
```

Expected: 2 passed.

**Step 5: Stage changes**

```bash
git add commander/src/ironclaude/notifications.py commander/tests/test_notifications.py
```

---

## Task 3: Fix Bug 1 — Hash dedup bypass for prompt-waiting workers

**Files:**
- Modify: `commander/src/ironclaude/main.py:1756-1780` (check_workers proactive check-in section)
- Modify: `commander/tests/test_daemon.py`

**Step 1: Write tests**

Add to `commander/tests/test_daemon.py`. Import `PM_GATE_STAGES` and `PM_GATE_SLACK_SECONDS` alongside existing `CHECKIN_CADENCE` import at line 15. Then add test class:

```python
class TestHashDedupBypassPromptWaiting:
    def test_prompt_waiting_bypasses_hash_dedup(self, daemon, tmp_path):
        """Check-in fires for prompt-waiting worker even when hash is unchanged."""
        worker = {
            "id": "w1", "tmux_session": "ic-w1",
            "spawned_at": "2026-03-08 00:00:00",
        }
        daemon.registry.get_running_workers.return_value = [worker]
        daemon.tmux.has_session.return_value = True
        daemon.tmux.capture_pane.return_value = "1. Sequential\n2. Parallel\n3. Inline"
        daemon.tmux.list_pane_pid.return_value = "12345"

        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        _setup_ironclaude_db(claude_dir, "12345", "abcdef01-2345-6789-abcd-ef0123456789", "plan_ready")
        daemon._claude_dir = claude_dir
        daemon.brain.send_message.return_value = True

        # Pre-set hash to match current output (simulates previous check-in sent)
        daemon._last_checkin_hash["w1"] = hash("1. Sequential\n2. Parallel\n3. Inline")
        # Pre-set last_sent far enough back that heartbeat elapsed
        daemon._last_checkin_sent["w1"] = time.time() - 1000
        daemon._last_checkin_stage["w1"] = "plan_ready"

        # Mock prompt_waiting detection to return True
        daemon._grader.grade = MagicMock(return_value={"waiting": True})

        daemon.check_workers()
        daemon.brain.send_message.assert_called_once()
        msg = daemon.brain.send_message.call_args[0][0]
        assert "[ACTION REQUIRED]" in msg

    def test_non_prompt_waiting_still_blocked_by_hash(self, daemon, tmp_path):
        """Hash dedup still blocks when prompt_waiting is False."""
        worker = {
            "id": "w1", "tmux_session": "ic-w1",
            "spawned_at": "2026-03-08 00:00:00",
        }
        daemon.registry.get_running_workers.return_value = [worker]
        daemon.tmux.has_session.return_value = True
        daemon.tmux.capture_pane.return_value = "same output"
        daemon.tmux.list_pane_pid.return_value = "12345"

        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        _setup_ironclaude_db(claude_dir, "12345", "abcdef01-2345-6789-abcd-ef0123456789", "executing")
        daemon._claude_dir = claude_dir

        daemon._last_checkin_hash["w1"] = hash("same output")
        daemon._last_checkin_sent["w1"] = time.time() - 1000
        daemon._last_checkin_stage["w1"] = "executing"

        daemon._grader.grade = MagicMock(return_value={"waiting": False})

        daemon.check_workers()
        daemon.brain.send_message.assert_not_called()

    def test_pm_gate_slack_fires_at_threshold(self, daemon, tmp_path):
        """Slack notification fires when prompt-waiting at PM gate stage for >30 min."""
        worker = {
            "id": "w1", "tmux_session": "ic-w1",
            "spawned_at": "2026-03-08 00:00:00",
        }
        daemon.registry.get_running_workers.return_value = [worker]
        daemon.tmux.has_session.return_value = True
        daemon.tmux.capture_pane.return_value = "1. Sequential\n2. Parallel"
        daemon.tmux.list_pane_pid.return_value = "12345"

        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        _setup_ironclaude_db(claude_dir, "12345", "abcdef01-2345-6789-abcd-ef0123456789", "plan_ready")
        daemon._claude_dir = claude_dir
        daemon.brain.send_message.return_value = True

        daemon._last_checkin_sent["w1"] = time.time() - 2000
        daemon._last_checkin_stage["w1"] = "plan_ready"

        # Simulate worker has been at plan_ready for 31 min
        daemon._stage_history["w1"] = [(time.time() - 1860, "plan_ready")]
        daemon._last_stage_seen["w1"] = "plan_ready"

        daemon._grader.grade = MagicMock(return_value={"waiting": True})

        daemon.check_workers()
        daemon.slack.post_message.assert_called_once()
        msg = daemon.slack.post_message.call_args[0][0]
        assert "[ALERT]" in msg
        assert "plan_ready" in msg

    def test_pm_gate_slack_deduped(self, daemon, tmp_path):
        """PM gate Slack notification only fires once per worker per stage."""
        worker = {
            "id": "w1", "tmux_session": "ic-w1",
            "spawned_at": "2026-03-08 00:00:00",
        }
        daemon.registry.get_running_workers.return_value = [worker]
        daemon.tmux.has_session.return_value = True
        daemon.tmux.capture_pane.return_value = "1. Sequential\n2. Parallel"
        daemon.tmux.list_pane_pid.return_value = "12345"

        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        _setup_ironclaude_db(claude_dir, "12345", "abcdef01-2345-6789-abcd-ef0123456789", "plan_ready")
        daemon._claude_dir = claude_dir
        daemon.brain.send_message.return_value = True

        daemon._last_checkin_sent["w1"] = time.time() - 2000
        daemon._last_checkin_stage["w1"] = "plan_ready"
        daemon._stage_history["w1"] = [(time.time() - 1860, "plan_ready")]
        daemon._last_stage_seen["w1"] = "plan_ready"
        daemon._pm_gate_slack_sent["w1"] = True  # Already sent

        daemon._grader.grade = MagicMock(return_value={"waiting": True})

        daemon.check_workers()
        daemon.slack.post_message.assert_not_called()
```

**Step 2: Run tests — verify they fail**

```bash
cd commander && python -m pytest tests/test_daemon.py::TestHashDedupBypassPromptWaiting -v
```

Expected: FAIL — hash dedup still blocks, no Slack call.

**Step 3: Implement Bug 1 fix**

In `check_workers()`, restructure the proactive check-in section (lines 1756-1780):

1. Move `prompt_waiting = self._detect_prompt_waiting(log_tail)` from line 1766 to BEFORE the hash check (before line 1762).

2. Modify the hash check at line 1763 to bypass when prompt_waiting:

```python
prompt_waiting = self._detect_prompt_waiting(log_tail)

current_hash = hash(log_tail)
if not stage_changed and current_hash == self._last_checkin_hash.get(worker_id):
    if not prompt_waiting:
        continue
```

3. Remove the duplicate `prompt_waiting = self._detect_prompt_waiting(log_tail)` that was at the old location (line 1766).

4. After `self._last_checkin_hash[worker_id] = current_hash` (line 1780), add PM gate Slack escalation:

```python
if prompt_waiting and stage in PM_GATE_STAGES:
    stage_entries = self._stage_history.get(worker_id, [])
    if stage_entries:
        time_at_stage = time.time() - stage_entries[-1][0]
        if time_at_stage >= PM_GATE_SLACK_SECONDS and not self._pm_gate_slack_sent.get(worker_id):
            from ironclaude.notifications import format_worker_gate_stuck_slack
            self.slack.post_message(
                format_worker_gate_stuck_slack(worker_id, int(time_at_stage / 60), stage)
            )
            self._pm_gate_slack_sent[worker_id] = True
```

**Step 4: Run tests — verify they pass**

```bash
cd commander && python -m pytest tests/test_daemon.py::TestHashDedupBypassPromptWaiting -v
```

Expected: 4 passed.

**Step 5: Run existing hash dedup tests to check for regressions**

```bash
cd commander && python -m pytest tests/test_daemon.py::TestCheckinHashDedup -v
```

Expected: All existing tests still pass.

**Step 6: Stage changes**

```bash
git add commander/src/ironclaude/main.py commander/tests/test_daemon.py
```

---

## Task 4: Fix Bug 3 — Slack notification at stuck alert tier

**Files:**
- Modify: `commander/src/ironclaude/main.py:1573-1580` (check_stuck_workers alert path)
- Modify: `commander/tests/test_daemon.py`

**Step 1: Write tests**

Add to `commander/tests/test_daemon.py`:

```python
class TestStuckWorkerSlackAlert:
    def test_slack_fires_for_prompt_waiting_stuck_alert(self, daemon):
        """Slack notification fires alongside Brain message when prompt_waiting at stuck alert."""
        worker = {"id": "w1", "tmux_session": "ic-w1"}
        daemon.registry.get_running_workers.return_value = [worker]
        daemon.tmux.has_session.return_value = True
        daemon.tmux.capture_pane.return_value = "1. Option A\n2. Option B"

        # Simulate output unchanged for 16 minutes (above STALENESS_PROMPT_ALERT=900)
        daemon._stuck_hash["w1"] = hash("1. Option A\n2. Option B")
        daemon._stuck_since["w1"] = time.time() - 960
        daemon._stuck_alert_sent["w1"] = False

        daemon._grader.grade = MagicMock(return_value={"waiting": True})
        daemon._last_stuck_check = 0

        daemon.check_stuck_workers()

        daemon.brain.send_message.assert_called_once()
        assert "[STUCK]" in daemon.brain.send_message.call_args[0][0]
        daemon.slack.post_message.assert_called_once()
        assert "[ALERT]" in daemon.slack.post_message.call_args[0][0]

    def test_no_slack_for_non_prompt_waiting_stuck_alert(self, daemon):
        """Slack notification does NOT fire when prompt_waiting is False at stuck alert."""
        worker = {"id": "w1", "tmux_session": "ic-w1"}
        daemon.registry.get_running_workers.return_value = [worker]
        daemon.tmux.has_session.return_value = True
        daemon.tmux.capture_pane.return_value = "Running tests..."

        daemon._stuck_hash["w1"] = hash("Running tests...")
        daemon._stuck_since["w1"] = time.time() - 1900  # Above STALENESS_ALERT_SECONDS=1800
        daemon._stuck_alert_sent["w1"] = False

        daemon._grader.grade = MagicMock(return_value={"waiting": False})
        daemon._last_stuck_check = 0

        daemon.check_stuck_workers()

        daemon.brain.send_message.assert_called_once()
        daemon.slack.post_message.assert_not_called()
```

**Step 2: Run tests — verify they fail**

```bash
cd commander && python -m pytest tests/test_daemon.py::TestStuckWorkerSlackAlert -v
```

Expected: FAIL — first test fails because Slack is not called.

**Step 3: Implement Bug 3 fix**

In `check_stuck_workers()`, modify the alert block at lines 1573-1580. After `self.brain.send_message(...)` and before `self._stuck_alert_sent[worker_id] = True`, add:

```python
if prompt_waiting:
    from ironclaude.notifications import format_worker_gate_stuck_slack
    self.slack.post_message(
        format_worker_gate_stuck_slack(worker_id, minutes, stage or "unknown")
    )
```

**Step 4: Run tests — verify they pass**

```bash
cd commander && python -m pytest tests/test_daemon.py::TestStuckWorkerSlackAlert -v
```

Expected: 2 passed.

**Step 5: Stage changes**

```bash
git add commander/src/ironclaude/main.py commander/tests/test_daemon.py
```

---

## Task 5: Fix Bug 2 — Liveness deferral cap for prompt-waiting workers

**Files:**
- Modify: `commander/src/ironclaude/main.py:1595-1623` (_confirm_and_kill_stuck_worker liveness check)
- Modify: `commander/tests/test_daemon.py`

**Step 1: Write tests**

Add to `commander/tests/test_daemon.py`:

```python
class TestLivenessDeferralCap:
    def test_prompt_waiting_kill_after_max_deferrals(self, daemon):
        """Kill proceeds for prompt-waiting worker after MAX_LIVENESS_DEFERRALS."""
        worker = {"id": "w1", "tmux_session": "ic-w1"}
        daemon.registry.get_running_workers.return_value = [worker]

        # Pre-set deferral count at max
        daemon._stuck_liveness_count["w1"] = 2

        with patch('ironclaude.main.psutil') as mock_psutil:
            mock_child = MagicMock()
            mock_child.cpu_percent.return_value = 5.0  # CPU active
            mock_parent = MagicMock()
            mock_parent.children.return_value = [mock_child]
            mock_psutil.Process.return_value = mock_parent

            daemon.tmux.list_pane_pid.return_value = "12345"
            daemon._confirm_and_kill_stuck_worker(
                "w1", "ic-w1", 3600.0, "plan_ready", True, None,
            )

        daemon.tmux.kill_session.assert_called_once_with("ic-w1", ssh_host=None)

    def test_non_prompt_waiting_still_defers(self, daemon):
        """Non-prompt-waiting worker still defers on liveness check regardless of count."""
        worker = {"id": "w1", "tmux_session": "ic-w1"}
        daemon.registry.get_running_workers.return_value = [worker]

        daemon._stuck_liveness_count["w1"] = 5  # Way past cap

        with patch('ironclaude.main.psutil') as mock_psutil:
            mock_child = MagicMock()
            mock_child.cpu_percent.return_value = 5.0
            mock_parent = MagicMock()
            mock_parent.children.return_value = [mock_child]
            mock_psutil.Process.return_value = mock_parent

            daemon.tmux.list_pane_pid.return_value = "12345"
            daemon._confirm_and_kill_stuck_worker(
                "w1", "ic-w1", 3600.0, "plan_ready", False, None,
            )

        daemon.tmux.kill_session.assert_not_called()
        assert daemon._stuck_kill_deferred.get("w1", 0) > 0

    def test_deferral_count_increments(self, daemon):
        """Liveness deferral increments the counter."""
        daemon._stuck_liveness_count["w1"] = 0

        with patch('ironclaude.main.psutil') as mock_psutil:
            mock_child = MagicMock()
            mock_child.cpu_percent.return_value = 5.0
            mock_parent = MagicMock()
            mock_parent.children.return_value = [mock_child]
            mock_psutil.Process.return_value = mock_parent

            daemon.tmux.list_pane_pid.return_value = "12345"
            daemon._confirm_and_kill_stuck_worker(
                "w1", "ic-w1", 3600.0, "plan_ready", False, None,
            )

        assert daemon._stuck_liveness_count["w1"] == 1

    def test_cleanup_removes_liveness_count(self, daemon):
        """After kill, liveness count is cleaned up."""
        daemon._stuck_liveness_count["w1"] = 3
        daemon._stuck_since["w1"] = time.time() - 3600
        daemon._stuck_hash["w1"] = 12345
        daemon._stuck_alert_sent["w1"] = True

        with patch('ironclaude.main.psutil') as mock_psutil:
            mock_psutil.Process.side_effect = Exception("no process")

            daemon.tmux.list_pane_pid.return_value = "12345"
            daemon._confirm_and_kill_stuck_worker(
                "w1", "ic-w1", 3600.0, "plan_ready", True, None,
            )

        assert "w1" not in daemon._stuck_liveness_count
```

**Step 2: Run tests — verify they fail**

```bash
cd commander && python -m pytest tests/test_daemon.py::TestLivenessDeferralCap -v
```

Expected: FAIL — no deferral cap exists, kill doesn't proceed at count 2.

**Step 3: Implement Bug 2 fix**

In `_confirm_and_kill_stuck_worker()`, modify the liveness check block at lines 1610-1619. Replace the existing CPU active branch:

```python
if child.cpu_percent() > 1.0:
    deferral_count = self._stuck_liveness_count.get(worker_id, 0) + 1
    self._stuck_liveness_count[worker_id] = deferral_count
    if prompt_waiting and deferral_count > MAX_LIVENESS_DEFERRALS:
        logger.info(
            f"Worker {worker_id} liveness deferred {deferral_count} times "
            f"but prompt_waiting=True — proceeding with kill"
        )
        break
    self._stuck_kill_deferred[worker_id] = (
        time.time() + STALENESS_LIVENESS_EXTENSION
    )
    logger.info(
        f"Worker {worker_id} liveness check passed "
        f"(CPU active, deferral {deferral_count}), deferring kill by "
        f"{STALENESS_LIVENESS_EXTENSION}s"
    )
    return
```

Note: the `break` exits the child loop and falls through to the kill section below. The `return` (existing behavior) still prevents kill for non-prompt-waiting workers.

**Step 4: Run tests — verify they pass**

```bash
cd commander && python -m pytest tests/test_daemon.py::TestLivenessDeferralCap -v
```

Expected: 4 passed.

**Step 5: Run full test suite to check regressions**

```bash
cd commander && python -m pytest tests/test_daemon.py -v
```

Expected: All tests pass.

**Step 6: Stage changes**

```bash
git add commander/src/ironclaude/main.py commander/tests/test_daemon.py
```
