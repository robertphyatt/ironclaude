# Brain Stuck-Worker Escalation Design

> **Created:** 2026-06-27
> **Status:** Design Complete
> **Directive:** d1245 — d1227 worker frozen 7+ hours by rate limit; Brain chose kill over "continue"

## Summary

On 2026-06-27, worker d1227 hit a Claude API rate limit at `final_plan_prep` and sat frozen for 7+ hours. The daemon's existing stuck-worker detection (d1074, d1127) correctly sent `[ACTION REQUIRED]` to the Brain. Two compounding failures followed:

1. **Brain inaction for 7+ hours**: The Brain received the alert, confirmed the rate limit in the log, and chose to "wait" — reasonable per directive #19 (don't switch to usage credits) but operationally wrong (no "continue" scheduled)
2. **Brain wrong recovery action**: When it finally acted, the Brain called `kill_worker` instead of `send_to_worker("continue")`, destroying a completed Opus plan that was preserved in the frozen session

Two independent root causes enabled these failures:

**Root cause A — Uncapped liveness deferral for non-`prompt_waiting` workers**: `_confirm_and_kill_stuck_worker()` at line 1636 applies `MAX_LIVENESS_DEFERRALS=2` only to `prompt_waiting=True` workers. Rate-limited workers at `final_plan_prep` are `prompt_waiting=False`. Background node.js processes with any CPU activity (`cpu_percent() > 1.0`) deferred the kill indefinitely — 15 minutes per deferral, no cap, resulting in 7+ hours.

**Root cause B — No rate-limit context in daemon alert**: The `[STUCK]` message sent at 30 minutes was generic: "output unchanged for Nmin, check worker status." The Brain had no explicit instruction that (a) the correct recovery is `send_to_worker("continue")` not `kill_worker`, and (b) the daemon could handle recovery autonomously.

This design addresses both: Fix 1 adds daemon-level rate-limit detection with autonomous "continue" scheduling; Fix 2 caps the liveness deferral for all workers regardless of `prompt_waiting` state.

## Architecture

No new systems. All changes confined to `commander/src/ironclaude/main.py` and `commander/src/ironclaude/notifications.py`.

**Fix 1 — Rate-limit detection + scheduled recovery (new capability)**:
`check_stuck_workers()` scans captured tmux pane output for rate-limit patterns alongside the existing hash comparison. When detected: rate-limit state is recorded per worker, the normal kill countdown is bypassed, and Brain + Slack receive a `[RATE LIMIT]` alert with explicit "DO NOT kill — daemon is handling recovery." At `reset_time + RATE_LIMIT_RESET_BUFFER (60s)`, the daemon sends `"continue"` via `tmux.send_keys()`. The hash is cleared so staleness tracking restarts from zero. A `RATE_LIMIT_CONTINUE_TIMEOUT (180s)` verification window follows: if the hash changes within 3 minutes, recovery succeeded; if the hash is still unchanged, the worker falls through to the existing kill path.

**Fix 2 — Liveness deferral cap for non-`prompt_waiting` workers (bug fix)**:
Adds `MAX_LIVENESS_DEFERRALS_NOPROMPT = 4` constant. Applies the cap to all workers — the existing cap (2 deferrals for `prompt_waiting` workers) is extended to all workers with a higher limit (4 deferrals = 60 min extra). Maximum exposure: 60 min (STALENESS_KILL_SECONDS) + 60 min (4 × 15 min deferrals) = 2 hours before forced kill.

## Components

### New Constants (top of `main.py`, near existing staleness constants)

```python
# Rate-limit detection and recovery
_RATE_LIMIT_RE = re.compile(
    r'rate\s*limit|usage\s*limit|session\s*reset|resets?\s+at\s+\d',
    re.IGNORECASE,
)
_RESET_TIME_RE = re.compile(
    r'resets?\s+at\s+(\d{1,2}:\d{2}\s*(?:AM|PM))',
    re.IGNORECASE,
)
RATE_LIMIT_RESET_BUFFER = 60        # extra seconds after reset time before sending "continue"
RATE_LIMIT_CONTINUE_TIMEOUT = 180   # seconds to wait for new output after "continue"
MAX_LIVENESS_DEFERRALS_NOPROMPT = 4 # liveness deferral cap for non-prompt-waiting workers
```

### New State (`IroncladeDaemon.__init__`)

```python
self._rate_limit_detected: dict[str, bool] = {}
self._rate_limit_reset_at: dict[str, float] = {}         # unix timestamp of parsed reset time
self._rate_limit_continue_sent: dict[str, bool] = {}
self._rate_limit_continue_sent_at: dict[str, float] = {} # when "continue" was sent
self._rate_limit_alerted: dict[str, bool] = {}           # dedup Brain/Slack alert
```

### New Methods (`IroncladeDaemon`)

**`_parse_rate_limit_reset_time(log_tail: str) -> float`**

Extracts reset time from rate-limit message. Returns unix timestamp, defaults to `time.time() + 3600` if parsing fails.

```python
def _parse_rate_limit_reset_time(self, log_tail: str) -> float:
    match = _RESET_TIME_RE.search(log_tail)
    if not match:
        return time.time() + 3600
    try:
        from datetime import datetime, timedelta
        dt = datetime.strptime(match.group(1).strip(), "%I:%M %p")
        now_dt = datetime.now()
        reset_dt = now_dt.replace(hour=dt.hour, minute=dt.minute, second=0, microsecond=0)
        if reset_dt < now_dt:
            reset_dt += timedelta(days=1)
        return reset_dt.timestamp()
    except ValueError:
        return time.time() + 3600
```

**`_clear_rate_limit_state(worker_id: str) -> None`**

Clears all rate-limit tracking state for a worker (called on recovery or worker exit).

**`_handle_rate_limit_recovery(worker_id, session_name, log_tail, ssh_host) -> None`**

Called from `check_stuck_workers()` when `_rate_limit_detected[worker_id]` is True and hash is unchanged. Checks whether reset time has passed; sends "continue" and starts verification countdown.

### Modified Methods

| Method | File | Lines | Change |
|--------|------|-------|--------|
| `check_stuck_workers()` | main.py | 1530–1611 | Add rate-limit scan on each cycle; route to `_handle_rate_limit_recovery` when detected; clear rate-limit state on hash change |
| `_confirm_and_kill_stuck_worker()` | main.py | 1613–1698 | Apply `MAX_LIVENESS_DEFERRALS_NOPROMPT` cap for non-`prompt_waiting` workers |

### New Formatter (`notifications.py`)

**`format_worker_rate_limited_slack(worker_id, minutes_to_reset) -> str`**

Posts to Slack when rate limit detected. Example:
```
[RATE LIMIT] Worker d1227 hit API rate limit (~42min until reset).
Daemon will send "continue" automatically at reset time.
DO NOT call kill_worker — the session preserves full context including completed plans.
```

**`format_worker_rate_limit_recovery_slack(worker_id, success) -> str`**

Posts to Slack after recovery attempt. On success: "d1227 recovered after 'continue' — resuming." On failure: "d1227 did not recover after 'continue' — killing and notifying Brain."

## Data Flow

```
check_stuck_workers() — runs every 60s:

  For each running worker:
    1. Capture pane → log_tail (existing)
    2. Compute current_hash (existing)

    3. [NEW] If current_hash != stored hash:
         - Clear stuck state (existing)
         - [NEW] If _rate_limit_detected: _clear_rate_limit_state() — worker recovered
         - Continue to next worker

    4. Hash unchanged path:
         [NEW] If _rate_limit_detected[worker_id]:
           → _handle_rate_limit_recovery(worker_id, ...)
           → continue (skip normal staleness processing)

         [NEW] Else (no rate limit already detected):
           Scan log_tail for _RATE_LIMIT_RE:
             If match:
               - _rate_limit_detected[worker_id] = True
               - _rate_limit_reset_at[worker_id] = _parse_rate_limit_reset_time(log_tail)
               - Send [RATE LIMIT] alert to Brain + Slack (deduped)
               - Suppress _stuck_since from accumulating further
               - continue (skip normal staleness processing this cycle)
             Else:
               Normal staleness processing (existing: alert at 30min, kill at 60min)

_handle_rate_limit_recovery():
  now = time.time()
  reset_at = _rate_limit_reset_at[worker_id]

  Case A: "continue" already sent:
    If now - continue_sent_at > RATE_LIMIT_CONTINUE_TIMEOUT (180s):
      → Log failure, _clear_rate_limit_state()
      → _confirm_and_kill_stuck_worker() (fall through to kill path)
    Else:
      → Still within timeout window, wait

  Case B: "continue" not yet sent:
    If now >= reset_at + RATE_LIMIT_RESET_BUFFER (60s):
      → tmux.send_keys(session_name, "continue")
      → _rate_limit_continue_sent[worker_id] = True
      → _rate_limit_continue_sent_at[worker_id] = now
      → Clear _stuck_hash, _stuck_since, _stuck_alert_sent (fresh start for staleness)
      → Send [RATE LIMIT RECOVERY] message to Brain
    Else:
      → Still waiting for reset time, do nothing

_confirm_and_kill_stuck_worker() — liveness deferral cap fix:
  On CPU activity detected for child process:
    deferral_count = _stuck_liveness_count.get(worker_id, 0) + 1
    _stuck_liveness_count[worker_id] = deferral_count
    [CHANGED] cap = MAX_LIVENESS_DEFERRALS if prompt_waiting else MAX_LIVENESS_DEFERRALS_NOPROMPT
    if deferral_count > cap:
      break  # force kill
    _stuck_kill_deferred[worker_id] = now + STALENESS_LIVENESS_EXTENSION
    return  # deferred
```

## Error Handling

**Edge case 1: Reset time already past when daemon first detects rate limit**
Daemon restart mid-wait, or rate limit detected late. `_handle_rate_limit_recovery()` will send "continue" on the first check (since `now >= reset_at + buffer` is already true). Recovery attempt fires immediately rather than waiting.

**Edge case 2: `_RESET_TIME_RE` fails to parse reset time**
`_parse_rate_limit_reset_time()` returns `time.time() + 3600` (1-hour default). Daemon waits 60 minutes before sending "continue". Conservative but safe — better to wait too long than to send "continue" while rate-limited.

**Edge case 3: Rate-limit state lost on daemon restart**
State is in-memory only, not persisted. After restart, normal staleness detection resumes from zero. Worker will hit kill threshold within 1 hour (plus up to 60 min from liveness deferrals with new cap = 2 hours max). This is acceptable — daemon restarts are rare, and Fix 2 ensures the kill eventually fires.

**Edge case 4: "continue" sent but worker hits a NEW rate limit immediately**
Hash changes transiently (the "continue" triggers some output), rate-limit state is cleared, but then new rate-limit message appears. Detection loop fires again on next cycle — full rate-limit recovery path repeats.

**Edge case 5: Rate-limit pattern false positive**
Non-rate-limit output matches `_RATE_LIMIT_RE`. "continue" is sent at `now + 3600 + 60s`. If the worker was genuinely hung (not rate-limited), "continue" is a no-op — hash stays unchanged → kill fires within 3 minutes of "continue" timeout. Maximum extra delay: ~1 hour from false detection to kill. Acceptable given the cost of a wrong kill (destroyed plans).

**Edge case 6: Worker exits (session dies) during rate-limit wait**
`check_workers()` detects the dead tmux session independently of `check_stuck_workers()`. Worker is marked completed, state cleaned up, Brain notified via `[MANDATORY SWEEP]`. The rate-limit state dicts use worker_id keys — the cleanup in `check_stuck_workers()` (lines 1604–1611) prunes state for workers no longer in `running_ids`, extended to include rate-limit state keys.

## Testing Strategy

### Unit Tests

1. **Rate-limit detection**: Mock `tmux.capture_pane` returning text with "resets at 2:30 PM". Assert `_rate_limit_detected[wid]` is True and Brain receives `[RATE LIMIT]` message with "DO NOT kill" text.

2. **Reset time parsing**: Unit test `_parse_rate_limit_reset_time()` with valid format ("resets at 2:30 PM"), missing time, and malformed input. Assert correct timestamp or 3600s default respectively.

3. **"continue" scheduling**: Pre-set `_rate_limit_reset_at[wid] = time.time() - 70` (past reset + buffer). Call `_handle_rate_limit_recovery()`. Assert `tmux.send_keys` called with `"continue"`.

4. **Recovery verification — success**: Pre-set `_rate_limit_continue_sent[wid] = True`, `_rate_limit_continue_sent_at[wid] = time.time() - 10`. Mock new hash (hash changed). Assert `_rate_limit_detected` is cleared.

5. **Recovery verification — failure**: Pre-set `_rate_limit_continue_sent[wid] = True`, `_rate_limit_continue_sent_at[wid] = time.time() - 200` (past timeout). Mock same hash (unchanged). Assert `_confirm_and_kill_stuck_worker` called.

6. **Liveness cap — non-prompt-waiting**: Pre-set `_stuck_liveness_count[wid] = 4`, `prompt_waiting=False`. Call `_confirm_and_kill_stuck_worker` with CPU-active mock child. Assert kill proceeds (cap exceeded).

7. **Liveness cap — prompt-waiting unchanged**: Pre-set `_stuck_liveness_count[wid] = 2`, `prompt_waiting=True`. Assert kill proceeds (existing cap behavior unchanged).

### Integration Validation

Simulate a rate-limited worker by:
1. Spawning test session with a process that outputs "Rate limit reached. Resets at HH:MM PM" and then freezes
2. Verify `[RATE LIMIT]` alert sent to Brain within 60s
3. Verify "continue" sent to tmux at reset_time + 60s
4. Verify `[RATE LIMIT RECOVERY]` message sent to Brain on success

## Implementation Notes

- **Regex needs field verification**: `_RATE_LIMIT_RE` and `_RESET_TIME_RE` patterns must be verified against an actual rate-limit event's tmux output. The patterns above are based on known Claude Code behavior but may need tuning for edge-case formatting.
- **Daemon does NOT interact with the rate-limit menu** (options 1/2). That is Brain's domain (directive #19). The daemon only reads the reset time from menu text and sends "continue" after the reset.
- **Rate-limit state is not persisted to DB** — in-memory only. Future enhancement: add a `worker_rate_limit_state` DB table (mirroring `worker_staleness`) so state survives daemon restarts.
- **No change to `kill_worker` MCP tool** — relies on enriched Brain alert message containing "DO NOT kill" to prevent the Brain from calling `kill_worker` during rate-limit recovery. If defense-in-depth is later needed, `kill_worker()` could check `_rate_limit_detected` (via a shared flag or DB) and return an error if the worker is in recovery state.
- **`tmux.send_keys` for "continue"**: Same mechanism as `approve_plan` ("yes") and menu responses. No new infrastructure needed.
- **Cleanup of rate-limit state in worker exit path**: The pruning loop at `check_stuck_workers()` lines 1604–1611 must also clean `_rate_limit_detected`, `_rate_limit_reset_at`, `_rate_limit_continue_sent`, `_rate_limit_continue_sent_at`, `_rate_limit_alerted` for workers no longer in `running_ids`.
