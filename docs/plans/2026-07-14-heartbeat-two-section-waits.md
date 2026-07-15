# Heartbeat Two-Section Waiting Display Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** Split the Slack heartbeat's single "WAITING ON YOU" block into two always-paired, explicitly labeled sections ("WAITING ON COMMANDER" and "WAITING ON {operator_name}"), each falling back to "there is nothing" when empty, with matching per-worker tag relabeling.

**Architecture:** `format_heartbeat()` gains `operator_name: str = "Operator"` and `commander_waits: dict | None = None` params (the latter a scaffold — never populated by any caller, always renders "there is nothing"). `main.py`'s two callsites (`post_heartbeat`, `_maybe_capture_operator_wait`) read `operator_name` from `self.config.get("operator_name", "Operator")` and pass/interpolate it. Pure formatting-layer change; no new state or detection logic.

**Tech Stack:** Python (commander), pytest.

---

## Task 1: RED — `format_heartbeat` tests for two-section format
**Files:**
- Modify: `commander/tests/test_notifications.py:364-392` (class `TestHeartbeatWaits`)

**Step 1: Replace `TestHeartbeatWaits` with the new two-section assertions**

Replace lines 364-392 with:

```python
class TestHeartbeatWaits:
    """format_heartbeat surfaces 'waiting on commander'/'waiting on operator' state
    in every heartbeat, as two always-paired labeled sections."""

    def test_waits_shows_both_sections_and_tags_worker(self):
        workers = [{"id": "d1267", "description": "Your task: Fix band collapse", "workflow_stage": "executing"}]
        waits = {"d1267": {"question": "approve the migration?"}}
        msg = format_heartbeat(workers, waits=waits, operator_name="Robert")
        assert "⏳ *WAITING ON COMMANDER:*" in msg
        assert "⏳ *WAITING ON Robert:*" in msg
        assert "d1267" in msg
        assert "approve the migration?" in msg
        # the worker's own line is tagged as waiting on the operator
        worker_line = next(ln for ln in msg.splitlines() if ln.startswith("•") and "d1267" in ln)
        assert "waiting on robert" in worker_line.lower()

    def test_commander_section_says_there_is_nothing(self):
        """No caller populates commander_waits today — the section is always a scaffold."""
        workers = [{"id": "d1267", "description": "Your task: Fix band collapse", "workflow_stage": "executing"}]
        waits = {"d1267": {"question": "approve the migration?"}}
        msg = format_heartbeat(workers, waits=waits, operator_name="Robert")
        lines = msg.splitlines()
        idx = lines.index("⏳ *WAITING ON COMMANDER:*")
        assert lines[idx + 1].strip() == "there is nothing"

    def test_operator_section_says_there_is_nothing_when_only_commander_populated(self):
        """Symmetric fallback, exercised directly even though main.py never produces
        this combination today (commander_waits is never populated by any caller)."""
        workers = [{"id": "d1267", "description": "Your task: Fix band collapse", "workflow_stage": "executing"}]
        commander_waits = {"d1267": {"question": "deploy approved?"}}
        msg = format_heartbeat(workers, waits={}, commander_waits=commander_waits, operator_name="Robert")
        assert "⏳ *WAITING ON COMMANDER:*" in msg
        assert "deploy approved?" in msg
        lines = msg.splitlines()
        idx = lines.index("⏳ *WAITING ON Robert:*")
        assert lines[idx + 1].strip() == "there is nothing"
        worker_line = next(ln for ln in msg.splitlines() if ln.startswith("•") and "d1267" in ln)
        assert "waiting on commander" in worker_line.lower()

    def test_waits_none_is_unchanged(self):
        workers = [{"id": "w1", "description": "Your task: Do stuff", "workflow_stage": "executing"}]
        assert format_heartbeat(workers, waits=None) == format_heartbeat(workers)
        assert "WAITING ON" not in format_heartbeat(workers, waits=None)

    def test_waits_empty_is_unchanged(self):
        workers = [{"id": "w1", "description": "Your task: Do stuff", "workflow_stage": "executing"}]
        assert format_heartbeat(workers, waits={}) == format_heartbeat(workers)

    def test_waits_shown_even_with_no_active_workers(self):
        """A held wait must surface even if the worker session is no longer listed."""
        msg = format_heartbeat([], waits={"d1267": {"question": "approve?"}})
        assert "WAITING ON COMMANDER" in msg
        assert "WAITING ON Operator" in msg
        assert "d1267" in msg
        assert "approve?" in msg

    def test_operator_name_defaults_to_operator(self):
        msg = format_heartbeat([], waits={"d1267": {"question": "approve?"}})
        assert "⏳ *WAITING ON Operator:*" in msg
```

**Step 2: Run the tests to verify they fail**

Run:
```bash
cd commander && python -m pytest tests/test_notifications.py::TestHeartbeatWaits -v
```

Expected: FAIL — `TypeError: format_heartbeat() got an unexpected keyword argument 'operator_name'` (or `'commander_waits'`), since `format_heartbeat` does not yet accept these params.

**Step 3: Fix the other two pre-existing literal assertions in this file**

Search for any other `"WAITING ON YOU"` literal in `commander/tests/test_notifications.py` outside `TestHeartbeatWaits` (there is one more, in a later test around line 390 in the original file — verify by searching the file for `WAITING ON YOU` after Step 1's edit). Update each remaining occurrence to `"WAITING ON Operator"` (default `operator_name`), passing no explicit `operator_name` kwarg in that test so the default applies. If the surrounding test already calls `format_heartbeat` without `operator_name`, only the assertion string needs updating.

**Step 4: Re-run the full test file to confirm all `WAITING ON YOU` literals are gone and only the intended new failures remain**

Run:
```bash
cd commander && grep -n "WAITING ON YOU\|waiting on you" tests/test_notifications.py
```

Expected: no output (no remaining matches).

**Step 5: Stage changes**

Run:
```bash
git add commander/tests/test_notifications.py
```

Expected: changes staged (professional mode blocks commit).

---

## Task 2: GREEN — implement two-section `format_heartbeat`
**Files:**
- Modify: `commander/src/ironclaude/notifications.py:96-129` (function `format_heartbeat`)

**Depends on:** Task 1

**Step 1: Replace `format_heartbeat`**

Replace lines 96-129 with:

```python
def format_heartbeat(
    workers: list[dict],
    brain_usage: dict | None = None,
    waits: dict | None = None,
    commander_waits: dict | None = None,
    operator_name: str = "Operator",
) -> str:
    waits = waits or {}
    commander_waits = commander_waits or {}
    if not workers and not waits and not commander_waits:
        return "*Heartbeat* | No active workers"
    lines = ["*Heartbeat*"]
    if waits or commander_waits:
        # Contract: if anything is holding on either audience, EVERY heartbeat says so,
        # and both sections always appear together.
        lines.append("⏳ *WAITING ON COMMANDER:*")
        if commander_waits:
            for wid, info in commander_waits.items():
                question = _escape_mrkdwn(str((info or {}).get("question") or "").strip()) or "(awaiting reply)"
                lines.append(f"  • `{wid}` — {question}")
        else:
            lines.append("  there is nothing")
        lines.append(f"⏳ *WAITING ON {operator_name}:*")
        if waits:
            for wid, info in waits.items():
                question = _escape_mrkdwn(str((info or {}).get("question") or "").strip()) or "(awaiting your reply)"
                lines.append(f"  • `{wid}` — {question}")
        else:
            lines.append("  there is nothing")
    for w in workers:
        snippet = _extract_task_snippet(w.get("description"))
        desc = _escape_mrkdwn(snippet)
        if len(desc) > 60:
            desc = desc[:60] + "..."
        stage = w.get("workflow_stage") or "unknown"
        if w["id"] in commander_waits:
            tag = " — ⏳ waiting on commander"
        elif w["id"] in waits:
            tag = f" — ⏳ waiting on {operator_name}"
        else:
            tag = ""
        lines.append(f'• {w["id"]} — "{desc}" ({stage}{tag})')
    if brain_usage is not None:
        inp = brain_usage.get("input_tokens", 0)
        out = brain_usage.get("output_tokens", 0)
        total = brain_usage.get("total_tokens", 0)
        line = f"🧠 Brain: {_fmt_tokens(total)} tokens ({_fmt_tokens(inp)} in + {_fmt_tokens(out)} out)"
        if total == 0:
            age = brain_usage.get("seconds_since_last_activity")
            if age is not None:
                line += f" — turn in progress (last activity {_fmt_duration(age)} ago)"
        lines.append(line)
    return "\n".join(lines)
```

**Step 2: Run tests to verify they pass**

Run:
```bash
cd commander && python -m pytest tests/test_notifications.py -v
```

Expected: all tests PASS, including all of `TestHeartbeatWaits`.

**Step 3: Confirm no remaining literal anywhere in the source file**

Run:
```bash
cd commander && grep -n "WAITING ON YOU\|waiting on you" src/ironclaude/notifications.py
```

Expected: no output.

**Step 4: Stage changes**

Run:
```bash
git add commander/src/ironclaude/notifications.py
```

Expected: changes staged.

---

## Task 3: RED — update `test_main_validate.py` for the new one-time-alert literal
**Files:**
- Modify: `commander/tests/test_main_validate.py:416-487`

**Step 1: Update the three assertions that check the one-time alert literal**

At line 424, in `test_awaiting_operator_message_captures_and_alerts`, change:
```python
assert "Waiting on you" in posts and "d1267" in posts and "approve the migration?" in posts
```
to:
```python
assert "Waiting on Operator" in posts and "d1267" in posts and "approve the migration?" in posts
```

At line 486, in `test_alert_deduped_for_same_question`, change:
```python
alerts = [c for c in d.slack.post_message.call_args_list if "Waiting on you" in str(c.args[0])]
```
to:
```python
alerts = [c for c in d.slack.post_message.call_args_list if "Waiting on Operator" in str(c.args[0])]
```

**Step 2: Strengthen `test_post_heartbeat_passes_waits` to verify `operator_name` pass-through**

At line 458-470, after the existing assertion `assert "d1267" in (kwargs.get("waits") or {})`, add:
```python
        assert kwargs.get("operator_name") == "Operator"
```

**Step 3: Run the tests to verify the two literal-assertion tests fail**

Run:
```bash
cd commander && python -m pytest tests/test_main_validate.py::TestOperatorWaits -v
```

Expected: FAIL — `test_awaiting_operator_message_captures_and_alerts` and `test_alert_deduped_for_same_question` fail (posts still contain `"Waiting on you"`, not `"Waiting on Operator"`); `test_post_heartbeat_passes_waits` fails with `KeyError`/`assert None == "Operator"` (no `operator_name` kwarg passed yet).

**Step 4: Stage changes**

Run:
```bash
git add commander/tests/test_main_validate.py
```

Expected: changes staged.

---

## Task 4: GREEN — thread `operator_name` through `main.py` callsites
**Files:**
- Modify: `commander/src/ironclaude/main.py:1385-1417` (`_maybe_capture_operator_wait`)
- Modify: `commander/src/ironclaude/main.py:2420-2447` (`post_heartbeat`)

**Depends on:** Task 2, Task 3

**Step 1: Update `_maybe_capture_operator_wait`'s one-time alert**

At `main.py:1411-1415`, change:
```python
        if self._operator_wait_alerted.get(worker_id) != question:
            self._operator_wait_alerted[worker_id] = question
            self.slack.post_message(
                f"⏳ *Waiting on you:* `{worker_id}` — {_escape_mrkdwn(question) or '(awaiting your reply)'}"
            )
```
to:
```python
        if self._operator_wait_alerted.get(worker_id) != question:
            self._operator_wait_alerted[worker_id] = question
            operator_name = self.config.get("operator_name", "Operator")
            self.slack.post_message(
                f"⏳ *Waiting on {operator_name}:* `{worker_id}` — {_escape_mrkdwn(question) or '(awaiting your reply)'}"
            )
```

**Step 2: Update `post_heartbeat`'s `format_heartbeat` call**

At `main.py:2443-2447`, change:
```python
        brain_usage = self.brain.get_token_usage() if self.brain is not None else None
        self._prune_operator_waits(now)
        self.slack.post_message(
            format_heartbeat(worker_details, brain_usage=brain_usage, waits=dict(self._operator_waits))
        )
```
to:
```python
        brain_usage = self.brain.get_token_usage() if self.brain is not None else None
        self._prune_operator_waits(now)
        operator_name = self.config.get("operator_name", "Operator")
        self.slack.post_message(
            format_heartbeat(
                worker_details,
                brain_usage=brain_usage,
                waits=dict(self._operator_waits),
                operator_name=operator_name,
            )
        )
```

**Step 3: Run tests to verify they pass**

Run:
```bash
cd commander && python -m pytest tests/test_main_validate.py::TestOperatorWaits -v
```

Expected: all tests PASS.

**Step 4: Confirm no remaining literal anywhere in main.py**

Run:
```bash
cd commander && grep -n "Waiting on you\|waiting on you" src/ironclaude/main.py
```

Expected: no output.

**Step 5: Run the full commander test suite**

Run:
```bash
cd /Users/roberthyatt/Code/ironclaude && make test-ironclaude
```

Expected: all tests PASS.

**Step 6: Stage changes**

Run:
```bash
git add commander/src/ironclaude/main.py
```

Expected: changes staged (professional mode blocks commit).

---

## Final Verification

Run:
```bash
cd commander && grep -rn "WAITING ON YOU\|Waiting on you\|waiting on you" src/ironclaude/notifications.py src/ironclaude/main.py tests/test_notifications.py tests/test_main_validate.py
```

Expected: no output — zero remaining old literals across all four files.
