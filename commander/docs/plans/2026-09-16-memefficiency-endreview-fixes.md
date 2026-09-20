# Memory-Efficiency End-Review Fixes — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** Fix the two Important integration defects + three idle-TTL observations the
adversarial end review found in the just-completed memory-efficiency effort.

**Requirements:** docs/plans/2026-09-16-memefficiency-endreview-fixes-design.md

**Architecture:** Three small, targeted corrective fixes in files the parent effort
already touched (main.py, notifications.py, commander tests). Design premises of
A2/D2/B hold; these are implementation bugs.

**Tech Stack:** Python 3.11, pytest.

**Grounded facts (verified against current source this loop):**
- `main.py:24` `import psutil`; `_format_mem_line` :120-136; loop `for p in psutil.process_iter(["name","memory_info"]): try: procs.append((p.info["memory_info"].rss, p.info["name"])) except (psutil.NoSuchProcess, psutil.AccessDenied, TypeError): pass` — `p.info["memory_info"]` is `None` for unreadable procs → `None.rss` raises `AttributeError` (not in the except) → outer `except Exception` → "mem: unavailable". `test_main_validate.py:12` has `from ironclaude import main as main_module`.
- `main.py` `_handle_restart`: step 4 `_daemon.brain.shutdown()` :1265-1266 (real `BrainClient.shutdown`→`_kill_brain_subprocess` sets `_brain_pid=None` brain_client.py:1143 + unlinks `BRAIN_PID_FILE` :1145); step 5 `_kill_orphan_brains(getattr(...brain..._brain_pid, None))` :1272; step 7 `brain_pid = _daemon.brain._brain_pid` :1296; `os.execvp(...)` :1329. `_logged_kill` is a main.py binding (`from ironclaude.signal_forensics import _logged_kill` :73).
- **The safe `_handle_restart` test harness is `tests/test_daemon.py` `class TestHandleRestart` :1742-1910** (esp. `test_handle_restart_uses_targeted_brain_kill_not_pkill` :1877, which patches `os.execvp`, `os.kill`, `subprocess.run`, `time.sleep` and sets `mock_daemon.brain._brain_pid=99999`). `tests/test_signal_handler.py` has NO `_handle_restart` test and NO `os.execvp` patch. An unpatched `os.execvp` replaces the pytest process.
- `main.py` `_reap_idle_worker(self, worker_id, session_name, ssh_host, idle_seconds)` :4225-4246; gate :4269-4278 (`remote_log_dir` in scope from `_resolve_worker_ssh` :4255; `armed` in scope :4270); `IDLE_ACTIVITY_GRACE_SECONDS=5.0` :859; sole caller is the gate :4277. `tmux.get_log_mtime(name, ssh_host=, remote_log_dir=)` tmux_manager.py:514. `notifications.format_worker_idle_ttl_reaped` :57 (asserted by no test — grep empty).
- A bare `MagicMock` `get_log_mtime` return compared with `>` a float raises `TypeError` (MagicMock rich-compare returns NotImplemented); tests must set it to a numeric.

**Execution invariants:** literal absolute paths; Bash cwd `commander/`; `git -C <repo>`; TDD RED before GREEN; PYTHONUNBUFFERED=1 with pytest. `<repo>` = `/Users/roberthyatt/Code/ironclaude`.

---

## Task 1: Fix 1 — `_format_mem_line` None-guard (A2)

**Files:** Modify `commander/src/ironclaude/main.py`; Test `commander/tests/test_main_validate.py`.

**Step 1 (RED):** In `test_main_validate.py` add `test_format_mem_line_skips_unreadable_procs`.
Patch psutil ATTRIBUTES (not the module — replacing `main_module.psutil` makes the
`except` tuple a tuple of Mocks → "catching classes that do not inherit from
BaseException"):
```python
from types import SimpleNamespace
from unittest.mock import patch
from ironclaude import main as main_module

def test_format_mem_line_skips_unreadable_procs():
    with patch.object(main_module.psutil, "virtual_memory", return_value=SimpleNamespace(available=12 * 1024**3)), \
         patch.object(main_module.psutil, "swap_memory", return_value=SimpleNamespace(used=2 * 1024**3)), \
         patch.object(main_module.psutil, "process_iter", return_value=[
             SimpleNamespace(info={"name": "root-proc", "memory_info": None}),
             SimpleNamespace(info={"name": "real-proc", "memory_info": SimpleNamespace(rss=1 * 1024**3)}),
         ]):
        out = main_module._format_mem_line()
    assert out.startswith("mem: ")
    assert "free" in out and "swap" in out and "real-proc" in out
    assert "unavailable" not in out
```
Run:
```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_main_validate.py -k format_mem_line -x
```
Expected: FAIL — "mem: unavailable ('NoneType' object has no attribute 'rss')".

**Step 2 (GREEN):** In `_format_mem_line` (~:126) change the loop body:
```python
        for p in psutil.process_iter(["name", "memory_info"]):
            try:
                mi = p.info["memory_info"]
                if mi is None:
                    continue
                procs.append((mi.rss, p.info["name"]))
            except (psutil.NoSuchProcess, psutil.AccessDenied, TypeError):
                pass
```

**Step 3 (verify):** Re-run Step 1 → PASS ("mem: 12.0G free / swap 2.0G / top real-proc=1.0G"). Then `PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_main_validate.py tests/test_notifications.py -x` → PASS.

**Step 4 (stage):**
```bash
git -C /Users/roberthyatt/Code/ironclaude add -- commander/src/ironclaude/main.py commander/tests/test_main_validate.py
```

---

## Task 2: Fix 2 — capture Brain PID before `brain.shutdown()` (D2)

**Files:** Modify `commander/src/ironclaude/main.py`; Test `commander/tests/test_daemon.py`.

**Depends on:** Task 1 (same file — order after Fix 1).

**Step 1 (RED):** In `test_daemon.py` `class TestHandleRestart` (:1742-1910), add
`test_handle_restart_captures_brain_pid_before_shutdown`, mirroring
`test_handle_restart_uses_targeted_brain_kill_not_pkill` (:1877)'s patch set. It MUST
patch `os.execvp`, `os.kill`, `main.subprocess.run` (fake `stdout=""`), `main.time.sleep`
— an unpatched `os.execvp` replaces the pytest process. Model the REAL clear via a
`brain.shutdown` side_effect:
```python
def test_handle_restart_captures_brain_pid_before_shutdown(self):
    import signal
    from ironclaude import main as main_module
    mock_daemon = MagicMock()
    mock_daemon.brain._brain_pid = 99999
    mock_daemon.brain.shutdown.side_effect = lambda: setattr(mock_daemon.brain, "_brain_pid", None)
    logged = []
    def fake_subprocess_run(*a, **k):
        m = MagicMock(); m.stdout = ""; return m
    with patch.object(main_module, "_daemon", mock_daemon), \
         patch("os.execvp"), \
         patch("os.kill"), \
         patch.object(main_module, "_kill_orphan_brains") as mock_orphans, \
         patch.object(main_module, "_logged_kill", side_effect=lambda pid, sig, reason: logged.append((pid, sig))), \
         patch.object(main_module.subprocess, "run", side_effect=fake_subprocess_run), \
         patch.object(main_module.time, "sleep"):
        main_module._handle_restart(signal.SIGHUP, None)
    mock_orphans.assert_called_once_with(99999)        # step 5 got the pre-shutdown pid
    assert (99999, signal.SIGTERM) in logged           # step 7 targeted kill
```
Run:
```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_daemon.py -k captures_brain_pid -x
```
Expected: FAIL (both read `_brain_pid` after the shutdown clear → `_kill_orphan_brains(None)`, no step-7 kill).

**Step 2 (GREEN):** In `_handle_restart`, immediately BEFORE the step-4 `try:` that calls
`_daemon.brain.shutdown()` (~:1263), capture:
```python
        # Capture the recorded Brain PID BEFORE brain.shutdown() clears it (step 4
        # sets _brain_pid=None and removes BRAIN_PID_FILE), so the belt-and-suspenders
        # kills of steps 5 and 7 target the real pid instead of None.
        _recorded_brain_pid = getattr(getattr(_daemon, "brain", None), "_brain_pid", None)
```
Change step 5 (:1272) to `_kill_orphan_brains(_recorded_brain_pid)` and step 7 (:1296) to
`brain_pid = _recorded_brain_pid` (keep the `if brain_pid is not None:` guard + `_logged_kill`).
**Deviation from design (record, intentional):** the design mentioned a post-shutdown
`/tmp/ic/brain.pid` fallback — DROPPED. `_brain_pid` and `BRAIN_PID_FILE` are written in
lockstep (brain_client.py:801-804), so pre-shutdown the file adds nothing when the var is
set; and post-shutdown the file is unlinked (:1145). A stale file from a prior daemon
would risk PID-recycling (contradicts never-stale-kill). Pre-shutdown var capture is the
correct, sufficient fix.

**Step 3 (verify):** Re-run Step 1 → PASS. Then `PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_daemon.py -k TestHandleRestart tests/test_kill_orphan_brains.py -x` → PASS (existing `test_handle_restart_uses_targeted_brain_kill_not_pkill` stays green — its mock never clears `_brain_pid`, so 99999 flows through).

**Step 4 (stage):**
```bash
git -C /Users/roberthyatt/Code/ironclaude add -- commander/src/ironclaude/main.py commander/tests/test_daemon.py
```

---

## Task 3: Fix 3 — idle-TTL polish (obs1 bool-gated continue, obs2 pre-kill mtime re-read, obs3 honest message)

**Files:** Modify `commander/src/ironclaude/main.py`, `commander/src/ironclaude/notifications.py`; Test `commander/tests/test_idle_worker_ttl.py`.

**Depends on:** Task 2 (same file — order after Fix 2).

**Step 1 (RED):** Update `test_idle_worker_ttl.py` for the new `_reap_idle_worker`
signature `(worker_id, session_name, ssh_host, remote_log_dir, armed)` and the new
behaviour:
- **reap-order** (existing `test_reap_order_...`): set `armed = time.time() - 2000`;
  `daemon.tmux.get_log_mtime.return_value = armed` (a NUMERIC ≤ armed+grace — a bare
  MagicMock return would raise TypeError on the `>` compare); call
  `daemon._reap_idle_worker(wid, session, None, None, armed)`;
  `parent.attach_mock(daemon.tmux.get_log_mtime, "mtime")`; expect
  `["finalize", "drive", "mtime", "kill", "finalize", "drive"]`.
- **obs2 (kill-window race)** — DIRECT `_reap_idle_worker` test on `_make_reap_daemon`:
  `armed = time.time() - 2000`; `daemon.tmux.get_log_mtime.return_value = armed + IDLE_ACTIVITY_GRACE_SECONDS + 1`
  (fresh activity); `daemon._worker_idle_since[wid] = armed`; result =
  `daemon._reap_idle_worker(wid, session, None, None, armed)`; assert `result is False`,
  `daemon.tmux.kill_session.assert_not_called()`, `daemon._worker_idle_since[wid] == armed`,
  `daemon.tmux.get_log_mtime.assert_called_once_with(session, ssh_host=None, remote_log_dir=None)`.
  (Do NOT use a `[stale, fresh]` side_effect — `_reap` reads mtime once.)
- **obs1 (bool-gated continue)** — gate pair on `_make_gate_daemon` (ttl elapsed,
  `get_log_mtime.return_value = armed`): (a) `daemon._reap_idle_worker = Mock(return_value=False)`
  → gate falls through → session-died branch → assert `wid in daemon._session_died_notified`
  and `daemon.brain.send_message.assert_called_once()`; (b) `Mock(return_value=True)` →
  gate `continue`s → `daemon.brain.send_message.assert_not_called()` and
  `wid not in daemon._session_died_notified`.
- **obs1 call-args** — update `test_ttl_elapsed_reaps_when_idle` (:195) to assert
  `called_args.args == (wid, session, None, None, armed)` (new signature; gate passes
  `armed` + `remote_log_dir`, not `time.time()-armed`).
- **obs3 (message)** — assert `format_worker_idle_ttl_reaped(wid, 5)` output has
  `"integrated/rescued" not in msg`, `wid in msg`, `"reaped" in msg.lower()`.
- Deferred tests (held/retrying/surfaced) return before the re-read — signature-update only.
Run:
```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_idle_worker_ttl.py -x
```
Expected: FAIL.

**Step 2 (GREEN — `_reap_idle_worker`):** Signature
`def _reap_idle_worker(self, worker_id, session_name, ssh_host, remote_log_dir, armed):`;
`idle_seconds = time.time() - armed`; `pre = finalize(terminal=False)`; `disp = drive(...)`;
`if disp in ("retrying","held","surfaced"): log + return False`; BEFORE `kill_session`:
`fresh = self.tmux.get_log_mtime(session_name, ssh_host=ssh_host, remote_log_dir=remote_log_dir); if fresh is not None and fresh > armed + IDLE_ACTIVITY_GRACE_SECONDS: log "reap aborted: fresh activity" + return False`;
else `kill_session` → `finalize(terminal=True)` → `drive` → worker_finished-if-completed →
`slack.post_message(format_worker_idle_ttl_reaped(worker_id, int(idle_seconds // 60)))` →
`brain.send_message("[SWEEP] ...")` (neutral wording) → `self._worker_idle_since.pop(worker_id, None)` → `return True`.

**Step 3 (GREEN — gate + message):** Gate reap branch (:4275-4277) →
`if self._reap_idle_worker(worker_id, session_name, ssh_host, remote_log_dir, armed): continue`
(continue ONLY on True; False/deferred falls through to marker/session-died handling).
Reword `notifications.format_worker_idle_ttl_reaped` and the `[SWEEP]` Brain string to
state the reap neutrally — no unconditional "work integrated/rescued" (e.g. "reaped after
N min idle; finalization handled by the integration seam").

**Step 4 (verify):** Re-run Step 1 → PASS. Then `PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_idle_worker_ttl.py tests/test_main_validate.py tests/test_notifications.py -x` → PASS.

**Step 5 (stage):**
```bash
git -C /Users/roberthyatt/Code/ironclaude add -- commander/src/ironclaude/main.py commander/src/ironclaude/notifications.py commander/tests/test_idle_worker_ttl.py
```

---

## Notes
- All three edit main.py → strictly sequential (depends chain 1→2→3); inline execution.
- Terminal regression before completion: full commander `pytest tests/` green.
- Commit/version/push operator-gated; boy-scout v1.1.11 entanglement in main.py surfaced at commit time (unchanged from the parent effort).
