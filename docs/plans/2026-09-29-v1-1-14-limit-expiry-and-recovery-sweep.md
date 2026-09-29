# v1.1.14 Follow-up: Usage-Limit Expiry and Recovery Sweep Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** Make the usage-limited Brain state expire at the reported reset time, hand the Brain every operator message left unanswered during a limit, and make the aging nudge match the reply-owns-closure rule.

**Requirements:** `docs/plans/2026-09-28-v1-1-14-slack-reply-and-limit-fixes-requirements.md` (R5)

**Design:** `docs/plans/2026-09-28-v1-1-14-slack-reply-and-limit-fixes-design.md` (component 5)

**Architecture:**
- `brain_client` records `_usage_limit_until`, the parsed reset time plus 15 minutes (or now plus 1 hour when there is no reset time). `needs_restart()` suppresses restarts only until then; after that it clears the limit and runs the normal liveness checks.
- The daemon records `_brain_limited_since` at the first limit-tagged response. On the next response that isn't limit-tagged, it sends the Brain one `[USAGE LIMIT RECOVERED]` message listing the operator messages from the limit onward that have no directive or acknowledgement.

**Starting state:** this builds on the staged v1.1.14 Tasks 1–6, where `_usage_limit`, `_LIMIT_PREFIX`, `detect_account_limit` in `brain_client` and the limit branch in `poll_brain_responses` already exist.

**Tech Stack:** Python 3.11, pytest.

## Execution invariants (every step)

- **Shell state does not persist between steps.** Use literal absolute paths.
- **Test commands** run as `cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest …`. Staging uses `git -C /Users/roberthyatt/Code/ironclaude add …`.
- **`docs/` is gitignored.** Plan artifacts need `git add -f`.
- **No `2>/dev/null` on evidence commands.**
- **Every new test names the broken state it catches.**
- **No commit, tag, push or deploy.**

---

## Task 7: The usage-limited state expires (R5.1)

**Files:**
- Modify: `commander/src/ironclaude/brain_client.py`:
  - imports (after the `fable_availability` import);
  - `__init__` (after `self._usage_limit: str | None = None`);
  - the opus limit branch in `_run_session`;
  - `needs_restart()`;
  - `restart()`.
- Test: `commander/tests/test_brain_client.py` (`TestUsageLimitState`, and a new class after it).

**Step 1: Update the existing tests and add new ones (RED).**

(a) In `TestUsageLimitState.test_needs_restart_suppressed_while_limited`, directly after `client._usage_limit = self.LIMIT`, add:

```python
        client._usage_limit_until = time.time() + 600
```

(b) In `TestUsageLimitState.test_restart_clears_limit_and_turn_target`, directly after `client._turn_reply_ts = "1699.5"`, add `client._usage_limit_until = 123.0`. After the last assert, add:

```python
        assert client._usage_limit_until == 0.0
```

(c) Insert this class immediately after `TestUsageLimitState`, before `class TestModelUnavailableFableTransition:`:

```python
class TestUsageLimitExpiry:
    """R5.1: the usage-limited state lasts until the reported reset + 15 min (or 1 h when no
    reset time is given); after that needs_restart clears it and normal liveness resumes."""

    LIMIT = TestUsageLimitState.LIMIT

    def test_expiry_is_parsed_reset_plus_margin(self, monkeypatch):
        from ironclaude import brain_client as bc
        monkeypatch.setattr(bc, "parse_reset_time", lambda text, now: 50000.0)
        client, _ = TestUsageLimitState._run("opus", [TestTurnReplyTarget._am(self.LIMIT)])
        assert client._usage_limit_until == 50900.0

    def test_expiry_without_reset_is_one_hour(self, monkeypatch):
        from ironclaude import brain_client as bc
        monkeypatch.setattr(bc, "parse_reset_time", lambda text, now: None)
        before = time.time()
        client, _ = TestUsageLimitState._run("opus", [TestTurnReplyTarget._am(self.LIMIT)])
        after = time.time()
        assert before + 3600 <= client._usage_limit_until <= after + 3600

    def test_needs_restart_resumes_after_expiry(self):
        client = TestBrainLivenessTimeout._make_alive_client(timeout_seconds=300)
        client._last_message_time = time.time() - 600
        client._last_response_time = client._last_message_time - 10
        client._usage_limit = self.LIMIT
        client._usage_limit_until = time.time() + 600
        assert client.needs_restart() is False
        assert client._usage_limit == self.LIMIT
        client._usage_limit_until = time.time() - 1
        assert client.needs_restart() is True
        assert client._usage_limit is None
        client._stop_event.set()
```

Broken states caught:
- **The two expiry tests:** fail if the window is never recorded or uses the wrong margin.
- **`test_needs_restart_resumes_after_expiry`:** fails if the guard never expires (today's defect, M2), or if expiry doesn't clear the state.
- **The updated `test_restart_clears…`:** fails if `restart()` keeps a stale window.

**Step 2: Run the tests and confirm they fail.**

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest -q tests/test_brain_client.py::TestUsageLimitExpiry tests/test_brain_client.py::TestUsageLimitState
```

Expected: FAIL.
- `TestUsageLimitExpiry` fails because `parse_reset_time` and `_usage_limit_until` don't exist yet.
- `test_needs_restart_resumes_after_expiry` still returns False after expiry.
- `test_restart_clears_limit_and_turn_target` fails on `_usage_limit_until`.
- `test_needs_restart_suppressed_while_limited` still passes, since today's guard never expires.

**Step 3: Implement.** In `commander/src/ironclaude/brain_client.py`:

(a) After `from ironclaude.fable_availability import mark_fable_unavailable as _mark_fable_unavailable`, add:

```python
from ironclaude.fable_availability import parse_reset_time
```

(b) In `__init__`, directly after `self._usage_limit: str | None = None`, add:

```python
        # R5.1: epoch after which a usage-limited state stops suppressing liveness checks
        # (reported reset + 15 min, or 1 h after the bounce when no reset time is given).
        self._usage_limit_until: float = 0.0
```

(c) In `_run_session`'s opus limit branch, directly after `self._usage_limit = full_text`, add:

```python
                        _now = time.time()
                        _reset = parse_reset_time(full_text, _now)
                        self._usage_limit_until = (_reset + 900) if _reset is not None else _now + 3600
```

(d) In `needs_restart()`, replace

```python
        if self._usage_limit is not None:
            return False
```

with

```python
        if self._usage_limit is not None:
            if time.time() < self._usage_limit_until:
                return False
            # R5.1: the reset window has passed — stop suppressing, fall through to the
            # normal checks (idle PING probe, hard net, timeouts) so a stale state or a
            # wedged subprocess recovers.
            logger.info("Brain usage-limit window passed; resuming liveness checks")
            self._usage_limit = None
```

(e) In `restart()`, directly after `self._usage_limit = None`, add:

```python
        self._usage_limit_until = 0.0
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

## Task 8: Recovery sweep and aging-nudge wording (R5.2, R5.3)

**Files:**
- Modify: `commander/src/ironclaude/main.py`:
  - `__init__` (after `self._limit_alerted: dict[str, float] = {}`);
  - `_get_unprocessed_messages`;
  - `check_message_aging` nudge text;
  - a new `_sweep_after_usage_limit` method placed directly after `_get_unprocessed_messages`;
  - `poll_brain_responses`.
- Test: `commander/tests/test_main_validate.py` (`_make_poll_daemon`, and new tests after `test_brain_limit_prefixed_text_without_reset_still_alerts`).
- Test: `commander/tests/test_enforcement.py` (`TestCheckMessageAging.test_alerts_on_old_unprocessed`).

**Step 1: Write the tests (RED).**

(a) In `commander/tests/test_main_validate.py` `_make_poll_daemon`, add this line directly after `d._orphaned_unmerged_count = 0`:

```python
    d._brain_limited_since = None
```

(b) Add these after `test_brain_limit_prefixed_text_without_reset_still_alerts`:

```python
_SWEEP_LIMIT = "You've hit your limit · resets 4:10am (America/Chicago)"


def _sweep_daemon():
    d = _make_poll_daemon()
    d._limit_alerted = {}
    d._last_heartbeat_ts = "1700.1"
    d.config = {"slack_operator_user_id": "U_OP"}
    return d


def test_recovery_sweep_sends_unanswered_messages_once(monkeypatch):
    """R5.2: after a limit bounce, the first non-limit Brain response hands the Brain every
    operator message since the limit that has no disposition — once."""
    from ironclaude.brain_client import _LIMIT_PREFIX, _NARRATION_PREFIX
    from ironclaude.db import persist_operator_message_acknowledgement
    d = _sweep_daemon()
    monkeypatch.setattr("ironclaude.main.time.time", lambda: 10000.0)
    d.brain.get_pending_responses.return_value = [f"{_LIMIT_PREFIX}{_SWEEP_LIMIT}"]
    d.poll_brain_responses()
    assert d._brain_limited_since == 10000.0
    d.brain.send_message.assert_not_called()
    persist_operator_message_acknowledgement(d._db, "9950.000001", "closed")
    d.slack.get_recent_messages.return_value = [
        {"user": "U_OP", "ts": "9990.000001", "text": "are you there?"},
        {"user": "U_OP", "ts": "9950.000001", "text": "already closed"},
        {"user": "U_OTHER", "ts": "9991.000001", "text": "not the operator"},
    ]
    d.brain.get_pending_responses.return_value = [f"{_NARRATION_PREFIX}Back online."]
    d.poll_brain_responses()
    d.slack.get_recent_messages.assert_called_once_with(limit=200, oldest="9700.0")
    d.brain.send_message.assert_called_once()
    sent = d.brain.send_message.call_args.args[0]
    assert sent.startswith("[USAGE LIMIT RECOVERED]")
    assert "ts=9990.000001: are you there?" in sent
    assert "already closed" not in sent
    assert "not the operator" not in sent
    assert d._brain_limited_since is None
    d.brain.get_pending_responses.return_value = [f"{_NARRATION_PREFIX}More narration."]
    d.poll_brain_responses()
    d.brain.send_message.assert_called_once()


def test_recovery_sweep_triggered_by_ping_ack(monkeypatch):
    from ironclaude.brain_client import _NARRATION_PREFIX
    d = _sweep_daemon()
    monkeypatch.setattr("ironclaude.main.time.time", lambda: 10000.0)
    d._brain_limited_since = 9000.0
    d.slack.get_recent_messages.return_value = [
        {"user": "U_OP", "ts": "9500.000001", "text": "status?"},
    ]
    d.brain.get_pending_responses.return_value = [f"{_NARRATION_PREFIX}[PING-ACK]"]
    d.poll_brain_responses()
    d.brain.send_message.assert_called_once()
    assert "ts=9500.000001: status?" in d.brain.send_message.call_args.args[0]
    assert d._brain_limited_since is None


def test_recovery_sweep_with_nothing_unanswered_sends_nothing(monkeypatch):
    from ironclaude.brain_client import _NARRATION_PREFIX
    d = _sweep_daemon()
    monkeypatch.setattr("ironclaude.main.time.time", lambda: 10000.0)
    d._brain_limited_since = 9000.0
    d.slack.get_recent_messages.return_value = []
    d.brain.get_pending_responses.return_value = [f"{_NARRATION_PREFIX}Back online."]
    d.poll_brain_responses()
    d.brain.send_message.assert_not_called()
    assert d._brain_limited_since is None


def test_recovery_sweep_fetch_failure_retries_next_response(monkeypatch):
    from ironclaude.brain_client import _NARRATION_PREFIX
    d = _sweep_daemon()
    monkeypatch.setattr("ironclaude.main.time.time", lambda: 10000.0)
    d._brain_limited_since = 9000.0
    d.slack.get_recent_messages.side_effect = RuntimeError("slack down")
    d.brain.get_pending_responses.return_value = [f"{_NARRATION_PREFIX}Back online."]
    d.poll_brain_responses()
    d.brain.send_message.assert_not_called()
    assert d._brain_limited_since == 9000.0
    d.slack.get_recent_messages.side_effect = None
    d.slack.get_recent_messages.return_value = [
        {"user": "U_OP", "ts": "9500.000001", "text": "still there?"},
    ]
    d.poll_brain_responses()
    d.brain.send_message.assert_called_once()
    assert d._brain_limited_since is None
```

Broken states caught:
- **Once-only test:** fails with no sweep (today's defect, M1). It also fails if the sweep includes acknowledged or non-operator messages, uses the wrong bound, or sweeps twice.
- **PING-ACK test:** fails if the sweep runs only after the PING-ACK filter.
- **Nothing-unanswered test:** fails if an empty sweep sends a message or keeps the timestamp.
- **Failure test:** fails if a fetch error is treated as "nothing to send", which would lose messages.

(c) In `commander/tests/test_enforcement.py` `TestCheckMessageAging.test_alerts_on_old_unprocessed`, add after `assert "Fix the login bug" in msg`:

```python
        assert "reply with [reply-to:" in msg
        assert "or acknowledge it" not in msg
```

**Step 2: Run the tests and confirm they fail.**

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest -q tests/test_main_validate.py tests/test_enforcement.py -k "recovery_sweep or alerts_on_old_unprocessed"
```

Expected: 5 failed. The 4 sweep tests fail on the missing sweep and the missing `_brain_limited_since` handling. The aging test fails on the old wording.

**Step 3: Implement in `commander/src/ironclaude/main.py`.**

(a) `__init__`: directly after `self._limit_alerted: dict[str, float] = {}`, add:

```python
        # R5.2: epoch of the first Brain usage-limit bounce not yet followed by a recovery
        # sweep (None = not limited). See _sweep_after_usage_limit.
        self._brain_limited_since: float | None = None
```

(b) Replace the `_get_unprocessed_messages` header and its Slack fetch block

```python
    def _get_unprocessed_messages(self, max_age_seconds: int = 1800) -> list[dict]:
        """Find operator messages older than max_age with no durable disposition."""
        operator_user_id = self.config.get("slack_operator_user_id", "")
        if not operator_user_id or self._db is None:
            return []
        try:
            oldest = str(time.time() - 7200)
            messages = self.slack.get_recent_messages(limit=50, oldest=oldest)
        except Exception:
            return []
        now = time.time()
        try:
            dispositions = self._get_operator_message_dispositions()
        except Exception:
            return []
```

with

```python
    def _get_unprocessed_messages(
        self, max_age_seconds: int = 1800, oldest: float | None = None, limit: int = 50,
    ) -> list[dict]:
        """Find operator messages older than max_age with no durable disposition.

        With ``oldest`` (the usage-limit recovery sweep), history starts there instead of
        2 h ago and fetch/ledger errors propagate so the caller can retry; the aging
        caller keeps the old swallow-and-return-[] behavior."""
        operator_user_id = self.config.get("slack_operator_user_id", "")
        if not operator_user_id or self._db is None:
            return []
        try:
            bound = str(time.time() - 7200) if oldest is None else str(oldest)
            messages = self.slack.get_recent_messages(limit=limit, oldest=bound)
        except Exception:
            if oldest is not None:
                raise
            return []
        now = time.time()
        try:
            dispositions = self._get_operator_message_dispositions()
        except Exception:
            if oldest is not None:
                raise
            return []
```

The rest of the method is unchanged.

(c) Add this method directly after `_get_unprocessed_messages`:

```python
    def _sweep_after_usage_limit(self) -> None:
        """R5.2: after a Brain usage-limit bounce ends, hand the Brain every operator message
        that arrived during the limit and still has no directive or acknowledgement — the
        one-shot aging nudge cannot cover a limit that outlasts it. A fetch failure keeps
        the limit timestamp so the next Brain response retries."""
        since = self._brain_limited_since
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
        self._brain_limited_since = None
        if not pending:
            logger.info("Usage-limit recovery sweep: no unanswered operator messages")
            return
        lines = "\n".join(f"- ts={m['ts']}: {m.get('text', '')[:300]}" for m in pending)
        self.brain.send_message(
            "[USAGE LIMIT RECOVERED] These operator messages arrived while you were "
            "usage-limited and have no reply or directive yet. Handle each now: reply with "
            "[reply-to:<ts>] followed by your answer, or submit_directive().\n" + lines
        )
        logger.info("Usage-limit recovery sweep sent %d operator message(s) to the Brain", len(pending))
```

(d) In `check_message_aging`, replace

```python
                f"(ts: {ts}). Read this message and submit_directive() or acknowledge it."
```

with

```python
                f"(ts: {ts}). Read this message and reply with [reply-to:{ts}] followed by "
                f"your answer, or submit_directive(); use acknowledge_operator_message only "
                f"to close it without a reply."
```

(e) In `poll_brain_responses`, directly after `logger.info(f"Brain response: {text[:100]}...")`, add:

```python
            # R5.2: the first non-limit response after a usage-limit bounce means the Brain
            # is answering again (a [PING-ACK] counts) — sweep unanswered operator messages.
            if self._brain_limited_since is not None and not text.startswith(_LIMIT_PREFIX):
                self._sweep_after_usage_limit()
```

(f) In `poll_brain_responses`, replace

```python
            if text.startswith(_LIMIT_PREFIX):
                # R3.2: the Brain's own usage-limit bounce is surfaced by the alert above,
                # never relayed as narration or chatter.
                continue
```

with

```python
            if text.startswith(_LIMIT_PREFIX):
                # R3.2: the Brain's own usage-limit bounce is surfaced by the alert above,
                # never relayed as narration or chatter. R5.2: remember when the limit began.
                if self._brain_limited_since is None:
                    self._brain_limited_since = time.time()
                continue
```

**Step 4: Run the tests and confirm they pass.**

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest -q tests/test_main_validate.py tests/test_enforcement.py tests/test_daemon.py
```

Expected: all pass, 0 failed.

**Step 5: Stage.**

```bash
git -C /Users/roberthyatt/Code/ironclaude add commander/src/ironclaude/main.py commander/tests/test_main_validate.py commander/tests/test_enforcement.py
```

---

## Task 9: CHANGELOG update and full suite (R5.4)

**Depends on:** Tasks 7 and 8.

**No tests required:** this task edits docs only; the full suite verifies it.

**Files:**
- Modify: `CHANGELOG.md` (the `## 1.1.14` section)

**Step 1: Run the full commander suite.**

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest -q
```

Expected: 0 failed. Record the passed count N.

**Step 2: Edit the CHANGELOG.**

(a) In the `## 1.1.14` third bullet, replace

```
The state clears on the next real reply, and the existing aging reminder re-sends unanswered messages.
```

with

```
The limited state lasts until the reported reset time plus 15 minutes (one hour when no reset time is given) — after that the normal liveness probes and restarts resume, so a stale heartbeat or a wedged session recovers — and it also clears on the next real reply. When the Brain answers again (a `[PING-ACK]` counts), the daemon hands it every operator message that arrived during the limit and still has no reply or directive, as one `[USAGE LIMIT RECOVERED]` message; a failed Slack fetch retries on the next response.
```

(b) In the same section's second bullet, replace

```
The persistence layer gains
```

with

```
The unprocessed-message aging reminder now says the same (reply with `[reply-to:<ts>]` or `submit_directive()`). The persistence layer gains
```

(c) In the Deploy line, replace `Suites: commander 3397.` with `Suites: commander <N>.`, using the count from Step 1.

**Step 3: Verify.**

```bash
rg -n -F -e "USAGE LIMIT RECOVERED" -e "plus 15 minutes" -e "aging reminder now says the same" /Users/roberthyatt/Code/ironclaude/CHANGELOG.md
```

Expected: 2 lines. `CHANGELOG.md:17` (the second bullet) contains the aging-reminder text; `CHANGELOG.md:18` (the third bullet) contains both "plus 15 minutes" and "USAGE LIMIT RECOVERED", so rg prints it once.

```bash
rg -n -F "re-sends unanswered messages" /Users/roberthyatt/Code/ironclaude/CHANGELOG.md
```

Expected: no output, exit 1.

**Step 4: Stage.**

```bash
git -C /Users/roberthyatt/Code/ironclaude add CHANGELOG.md
```

```bash
git -C /Users/roberthyatt/Code/ironclaude add -f docs/plans/2026-09-28-v1-1-14-slack-reply-and-limit-fixes-requirements.md docs/plans/2026-09-28-v1-1-14-slack-reply-and-limit-fixes-design.md docs/plans/2026-09-29-v1-1-14-limit-expiry-and-recovery-sweep.md docs/plans/2026-09-29-v1-1-14-limit-expiry-and-recovery-sweep.plan.json
```

Expected: exit 0.
