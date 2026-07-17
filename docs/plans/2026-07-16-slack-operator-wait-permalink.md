# Slack Permalink for "Waiting on Operator" Escalations Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** Add a Slack permalink to daemon-posted "Waiting on Operator" escalation messages, and a "Link" field to the Brain-authored `[BLOCKED]` escalation template.

**Architecture:** `SlackBot` gains `get_permalink`, `update_message`, and a `prefix` property; `_maybe_capture_operator_wait` in `main.py` posts the alert, fetches its permalink, and edits the message in place to append `Link: <permalink>`. Separately, `workflow.md`'s Decision Format template gains a required `**Link:**` field pointing to the context (code/doc/episodic-memory) grounding a Brain-authored blocked-task decision, since that message has no Slack `ts` yet at authoring time.

**Tech Stack:** Python, `slack_sdk.WebClient` (`chat_getPermalink`, `chat_update`), pytest + `unittest.mock`.

---

## Task 1: SlackBot permalink + update-message support

**Files:**
- Modify: `commander/src/ironclaude/slack_interface.py`
- Test: `commander/tests/test_slack_interface.py` (existing file — append new test classes; do not touch existing tests)

**Step 1: Write tests (RED)**

Append to the end of `commander/tests/test_slack_interface.py` (after the `TestSlackBotDownloadFile` class, following the file's existing `@patch("ironclaude.slack_interface.WebClient")` convention):

```python
class TestGetPermalink:
    @patch("ironclaude.slack_interface.WebClient")
    def test_get_permalink_returns_url_on_success(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client.chat_getPermalink.return_value = {
            "permalink": "https://workspace.slack.com/archives/C123/p1234567890"
        }
        mock_client_cls.return_value = mock_client
        bot = SlackBot(token="xoxb-test", channel_id="C123")
        result = bot.get_permalink("1234567890.123456")
        assert result == "https://workspace.slack.com/archives/C123/p1234567890"

    @patch("ironclaude.slack_interface.WebClient")
    def test_get_permalink_returns_none_on_api_failure(self, mock_client_cls, caplog):
        mock_client = MagicMock()
        mock_client.chat_getPermalink.side_effect = Exception("api error")
        mock_client_cls.return_value = mock_client
        bot = SlackBot(token="xoxb-test", channel_id="C123")
        with caplog.at_level(logging.WARNING, logger="ironclaude.slack"):
            result = bot.get_permalink("1234567890.123456")
        assert result is None

    @patch("ironclaude.slack_interface.WebClient")
    def test_get_permalink_uses_correct_channel_and_ts(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client.chat_getPermalink.return_value = {"permalink": "https://x"}
        mock_client_cls.return_value = mock_client
        bot = SlackBot(token="xoxb-test", channel_id="C123")
        bot.get_permalink("1234567890.123456")
        mock_client.chat_getPermalink.assert_called_once_with(
            channel="C123", message_ts="1234567890.123456"
        )


class TestUpdateMessage:
    @patch("ironclaude.slack_interface.WebClient")
    def test_update_message_returns_true_on_success(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        bot = SlackBot(token="xoxb-test", channel_id="C123")
        result = bot.update_message("1234.5678", "updated text")
        assert result is True

    @patch("ironclaude.slack_interface.WebClient")
    def test_update_message_returns_false_on_api_failure(self, mock_client_cls, caplog):
        mock_client = MagicMock()
        mock_client.chat_update.side_effect = Exception("api error")
        mock_client_cls.return_value = mock_client
        bot = SlackBot(token="xoxb-test", channel_id="C123")
        with caplog.at_level(logging.WARNING, logger="ironclaude.slack"):
            result = bot.update_message("1234.5678", "updated text")
        assert result is False

    @patch("ironclaude.slack_interface.WebClient")
    def test_update_message_calls_correct_channel_ts_text(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        bot = SlackBot(token="xoxb-test", channel_id="C123")
        bot.update_message("1234.5678", "updated text")
        mock_client.chat_update.assert_called_once_with(
            channel="C123", ts="1234.5678", text="updated text"
        )


class TestSlackBotPrefixProperty:
    @patch("ironclaude.slack_interface.WebClient")
    def test_prefix_property_returns_configured_prefix(self, mock_client_cls):
        bot = SlackBot(token="xoxb-test", channel_id="C123")
        assert bot.prefix == "[IRONCLAUDE] "
```

Run:
```bash
cd commander && PYTHONUNBUFFERED=1 python -m pytest tests/test_slack_interface.py -v -k "TestGetPermalink or TestUpdateMessage or TestSlackBotPrefixProperty"
```

Expected: FAIL — `AttributeError: 'SlackBot' object has no attribute 'get_permalink'` (and similarly for `update_message`/`prefix`).

**Step 2: Implement (GREEN)**

In `commander/src/ironclaude/slack_interface.py`, add these three members to the `SlackBot` class, immediately after `get_message` (after line 300, before the `# --- Slash command definitions ---` section at line 302-303):

```python
    def get_permalink(self, message_ts: str) -> str | None:
        """Fetch a Slack permalink for a message. Returns None on failure."""
        try:
            response = self._client.chat_getPermalink(
                channel=self._channel_id, message_ts=message_ts,
            )
            return response.get("permalink")
        except Exception as e:
            logger.warning(f"Slack get_permalink failed: {e}")
            return None

    def update_message(self, ts: str, new_text: str) -> bool:
        """Edit an existing message in place. new_text must already include
        any prefix — this method sends it verbatim. Returns True on success."""
        try:
            self._client.chat_update(channel=self._channel_id, ts=ts, text=new_text)
            return True
        except Exception as e:
            logger.warning(f"Slack update_message failed: {e}")
            return False

    @property
    def prefix(self) -> str:
        return self._prefix
```

Run:
```bash
cd commander && PYTHONUNBUFFERED=1 python -m pytest tests/test_slack_interface.py -v
```

Expected: all tests in the file PASS (existing tests + 7 new: 3 `get_permalink` + 3 `update_message` + 1 `prefix`).

**Step 3: Stage changes**

Run:
```bash
git add commander/src/ironclaude/slack_interface.py commander/tests/test_slack_interface.py
```

Expected: changes staged (professional mode blocks commit).

---

## Task 2: Wire permalink enrichment into `_maybe_capture_operator_wait`

**Depends on:** Task 1

**Files:**
- Modify: `commander/src/ironclaude/main.py:1465-1500` (the `_maybe_capture_operator_wait` method)
- Test: `commander/tests/test_main_operator_wait.py` (new file)

**Step 1: Write tests (RED)**

Create `commander/tests/test_main_operator_wait.py`:

```python
"""Tests for the permalink-enrichment path in IroncladeDaemon._maybe_capture_operator_wait."""
from unittest.mock import MagicMock

from ironclaude.main import IroncladeDaemon


def _make_daemon():
    daemon = IroncladeDaemon.__new__(IroncladeDaemon)
    daemon._grader = MagicMock()
    daemon._operator_wait_alerted = {}
    daemon._operator_waits = {}
    daemon.config = {"operator_name": "Robert"}
    daemon.slack = MagicMock()
    return daemon


def _grade_awaiting(worker_id="d1267", question="Should I use approach A or B?"):
    return {
        "awaiting_operator": True,
        "worker_id": worker_id,
        "question": question,
    }


class TestOperatorWaitPermalink:
    def test_operator_wait_updates_message_with_permalink(self):
        daemon = _make_daemon()
        daemon._grader.grade.return_value = _grade_awaiting()
        daemon.slack.post_message.return_value = "1234.5678"
        daemon.slack.get_permalink.return_value = "https://workspace.slack.com/archives/C123/p12345678"
        daemon.slack.prefix = "[IRONCLAUDE] "

        captured = daemon._maybe_capture_operator_wait("Still holding, awaiting your decision")

        assert captured is True
        daemon.slack.update_message.assert_called_once_with(
            "1234.5678",
            "[IRONCLAUDE] ⏳ *Waiting on Robert:* `d1267` — Should I use approach A or B?\nLink: https://workspace.slack.com/archives/C123/p12345678",
        )

    def test_operator_wait_skips_update_when_post_returns_none(self):
        daemon = _make_daemon()
        daemon._grader.grade.return_value = _grade_awaiting()
        daemon.slack.post_message.return_value = None

        daemon._maybe_capture_operator_wait("Still holding, awaiting your decision")

        daemon.slack.get_permalink.assert_not_called()
        daemon.slack.update_message.assert_not_called()

    def test_operator_wait_skips_update_when_permalink_returns_none(self):
        daemon = _make_daemon()
        daemon._grader.grade.return_value = _grade_awaiting()
        daemon.slack.post_message.return_value = "1234.5678"
        daemon.slack.get_permalink.return_value = None

        daemon._maybe_capture_operator_wait("Still holding, awaiting your decision")

        daemon.slack.update_message.assert_not_called()
```

Run:
```bash
cd commander && PYTHONUNBUFFERED=1 python -m pytest tests/test_main_operator_wait.py -v
```

Expected: FAIL — `test_operator_wait_updates_message_with_permalink` fails because `update_message` is never called (current code discards `post_message`'s return value); the other two tests currently PASS vacuously (since nothing calls `get_permalink`/`update_message` at all yet) but are included now so all three lock in the target behavior together once Step 2 lands.

**Step 2: Implement (GREEN)**

In `commander/src/ironclaude/main.py`, replace lines 1493-1498:

```python
        if self._operator_wait_alerted.get(worker_id) != question:
            self._operator_wait_alerted[worker_id] = question
            operator_name = self.config.get("operator_name", "Operator")
            self.slack.post_message(
                f"⏳ *Waiting on {operator_name}:* `{worker_id}` — {_escape_mrkdwn(question) or '(awaiting your reply)'}"
            )
```

with:

```python
        if self._operator_wait_alerted.get(worker_id) != question:
            self._operator_wait_alerted[worker_id] = question
            operator_name = self.config.get("operator_name", "Operator")
            alert_body = f"⏳ *Waiting on {operator_name}:* `{worker_id}` — {_escape_mrkdwn(question) or '(awaiting your reply)'}"
            ts = self.slack.post_message(alert_body)
            if ts:
                permalink = self.slack.get_permalink(ts)
                if permalink:
                    self.slack.update_message(ts, f"{self.slack.prefix}{alert_body}\nLink: {permalink}")
```

Run:
```bash
cd commander && PYTHONUNBUFFERED=1 python -m pytest tests/test_main_operator_wait.py -v
```

Expected: all 3 tests PASS.

**Step 3: Stage changes**

Run:
```bash
git add commander/src/ironclaude/main.py commander/tests/test_main_operator_wait.py
```

Expected: changes staged.

---

## Task 3: Add required `Link:` field to workflow.md Decision Format

**Files:**
- Modify: `commander/src/brain/rules/workflow.md:736-761`

No tests required: pure documentation/template content — there is no test framework covering prose template text, and the field's presence has no runtime code path to exercise (the Brain reads and follows this file as instructions; it isn't parsed or validated by code).

**Step 1: Edit the template**

In `commander/src/brain/rules/workflow.md`, replace lines 736-761:

```
#### Decision Format

Every blocked-task escalation message posted to Slack MUST follow this template. No ad-hoc prose — use the structure:

```
[BLOCKED] Task <ID>: <Task description>

**Problem:** <1–2 sentences stating what is blocked and why>

**Options:**
1. **<Option A>** — <description>
   - Pro: <benefit>
   - Con: <drawback>
2. **<Option B>** — <description>
   - Pro: <benefit>
   - Con: <drawback>
(2–4 options total)

**Recommendation:** Option <N> — <reasoning tied to known project priorities>

**Prediction:** {OPERATOR_NAME} will likely choose Option <N> because <reasoning from episodic memory and known preferences>.

**To unblock:** <specific action {OPERATOR_NAME} needs to take>
```

Predictions must be grounded in episodic memory. Search for how {OPERATOR_NAME} has resolved similar blockers before composing the prediction.
```

with:

```
#### Decision Format

Every blocked-task escalation message posted to Slack MUST follow this template. No ad-hoc prose — use the structure:

```
[BLOCKED] Task <ID>: <Task description>

**Problem:** <1–2 sentences stating what is blocked and why>

**Options:**
1. **<Option A>** — <description>
   - Pro: <benefit>
   - Con: <drawback>
2. **<Option B>** — <description>
   - Pro: <benefit>
   - Con: <drawback>
(2–4 options total)

**Recommendation:** Option <N> — <reasoning tied to known project priorities>

**Prediction:** {OPERATOR_NAME} will likely choose Option <N> because <reasoning from episodic memory and known preferences>.

**To unblock:** <specific action {OPERATOR_NAME} needs to take>

**Link:** <link to the code, doc, or episodic-memory context grounding this decision — not a link to this message itself, which does not exist yet when this template is composed>
```

Predictions must be grounded in episodic memory. Search for how {OPERATOR_NAME} has resolved similar blockers before composing the prediction. The **Link:** field is required — point to whatever material best lets {OPERATOR_NAME} verify the recommendation without re-deriving it (a file:line, a design doc, or a prior decision).
```

**Step 2: Stage changes**

Run:
```bash
git add commander/src/brain/rules/workflow.md
```

Expected: changes staged.

---

## Task 4: Full suite verification and doc staging

**Depends on:** Task 1, Task 2, Task 3

**Files:** none modified — verification only.

**Step 1: Run full test suite**

Run:
```bash
cd commander && PYTHONUNBUFFERED=1 python -m pytest tests/ -v
```

Expected: all tests PASS, including the 3 new `TestGetPermalink` tests, 3 new `TestUpdateMessage` tests, 1 new `TestSlackBotPrefixProperty` test, and 3 new `TestOperatorWaitPermalink` tests (10 new total across the two files) — no regressions in the pre-existing staged threading-feature tests in `test_daemon.py`/`test_main_validate.py`/`test_slack_interface.py`.

**Step 2: Stage design and plan docs**

Run:
```bash
git add -f docs/plans/2026-07-16-slack-operator-wait-permalink-design.md docs/plans/2026-07-16-slack-operator-wait-permalink.md docs/plans/2026-07-16-slack-operator-wait-permalink.plan.json
```

Expected: design + plan docs staged (`docs/` is gitignored; `-f` overrides).
