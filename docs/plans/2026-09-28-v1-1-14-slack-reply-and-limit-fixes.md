# v1.1.14 Slack Reply and Usage-Limit Fixes Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** Fix three Brain→Slack problems and release v1.1.14:
- a Brain reply split across messages lands in the heartbeat thread instead of the operator's thread;
- a no-reply acknowledgement is invisible to the operator;
- on the opus fallback, a usage-limit bounce is swallowed silently.

**Requirements:** `docs/plans/2026-09-28-v1-1-14-slack-reply-and-limit-fixes-requirements.md`

**Design:** `docs/plans/2026-09-28-v1-1-14-slack-reply-and-limit-fixes-design.md`

**Architecture:**
- `brain_client` carries a turn's `[reply-to:<ts>]` target onto later unmarked text in the same turn. The daemon's existing marker-led routing then threads the text and adds ✅.
- `acknowledge_operator_message` posts its reason and ✅ in the operator's thread when it creates the acknowledgement. The Brain prompt makes the reply path own closure, so conversational replies no longer call it first.
- On the opus fallback, `brain_client` tags a usage-limit reply with `_LIMIT_PREFIX`, records `_usage_limit`, and withholds liveness credit and timeout restarts. The daemon raises the existing account-limit alert, and the heartbeat reports the limit honestly.

**Tech Stack:** Python 3.11, pytest, claude_agent_sdk (mocked), slack_sdk (mocked), SQLite.

## Execution invariants (every step)

- **Shell state does not persist between steps.** Use literal absolute paths, and never rely on a variable set by an earlier step.
- **Test commands** run as `cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest …`. Staging uses `git -C /Users/roberthyatt/Code/ironclaude add …`.
- **`docs/` is gitignored.** Plan artifacts need `git add -f`.
- **zsh `nomatch`:** quote any glob.
- **No `2>/dev/null` on evidence commands.** An empty `rg` result must be distinguishable from a failure; `rg` exits 1 on no match.
- **Every new test names the broken state it catches.** Each one fails when its fix is reverted.
- **No commits.** The release commit and tag come after execution and the end review. Nothing is pushed or deployed without the operator's explicit go.

---

## Task 1: Carry the reply target through a Brain turn (R1.1–R1.5)

**Files:**
- Modify: `commander/src/ironclaude/brain_client.py`:
  - imports (after :30);
  - `__init__` (after :239);
  - `_run_session` (:815-852);
  - `restart()` (:1048).
- Test: `commander/tests/test_brain_client.py` (new class after `TestModelUnavailableText`, which ends at :2100).
- Test: `commander/tests/test_main_validate.py` (new test after `test_reply_long_text_is_chunked`, :571-576).

**Step 1: Write the brain_client tests (RED).** Insert this class immediately before `class TestModelUnavailableFableTransition:` in `commander/tests/test_brain_client.py`:

```python
class TestTurnReplyTarget:
    """R1: a [reply-to:<ts>] marker seen in a Brain turn is carried onto later unmarked
    text in the SAME turn (the Brain may send the marker alone, then the answer after a
    tool call; the daemon drops the marker-only message as a ghost post)."""

    @staticmethod
    def _run(messages):
        import asyncio
        from unittest.mock import patch, MagicMock

        class CapturingOptions:
            def __init__(self, **kwargs):
                pass

        async def fake_query(prompt=None, options=None):
            for m in messages:
                yield m

        client = BrainClient()
        client._grader = MagicMock()
        client._grader.grade.return_value = {"permission_seeking": False}
        with patch("claude_agent_sdk.ClaudeAgentOptions", CapturingOptions), \
             patch("claude_agent_sdk.query", fake_query):
            client._episodic_memory_path = "/fake/memory.js"
            asyncio.run(client._brain_session("my-prompt", None, None))
        return client

    @staticmethod
    def _am(text):
        from claude_agent_sdk import AssistantMessage
        from claude_agent_sdk.types import TextBlock
        return AssistantMessage(content=[TextBlock(text=text)], model="opus")

    @staticmethod
    def _result():
        from unittest.mock import MagicMock
        from claude_agent_sdk.types import ResultMessage
        r = MagicMock(spec=ResultMessage)
        r.session_id = "s"
        r.usage = None
        r.total_cost_usd = None
        return r

    def test_marker_only_then_unmarked_answer_carries_target(self):
        from ironclaude.brain_client import _NARRATION_PREFIX
        client = self._run([self._am("[reply-to:1699.5]"), self._am("Here is the answer.")])
        assert client.get_pending_responses() == [
            f"{_NARRATION_PREFIX}[reply-to:1699.5]",
            f"{_NARRATION_PREFIX}[reply-to:1699.5] Here is the answer.",
        ]

    def test_target_cleared_at_turn_end(self):
        from ironclaude.brain_client import _NARRATION_PREFIX
        client = self._run([
            self._am("[reply-to:1699.5] ack"), self._result(), self._am("unrelated narration"),
        ])
        assert client.get_pending_responses()[-1] == f"{_NARRATION_PREFIX}unrelated narration"

    def test_later_marker_replaces_target(self):
        from ironclaude.brain_client import _NARRATION_PREFIX
        client = self._run([
            self._am("[reply-to:1.1] a"), self._am("[reply-to:2.2] b"), self._am("c"),
        ])
        assert client.get_pending_responses()[-1] == f"{_NARRATION_PREFIX}[reply-to:2.2] c"

    def test_malformed_marker_is_not_a_target(self):
        from ironclaude.brain_client import _NARRATION_PREFIX
        client = self._run([self._am("[reply-to:abc] x"), self._am("plain")])
        assert client.get_pending_responses()[-1] == f"{_NARRATION_PREFIX}plain"
```

Broken states caught:
- `test_marker_only…` fails without the carry-over, because the answer is queued as bare narration.
- `test_target_cleared…` fails if the target is never reset.
- `test_later_marker…` fails if the first target is kept.
- `test_malformed…` fails if a malformed marker becomes a target.

**Step 2: Write the end-to-end daemon test (RED).** In `commander/tests/test_main_validate.py`, add this method to `class TestOperatorWaits` right after `test_reply_long_text_is_chunked`:

```python
    def test_split_reply_turn_threads_answer_under_operator_message(self):
        """R1 end-to-end: the real BrainClient output for a marker-only message followed by an
        unmarked answer threads the answer under the operator's message with ✅, while the
        marker-only message stays dropped (ghost-post guard)."""
        import asyncio
        from claude_agent_sdk import AssistantMessage
        from claude_agent_sdk.types import TextBlock
        from ironclaude.brain_client import BrainClient

        class CapturingOptions:
            def __init__(self, **kwargs):
                pass

        async def fake_query(prompt=None, options=None):
            yield AssistantMessage(content=[TextBlock(text="[reply-to:1699.5]")], model="opus")
            yield AssistantMessage(content=[TextBlock(text="Here is the answer.")], model="opus")

        client = BrainClient()
        client._grader = MagicMock()
        client._grader.grade.return_value = {"permission_seeking": False}
        with patch("claude_agent_sdk.ClaudeAgentOptions", CapturingOptions), \
             patch("claude_agent_sdk.query", fake_query):
            client._episodic_memory_path = "/fake/memory.js"
            asyncio.run(client._brain_session("my-prompt", None, None))

        d = _make_poll_daemon()
        d._last_heartbeat_ts = "1700.1"
        d.brain.get_pending_responses.return_value = client.get_pending_responses()
        d.poll_brain_responses()
        d.slack.post_message.assert_called_once()
        args, kwargs = d.slack.post_message.call_args
        assert kwargs.get("thread_ts") == "1699.5"
        assert "Here is the answer." in args[0]
        d.slack.add_reaction.assert_called_once_with("white_check_mark", "1699.5")
```

Broken state caught: without the carry-over, the answer goes to the heartbeat thread (`thread_ts == "1700.1"`) and gets no ✅.

**Step 3: Run the tests and confirm they fail.**

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest -q tests/test_brain_client.py::TestTurnReplyTarget "tests/test_main_validate.py::TestOperatorWaits::test_split_reply_turn_threads_answer_under_operator_message"
```

Expected: `test_marker_only_then_unmarked_answer_carries_target`, `test_later_marker_replaces_target` and `test_split_reply_turn_threads_answer_under_operator_message` FAIL with AssertionError. `test_target_cleared_at_turn_end` and `test_malformed_marker_is_not_a_target` already pass: they are guards against over-carrying.

**Step 4: Implement.** In `commander/src/ironclaude/brain_client.py`:

(a) After the line `from ironclaude.fable_availability import mark_fable_unavailable as _mark_fable_unavailable`, add:

```python
from ironclaude.slack_interface import parse_reply_to_marker
```

(b) In `__init__`, directly after `self._ping_sent_at: float = 0.0`, add:

```python
        # R1: the [reply-to:<ts>] target of the current Brain turn (None = no target).
        # Carried onto later unmarked text in the same turn; cleared on ResultMessage.
        self._turn_reply_ts: str | None = None
```

(c) Add this method directly before `def send_message(self, text: str) -> bool:`:

```python
    def _apply_turn_reply_target(self, text: str) -> str:
        """Carry the turn's [reply-to:<ts>] target onto later unmarked text (R1).

        The Brain may send the marker alone, then the answer after a tool call. The
        daemon drops the marker-only message (ghost-post guard), so without this the
        answer loses its thread target and lands in the heartbeat thread. A malformed
        marker never becomes a target (the daemon drops that message)."""
        parsed = parse_reply_to_marker(text)
        if parsed is None:
            return text
        _body, ts = parsed
        if ts is not None:
            self._turn_reply_ts = ts
            return text.lstrip()
        if self._turn_reply_ts is not None:
            return f"[reply-to:{self._turn_reply_ts}] {text}"
        return text
```

(d) In `_run_session`, inside `if isinstance(message, ResultMessage):`, directly after `self._executing_tool = False`, add:

```python
                        self._turn_reply_ts = None
```

(e) In `_run_session`, replace

```python
                                self._response_queue.put(f"{_NARRATION_PREFIX}{full_text}")
```

with

```python
                                self._response_queue.put(
                                    f"{_NARRATION_PREFIX}{self._apply_turn_reply_target(full_text)}"
                                )
```

(f) In `restart()`, directly after the line `self._ping_sent_at = 0.0` (the one that follows `self._executing_tool = False`), add:

```python
        self._turn_reply_ts = None
```

**Step 5: Run the tests and confirm they pass.**

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest -q tests/test_brain_client.py tests/test_main_validate.py
```

Expected: all pass, 0 failed.

**Step 6: Stage.**

```bash
git -C /Users/roberthyatt/Code/ironclaude add commander/src/ironclaude/brain_client.py commander/tests/test_brain_client.py commander/tests/test_main_validate.py
```

Expected: no output, exit 0.

---

## Task 2: Honest usage-limit state on the opus fallback (R3.1–R3.5, R3.7)

**Depends on:** Task 1, which edits the same `_run_session` block.

**Files:**
- Modify: `commander/src/ironclaude/brain_client.py`:
  - constants (after `_NARRATION_PREFIX`, :139);
  - new detector block after `_is_usage_limit_text` (:102-111);
  - `__init__`;
  - `_run_session`;
  - `needs_restart()`;
  - `restart()`;
  - `get_token_usage()`.
- Modify: `commander/src/ironclaude/main.py`:
  - :45 import;
  - :861-877 (the detector moves out);
  - `poll_brain_responses` limit block (:3230-3240).
- Test: `commander/tests/test_brain_client.py` (the `TestTokenUsageAccumulation.test_get_token_usage_initial_zeros` exact dict at :2707-2714, and a new class).
- Test: `commander/tests/test_main_validate.py` (new module-level tests after `test_limit_alert_fires_even_when_waiting`, :1092-1100).

**Step 1: Write the brain_client tests (RED).**

(a) In `TestTokenUsageAccumulation.test_get_token_usage_initial_zeros`, replace the expected dict with:

```python
        assert usage == {
            "input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "cost_usd": 0.0,
            "seconds_since_last_activity": None, "usage_limited": None,
        }
```

(b) Add this class immediately before `class TestModelUnavailableFableTransition:`, after `TestTurnReplyTarget`:

```python
class TestUsageLimitState:
    """R3: on the opus fallback a usage-limit reply is surfaced (limit-prefixed), is not
    liveness, suppresses timeout restarts, and clears on the next real reply."""

    LIMIT = "You've hit your limit · resets 4:10am (America/Chicago)"

    @staticmethod
    def _run(model, messages, tmp_path=None, monkeypatch=None):
        import asyncio
        from unittest.mock import patch, MagicMock

        if monkeypatch is not None:
            from ironclaude import fable_availability
            monkeypatch.setattr(fable_availability, "_STATE_PATH", tmp_path / "s.json")

        models_built = []

        class CapturingOptions:
            def __init__(self, **kwargs):
                models_built.append(kwargs.get("model"))

        calls = {"n": 0}

        async def fake_query(prompt=None, options=None):
            calls["n"] += 1
            if calls["n"] == 1:
                for m in messages:
                    yield m

        client = BrainClient(model=model)
        client._grader = MagicMock()
        client._grader.grade.return_value = {"permission_seeking": False}
        with patch("claude_agent_sdk.ClaudeAgentOptions", CapturingOptions), \
             patch("claude_agent_sdk.query", fake_query):
            client._episodic_memory_path = "/fake/memory.js"
            asyncio.run(client._brain_session("my-prompt", None, None))
        return client, models_built

    def test_opus_limit_is_surfaced_and_not_liveness(self):
        from ironclaude.brain_client import _LIMIT_PREFIX
        client, _ = self._run("opus", [
            TestTurnReplyTarget._am(self.LIMIT), TestTurnReplyTarget._result(),
        ])
        assert client.get_pending_responses() == [f"{_LIMIT_PREFIX}{self.LIMIT}"]
        assert client._last_response_time == 0.0
        assert client._usage_limit == self.LIMIT
        assert client._executing_tool is False
        assert client.get_token_usage()["usage_limited"] == "resets 4:10am (America/Chicago)"

    def test_limit_clears_on_next_real_reply(self):
        from ironclaude.brain_client import _LIMIT_PREFIX, _NARRATION_PREFIX
        client, _ = self._run("opus", [
            TestTurnReplyTarget._am(self.LIMIT), TestTurnReplyTarget._result(),
            TestTurnReplyTarget._am("Back online."), TestTurnReplyTarget._result(),
        ])
        assert client.get_pending_responses() == [
            f"{_LIMIT_PREFIX}{self.LIMIT}", f"{_NARRATION_PREFIX}Back online.",
        ]
        assert client._usage_limit is None
        assert client._last_response_time > 0.0
        assert client.get_token_usage()["usage_limited"] is None

    def test_limit_without_reset_reports_unknown(self):
        client, _ = self._run("opus", [TestTurnReplyTarget._am("Sorry, you hit your limit")])
        assert client.get_token_usage()["usage_limited"] == "reset time unknown"

    def test_non_opus_limit_still_falls_back(self, tmp_path, monkeypatch):
        from ironclaude.brain_client import _LIMIT_PREFIX
        client, models_built = self._run(
            "fable", [TestTurnReplyTarget._am(self.LIMIT)], tmp_path, monkeypatch,
        )
        assert client._model == "opus"
        assert any(m == "opus[1m]" for m in models_built), models_built
        assert all(not r.startswith(_LIMIT_PREFIX) for r in client.get_pending_responses())

    def test_needs_restart_suppressed_while_limited(self):
        client = TestBrainLivenessTimeout._make_alive_client(timeout_seconds=300)
        client._last_message_time = time.time() - 600
        client._last_response_time = client._last_message_time - 10
        client._usage_limit = self.LIMIT
        assert client.needs_restart() is False
        client._usage_limit = None
        assert client.needs_restart() is True
        client._stop_event.set()

    def test_dead_thread_still_restarts_while_limited(self):
        client = TestBrainLivenessTimeout._make_alive_client(timeout_seconds=300)
        client._usage_limit = self.LIMIT
        client._stop_event.set()
        client._thread.join(timeout=5)
        assert client.needs_restart() is True

    def test_restart_clears_limit_and_turn_target(self):
        client = BrainClient()
        client._usage_limit = self.LIMIT
        client._turn_reply_ts = "1699.5"
        client.start = lambda *a, **kw: setattr(client, "_running", True)
        client._kill_brain_subprocess = lambda: None
        client.restart("test prompt")
        assert client._usage_limit is None
        assert client._turn_reply_ts is None

    def test_detector_moved_and_reexported(self):
        from ironclaude import brain_client, main
        assert main.detect_account_limit is brain_client.detect_account_limit
```

Broken states caught:
- **`test_opus_limit…`:** fails if the limit is not queued, the bounce counts as liveness, or no reset is reported.
- **`test_limit_clears…`:** fails if the limit state never clears.
- **`test_limit_without_reset…`:** fails if a limit with no reset time is reported as not limited.
- **`test_non_opus…`:** fails if the fallback path breaks. It already passes today, as a regression guard.
- **`test_needs_restart…`:** fails if a timeout restart fires while limited. Its second assert proves the guard is what suppresses the restart.
- **`test_dead_thread…`:** fails if the limit guard masks a dead Brain.
- **`test_restart_clears…`:** fails if `restart()` keeps stale state.
- **`test_detector…`:** fails if `main` keeps its own copy of the detector.

**Step 2: Write the daemon tests (RED).** In `commander/tests/test_main_validate.py`, add after `test_limit_alert_fires_even_when_waiting`:

```python
def test_brain_limit_prefixed_text_alerts_and_is_not_relayed(monkeypatch):
    """R3.2: the Brain's own usage-limit bounce (limit-prefixed) raises the account-limit
    alert once per cooldown and is never posted as narration or chatter."""
    from ironclaude.brain_client import _LIMIT_PREFIX
    d = _make_poll_daemon()
    d._limit_alerted = {}
    d._last_heartbeat_ts = "1700.1"
    monkeypatch.setattr("ironclaude.main.time.time", lambda: 1000.0)
    msg = f"{_LIMIT_PREFIX}You've hit your limit · resets 4:10am (America/Chicago)"
    d.brain.get_pending_responses.return_value = [msg]
    d.poll_brain_responses()
    assert d.slack.post_message.call_count == 1
    args, kwargs = d.slack.post_message.call_args
    assert "Usage limit hit (resets 4:10am (America/Chicago))" in args[0]
    assert kwargs.get("thread_ts") is None
    d.brain.get_pending_responses.return_value = [msg]
    d.poll_brain_responses()
    assert d.slack.post_message.call_count == 1
    d.brain.send_message.assert_not_called()


def test_brain_limit_prefixed_text_without_reset_still_alerts(monkeypatch):
    from ironclaude.brain_client import _LIMIT_PREFIX
    d = _make_poll_daemon()
    d._limit_alerted = {}
    d._last_heartbeat_ts = "1700.1"
    monkeypatch.setattr("ironclaude.main.time.time", lambda: 1000.0)
    d.brain.get_pending_responses.return_value = [f"{_LIMIT_PREFIX}Sorry, you hit your limit"]
    d.poll_brain_responses()
    assert d.slack.post_message.call_count == 1
    assert "Usage limit hit (reset time unknown)" in d.slack.post_message.call_args.args[0]
```

Broken states caught:
- Without the prefix `continue`, the text is also relayed under the heartbeat thread, so `call_count == 2`.
- Without the unknown-reset fallback, a limit message with no reset time raises no alert.

**Step 3: Run the tests and confirm they fail.**

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest -q tests/test_brain_client.py::TestUsageLimitState tests/test_brain_client.py::TestTokenUsageAccumulation tests/test_main_validate.py -k "UsageLimitState or initial_zeros or brain_limit_prefixed"
```

Expected: FAIL. Importing `_LIMIT_PREFIX` raises ImportError, `usage_limited` is missing from the dict, and `_usage_limit` doesn't exist. `test_non_opus_limit_still_falls_back` and `test_dead_thread_still_restarts_while_limited` may error only on the missing symbol or attribute.

**Step 4: Move the detector into brain_client.**

(a) In `commander/src/ironclaude/brain_client.py`, directly after the `_is_usage_limit_text` function (which ends with `return "hit your limit" in text.lower()`), add these lines, moved verbatim from `main.py:862-877`:

```python


# separators seen in the wild: middle-dot, colon, hyphen, em-dash; apostrophe may be straight or curly
_ACCOUNT_LIMIT_RE = re.compile(r"you['’]?ve hit your limit(?:\s*[·:\-—]\s*(resets[^\n]*))?", re.IGNORECASE)
_WORKER_LIMIT_RE = re.compile(r"(?<!no )(?:session limit hit|rate-limit menu)", re.IGNORECASE)  # not "no session limit hit"


def detect_account_limit(text: str):
    """Return a human reset string if `text` signals an account/worker usage limit, else None.
    Shared signal set with the Fable-gate deferred usage detector ('hit your limit')."""
    if not text:
        return None
    m = _ACCOUNT_LIMIT_RE.search(text)
    if m:
        return (m.group(1) or "reset time unknown").strip()
    if _WORKER_LIMIT_RE.search(text):
        return "reset time unknown"
    return None
```

(b) In `commander/src/ironclaude/main.py`, delete these lines: the `# separators seen in the wild…` comment, `_ACCOUNT_LIMIT_RE`, `_WORKER_LIMIT_RE`, the blank lines, and the whole `def detect_account_limit` function. Keep `_LIMIT_COOLDOWN_S = 1800 …`.

(c) In `main.py`, change `from ironclaude.brain_client import BrainClient, _NARRATION_PREFIX` to:

```python
from ironclaude.brain_client import BrainClient, _LIMIT_PREFIX, _NARRATION_PREFIX, detect_account_limit
```

**Step 5: Implement the limit state in brain_client.**

(a) Directly after `_NARRATION_PREFIX = "[NARRATION] "`, add:

```python

# Tag for an account usage-limit reply received on the opus fallback (nothing higher to
# fall back to). main.py's poll_brain_responses raises the account-limit alert for it
# and never relays it as narration or chatter.
_LIMIT_PREFIX = "[BRAIN-LIMIT] "
```

(b) In `__init__`, directly after the `self._turn_reply_ts: str | None = None` line from Task 1, add:

```python
        # R3: the usage-limit text of the last bounce on the opus fallback (None = not
        # limited). While set: no liveness credit, no timeout restart; cleared by the next
        # real reply and by restart().
        self._usage_limit: str | None = None
```

(c) In `_run_session`, replace the whole loop body, from `self._note_sdk_activity()` at the top of the `async for` loop down to `await self._message_queue.put(correction)`, with:

```python
                    if isinstance(message, ResultMessage):
                        self._session_id = message.session_id
                        self._executing_tool = False
                        self._turn_reply_ts = None
                        if message.usage:
                            self._total_input_tokens += message.usage.get("input_tokens", 0) or 0
                            self._total_output_tokens += message.usage.get("output_tokens", 0) or 0
                        if message.total_cost_usd is not None:
                            self._total_cost_usd += message.total_cost_usd
                        # R3.3: a turn that ended on a usage-limit bounce is not liveness.
                        if self._usage_limit is None:
                            self._note_sdk_activity()
                        continue
                    if not isinstance(message, AssistantMessage):
                        self._note_sdk_activity()
                        continue
                    text_parts = []
                    for block in message.content:
                        if isinstance(block, TextBlock):
                            text_parts.append(block.text)
                    full_text = "\n\n".join(text_parts) if text_parts else ""
                    # Message-shaped model-unavailability OR account usage-limit:
                    # the SDK returned the error as normal assistant text rather
                    # than raising, so the exception fallback never fires. Detect
                    # it and fall back to opus (unless already on opus — nothing
                    # higher to try). A usage-limit reason is sized to the account
                    # reset time by fable_availability; a genuine outage to 24h.
                    if (
                        full_text
                        and (_is_model_unavailable_text(full_text)
                             or _is_usage_limit_text(full_text))
                        and "opus" not in self._model.lower()
                    ):
                        raise _ModelUnavailableFromMessage(full_text)
                    if full_text and _is_usage_limit_text(full_text):
                        # R3.1: on opus there is nothing higher to fall back to. Surface the
                        # limit for the daemon's account-limit alert; the bounce is NOT
                        # liveness (no _note_sdk_activity) and suppresses timeout restarts.
                        self._executing_tool = False
                        self._usage_limit = full_text
                        self._session_log_write(f"MSG_LIMIT chars={len(full_text)} preview={full_text[:100]!r}")
                        logger.warning(f"Brain usage-limited: {full_text[:200]!r}")
                        self._response_queue.put(f"{_LIMIT_PREFIX}{full_text}")
                        continue
                    self._note_sdk_activity()
                    if not full_text:
                        continue
                    self._usage_limit = None
                    self._executing_tool = False
                    self._session_log_write(f"MSG_SEND chars={len(full_text)} preview={full_text[:100]!r}")
                    logger.info(f"Brain response received ({len(full_text)} chars)")
                    # Thread-only narration relay: operators see the Brain thinking
                    # under the heartbeat thread. Tagged so poll_brain_responses routes
                    # it around the directive-ref/reason gate (never [CONTEXT REQUIRED]).
                    # Skip outage text on the opus edge where the raise above is bypassed;
                    # a usage limit is surfaced above via _LIMIT_PREFIX.
                    if not _is_model_unavailable_text(full_text):
                        self._response_queue.put(
                            f"{_NARRATION_PREFIX}{self._apply_turn_reply_target(full_text)}"
                        )
                    correction = await self._maybe_correct_permission_seeking(full_text)
                    if correction is not None:
                        await self._message_queue.put(correction)
```

Behaviour preserved:
- The first two statements inside `async for message in query(…):` were `self._note_sdk_activity()`, then `if isinstance(message, ResultMessage):`. The new body begins at `if isinstance(message, ResultMessage):`. Every SDK message except a limit AssistantMessage, or a ResultMessage while limited, still calls `_note_sdk_activity()`.
- An AssistantMessage without text still clears nothing and queues nothing, as before.

(d) In `needs_restart()`, directly after this block:

```python
        if not self.is_alive():
            self._restart_reason = "dead (thread not alive)"
            return True
```

add:

```python
        # R3.4: a usage-limited Brain is waiting on an account reset — a restart cannot
        # cure that, and every attempt would count toward the restart circuit breaker.
        if self._usage_limit is not None:
            return False
```

(e) In `restart()`, directly after the `self._turn_reply_ts = None` line from Task 1, add:

```python
        self._usage_limit = None
```

(f) In `get_token_usage()`, add this key after `"seconds_since_last_activity": age,`:

```python
            "usage_limited": (
                (detect_account_limit(self._usage_limit) or "reset time unknown")
                if self._usage_limit is not None else None
            ),
```

**Step 6: Route limit-prefixed text in the daemon.** In `main.py` `poll_brain_responses`:

(a) Replace `limit = detect_account_limit(text)` with:

```python
            limit = detect_account_limit(text) or (
                "reset time unknown" if text.startswith(_LIMIT_PREFIX) else None
            )
```

(b) Directly after the `if limit:` block, which ends with the `self.slack.post_message(f"⚠️ Usage limit hit ({limit}). Send \`login\` to switch accounts.")` line, add at the same indentation as `if limit:`:

```python
            if text.startswith(_LIMIT_PREFIX):
                # R3.2: the Brain's own usage-limit bounce is surfaced by the alert above,
                # never relayed as narration or chatter.
                continue
```

**Step 7: Run the tests and confirm they pass.**

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest -q tests/test_brain_client.py tests/test_main_validate.py tests/test_daemon.py
```

Expected: all pass, 0 failed.

**Step 8: Confirm the detector exists in one place only.**

```bash
rg -n -e "def detect_account_limit" -e "^_ACCOUNT_LIMIT_RE" -e "^_WORKER_LIMIT_RE" /Users/roberthyatt/Code/ironclaude/commander/src/ironclaude
```

Expected: exactly three lines, all in `brain_client.py`.

**Step 9: Stage.**

```bash
git -C /Users/roberthyatt/Code/ironclaude add commander/src/ironclaude/brain_client.py commander/src/ironclaude/main.py commander/tests/test_brain_client.py commander/tests/test_main_validate.py
```

Expected: exit 0.

---

## Task 3: Heartbeat shows the usage-limited state (R3.6)

**Files:**
- Modify: `commander/src/ironclaude/notifications.py:191-200`
- Test: `commander/tests/test_notifications.py` (after `test_heartbeat_nonzero_tokens_unaffected`, :399-403)

**Step 1: Write the tests (RED).** Add these to the same class, after `test_heartbeat_nonzero_tokens_unaffected`:

```python
    def test_heartbeat_usage_limited_replaces_turn_in_progress(self):
        workers = [{"id": "w-1", "description": "Your task: Fix auth", "workflow_stage": "executing"}]
        brain_usage = {"total_tokens": 0, "input_tokens": 0, "output_tokens": 0,
                       "seconds_since_last_activity": 180,
                       "usage_limited": "resets 4:10am (America/Chicago)"}
        msg = format_heartbeat(workers, brain_usage=brain_usage)
        assert "usage-limited (resets 4:10am (America/Chicago)); unanswered messages retried after reset" in msg
        assert "turn in progress" not in msg

    def test_heartbeat_usage_limited_escapes_mrkdwn(self):
        workers = [{"id": "w-1", "description": "Your task: Fix auth", "workflow_stage": "executing"}]
        brain_usage = {"total_tokens": 10, "input_tokens": 5, "output_tokens": 5,
                       "usage_limited": "resets <soon> & later"}
        msg = format_heartbeat(workers, brain_usage=brain_usage)
        assert "usage-limited (resets &lt;soon&gt; &amp; later)" in msg
```

Broken states caught:
- Without the new branch, the message says "turn in progress" and has no limited line.
- Without escaping, raw `<`, `>` or `&` would reach Slack mrkdwn.

**Step 2: Run the tests and confirm they fail.**

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest -q tests/test_notifications.py -k usage_limited
```

Expected: 2 failed.

**Step 3: Implement.** In `format_heartbeat`, replace

```python
        if total == 0:
            age = brain_usage.get("seconds_since_last_activity")
            if age is not None:
                line += f" — turn in progress (last activity {_fmt_duration(age)} ago)"
```

with

```python
        limited = brain_usage.get("usage_limited")
        if limited:
            line += (
                f" — usage-limited ({_escape_mrkdwn(str(limited))}); "
                "unanswered messages retried after reset"
            )
        elif total == 0:
            age = brain_usage.get("seconds_since_last_activity")
            if age is not None:
                line += f" — turn in progress (last activity {_fmt_duration(age)} ago)"
```

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

## Task 4: Acknowledgement closure posted in the operator's thread (R2.1–R2.5)

**Files:**
- Modify: `commander/src/ironclaude/db.py:280-332`
- Modify: `commander/src/ironclaude/orchestrator_mcp.py`:
  - :38-41 import;
  - :1757-1761 method;
  - :7467-7477 MCP wrapper docstring.
- Test: `commander/tests/test_db.py` (after `test_persist_operator_message_acknowledgement_is_immutable_with_plain_rows`, :477-489)
- Test: `commander/tests/test_orchestrator_mcp.py` (`TestAcknowledgeOperatorMessage`, :10863-10924)

**Step 1: Write the db test (RED).** Add after `test_persist_operator_message_acknowledgement_is_immutable_with_plain_rows`:

```python
def test_persist_with_status_reports_created_only_for_the_inserting_call(tmp_path):
    from ironclaude.db import persist_operator_message_acknowledgement_with_status
    conn = init_db(str(tmp_path / "status.db"))
    source_ts = "1785731067.460039"
    first, created = persist_operator_message_acknowledgement_with_status(conn, source_ts, "first reason")
    again, created_again = persist_operator_message_acknowledgement_with_status(conn, source_ts, "second reason")
    assert created is True
    assert created_again is False
    assert again == first
    assert persist_operator_message_acknowledgement(conn, source_ts, "third reason") == first
    conn.close()
```

**Step 2: Write the orchestrator tests (RED).**

(a) In `test_acknowledge_operator_message_delegates_to_shared_persistence`, change the fake and its patch target to:

```python
        def persist(conn, source_ts, reason):
            calls.append((conn, source_ts, reason))
            return expected, False

        monkeypatch.setattr(orchestrator_mcp, "persist_operator_message_acknowledgement_with_status", persist)
```

Leave the two asserts unchanged.

(b) Add these methods to `TestAcknowledgeOperatorMessage`:

```python
    def _tools_with_slack(self, db_conn, registry, mock_tmux, slack):
        return OrchestratorTools(registry, mock_tmux, db_conn=db_conn, slack_bot=slack)

    def test_new_acknowledgement_posts_reason_in_thread_and_reacts(self, db_conn, registry, mock_tmux):
        slack = MagicMock()
        slack.post_message.return_value = "1785731070.000100"
        tools = self._tools_with_slack(db_conn, registry, mock_tmux, slack)
        result = tools.acknowledge_operator_message(self.SOURCE_TS, "No reply needed — informational.")
        assert result["reason"] == "No reply needed — informational."
        slack.post_message.assert_called_once_with("No reply needed — informational.", thread_ts=self.SOURCE_TS)
        slack.add_reaction.assert_called_once_with("white_check_mark", self.SOURCE_TS)

    def test_repeat_acknowledgement_posts_nothing(self, db_conn, registry, mock_tmux):
        slack = MagicMock()
        slack.post_message.return_value = "1785731070.000100"
        tools = self._tools_with_slack(db_conn, registry, mock_tmux, slack)
        tools.acknowledge_operator_message(self.SOURCE_TS, "first")
        tools.acknowledge_operator_message(self.SOURCE_TS, "second")
        assert slack.post_message.call_count == 1
        assert slack.add_reaction.call_count == 1

    def test_acknowledgement_after_delivered_reply_posts_nothing(self, db_conn, registry, mock_tmux):
        from ironclaude.db import DIRECT_REPLY_FALLBACK_REASON, persist_operator_message_acknowledgement
        persist_operator_message_acknowledgement(db_conn, self.SOURCE_TS, DIRECT_REPLY_FALLBACK_REASON)
        slack = MagicMock()
        tools = self._tools_with_slack(db_conn, registry, mock_tmux, slack)
        tools.acknowledge_operator_message(self.SOURCE_TS, "late close")
        slack.post_message.assert_not_called()
        slack.add_reaction.assert_not_called()

    def test_slack_error_never_raises_and_ack_is_kept(self, db_conn, registry, mock_tmux):
        slack = MagicMock()
        slack.post_message.side_effect = RuntimeError("slack down")
        tools = self._tools_with_slack(db_conn, registry, mock_tmux, slack)
        result = tools.acknowledge_operator_message(self.SOURCE_TS, "closing")
        assert result["reason"] == "closing"
        slack.add_reaction.assert_not_called()
        assert db_conn.execute(
            "SELECT reason FROM operator_message_acknowledgements WHERE source_ts=?",
            (self.SOURCE_TS,),
        ).fetchone()[0] == "closing"

    def test_failed_post_skips_reaction(self, db_conn, registry, mock_tmux):
        slack = MagicMock()
        slack.post_message.return_value = None
        tools = self._tools_with_slack(db_conn, registry, mock_tmux, slack)
        tools.acknowledge_operator_message(self.SOURCE_TS, "closing")
        slack.add_reaction.assert_not_called()

    def test_mcp_tool_description_says_reason_is_posted(self, db_conn, registry, mock_tmux):
        from ironclaude.orchestrator_mcp import _create_mcp_server
        server = _create_mcp_server(self._tools(db_conn, registry, mock_tmux))
        description = server._tool_manager.get_tool("acknowledge_operator_message").description
        assert "posted in the operator message's thread" in description
        assert "with no reply" in description
```

Before adding these, confirm that `MagicMock` is imported at module level in `test_orchestrator_mcp.py`:

```bash
rg -n "^from unittest.mock import" /Users/roberthyatt/Code/ironclaude/commander/tests/test_orchestrator_mcp.py
```

If the output lacks `MagicMock`, add `MagicMock` to that import line.

Broken states caught:
- **Posting tests:** a missing post or reaction fails them.
- **Repeat and after-reply tests:** posting on an existing row fails them.
- **Error test:** a raising Slack call propagates and fails it.
- **Failed-post test:** reacting after a failed post fails it.
- **Description test:** the stale docstring fails it.

**Step 3: Run the tests and confirm they fail.**

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest -q tests/test_db.py tests/test_orchestrator_mcp.py -k "with_status or AcknowledgeOperatorMessage"
```

Expected: FAIL. The ImportError or AttributeError comes from the missing `persist_operator_message_acknowledgement_with_status`, and the new posting tests fail their asserts.

**Step 4: Implement db.py.** Replace the function header and docstring

```python
def persist_operator_message_acknowledgement(
    conn: sqlite3.Connection, source_ts: str, reason: str
) -> dict:
    """Persist, or return, an immutable no-action disposition for one message."""
```

with

```python
def persist_operator_message_acknowledgement(
    conn: sqlite3.Connection, source_ts: str, reason: str
) -> dict:
    """Persist, or return, an immutable no-action disposition for one message."""
    return persist_operator_message_acknowledgement_with_status(conn, source_ts, reason)[0]


def persist_operator_message_acknowledgement_with_status(
    conn: sqlite3.Connection, source_ts: str, reason: str
) -> tuple[dict, bool]:
    """Like persist_operator_message_acknowledgement, plus whether THIS call created the
    row (True only on the inserting path; False for an existing or concurrently-inserted
    row)."""
```

In the moved body, which is now in `_with_status`, change the three returns:
- `return result_from(existing)` → `return result_from(existing), False`
- `return result_from(concurrent)` → `return result_from(concurrent), False`
- the final `return result_from(persisted)` → `return result_from(persisted), True`

**Step 5: Implement orchestrator_mcp.py.**

(a) In the import block, add the helper:

```python
from ironclaude.db import (
    DIRECT_REPLY_FALLBACK_REASON,
    persist_operator_message_acknowledgement,
    persist_operator_message_acknowledgement_with_status,
)
```

(b) Replace the method:

```python
    def acknowledge_operator_message(self, source_ts: str, reason: str) -> dict:
        """Persist an immutable no-action disposition for one Slack message.

        This closes a message with no reply. When this call creates the acknowledgement,
        the reason is posted in the operator message's thread and the message gets ✅,
        so a closure is always visible. A repeat call, or a message already closed by a
        delivered [reply-to] reply, posts nothing. Slack failures never raise."""
        if self._db is None:
            raise RuntimeError("Database connection required for directive operations")
        result, created = persist_operator_message_acknowledgement_with_status(
            self._db, source_ts, reason
        )
        if created and self._slack is not None:
            try:
                if self._slack.post_message(result["reason"], thread_ts=source_ts) is not None:
                    self._slack.add_reaction("white_check_mark", source_ts)
            except Exception:
                logger.warning(
                    "Acknowledgement closure post failed | source_ts=%s", source_ts, exc_info=True,
                )
        return result
```

(c) Replace the MCP wrapper docstring with:

```python
        """Close one operator Slack message with no reply, recording an immutable disposition.

        Use this only when you will NOT reply; a [reply-to:<ts>] reply records its own
        acknowledgement. The reason is posted in the operator message's thread (with ✅)
        when this call creates the acknowledgement, so write it as a short sentence for
        the operator.

        Args:
            source_ts: Exact Slack timestamp string for the operator message.
            reason: Non-blank reason the message needs no directive and no reply.

        Returns JSON with the stored acknowledgement. Repeated calls return the
        original acknowledgement unchanged and post nothing.
        """
```

**Step 6: Run the tests and confirm they pass.**

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest -q tests/test_db.py tests/test_orchestrator_mcp.py tests/test_main_validate.py tests/test_codex_brain_client.py
```

Expected: all pass, 0 failed.

**Step 7: Stage.**

```bash
git -C /Users/roberthyatt/Code/ironclaude add commander/src/ironclaude/db.py commander/src/ironclaude/orchestrator_mcp.py commander/tests/test_db.py commander/tests/test_orchestrator_mcp.py
```

---

## Task 5: Brain prompt — full replies, reply owns closure (R1.6, R2.6)

**Files:**
- Modify: `commander/src/brain/system_prompt.md:368-378`
- Modify: `commander/src/brain/rules/workflow.md:888`
- Test: `commander/tests/test_daemon.py:117-122`

**Step 1: Replace the prompt-contract test (RED).** Replace `test_brain_direct_reply_requires_acknowledgement_before_threaded_reply` (`test_daemon.py:117-122`) with:

```python
def test_brain_direct_reply_owns_closure_and_ack_is_for_no_reply():
    for text in _brain_instruction_surfaces():
        assert "acknowledge_operator_message(source_ts, reason)" in text
        assert "only to close a message you will not reply to" in text
        assert "the daemon records the acknowledgement" in text
        assert "no directive, worker, or repository action" in text
        assert "[reply-to:<source_ts>]" in text
        assert "must succeed before direct reply" not in text


def test_brain_reply_marker_never_sent_alone():
    text = _brain_instruction_surfaces()[0]
    assert "brief acknowledgement or ETA first" not in text
    assert "never send the marker alone" in text
    assert "in the same message" in text
```

Broken states caught:
- The old ack-first wording still in place fails the absence assert and the new-wording asserts.
- The old "brief acknowledgement first" paragraph fails the second test.

**Step 2: Run the tests and confirm they fail.**

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest -q tests/test_daemon.py -k "owns_closure or never_sent_alone"
```

Expected: 2 failed.

**Step 3: Edit `system_prompt.md`.** Replace lines 368-372 (the paragraph starting `When an \`OPERATOR MESSAGE\` arrives while you are part-way`) with:

```
When an `OPERATOR MESSAGE` arrives while you are part-way through a multi-step tool
sequence, reply before continuing: begin with `[reply-to:<ts>]` and put a real one-line
answer, status, or ETA in the same message — never send the marker alone (a marker-only
message is discarded). Then continue your work — the operator must never wait on a long
tool run just to learn you have seen them. This is prioritisation, not a gate: never
skip required work, only front-load the reply.
```

Replace line 378 with:

```
For a non-actionable direct reply, reply with `[reply-to:<source_ts>]` followed by your answer in the same message; the daemon records the acknowledgement. Call `acknowledge_operator_message(source_ts, reason)` only to close a message you will not reply to — its reason is posted in the operator's thread. Take no directive, worker, or repository action.
```

This keeps the lowercase substring `no directive, worker, or repository action`, which the test asserts.

**Step 4: Edit `workflow.md:888`.** Replace the line with:

```
   - NO → Reply with `[reply-to:<source_ts>]` followed by your answer in the same message; the daemon records the acknowledgement. Call `acknowledge_operator_message(source_ts, reason)` only to close a message you will not reply to — its reason is posted in the operator's thread. Take no directive, worker, or repository action.
```

**Step 5: Run the tests and confirm they pass.**

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest -q tests/test_daemon.py
```

Expected: all pass, 0 failed.

**Step 6: Confirm no stale copy remains.**

```bash
rg -n -F -e "must succeed before direct reply" -e "brief acknowledgement or ETA" /Users/roberthyatt/Code/ironclaude/commander/src /Users/roberthyatt/Code/ironclaude/worker
```

Expected: no output, exit 1.

**Step 7: Stage.**

```bash
git -C /Users/roberthyatt/Code/ironclaude add commander/src/brain/system_prompt.md commander/src/brain/rules/workflow.md commander/tests/test_daemon.py
```

---

## Task 6: v1.1.14 version bump, CHANGELOG, README, full suite (R4)

**Depends on:** Tasks 1–5.

**No tests required:** this task edits only versions and docs. `test_version_consistency.py` and the full suite are its verification.

**Files:**
- Modify: `.claude-plugin/marketplace.json:10`
- Modify: `worker/.claude-plugin/plugin.json:3`
- Modify: `worker/.codex-plugin/plugin.json:3`
- Modify: `worker/mcp-servers/workspace-manager/package.json:3`
- Modify: `worker/mcp-servers/workspace-manager/package-lock.json:3` and `:9`
- Modify: `commander/pyproject.toml:4`
- Modify: `CHANGELOG.md` (new section above `## 1.1.13`)
- Modify: `README.md:18-31`

**Step 1: Get the Codex cachebuster stamp.**

```bash
date -u +%Y%m%d%H%M%S
```

Expected: 14 digits. Use this literal value as `<STAMP>` in Step 2.

**Step 2: Bump every version field.** Make each edit with the Edit tool:
- **`.claude-plugin/marketplace.json`:** `"version": "1.1.13",` → `"version": "1.1.14",`
- **`worker/.claude-plugin/plugin.json`:** `"version": "1.1.13",` → `"version": "1.1.14",`
- **`worker/.codex-plugin/plugin.json`:** `"version": "1.1.13+codex.20260926152651",` → `"version": "1.1.14+codex.<STAMP>",`
- **`worker/mcp-servers/workspace-manager/package.json`:** `"version": "1.1.13",` → `"version": "1.1.14",`
- **`worker/mcp-servers/workspace-manager/package-lock.json`:** both `"version": "1.1.13",` occurrences (lines 3 and 9) → `"version": "1.1.14",`. Do not touch `"ieee754": "^1.1.13"`.
- **`commander/pyproject.toml`:** `version = "1.1.13"` → `version = "1.1.14"`

**Step 3: Verify the bump.**

```bash
rg -n -F 1.1.14 /Users/roberthyatt/Code/ironclaude/.claude-plugin/marketplace.json /Users/roberthyatt/Code/ironclaude/worker/.claude-plugin/plugin.json /Users/roberthyatt/Code/ironclaude/worker/.codex-plugin/plugin.json /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager/package.json /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager/package-lock.json /Users/roberthyatt/Code/ironclaude/commander/pyproject.toml
```

Expected: 7 lines, one per version field (package-lock twice).

```bash
rg -n -F '"1.1.13' /Users/roberthyatt/Code/ironclaude/.claude-plugin/marketplace.json /Users/roberthyatt/Code/ironclaude/worker/.claude-plugin/plugin.json /Users/roberthyatt/Code/ironclaude/worker/.codex-plugin/plugin.json /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager/package.json /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager/package-lock.json /Users/roberthyatt/Code/ironclaude/commander/pyproject.toml
```

Expected: no output, exit 1.

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest -q tests/test_version_consistency.py
```

Expected: all pass, 0 failed.

**Step 4: Run the full commander suite.**

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest -q
```

Expected: 0 failed. Record the exact passed count N from the summary line for Step 5.

**Step 5: CHANGELOG.** Insert this section directly above `## 1.1.13: …`, replacing `<N>` with the measured count from Step 4:

```markdown
## 1.1.14: Brain replies reach the operator's thread, visible no-reply closures, and honest usage-limit state

- **A Brain reply split across messages now lands in the operator's thread.** The Brain sometimes sent `[reply-to:<ts>]` alone, then the real answer after a tool call. The daemon correctly drops a marker-only message (ghost-post guard), so the answer lost its target, went to the heartbeat thread as narration, and the operator's message never got ✅. `brain_client` now carries a turn's reply target onto later unmarked text in the same turn (cleared at turn end); the daemon's existing routing threads it and adds ✅. The Brain prompt now requires the marker and a real answer in the same message. (`brain_client.py`, `system_prompt.md`; covered by `test_brain_client.py` / `test_main_validate.py`.)
- **Reply owns closure; a no-reply close is visible.** A conversational message now gets only a `[reply-to]` reply — the reply path records the acknowledgement — and `acknowledge_operator_message` is used only to close a message with no reply. When it creates the acknowledgement, its reason is posted in the operator's thread with ✅; a repeat call, or a message already closed by a delivered reply, posts nothing, and Slack failures never raise. The persistence layer gains `persist_operator_message_acknowledgement_with_status`; the existing function's result is unchanged. (`db.py`, `orchestrator_mcp.py`, `system_prompt.md`, `rules/workflow.md`; covered by `test_db.py` / `test_orchestrator_mcp.py` / `test_daemon.py`.)
- **A usage-limited Brain on the opus fallback is no longer silent.** The "You've hit your limit" reply was neither raised (already on opus) nor queued, so no limit alert fired, the bounce counted as liveness, and the heartbeat read "turn in progress" while operator messages went unanswered. It is now queued with a limit tag that raises the existing account-limit alert (30-minute cooldown) and is never relayed as narration; it earns no liveness credit; timeout restarts are suppressed while limited (a restart cannot clear a limit) though a dead Brain is still restarted; and the heartbeat's Brain line reads "usage-limited (resets …); unanswered messages retried after reset". The state clears on the next real reply, and the existing aging reminder re-sends unanswered messages. `detect_account_limit` moved to `brain_client.py` (re-exported by `main`). (`brain_client.py`, `main.py`, `notifications.py`; covered by `test_brain_client.py` / `test_main_validate.py` / `test_notifications.py`.)
- Deploy: restart Commander (daemon, Brain prompt, and orchestrator MCP change; no workspace-manager or hook changes). Suites: commander <N>.
```

**Step 6: README.** In `README.md`:
- Replace the `## What's New in v1.1.13` heading with `## What's New in v1.1.14`, followed by the three bullets below.
- Turn the existing v1.1.13 bullets into a `### Earlier — v1.1.13` section.
- Delete the old `### Earlier — v1.1.12` heading and its bullets, keeping the final `- See [CHANGELOG.md](CHANGELOG.md) …` bullet at the end of the new Earlier section.

This matches the one-back layout of v1.1.13.

```markdown
## What's New in v1.1.14

- **Brain replies reach your thread.** When the Brain answered in two pieces — the reply marker alone, then the answer after a tool call — the answer used to land in the heartbeat thread and your message never got ✅. The Brain now carries the reply target through the whole turn, and its prompt requires a real answer alongside the marker.
- **No-reply closures are visible.** A conversational message now gets a threaded reply that closes it. When the Brain closes a message without replying, its reason is posted in your thread with ✅, so nothing is silently buried.
- **A usage-limited Brain says so.** On the opus fallback, a usage-limit reply now raises the "Usage limit hit" Slack alert, isn't mistaken for a live Brain, doesn't trigger pointless restarts, and the heartbeat shows "usage-limited (resets …)" instead of "turn in progress". Unanswered messages are retried after the reset.
```

**Step 7: Verify the docs.**

```bash
rg -n -F -e "## 1.1.14:" -e "## What's New in v1.1.14" -e "### Earlier — v1.1.13" /Users/roberthyatt/Code/ironclaude/CHANGELOG.md /Users/roberthyatt/Code/ironclaude/README.md
```

Expected: 3 lines. `rg -n -F "Earlier — v1.1.12" /Users/roberthyatt/Code/ironclaude/README.md` gives no output (exit 1).

**Step 8: Stage the release and the plan artifacts.**

```bash
git -C /Users/roberthyatt/Code/ironclaude add .claude-plugin/marketplace.json worker/.claude-plugin/plugin.json worker/.codex-plugin/plugin.json worker/mcp-servers/workspace-manager/package.json worker/mcp-servers/workspace-manager/package-lock.json commander/pyproject.toml CHANGELOG.md README.md
```

```bash
git -C /Users/roberthyatt/Code/ironclaude add -f docs/plans/2026-09-28-v1-1-14-slack-reply-and-limit-fixes-requirements.md docs/plans/2026-09-28-v1-1-14-slack-reply-and-limit-fixes-design.md docs/plans/2026-09-28-v1-1-14-slack-reply-and-limit-fixes.md docs/plans/2026-09-28-v1-1-14-slack-reply-and-limit-fixes.plan.json
```

Expected: exit 0. The release commit (no trailers) and the `v1.1.14` tag follow the end review. Nothing is pushed or deployed without the operator's explicit go.
