# v1.1.14 Simplification: Replace the Recovery Sweep with a Notice Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** Remove the usage-limit recovery sweep and replace it with:
- one Slack recovery notice;
- the aging reminder staying quiet while the Brain is limited;
- honest heartbeat and docs wording.

**Requirements:** `docs/plans/2026-09-28-v1-1-14-slack-reply-and-limit-fixes-requirements.md` (R8)

**Design:** `docs/plans/2026-09-28-v1-1-14-slack-reply-and-limit-fixes-design.md` (component 8)

**Starting state:** this builds on the staged v1.1.14 work.
- **`main.py`** currently has:
  - the `_brain_limited_since` and `_brain_limited_last` fields;
  - `_get_unprocessed_messages(self, max_age_seconds=1800, oldest=None, limit=50)`, which re-raises when `oldest` is given;
  - `_sweep_after_usage_limit`;
  - a `sweep_due` flag, set in the `poll_brain_responses` loop and acted on after it;
  - the `_LIMIT_PREFIX` branch, which sets both timestamps.
- **`test_main_validate.py`** has `_SWEEP_LIMIT`, `_sweep_daemon`, 8 `test_recovery_sweep_*` / `test_brain_limited_last_*` tests between `test_brain_limit_prefixed_text_without_reset_still_alerts` and `test_format_mem_line_skips_unreadable_procs`, and `_make_poll_daemon` with `_brain_limited_since` and `_brain_limited_last`.
- **The brain_client limit handling** (tag, no liveness credit, expiry, PING clear, 6 h cap) is unchanged by this plan.

**Tech Stack:** Python 3.11, pytest.

## Execution invariants (every step)

- **Shell state does not persist between steps.** Use literal absolute paths.
- **Test commands** run as `cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest …`. Staging uses `git -C /Users/roberthyatt/Code/ironclaude add …`.
- **`docs/` is gitignored.** Plan artifacts need `git add -f`.
- **No `2>/dev/null` on evidence commands.** `rg` exits 1 on no match.
- **No commit, tag, push or deploy.**

---

## Task 13: Remove the sweep; add the recovery notice and aging suppression (R8.1–R8.3)

**Files:**
- Modify: `commander/src/ironclaude/main.py`
- Test: `commander/tests/test_main_validate.py`
- Test: `commander/tests/test_enforcement.py`

**Step 1: Replace the sweep tests with notice tests (RED).**

(a) In `commander/tests/test_main_validate.py` `_make_poll_daemon`, delete the line `    d._brain_limited_last = None`. Keep `d._brain_limited_since = None`.

(b) Delete everything from the line `_SWEEP_LIMIT = "You've hit your limit · resets 4:10am (America/Chicago)"` down to the blank lines just before `def test_format_mem_line_skips_unreadable_procs():`. That removes `_SWEEP_LIMIT`, `_sweep_daemon`, the 8 `test_recovery_sweep_*` tests and `test_brain_limited_last_tracks_every_bounce`. Replace them with the block below, keeping two blank lines before `def test_format_mem_line_skips_unreadable_procs():`:

```python
_NOTICE_LIMIT = "You've hit your limit · resets 4:10am (America/Chicago)"


def _recovery_notices(d):
    return [
        c for c in d.slack.post_message.call_args_list
        if "Brain recovered from its usage limit" in str(c.args[0])
    ]


def test_recovery_notice_posted_once_after_limit(monkeypatch):
    """R8.2: the first non-limit Brain response after a usage-limit bounce posts ONE
    top-level Slack recovery notice and clears the limit timestamp; nothing is re-fed to
    the Brain."""
    from ironclaude.brain_client import _LIMIT_PREFIX, _NARRATION_PREFIX
    d = _make_poll_daemon()
    d._limit_alerted = {}
    d._last_heartbeat_ts = "1700.1"
    monkeypatch.setattr("ironclaude.main.time.time", lambda: 10000.0)
    d.brain.get_pending_responses.return_value = [f"{_LIMIT_PREFIX}{_NOTICE_LIMIT}"]
    d.poll_brain_responses()
    assert d._brain_limited_since == 10000.0
    assert _recovery_notices(d) == []
    d.brain.get_pending_responses.return_value = [
        f"{_NARRATION_PREFIX}Back online.", f"{_NARRATION_PREFIX}Still here.",
    ]
    d.poll_brain_responses()
    notices = _recovery_notices(d)
    assert len(notices) == 1
    assert "resend anything older than 2 hours" in notices[0].args[0]
    assert notices[0].kwargs.get("thread_ts") is None
    assert d._brain_limited_since is None
    d.brain.send_message.assert_not_called()


def test_recovery_notice_on_ping_ack():
    """R8.2: a [PING-ACK] is a real response too, so it also ends the limit."""
    from ironclaude.brain_client import _NARRATION_PREFIX
    d = _make_poll_daemon()
    d._brain_limited_since = 9000.0
    d.brain.get_pending_responses.return_value = [f"{_NARRATION_PREFIX}[PING-ACK]"]
    d.poll_brain_responses()
    assert len(_recovery_notices(d)) == 1
    assert d._brain_limited_since is None
```

(c) In `commander/tests/test_enforcement.py` `class TestCheckMessageAging`, add this method directly after `test_alerts_on_old_unprocessed`:

```python
    def test_suppressed_while_brain_usage_limited(self, daemon):
        """R8.3: while the Brain is usage-limited the aging reminder stays quiet (and marks
        nothing), so it fires normally after recovery."""
        daemon._last_message_aging_check = 0.0
        daemon._brain_limited_since = time.time() - 600
        old_ts = str(time.time() - 2400)
        daemon.slack.get_recent_messages.return_value = [
            {"text": "Fix the login bug", "ts": old_ts, "user": "U_OPERATOR"},
        ]
        daemon.check_message_aging()
        daemon.brain.send_message.assert_not_called()
        assert old_ts not in daemon._message_aging_alerted
```

Broken states caught:
- **Notice test:** fails without the notice, if the notice repeats, if it is threaded, if the timestamp is not cleared, or if anything is still re-fed to the Brain.
- **PING-ACK test:** fails if a PING-ACK doesn't count as recovery.
- **Aging test:** fails if the reminder fires, or marks the message as nudged, while the Brain is limited.

**Step 2: Run the tests and confirm they fail.**

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest -q tests/test_main_validate.py tests/test_enforcement.py -k "recovery_notice or suppressed_while_brain_usage_limited"
```

Expected: 3 failed. With today's code, the sweep clears the timestamp with no notice, and the aging reminder fires.

**Step 3: Implement in `commander/src/ironclaude/main.py`.**

(a) In `__init__`, replace

```python
        # R5.2: epoch of the first Brain usage-limit bounce not yet followed by a recovery
        # sweep (None = not limited). See _sweep_after_usage_limit.
        self._brain_limited_since: float | None = None
        # R6.1: epoch of the LATEST limit bounce; the recovery sweep only hands over operator
        # messages that arrived up to it (later ones were never bounced).
        self._brain_limited_last: float | None = None
```

with

```python
        # R8: epoch of the first Brain usage-limit bounce not yet followed by a real response
        # (None = not limited). Drives the one recovery notice and the aging-reminder pause.
        self._brain_limited_since: float | None = None
```

(b) Restore `_get_unprocessed_messages` to its original form. Replace

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

with

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

(c) Delete the whole `_sweep_after_usage_limit` method, from `    def _sweep_after_usage_limit(self) -> None:` through its final line, `        logger.info("Usage-limit recovery sweep sent %d operator message(s) to the Brain", len(pending))`, plus the blank line after it. The next method, `_validate_brain_message`, must stay separated from `_get_unprocessed_messages` by exactly one blank line.

(d) In `check_message_aging`, directly after `self._last_message_aging_check = now`, add:

```python
        # R8.3: while the Brain is usage-limited a nudge would only bounce and burn the
        # message's one reminder — stay quiet (mark nothing) until it answers again.
        if self._brain_limited_since is not None:
            return
```

(e) In `poll_brain_responses`, replace

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

with

```python
        for text in self.brain.get_pending_responses():
            logger.info(f"Brain response: {text[:100]}...")
            # R8.2: the first non-limit response after a usage-limit bounce means the Brain
            # is answering again (a [PING-ACK] counts) — tell the operator once.
            if self._brain_limited_since is not None and not text.startswith(_LIMIT_PREFIX):
                since = datetime.fromtimestamp(self._brain_limited_since).strftime("%H:%M")
                self.slack.post_message(
                    f"✅ Brain recovered from its usage limit (limited since {since}). "
                    "Unanswered messages from that window get the normal aging reminder; "
                    "resend anything older than 2 hours."
                )
                self._brain_limited_since = None
```

(f) In the `_LIMIT_PREFIX` branch of `poll_brain_responses`, replace

```python
                _bounce_at = time.time()
                if self._brain_limited_since is None:
                    self._brain_limited_since = _bounce_at
                self._brain_limited_last = _bounce_at
                continue
```

with

```python
                if self._brain_limited_since is None:
                    self._brain_limited_since = time.time()
                continue
```

(g) At the end of `poll_brain_responses`, delete these two lines, which follow the `for` loop:

```python
        if sweep_due and self._brain_limited_since is not None:
            self._sweep_after_usage_limit()
```

**Step 4: Run the tests and confirm they pass.**

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest -q tests/test_main_validate.py tests/test_enforcement.py tests/test_daemon.py
```

Expected: all pass, 0 failed.

**Step 5: Confirm that no sweep code or tests remain.**

```bash
rg -n -e "_sweep_after_usage_limit" -e "_brain_limited_last" -e "sweep_due" -e "USAGE LIMIT RECOVERED" -e "oldest: float" -e "_SWEEP_LIMIT" /Users/roberthyatt/Code/ironclaude/commander/src /Users/roberthyatt/Code/ironclaude/commander/tests
```

Expected: no output, exit 1.

**Step 6: Stage.**

```bash
git -C /Users/roberthyatt/Code/ironclaude add commander/src/ironclaude/main.py commander/tests/test_main_validate.py commander/tests/test_enforcement.py
```

---

## Task 14: Honest heartbeat wording (R8.4)

**Files:**
- Modify: `commander/src/ironclaude/notifications.py:200`
- Test: `commander/tests/test_notifications.py:411`

**Step 1: Update the test (RED).** In `test_heartbeat_usage_limited_replaces_turn_in_progress`, replace

```python
        assert "usage-limited (resets 4:10am (America/Chicago)); unanswered messages retried after reset" in msg
```

with

```python
        assert "usage-limited (resets 4:10am (America/Chicago)); resend urgent messages after reset" in msg
        assert "retried" not in msg
```

**Step 2: Run the test and confirm it fails.**

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest -q tests/test_notifications.py -k usage_limited_replaces
```

Expected: 1 failed.

**Step 3: Implement.** In `commander/src/ironclaude/notifications.py`, replace the string literal `"unanswered messages retried after reset"` with `"resend urgent messages after reset"`.

**Step 4: Run the tests and confirm they pass.**

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest -q tests/test_notifications.py
```

Expected: all pass, 0 failed.

**Step 5: Stage.**

```bash
git -C /Users/roberthyatt/Code/ironclaude add commander/src/ironclaude/notifications.py commander/tests/test_notifications.py
```

---

## Task 15: CHANGELOG, README and full suite (R8.5)

**Depends on:** Tasks 13 and 14.

**No tests required:** this task edits docs only; the full suite verifies it.

**Files:**
- Modify: `CHANGELOG.md` (the `## 1.1.14` third bullet, line 18; a known-issue line after the Deploy line)
- Modify: `README.md:22`

**Step 1: Run the full commander suite.**

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest -q
```

Expected: 0 failed. Record the passed count N.

**Step 2: Edit the CHANGELOG.**

(a) Replace the entire `## 1.1.14` third bullet, the line starting `- **A usage-limited Brain on the opus fallback is no longer silent.**` (line 18), with this single line:

```
- **A usage-limited Brain on the opus fallback is no longer silent.** The "You've hit your limit" reply was neither raised (already on opus) nor queued, so no limit alert fired, the bounce counted as liveness, and the heartbeat read "turn in progress" while operator messages went unanswered. It is now queued with a limit tag that raises the existing account-limit alert (30-minute cooldown) and is never relayed as narration; it earns no liveness credit (a bounced `[PING]` probe is cleared without counting as liveness); timeout restarts are suppressed while limited (a restart cannot clear a limit) though a dead Brain is still restarted; and the heartbeat's Brain line reads "usage-limited (resets …); resend urgent messages after reset". The limited state lasts until the reported reset time (capped at six hours) plus 15 minutes, or one hour when no reset time is given — after that the normal liveness probes and restarts resume — and it also clears on the next real reply. The first real response after a limit (a `[PING-ACK]` counts) posts one Slack notice that the Brain has recovered, and the unprocessed-message aging reminder stays quiet while the Brain is limited so it fires normally afterwards. `detect_account_limit` moved to `brain_client.py` (re-exported by `main`). (`brain_client.py`, `main.py`, `notifications.py`; covered by `test_brain_client.py` / `test_main_validate.py` / `test_enforcement.py` / `test_notifications.py`.)
```

(b) In the Deploy line, replace `Suites: commander 3410.` with `Suites: commander <N>.`, using the count from Step 1.

(c) Directly after the Deploy line, the last line of the `## 1.1.14` section, add this line:

```
- Known issue: a multi-day usage limit keeps the Brain on the opus fallback, cycling bounce → window expiry → probe, with no automatic return to the original model; restart Commander after the limit clears.
```

**Step 3: Edit the README.** In `README.md` line 22, replace the sentence `Unanswered messages are retried after the reset.` with `When it recovers, a Slack notice tells you so; resend anything urgent.`

**Step 4: Verify the docs.**

```bash
rg -n -F -e "USAGE LIMIT RECOVERED" -e "retried after" /Users/roberthyatt/Code/ironclaude/CHANGELOG.md /Users/roberthyatt/Code/ironclaude/README.md
```

Expected: no output, exit 1.

```bash
rg -n -F -e "Known issue: a multi-day usage limit" -e "a Slack notice tells you so" /Users/roberthyatt/Code/ironclaude/CHANGELOG.md /Users/roberthyatt/Code/ironclaude/README.md
```

Expected: 2 lines, one in each file.

**Step 5: Stage.**

```bash
git -C /Users/roberthyatt/Code/ironclaude add CHANGELOG.md README.md
```

```bash
git -C /Users/roberthyatt/Code/ironclaude add -f docs/plans/2026-09-28-v1-1-14-slack-reply-and-limit-fixes-requirements.md docs/plans/2026-09-28-v1-1-14-slack-reply-and-limit-fixes-design.md docs/plans/2026-09-29-v1-1-14-simplify-limit-recovery.md docs/plans/2026-09-29-v1-1-14-simplify-limit-recovery.plan.json
```

Expected: exit 0.
