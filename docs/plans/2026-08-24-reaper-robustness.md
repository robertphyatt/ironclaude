# Reaper Robustness Fixes Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** Harden two v1.1.8 reaper defects — `_has_push_pending` crashing the sweep on a valid-JSON non-object disposition, and the one-time push-pending WARNING never re-arming for a reused workspace_guid.

**Requirements:** docs/plans/2026-08-24-reaper-robustness-requirements.md

**Architecture:** Localized hardening of two existing functions in `commander/src/ironclaude/main.py`. No new components, no happy-path behavior change. Both defects are line-verified against current source.

**Tech Stack:** Python 3, pytest, sqlite3.

---

## Task 1: Reaper robustness (I-1 isinstance guard + I-2 re-arm) + tests

**Files:**
- Modify: `commander/src/ironclaude/main.py:288-296` (`_has_push_pending`), `commander/src/ironclaude/main.py:441-547` (`_reap_leaked_worktrees` sweep)
- Test: `commander/tests/test_worktree_reaper.py`

**Step 1: RED — add failing tests**

In `commander/tests/test_worktree_reaper.py`, add to `class TestHasPushPending` (after `test_false_for_non_push`, ~:637):

```python
    def test_false_for_valid_json_non_object(self):
        # json.loads returns None/list/int for these — .get would raise AttributeError.
        for value in ("null", "[1]", "3"):
            assert _has_push_pending(value) is False
```

Add to `class TestReapLeakedWorktrees` (after `test_push_pending_row_is_preserved_and_warned_exactly_once`, ~:189) a sweep-does-not-crash test and a re-arm test:

```python
    def test_sweep_does_not_crash_on_valid_json_non_object_disposition(self, tmp_path):
        commander = _make_commander_db(tmp_path / "commander.db")
        ws = tmp_path / "workspaces.db"
        ws_conn = _make_workspace_db(ws)
        _insert_worker(commander, "w1", status="completed", finished_ago="25 hours", workspace_guid=_W1)
        _insert_assignment(ws_conn, _W1, owner_session_id=_OWNER, lifecycle_status="integrated",
                           updated_ago="25 hours", disposition="null")
        ws_conn.close()
        client = Mock()
        tmux = Mock()
        tmux.has_session.return_value = False
        # Pre-fix: _has_push_pending("null") raises AttributeError out of the candidate
        # loop; this call would propagate it. Post-fix the sweep completes.
        counts = _reap_leaked_worktrees(commander, client, tmux, workspace_db_path=str(ws))
        assert counts["push_pending"] == 0

    def test_reused_guid_rewarns_after_resolution(self, tmp_path, caplog):
        commander = _make_commander_db(tmp_path / "commander.db")
        ws = tmp_path / "workspaces.db"
        ws_conn = _make_workspace_db(ws)
        _insert_worker(commander, "w1", status="completed", finished_ago="25 hours", workspace_guid=_W1)
        _insert_assignment(ws_conn, _W1, owner_session_id=_OWNER, lifecycle_status="integrated",
                           updated_ago="25 hours", disposition=_PP_DISPOSITION)
        ws_conn.close()
        client = Mock()
        tmux = Mock()
        tmux.has_session.return_value = False
        alerted: set[str] = set()

        # Episode 1: push-pending → warns once, guid recorded in the shared set.
        with caplog.at_level("WARNING"):
            _reap_leaked_worktrees(commander, client, tmux,
                                   workspace_db_path=str(ws), push_pending_alerted=alerted)
        first = [r for r in caplog.records if "pending push" in r.getMessage()]

        # Resolution: row is cleaned up → no longer a candidate → sweep prunes it from the set.
        ws_conn2 = sqlite3.connect(str(ws))
        ws_conn2.execute("UPDATE assignments SET lifecycle_status='cleaned' WHERE workspace_guid=?", (_W1,))
        ws_conn2.commit()
        ws_conn2.close()
        caplog.clear()
        with caplog.at_level("WARNING"):
            _reap_leaked_worktrees(commander, client, tmux,
                                   workspace_db_path=str(ws), push_pending_alerted=alerted)

        # Re-stuck on the SAME guid → must warn again (pre-fix the guid stays in the set forever → silent).
        ws_conn3 = sqlite3.connect(str(ws))
        ws_conn3.execute(
            "UPDATE assignments SET lifecycle_status='integrated', disposition=?, "
            "updated_at=datetime('now', '-25 hours') WHERE workspace_guid=?",
            (_PP_DISPOSITION, _W1),
        )
        ws_conn3.commit()
        ws_conn3.close()
        caplog.clear()
        with caplog.at_level("WARNING"):
            _reap_leaked_worktrees(commander, client, tmux,
                                   workspace_db_path=str(ws), push_pending_alerted=alerted)
        third = [r for r in caplog.records if "pending push" in r.getMessage()]

        assert len(first) == 1
        assert len(third) == 1
```

Run:
```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_worktree_reaper.py -x -q
```

Expected: FAIL — `test_false_for_valid_json_non_object` raises AttributeError; `test_sweep_does_not_crash...` errors out of the candidate loop; `test_reused_guid_rewarns_after_resolution` fails at `assert len(third) == 1` (third is empty).

**Step 2: GREEN — I-1 isinstance guard**

In `commander/src/ironclaude/main.py`, replace the body of `_has_push_pending` (:292-296):

```python
    try:
        parsed = json.loads(disposition_json)
    except (ValueError, TypeError):
        return False
    return isinstance(parsed, dict) and parsed.get("phase") in (
        "push-pending", "push-succeeded", "push-failed"
    )
```

**Step 3: GREEN — I-2 re-arm**

In `_reap_leaked_worktrees`, initialize a local `seen_push_pending` set immediately after the counts/alerted init (after `commander/src/ironclaude/main.py:445`, the `resolve_transport` default line):

```python
    seen_push_pending: set[str] = set()
```

In the push-pending branch (`main.py:498-506`), record the guid. After `counts["push_pending"] += 1` add:

```python
                seen_push_pending.add(guid)
```

Immediately before the sweep's final `return counts` (`main.py:547`), prune the alerted set to only guids still push-pending this sweep:

```python
    push_pending_alerted.intersection_update(seen_push_pending)
```

**Step 4: Run tests (GREEN)**

Run:
```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_worktree_reaper.py -q
```

Expected: all pass, including the two pre-existing push-pending tests.

**Step 5: Stage changes**

Run:
```bash
git -C /Users/roberthyatt/Code/ironclaude add commander/src/ironclaude/main.py commander/tests/test_worktree_reaper.py
```

Expected: Changes staged (professional mode blocks commit).

---

**No version bump:** v1.1.8 is already bumped (un-committed); these fold in. No CHANGELOG change — the v1.1.8 reaper entry already covers the feature.
