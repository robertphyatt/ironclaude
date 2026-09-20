# Memory-Efficiency End-Review Fixes — Design

> **Created:** 2026-09-16
> **Status:** Design Complete
> **Scope mode:** hold — fix exactly what the adversarial end review found in the
> just-completed "Concurrent IronClaude Memory-Efficiency Fixes" effort. No new scope.

## Summary

The end-of-work adversarial review of the 11-task memory-efficiency change (all
tasks per-task grade A) found two Important integration defects the per-task
reviews could not see, plus three correctness observations in the idle-TTL code.
This corrective loop fixes them. All changes are in files that effort already
touched; the design premises of A2/D2/B are sound — these are implementation bugs.

## Components

### Fix 1 (Important) — A2 heartbeat `_format_mem_line` always returns "unavailable"
`commander/src/ironclaude/main.py` `_format_mem_line` (~:120). `psutil.process_iter(["name","memory_info"])`
stores `memory_info=None` for processes it cannot read (macOS AccessDenied on
root-owned procs, e.g. launchd; `as_dict` swallows it to None). The loop does
`p.info["memory_info"].rss` → `None.rss` raises `AttributeError`, which the inner
`except (NoSuchProcess, AccessDenied, TypeError)` does NOT catch → the outer
`except Exception` returns `"mem: unavailable (...)"` every time on the daemon host.
The whole heartbeat memory line silently no-ops. Fix: read `mi = p.info["memory_info"]`
and `if mi is None: continue` before using `mi.rss` (mirrors `get_process_info`'s
`if mem else 0.0` guard in orchestrator_mcp.py). **Test gap:** `_format_mem_line`
has no unit test — add one (mock `process_iter` to yield a None-memory_info proc AND
a real one; assert the result is a real "mem: … free / swap … / top …" line
containing the readable proc, NOT "unavailable").

### Fix 2 (Important) — D2 `_kill_orphan_brains` gets None on the real restart path
`commander/src/ironclaude/main.py` `_handle_restart`. Step 4 `_daemon.brain.shutdown()`
→ `_kill_brain_subprocess()` sets `self._brain_pid = None` and unlinks the brain PID
file BEFORE step 5 reads `_daemon.brain._brain_pid` (~:1272) — so the belt-and-
suspenders always runs as `_kill_orphan_brains(None)` and the recorded-PID kill is
skipped (only the log-only pgrep runs). The design required targeting the recorded
pid; the `/tmp/ic/brain.pid` fallback was never implemented. Fix: in `_handle_restart`,
CAPTURE the pid BEFORE step 4 — `recorded_brain_pid = getattr(getattr(_daemon,"brain",None),"_brain_pid",None)`,
and if falsy, read the brain PID file (confirm the client's constant/path during
implementation) — then pass that captured value to `_kill_orphan_brains(...)` at
step 5. Apply the same pre-capture to the pre-existing step-7 targeted kill (Boy
Scout — it has the identical dead read). **Test:** assert `_handle_restart` passes
the pre-shutdown pid (mock `brain.shutdown` to clear `_brain_pid`; assert
`_kill_orphan_brains` received the original pid).

### Fix 3 (observations, same idle-TTL code) — correctness polish in `_reap_idle_worker` + gate
`commander/src/ironclaude/main.py` (+ `notifications.py`, `test_idle_worker_ttl.py`):
- **obs1 (notification suppression):** the gate `continue`s after `_reap_idle_worker`
  even when it DEFERRED (held/retrying/surfaced), so a deferred worker's normal idle /
  session-died Brain notifications are skipped every cycle while deferred. Fix:
  `_reap_idle_worker` returns `bool` (True=reaped, False=deferred); the gate `continue`s
  ONLY when it returns True. Deferred → fall through to the existing marker/session-died
  handling.
- **obs2 (kill-window race):** mtime is read at the gate, then the pre-finalize (git
  integration, possibly seconds) runs before `kill_session`; Brain work arriving in that
  window would be killed. Fix: in `_reap_idle_worker`, re-read `get_log_mtime` immediately
  before `kill_session`; if it advanced past the armed time + grace, abort the kill
  (return False, leave running, keep the clock — it disarms next cycle).
- **obs3 (misleading message):** the reaped Slack/Brain message claims "work
  integrated/rescued" unconditionally, including when pre-finalize returned a
  transient/None outcome. Fix: word it neutrally (the seam already logged the true
  disposition).

## Data Flow / Error Handling
No behavior change beyond the fixes. Fix 1: skip unreadable procs (fail-soft, line
still renders). Fix 2: capture-before-clear; falls back to the PID file then None
(log-only pgrep) — never pattern-kills. Fix 3: bool-gated continue; race re-check
aborts the kill (safe: leave running); honest messaging.

## Testing Strategy
TDD each. Fix 1: RED a `_format_mem_line` test (None memory_info in the iter) proving
it returns "unavailable" today, GREEN after the guard. Fix 2: RED a `_handle_restart`
ordering test proving `_kill_orphan_brains` receives None today, GREEN after
pre-capture. Fix 3: extend `test_idle_worker_ttl.py` — deferred → `_reap_idle_worker`
returns False + gate does NOT continue past normal handling (obs1); mtime advances
before kill → no kill (obs2); message wording (obs3). Terminal: full commander
`pytest tests/` green.

## Implementation Notes
- Files: `commander/src/ironclaude/main.py`, `commander/src/ironclaude/notifications.py`,
  `commander/tests/test_main_validate.py` (for `_format_mem_line` + `_handle_restart`
  ordering, or a focused new test file), `commander/tests/test_idle_worker_ttl.py`.
- Same-tier (Opus) blind plan review is proportionate (small, targeted fixes to
  already-reviewed code) — judge at the gate; tier-up only if warranted.
- Commit disposition unchanged from the parent effort: staged only; version/commit/push
  operator-gated; the boy-scout v1.1.11 entanglement in main.py is surfaced at commit
  time.
