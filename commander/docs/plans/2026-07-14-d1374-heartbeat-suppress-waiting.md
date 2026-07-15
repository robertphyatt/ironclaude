# d1374 — Suppress "Waiting on Robert" Heartbeat When No Operator Action Is Needed — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** Stop the daemon's heartbeat (and its one-time Slack alert) from claiming "Waiting on Robert" when no real operator action is pending, by backing the display with a deterministic `pending_confirmation` directive signal and a tightened classifier prompt.

**Architecture:** Two independent signals feed `format_heartbeat()`'s WAITING ON section: (1) a new deterministic query for directives in `pending_confirmation` status, and (2) the existing grader-classified `_operator_waits`, whose prompt is corrected to distinguish "operator decision needed" from "narration about an autonomous system condition" (the confirmed false-positive pattern from d1362). The section only renders when the union of both is non-empty.

**Tech Stack:** Python, sqlite3, pytest, unittest.mock.

---

## Task 1: Tighten the awaiting-operator classifier prompt

**Files:**
- Modify: `src/ironclaude/main.py:77-83` (`_AWAITING_OP_SYSTEM`)
- Modify: `tests/test_main_validate.py` (add one test to `TestOperatorWaits`, near line 444)

**No TDD RED/GREEN cycle applies to the prompt edit itself:** `_AWAITING_OP_SYSTEM` is an LLM instruction string. Every existing and new test in `TestOperatorWaits` stubs `d._grader.grade.return_value` directly (see `_make_poll_daemon`, `tests/test_main_validate.py:396-408`) — no test invokes a real grader, so no unit test can fail-then-pass purely from editing prompt text. This is analogous to the skill's documented exception ("pure config/documentation... document why"). The regression coverage added below documents the intended contract (what the grader *should* now return for this exact phrasing) rather than proving the prompt achieves it — that can only be verified against the live grader, which is out of scope for this plan's automated tests.

**Step 1: Read current prompt**

Confirm current text at `src/ironclaude/main.py:77-83`:
```python
_AWAITING_OP_SYSTEM = (
    "You classify a Brain status message. Determine whether the Brain is reporting that it is "
    "WAITING ON THE OPERATOR (the human) to reply or decide before work can continue.\n\n"
    "If yes, extract the worker id it is waiting about (e.g. d1267; use null if it is the Brain itself) "
    "and a short paraphrase of what it is waiting for.\n\n"
    'Respond ONLY with valid JSON: {"awaiting_operator": true|false, "worker_id": "..."|null, "question": "..."|null}'
)
```

**Step 2: Replace with the tightened prompt**

Replace lines 77-83 with:
```python
_AWAITING_OP_SYSTEM = (
    "You classify a Brain status message. Determine whether the Brain is reporting that it is "
    "WAITING ON THE OPERATOR (the human) to make a decision or judgment call before work can "
    "continue — NOT merely narrating that some autonomous system condition (a test suite "
    "finishing, a worker completing, a heartbeat label going idle, a timer elapsing) has not "
    "yet resolved on its own.\n\n"
    "Examples of awaiting_operator=true: \"holding for your approval on the migration\", "
    "\"waiting on your decision: ship now or wait for review?\", \"pinned decision needed from you\".\n"
    "Examples of awaiting_operator=false (system state, not a human decision): "
    "\"waiting for the heartbeat labels to become idle\", \"worker is waiting on tests to pass\", "
    "\"holding until the build finishes\".\n\n"
    "If yes, extract the worker id it is waiting about (e.g. d1267; use null if it is the Brain itself) "
    "and a short paraphrase of what it is waiting for.\n\n"
    'Respond ONLY with valid JSON: {"awaiting_operator": true|false, "worker_id": "..."|null, "question": "..."|null}'
)
```

**Step 3: Add regression-documentation test**

In `tests/test_main_validate.py`, inside `class TestOperatorWaits`, add (near `test_awaiting_phrase_but_grader_says_no_falls_through`):
```python
def test_d1362_style_system_narration_falls_through_when_grader_says_no(self):
    """Regression anchor for the confirmed false positive (daemon.log:14222):
    'operator_wait recorded for d1362: Waiting for the heartbeat labels to become idle.'
    This asserts the WIRING correctly excludes a wait when the grader says False for
    this exact phrasing — it does not prove the tightened prompt drives the real grader
    to say False (unverifiable without a live grader call)."""
    d = _make_poll_daemon()
    d.brain.get_pending_responses.return_value = [
        "#1362 heartbeat two-section labels shipped, waiting for the heartbeat labels to become idle"
    ]
    d._grader.grade.return_value = {"awaiting_operator": False, "worker_id": None, "question": None}
    d.poll_brain_responses()
    assert d._operator_waits == {}
    assert "*Brain:*" in _posts(d)
```

**Step 4: Run the test**

```bash
PYTHONUNBUFFERED=1 python -m pytest tests/test_main_validate.py -k TestOperatorWaits -v
```

Expected: all tests in `TestOperatorWaits` pass, including the new one.

**Step 5: Stage changes**

```bash
git add src/ironclaude/main.py tests/test_main_validate.py
```

Expected: changes staged (professional mode blocks commit).

---

## Task 2: Add deterministic `pending_confirmation` signal to the heartbeat

**Files:**
- Modify: `src/ironclaude/main.py:2421-2454` (`post_heartbeat`, plus a new private method added just above it)
- Modify: `tests/test_main_validate.py` (add two tests to `TestOperatorWaits`)

**Depends on:** Task 1 (same file, sequential edit — avoids concurrent edits to `main.py`)

**Step 1: Write the tests (RED)**

First, confirm whether `tests/test_main_validate.py` already imports `sqlite3` at the top of the file. If not, add `import sqlite3` to its import block (`main.py` under test imports it already, but the test module itself does not by default).

In `tests/test_main_validate.py`, inside `class TestOperatorWaits`, add:
```python
def test_pending_confirmation_directive_surfaces_in_heartbeat_waits(self):
    """Uses the REAL format_heartbeat (not mocked) to confirm a directive-sourced
    entry renders sensibly in the actual Slack message, per the design's explicit
    instruction to verify this rendering."""
    d = _make_poll_daemon()
    d._last_heartbeat = 0.0
    d.config = {"heartbeat_interval_seconds": 0}
    d.registry = MagicMock()
    d.registry.get_recent_workers.return_value = []
    d.brain.get_token_usage.return_value = {"total_tokens": 0, "input_tokens": 0, "output_tokens": 0}
    d._db = sqlite3.connect(":memory:")
    d._db.execute("CREATE TABLE directives (id INTEGER PRIMARY KEY, interpretation TEXT, status TEXT)")
    d._db.execute(
        "INSERT INTO directives (id, interpretation, status) VALUES "
        "(1362, 'Heartbeat two-section waits', 'pending_confirmation')"
    )
    d._db.commit()
    d._operator_waits = {}
    d.post_heartbeat()
    posted = _posts(d)
    assert "d1362" in posted
    assert "Heartbeat two-section waits" in posted


def test_no_pending_confirmation_and_no_operator_waits_suppresses_section(self):
    """Uses the REAL format_heartbeat (not mocked) — this is the actual regression
    guard for the reported symptom: no directive pending, no classified wait, so
    the real rendered Slack message must contain no WAITING ON section at all."""
    d = _make_poll_daemon()
    d._last_heartbeat = 0.0
    d.config = {"heartbeat_interval_seconds": 0}
    d.registry = MagicMock()
    d.registry.get_recent_workers.return_value = []
    d.brain.get_token_usage.return_value = {"total_tokens": 0, "input_tokens": 0, "output_tokens": 0}
    d._db = sqlite3.connect(":memory:")
    d._db.execute("CREATE TABLE directives (id INTEGER PRIMARY KEY, interpretation TEXT, status TEXT)")
    d._db.commit()
    d._operator_waits = {}
    d.post_heartbeat()
    posted = _posts(d)
    assert "WAITING ON" not in posted
    assert "⏳" not in posted
```

Note: neither test mocks `format_heartbeat` (unlike the plan's other heartbeat tests) — both exercise the real renderer end-to-end via `_posts(d)` (reading `d.slack.post_message.call_args_list`, already defined at `tests/test_main_validate.py:_posts`), so the first test actually confirms directive-sourced rendering (per the design's instruction) and the second actually confirms the WAITING ON section is absent from the real rendered message — a true regression guard for the reported symptom, not just an assertion on an intermediate dict.

**Step 2: Run tests, verify they fail**

```bash
PYTHONUNBUFFERED=1 python -m pytest tests/test_main_validate.py -k "pending_confirmation" -v
```

Expected: FAIL — `test_pending_confirmation_directive_surfaces_in_heartbeat_waits` fails because `waits` only ever contains `_operator_waits` today (the directive isn't merged in). The suppression test may pass trivially already; that's fine, it becomes a regression guard.

**Step 3: Implement (GREEN)**

In `src/ironclaude/main.py`, add a new private method immediately before `post_heartbeat` (currently line 2421):
```python
    def _get_pending_confirmation_waits(self) -> dict[str, dict]:
        """Deterministic operator-wait signal: directives sitting in pending_confirmation
        are, by construction, waiting on the operator's confirm/reject Slack reaction —
        no LLM classification needed, cannot false-positive."""
        if self._db is None:
            return {}
        try:
            rows = self._db.execute(
                "SELECT id, interpretation FROM directives WHERE status='pending_confirmation'"
            ).fetchall()
        except Exception as e:
            # Broad catch matches the existing pattern at main.py:2456-2470 — a query
            # failure here must not crash the heartbeat.
            logger.warning("pending_confirmation query skipped: %s", e)
            return {}
        return {f"d{row[0]}": {"question": (row[1] or "")[:150]} for row in rows}

```

Then modify `post_heartbeat` (lines 2444-2454) from:
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
to:
```python
        brain_usage = self.brain.get_token_usage() if self.brain is not None else None
        self._prune_operator_waits(now)
        operator_name = self.config.get("operator_name", "Operator")
        # Deterministic signal is merged LAST so it wins over a same-id `_operator_waits`
        # entry — a stale/false-positive classifier entry must never mask a real
        # pending_confirmation directive sharing the same d{id} key.
        merged_waits = {**dict(self._operator_waits), **self._get_pending_confirmation_waits()}
        self.slack.post_message(
            format_heartbeat(
                worker_details,
                brain_usage=brain_usage,
                waits=merged_waits,
                operator_name=operator_name,
            )
        )
```

**Step 4: Run tests, verify they pass**

```bash
PYTHONUNBUFFERED=1 python -m pytest tests/test_main_validate.py -k TestOperatorWaits -v
```

Expected: all tests in `TestOperatorWaits` pass, including both new ones and the existing `test_post_heartbeat_passes_waits` (unaffected — it sets `d._db = None`, so `_get_pending_confirmation_waits()` returns `{}` and the merge is a no-op).

**Step 5: Run the full test suite for regressions**

```bash
PYTHONUNBUFFERED=1 python -m pytest tests/ -v
```

Expected: no new failures (the `[ACTION REQUIRED]` daemon→Brain nudge flow at `main.py:2287`/`main.py:2496` is untouched by this change and its existing tests should be unaffected).

**Step 6: Stage changes**

```bash
git add src/ironclaude/main.py tests/test_main_validate.py
```

Expected: changes staged (professional mode blocks commit).

---

## Task 3: Restart daemon to deploy the fix, then verify live

**Files:** none (operational step, no code changes; `allowed_files` in the JSON lists the plan JSON itself as a harmless placeholder since the schema requires a non-empty list)

**Depends on:** Task 1, Task 2

**No tests required:** this task is an MCP tool invocation during execution (restarting the running daemon process), not a code change — there is no test framework applicable to a runtime restart action.

**Important caveat carried from tier-up review:** the Task 1 classifier prompt change has zero automated verification (every test mocks the grader). The only real-world check that the reported symptom is actually fixed is Step 3 below — do not skip it.

**Step 1: Confirm prior tasks are committed**

Verify Tasks 1 and 2 passed code review and their changes are committed (per executing-plans' normal per-task review/commit flow) before restarting — restarting before the fix is committed would deploy stale code.

**Step 2: Restart the daemon with the directive id**

Call the `restart_daemon` MCP tool (in `orchestrator_mcp.py`) with:
```
restart_daemon(directive_id=1374)
```

Expected: per `orchestrator_mcp.py:3619-3631`, the directive #1374 row is looked up, `UPDATE directives SET status='completed', updated_at=datetime('now') WHERE id=1374` commits, and only then does the daemon fork/restart via SIGHUP — atomically marking the directive complete before the Brain process dies, preventing the restart-loop this pairs with (d1364).

**Step 3: Post-restart live verification (manual — closes the loop the automated tests cannot)**

Over the next 1-2 heartbeat cycles after restart, monitor Slack for the heartbeat message during a period where workers are running autonomously and no directive is in `pending_confirmation`. Confirm the "WAITING ON {operator_name}" section does NOT appear (or, if it does, that it corresponds to a real `pending_confirmation` directive or a genuine operator-blocking question — not system-state narration like the d1362 pattern). If the false-positive pattern recurs with different wording, that is a signal the classifier prompt needs a further iteration (a new directive), not that this fix failed to deploy.

---
