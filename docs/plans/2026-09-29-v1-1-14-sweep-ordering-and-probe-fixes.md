# v1.1.14 Round-2 Fixes: Sweep Ordering, Bounced Probe, Reset Cap Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** Stop the recovery sweep from handing the Brain messages it is answering or that arrived after the limit. Stop a bounced `[PING]` from causing a restart at window expiry. Cap the parsed reset window at 6 hours. Keep the sweep pending when its send fails.

**Requirements:** `docs/plans/2026-09-28-v1-1-14-slack-reply-and-limit-fixes-requirements.md` (R6)

**Design:** `docs/plans/2026-09-28-v1-1-14-slack-reply-and-limit-fixes-design.md` (component 6)

**Starting state:** this builds on the staged v1.1.14 work (Tasks 1–9).
- `brain_client.py` has the opus bounce branch that sets `_usage_limit` and `_usage_limit_until`.
- `main.py` has `_brain_limited_since`, `_sweep_after_usage_limit`, and the in-loop sweep trigger in `poll_brain_responses`.
- `test_main_validate.py` has `_sweep_daemon` and four `test_recovery_sweep_*` tests.
- `test_brain_client.py` has `TestUsageLimitState._run(model, messages, tmp_path=None, monkeypatch=None)`.

**Tech Stack:** Python 3.11, pytest.

## Execution invariants (every step)

- **Shell state does not persist between steps.** Use literal absolute paths.
- **Test commands** run as `cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest …`. Staging uses `git -C /Users/roberthyatt/Code/ironclaude add …`.
- **`docs/` is gitignored.** Plan artifacts need `git add -f`.
- **No `2>/dev/null` on evidence commands.**
- **No commit, tag, push or deploy.**

---

## Task 10: A bounce clears the pending probe; the reset window is capped (R6.2, R6.3)

**Files:**
- Modify: `commander/src/ironclaude/brain_client.py` (the opus bounce branch in `_run_session`)
- Test: `commander/tests/test_brain_client.py` (the `TestUsageLimitState._run` helper, and a new class after `TestUsageLimitExpiry`)

**Step 1: Write the tests (RED).**

(a) In `TestUsageLimitState._run`, change the signature `def _run(model, messages, tmp_path=None, monkeypatch=None):` to `def _run(model, messages, tmp_path=None, monkeypatch=None, setup=None):`. Then, directly after the line `client._grader.grade.return_value = {"permission_seeking": False}` inside it, add:

```python
        if setup is not None:
            setup(client)
```

(b) Insert this class immediately after `TestUsageLimitExpiry`, before `class TestModelUnavailableFableTransition:`:

```python
class TestUsageLimitProbeAndCap:
    """R6.2: a limit bounce answers an outstanding [PING] (clears _ping_sent_at) without
    crediting liveness. R6.3: the parsed reset window is capped at 6 h (+15 min margin)."""

    LIMIT = TestUsageLimitState.LIMIT

    def test_bounce_clears_pending_ping_without_liveness(self):
        client, _ = TestUsageLimitState._run(
            "opus",
            [TestTurnReplyTarget._am(self.LIMIT), TestTurnReplyTarget._result()],
            setup=lambda c: setattr(c, "_ping_sent_at", 12345.0),
        )
        assert client._ping_sent_at == 0.0
        assert client._last_response_time == 0.0
        assert client._usage_limit == self.LIMIT

    def test_reset_window_capped_at_six_hours(self, monkeypatch):
        from ironclaude import brain_client as bc
        monkeypatch.setattr(bc, "parse_reset_time", lambda text, now: now + 20 * 3600)
        before = time.time()
        client, _ = TestUsageLimitState._run("opus", [TestTurnReplyTarget._am(self.LIMIT)])
        after = time.time()
        assert before + 6 * 3600 + 900 <= client._usage_limit_until <= after + 6 * 3600 + 900
```

Broken states caught:
- **Ping test:** fails if a bounce leaves `_ping_sent_at` set, which is the M2 restart path. It also fails if the bounce credits liveness.
- **Cap test:** fails if the window is uncapped; it would come out 20 hours plus 900 seconds.

**Step 2: Run the tests and confirm they fail.**

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest -q tests/test_brain_client.py::TestUsageLimitProbeAndCap
```

Expected: 2 failed (`_ping_sent_at == 12345.0`; `_usage_limit_until` is about 20 hours ahead).

**Step 3: Implement.** In `commander/src/ironclaude/brain_client.py`'s opus bounce branch, replace

```python
                        self._usage_limit = full_text
                        _now = time.time()
                        _reset = parse_reset_time(full_text, _now)
                        self._usage_limit_until = (_reset + 900) if _reset is not None else _now + 3600
```

with

```python
                        self._usage_limit = full_text
                        # R6.2: a bounce answers an outstanding [PING] probe without crediting
                        # liveness, so window expiry never reads it as an unanswered probe.
                        self._ping_sent_at = 0.0
                        _now = time.time()
                        _reset = parse_reset_time(full_text, _now)
                        if _reset is not None:
                            # R6.3: same 6 h clamp fable_availability applies to this parse.
                            _reset = min(_reset, _now + 6 * 3600)
                        self._usage_limit_until = (_reset + 900) if _reset is not None else _now + 3600
```

**Step 4: Run the tests and confirm they pass.**

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest -q tests/test_brain_client.py
```

Expected: all pass, 0 failed.

**Step 5: Stage.**

```bash
git -C /Users/roberthyatt/Code/ironclaude add commander/src/ironclaude/brain_client.py commander/tests/test_brain_client.py
```

---

## Task 11: Sweep after the batch, only up to the last bounce, and keep it on a failed send (R6.1, R6.4)

**Files:**
- Modify: `commander/src/ironclaude/main.py`:
  - `__init__` (after `self._brain_limited_since: float | None = None`);
  - `_sweep_after_usage_limit`;
  - `poll_brain_responses`.
- Test: `commander/tests/test_main_validate.py` (`_make_poll_daemon`, the existing `test_recovery_sweep_*` tests, and new tests after `test_recovery_sweep_fetch_failure_retries_next_response`).

**Step 1: Write the tests (RED).**

(a) In `_make_poll_daemon`, directly after `d._brain_limited_since = None`, add:

```python
    d._brain_limited_last = None
```

(b) In `test_recovery_sweep_triggered_by_ping_ack` and in `test_recovery_sweep_fetch_failure_retries_next_response`, directly after each line `d._brain_limited_since = 9000.0`, add:

```python
    d._brain_limited_last = 9600.0
```

(c) Add these after `test_recovery_sweep_fetch_failure_retries_next_response`:

```python
def test_brain_limited_last_tracks_every_bounce(monkeypatch):
    from ironclaude.brain_client import _LIMIT_PREFIX
    d = _sweep_daemon()
    clock = {"v": 10000.0}
    monkeypatch.setattr("ironclaude.main.time.time", lambda: clock["v"])
    d.brain.get_pending_responses.return_value = [f"{_LIMIT_PREFIX}{_SWEEP_LIMIT}"]
    d.poll_brain_responses()
    clock["v"] = 10500.0
    d.poll_brain_responses()
    assert d._brain_limited_since == 10000.0
    assert d._brain_limited_last == 10500.0


def test_recovery_sweep_excludes_messages_after_last_bounce(monkeypatch):
    """R6.1: an operator message that arrived after the last bounce was never bounced —
    the Brain is answering it now — so the sweep must not hand it over again."""
    from ironclaude.brain_client import _LIMIT_PREFIX, _NARRATION_PREFIX
    d = _sweep_daemon()
    clock = {"v": 10000.0}
    monkeypatch.setattr("ironclaude.main.time.time", lambda: clock["v"])
    d.brain.get_pending_responses.return_value = [f"{_LIMIT_PREFIX}{_SWEEP_LIMIT}"]
    d.poll_brain_responses()
    clock["v"] = 20000.0
    d.slack.get_recent_messages.return_value = [
        {"user": "U_OP", "ts": "19990.000001", "text": "status?"},
        {"user": "U_OP", "ts": "9990.000001", "text": "bounced earlier"},
    ]
    d.brain.get_pending_responses.return_value = [f"{_NARRATION_PREFIX}Working on it."]
    d.poll_brain_responses()
    d.brain.send_message.assert_called_once()
    sent = d.brain.send_message.call_args.args[0]
    assert "bounced earlier" in sent
    assert "status?" not in sent


def test_recovery_sweep_runs_after_batch_acks(monkeypatch):
    """R6.1: the sweep runs after the whole batch, so a reply in the same batch has already
    recorded its acknowledgement and is not handed back as unanswered."""
    from ironclaude.brain_client import _NARRATION_PREFIX
    d = _sweep_daemon()
    monkeypatch.setattr("ironclaude.main.time.time", lambda: 10000.0)
    d._brain_limited_since = 9000.0
    d._brain_limited_last = 9600.0
    d.slack.get_recent_messages.return_value = [
        {"user": "U_OP", "ts": "9500.000001", "text": "answered now"},
        {"user": "U_OP", "ts": "9400.000001", "text": "still open"},
    ]
    d.brain.get_pending_responses.return_value = [
        f"{_NARRATION_PREFIX}[reply-to:9500.000001] here you go",
    ]
    d.poll_brain_responses()
    d.brain.send_message.assert_called_once()
    sent = d.brain.send_message.call_args.args[0]
    assert "still open" in sent
    assert "answered now" not in sent


def test_recovery_sweep_keeps_timestamps_when_send_fails(monkeypatch):
    """R6.4: a sweep the Brain could not receive stays pending for the next response."""
    from ironclaude.brain_client import _NARRATION_PREFIX
    d = _sweep_daemon()
    monkeypatch.setattr("ironclaude.main.time.time", lambda: 10000.0)
    d._brain_limited_since = 9000.0
    d._brain_limited_last = 9600.0
    d.slack.get_recent_messages.return_value = [
        {"user": "U_OP", "ts": "9500.000001", "text": "hello"},
    ]
    d.brain.send_message.return_value = False
    d.brain.get_pending_responses.return_value = [f"{_NARRATION_PREFIX}Back online."]
    d.poll_brain_responses()
    assert d._brain_limited_since == 9000.0
    assert d._brain_limited_last == 9600.0
```

Broken states caught:
- **Tracks-every-bounce test:** fails if the last-bounce time isn't updated on each bounce.
- **Excludes-after-last-bounce test:** fails without the upper bound (M1).
- **After-batch test:** fails if the sweep runs before the same batch's reply acknowledgement (M1 ordering).
- **Send-fails test:** fails if a failed send clears the timestamps (obs 5).

**Step 2: Run the tests and confirm they fail.**

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest -q tests/test_main_validate.py -k "recovery_sweep or brain_limited_last"
```

Expected: FAIL. The 4 new tests fail. The two existing tests edited in (b) still pass, because `_brain_limited_last` is ignored for now. The other 2 existing sweep tests also pass.

**Step 3: Implement in `commander/src/ironclaude/main.py`.**

(a) In `__init__`, directly after `self._brain_limited_since: float | None = None`, add:

```python
        # R6.1: epoch of the LATEST limit bounce; the recovery sweep only hands over operator
        # messages that arrived up to it (later ones were never bounced).
        self._brain_limited_last: float | None = None
```

(b) Replace the body of `_sweep_after_usage_limit`, from `since = self._brain_limited_since` through its final `logger.info(...)` line, with:

```python
        since = self._brain_limited_since
        last = self._brain_limited_last if self._brain_limited_last is not None else since
        try:
            pending = self._get_unprocessed_messages(
                max_age_seconds=0, oldest=since - 300, limit=200,
            )
        except Exception:
            logger.warning(
                "Usage-limit recovery sweep failed; retrying on the next Brain response",
                exc_info=True,
            )
            return
        # R6.1: a message that arrived after the last bounce was never bounced.
        pending = [m for m in pending if float(m["ts"]) <= last]
        if not pending:
            self._brain_limited_since = None
            self._brain_limited_last = None
            logger.info("Usage-limit recovery sweep: no unanswered operator messages")
            return
        lines = "\n".join(f"- ts={m['ts']}: {m.get('text', '')[:300]}" for m in pending)
        sent = self.brain.send_message(
            "[USAGE LIMIT RECOVERED] These operator messages arrived while you were "
            "usage-limited and have no reply or directive yet. Handle each now: reply with "
            "[reply-to:<ts>] followed by your answer, or submit_directive().\n" + lines
        )
        if sent is False:
            # R6.4: the Brain could not take it — keep the sweep pending.
            logger.warning("Usage-limit recovery sweep could not reach the Brain; retrying on the next Brain response")
            return
        self._brain_limited_since = None
        self._brain_limited_last = None
        logger.info("Usage-limit recovery sweep sent %d operator message(s) to the Brain", len(pending))
```

(c) In `poll_brain_responses`, replace

```python
        for text in self.brain.get_pending_responses():
            logger.info(f"Brain response: {text[:100]}...")
            # R5.2: the first non-limit response after a usage-limit bounce means the Brain
            # is answering again (a [PING-ACK] counts) — sweep unanswered operator messages.
            if self._brain_limited_since is not None and not text.startswith(_LIMIT_PREFIX):
                self._sweep_after_usage_limit()
```

with

```python
        # R5.2/R6.1: a non-limit response after a usage-limit bounce means the Brain is
        # answering again (a [PING-ACK] counts). Sweep AFTER the whole batch so replies in
        # this batch have already recorded their acknowledgements.
        sweep_due = False
        for text in self.brain.get_pending_responses():
            logger.info(f"Brain response: {text[:100]}...")
            if self._brain_limited_since is not None and not text.startswith(_LIMIT_PREFIX):
                sweep_due = True
```

(d) In `poll_brain_responses`, in the `_LIMIT_PREFIX` branch, replace

```python
                if self._brain_limited_since is None:
                    self._brain_limited_since = time.time()
                continue
```

with

```python
                _bounce_at = time.time()
                if self._brain_limited_since is None:
                    self._brain_limited_since = _bounce_at
                self._brain_limited_last = _bounce_at
                continue
```

(e) At the very end of `poll_brain_responses`, after the `for` loop, at the same indentation as `sweep_due = False`, add:

```python
        if sweep_due and self._brain_limited_since is not None:
            self._sweep_after_usage_limit()
```

Before adding it, read the end of `poll_brain_responses` and confirm the method has no statements after the `for` loop. If it does, put this after them.

**Step 4: Run the tests and confirm they pass.**

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest -q tests/test_main_validate.py tests/test_enforcement.py tests/test_daemon.py
```

Expected: all pass, 0 failed.

**Step 5: Stage.**

```bash
git -C /Users/roberthyatt/Code/ironclaude add commander/src/ironclaude/main.py commander/tests/test_main_validate.py
```

---

## Task 12: CHANGELOG update and full suite (R6.5)

**Depends on:** Tasks 10 and 11.

**No tests required:** this task edits docs only; the full suite verifies it.

**Files:**
- Modify: `CHANGELOG.md` (the `## 1.1.14` third bullet and the Deploy line)

**Step 1: Run the full commander suite.**

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest -q
```

Expected: 0 failed. Record the passed count N.

**Step 2: Edit the CHANGELOG.**

(a) In the `## 1.1.14` third bullet, replace

```
When the Brain answers again (a `[PING-ACK]` counts), the daemon hands it every operator message that arrived during the limit and still has no reply or directive, as one `[USAGE LIMIT RECOVERED]` message; a failed Slack fetch retries on the next response.
```

with

```
Once the batch holding the Brain's first real response (a `[PING-ACK]` counts) has been processed, the daemon hands it every operator message that arrived up to the last bounce and still has no reply or directive, as one `[USAGE LIMIT RECOVERED]` message; a failed Slack fetch or send retries on the next response. A bounced `[PING]` probe clears the pending probe without counting as liveness, so it never triggers a restart, and a parsed reset time is capped at six hours.
```

(b) In the Deploy line, replace `Suites: commander 3404.` with `Suites: commander <N>.`, using the count from Step 1.

**Step 3: Verify.**

```bash
rg -n -F -e "up to the last bounce" -e "capped at six hours" /Users/roberthyatt/Code/ironclaude/CHANGELOG.md
```

Expected: 1 line, `CHANGELOG.md:18`.

```bash
rg -n -F "arrived during the limit and still has no reply" /Users/roberthyatt/Code/ironclaude/CHANGELOG.md
```

Expected: no output, exit 1.

**Step 4: Stage.**

```bash
git -C /Users/roberthyatt/Code/ironclaude add CHANGELOG.md
```

```bash
git -C /Users/roberthyatt/Code/ironclaude add -f docs/plans/2026-09-28-v1-1-14-slack-reply-and-limit-fixes-requirements.md docs/plans/2026-09-28-v1-1-14-slack-reply-and-limit-fixes-design.md docs/plans/2026-09-29-v1-1-14-sweep-ordering-and-probe-fixes.md docs/plans/2026-09-29-v1-1-14-sweep-ordering-and-probe-fixes.plan.json
```

Expected: exit 0.
