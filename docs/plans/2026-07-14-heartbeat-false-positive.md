# d1389 — Fix Spurious "WAITING ON" Heartbeat Noise Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** Eliminate the spurious always-empty "WAITING ON COMMANDER: there is nothing" heartbeat noise and tighten the operator-wait classifier's fast-path exclusion for Brain messages that narrate holding on another automated pipeline participant (subagent/worker/reviewer/Fable), plus shorten the false-positive TTL backstop.

**Architecture:** Two independent fixes in two files, no shared state. Fix 0 (`notifications.py`) is a subtractive render guard. Fixes 1–3 (`main.py`) add a fast-path negative-match regex ahead of the LLM classifier call, extend the classifier's prompt examples, and shorten the TTL constant.

**Tech Stack:** Python, pytest, unittest.mock.

---

## Task 1: notifications.py — guard the empty COMMANDER section

**Files:**
- Modify: `commander/src/ironclaude/notifications.py:96-154` (`format_heartbeat`) — note: line numbers verified against current source at plan-review time; the file also gained an unrelated `ollama_degraded` parameter from concurrent work since brainstorming, which shifted line numbers by ~4 but does not affect this fix's edit target.
- Test: `commander/tests/test_notifications.py:364-423` (`TestHeartbeatWaits`)

**Step 1: Update the three tests that assert today's buggy behavior (RED)**

Three existing tests directly assert the current always-rendered COMMANDER section behavior — they must change (or gain a populated `commander_waits`) to match the fix's intent, or the fix in Step 3 will make them fail for the wrong reason (a real behavior change, not a bug). (Caught by tier-up review: the plan originally missed `test_waits_shows_both_sections_and_tags_worker`, which also asserts the COMMANDER header is present with `commander_waits` empty.)

In `commander/tests/test_notifications.py`, replace `test_waits_shows_both_sections_and_tags_worker` (lines 368-378) — give it a populated `commander_waits` so its "both sections" assertion continues to reflect real post-fix behavior instead of the empty-section artifact:

```python
    def test_waits_shows_both_sections_and_tags_worker(self):
        workers = [{"id": "d1267", "description": "Your task: Fix band collapse", "workflow_stage": "executing"}]
        waits = {"d1267": {"question": "approve the migration?"}}
        commander_waits = {"d1268": {"question": "deploy approved?"}}
        msg = format_heartbeat(workers, waits=waits, commander_waits=commander_waits, operator_name="Robert")
        assert "⏳ *WAITING ON COMMANDER:*" in msg
        assert "⏳ *WAITING ON Robert:*" in msg
        assert "d1267" in msg
        assert "approve the migration?" in msg
        # the worker's own line is tagged as waiting on the operator
        worker_line = next(ln for ln in msg.splitlines() if ln.startswith("•") and "d1267" in ln)
        assert "waiting on robert" in worker_line.lower()
```

Replace `test_commander_section_says_there_is_nothing` (lines 380-387) with:

```python
    def test_commander_section_omitted_when_empty(self):
        """Regression guard for the confirmed d1389 bug: format_heartbeat always
        rendered an empty '⏳ WAITING ON COMMANDER: / there is nothing' block
        whenever any real operator wait existed, because no caller ever passes
        commander_waits. The section must now be omitted entirely when empty."""
        workers = [{"id": "d1267", "description": "Your task: Fix band collapse", "workflow_stage": "executing"}]
        waits = {"d1267": {"question": "approve the migration?"}}
        msg = format_heartbeat(workers, waits=waits, operator_name="Robert")
        assert "⏳ *WAITING ON COMMANDER:*" not in msg
        assert "there is nothing" not in msg
```

Replace `test_waits_shown_even_with_no_active_workers` (lines 412-418) — remove the now-invalid `"WAITING ON COMMANDER" in msg` assertion (this scenario has no `commander_waits`, so after the fix that section won't render):

```python
    def test_waits_shown_even_with_no_active_workers(self):
        """A held wait must surface even if the worker session is no longer listed."""
        msg = format_heartbeat([], waits={"d1267": {"question": "approve?"}})
        assert "WAITING ON COMMANDER" not in msg
        assert "WAITING ON Operator" in msg
        assert "d1267" in msg
        assert "approve?" in msg
```

Leave `test_operator_section_says_there_is_nothing_when_only_commander_populated` (lines 389-401) and all other tests in the class unchanged — `commander_waits` is non-empty in that test, so the COMMANDER section still renders correctly under the fix; only the always-empty case changes.

**Step 2: Run tests to verify expected failure**

```bash
cd commander && PYTHONUNBUFFERED=1 python -m pytest tests/test_notifications.py::TestHeartbeatWaits -v
```

Expected: `test_commander_section_omitted_when_empty` and `test_waits_shown_even_with_no_active_workers` FAIL (current code still renders the empty COMMANDER section). `test_waits_shows_both_sections_and_tags_worker` PASSES already (its updated `commander_waits` argument exercises the same code path under both old and new code — it's not a red/green test for this fix, just corrected to remain meaningful post-fix). All other tests in the class PASS.

**Step 3: Implement the guard (GREEN)**

In `commander/src/ironclaude/notifications.py`, replace the body of the `if waits or commander_waits:` block (current lines 112-128):

```python
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
```

with:

```python
    if waits or commander_waits:
        # commander_waits is currently unwired by every caller (a future task must
        # define what "waiting on commander" means) — only render its section when
        # it actually has entries, instead of an always-empty "there is nothing" line.
        if commander_waits:
            lines.append("⏳ *WAITING ON COMMANDER:*")
            for wid, info in commander_waits.items():
                question = _escape_mrkdwn(str((info or {}).get("question") or "").strip()) or "(awaiting reply)"
                lines.append(f"  • `{wid}` — {question}")
        lines.append(f"⏳ *WAITING ON {operator_name}:*")
        if waits:
            for wid, info in waits.items():
                question = _escape_mrkdwn(str((info or {}).get("question") or "").strip()) or "(awaiting your reply)"
                lines.append(f"  • `{wid}` — {question}")
        else:
            lines.append("  there is nothing")
```

**Step 4: Run tests to verify pass**

```bash
cd commander && PYTHONUNBUFFERED=1 python -m pytest tests/test_notifications.py::TestHeartbeatWaits -v
```

Expected: all tests in `TestHeartbeatWaits` PASS.

**Step 5: Stage changes**

```bash
git add commander/src/ironclaude/notifications.py commander/tests/test_notifications.py
```

Expected: changes staged (professional mode blocks commit).

---

## Task 2: main.py — retargeted fast-path exclusion, classifier examples, shorter TTL

**Files:**
- Modify: `commander/src/ironclaude/main.py:69-104` (module constants), `commander/src/ironclaude/main.py:1393-1408` (`_maybe_capture_operator_wait`)
- Test: `commander/tests/test_main_validate.py:416-547` (`TestOperatorWaits`)

**Step 1: Write new tests for the fast-path exclusion (RED)**

In `commander/tests/test_main_validate.py`, add these three methods to `class TestOperatorWaits` (after `test_alert_deduped_for_same_question`, before `test_pending_confirmation_directive_surfaces_in_heartbeat_waits`):

```python
    def test_not_awaiting_fast_path_skips_grader_for_fable_review(self):
        """No directive ref in the message, matching the existing
        test_conversational_message_not_captured_and_dropped pattern — this ensures
        _validate_brain_message's own grader.grade call (for message-format
        validation, a separate concern) never fires either, so assert_not_called()
        is unambiguous. (Caught by tier-up review: an earlier draft included a
        directive ref, which reached _validate_brain_message's grader call.)"""
        d = _make_poll_daemon()
        d.brain.get_pending_responses.return_value = ["holding for the Fable review result"]
        d.poll_brain_responses()
        assert d._operator_waits == {}
        d._grader.grade.assert_not_called()

    def test_not_awaiting_fast_path_skips_grader_for_subagent_verdict(self):
        d = _make_poll_daemon()
        d.brain.get_pending_responses.return_value = ["waiting on subagent verdict before proceeding"]
        d.poll_brain_responses()
        assert d._operator_waits == {}
        d._grader.grade.assert_not_called()

    def test_not_awaiting_fast_path_does_not_exclude_genuine_operator_wait(self):
        d = _make_poll_daemon()
        d._grader.grade.return_value = {"awaiting_operator": True, "worker_id": "d1267", "question": "approve the migration?"}
        d.brain.get_pending_responses.return_value = ["holding for your approval on the migration"]
        d.poll_brain_responses()
        assert "d1267" in d._operator_waits
        d._grader.grade.assert_called_once()
```

**Step 2: Run tests to verify expected failure**

```bash
cd commander && PYTHONUNBUFFERED=1 python -m pytest tests/test_main_validate.py::TestOperatorWaits -v
```

Expected: `test_not_awaiting_fast_path_skips_grader_for_fable_review` and `test_not_awaiting_fast_path_skips_grader_for_subagent_verdict` FAIL (`_grader.grade` is currently called — no fast-path exists yet). `test_not_awaiting_fast_path_does_not_exclude_genuine_operator_wait` PASSES already (no regression risk, included to pin current behavior before the change).

**Step 3: Add `_NOT_AWAITING_RE` constant (GREEN, part 1)**

In `commander/src/ironclaude/main.py`, immediately after the `_AWAITING_PHRASE_RE` definition (current lines 72-76), insert:

```python
_NOT_AWAITING_RE = re.compile(
    r"(?:holding|waiting)\s+(?:for|on)\s+(?:the\s+)?"
    r"(?:subagent|sub-agent|worker|reviewer|fable|blind\s+review|tier[- ]?up|advisor)\b",
    re.IGNORECASE,
)
```

**Step 4: Extend `_AWAITING_OP_SYSTEM` classifier prompt examples (GREEN, part 2)**

In the same file, in `_AWAITING_OP_SYSTEM` (current lines 77-91), change:

```python
    "Examples of awaiting_operator=false (system state, not a human decision): "
    "\"waiting for the heartbeat labels to become idle\", \"worker is waiting on tests to pass\", "
    "\"holding until the build finishes\".\n\n"
```

to:

```python
    "Examples of awaiting_operator=false (system state, not a human decision): "
    "\"waiting for the heartbeat labels to become idle\", \"worker is waiting on tests to pass\", "
    "\"holding until the build finishes\", \"holding for the Fable review result\", "
    "\"waiting on subagent verdict\".\n\n"
```

**Step 5: Wire the fast-path into `_maybe_capture_operator_wait` (GREEN, part 3)**

In `_maybe_capture_operator_wait` (current lines 1393-1408), change:

```python
        if not _AWAITING_PHRASE_RE.search(text):
            return False
        try:
```

to:

```python
        if not _AWAITING_PHRASE_RE.search(text):
            return False
        if _NOT_AWAITING_RE.search(text):
            return False
        try:
```

**Step 6: Reduce the TTL constant (GREEN, part 4)**

In the same file, change (current line 101):

```python
_OPERATOR_WAIT_TTL_SECONDS = 1800   # backstop: drop a wait the Brain stopped re-affirming
```

to:

```python
_OPERATOR_WAIT_TTL_SECONDS = 600    # backstop: drop a wait the Brain stopped re-affirming
```

**Step 7: Run tests to verify pass**

```bash
cd commander && PYTHONUNBUFFERED=1 python -m pytest tests/test_main_validate.py::TestOperatorWaits -v
```

Expected: all tests in `TestOperatorWaits` PASS, including the three new ones.

**Step 8: Run the full test suite for both touched files to catch cross-effects**

```bash
cd commander && PYTHONUNBUFFERED=1 python -m pytest tests/test_notifications.py tests/test_main_validate.py -v
```

Expected: all PASS.

**Step 9: Stage changes**

```bash
git add commander/src/ironclaude/main.py commander/tests/test_main_validate.py
```

Expected: changes staged (professional mode blocks commit).

---

## Post-execution: restart the daemon

Per the directive spec, the final execution step is a daemon restart via the `restart_daemon` MCP tool with `directive_id=1389` (marks the directive completed before SIGHUP, preventing the restart-loop failure mode fixed in d1364). This is the last action after both tasks are staged and reviewed — not a plan task itself, since it's an MCP call, not a file edit.
