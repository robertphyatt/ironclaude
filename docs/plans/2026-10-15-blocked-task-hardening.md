# Blocked-Task Follow-up Hardening Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** Stop a non-string ledger task description from crashing the Commander's idle enforcement, escape ledger task ids in Slack output, and pin the first-reminder delay with a test.

**Requirements:** `docs/plans/2026-10-15-blocked-task-hardening-design.md` (§Requirements R1–R3)

**Architecture:**
- Idle enforcement turns the description into a string before slicing it.
- Both blocked-task renderers pass the task id through the existing `_escape_mrkdwn`.
- A new test covers the reminder timer's existing first-reminder delay.

**Tech Stack:** Python 3.11, pytest (commander venv).

Loop: light. This touches no DB, auth, guard, concurrency, git or public API; it is about 3 files and 25 lines.

## Execution invariants

- Use literal absolute paths, and `git -C /Users/roberthyatt/Code/ironclaude`.
- Run pytest with `PYTHONUNBUFFERED=1` and `commander/.venv/bin/python`, from `/Users/roberthyatt/Code/ironclaude/commander`.
- Never stash, commit or push.
- The tests never read `~/.ironclaude`, because they reuse the `_daemon` and `_write_ledger` helpers in `test_blocked_tasks.py`, which use `tmp_path`.

**Verified at planning time:**

| What | Where / fact |
|---|---|
| Idle enforcement description slice | `main.py:5279-5281`: `task_list = ", ".join(t.get("description", "unknown")[:40] for t in pending_tasks[:5])`, in the tier-2 branch (`idle_duration < 360`) |
| Idle enforcement callers | `check_idle_enforcement` is at `main.py:5194`. It is throttled by `_last_idle_check` (starts at `0.0`, `:1590`), and it skips sends only when `self.brain._executing_tool is True` (`:5261`), never for a `MagicMock` Brain. |
| `_get_unprocessed_messages` | returns `[]` when `_db is None` (`main.py:2058`) |
| Heartbeat blocked-task line, raw id | `notifications.py:163` |
| Reminder, raw id | `notifications.py:238` |
| `_escape_mrkdwn` | `notifications.py:12`, escaping `&`, `<` and `>` |
| `test_blocked_tasks.py` | defines `_iso`, `_write_ledger`, `_daemon`, `_reminders` and `H`; imports `format_heartbeat` but not `format_blocked_reminder` |

---

## Task 1: Description coercion, id escaping and first-reminder delay test

TDD.

**Files:**
- Modify: `commander/src/ironclaude/main.py` (`:5280`)
- Modify: `commander/src/ironclaude/notifications.py` (`:163`, `:238`)
- Modify: `commander/tests/test_blocked_tasks.py` (append)
- Stage only: `docs/plans/2026-10-15-blocked-task-hardening-design.md`, `docs/plans/2026-10-15-blocked-task-hardening.md`, `docs/plans/2026-10-15-blocked-task-hardening.plan.json`

### Step 1: Tests (RED)

Append to `commander/tests/test_blocked_tasks.py`:
```python


from ironclaude.notifications import format_blocked_reminder


class TestHardening:
    def test_idle_enforcement_survives_non_string_description(self, tmp_path):
        _write_ledger(tmp_path / "brain", [
            {"id": "t9", "description": None, "status": "pending"}])
        d = _daemon(tmp_path)
        d._idle_enforcement_start = time.time() - 240
        d._idle_escalation_tier = 1
        d.check_idle_enforcement()
        msg = d.brain.send_message.call_args[0][0]
        assert "Ledger tasks: unknown." in msg

    def test_task_id_is_escaped_in_heartbeat_and_reminder(self):
        task = {"id": "<!channel>", "description": "x", "age_seconds": 60,
                "permalink": None}
        for text in (format_heartbeat([], blocked_tasks=[task]),
                     format_blocked_reminder(task, "Robert")):
            assert "&lt;!channel&gt;" in text
            assert "<!channel>" not in text

    def test_first_reminder_waits_for_interval_since_blocked(self, tmp_path):
        now = time.time()
        _write_ledger(tmp_path / "brain", [
            {"id": "t14", "description": "blocked: A/B", "status": "blocked",
             "status_set_at": _iso(now - 3600), "escalation_ts": "1791922986.893029"}])
        d = _daemon(tmp_path)
        d.slack.get_permalink.return_value = "https://slack/p/1"
        d.slack.post_message.return_value = "ts"
        d.post_heartbeat(now=now)
        assert _reminders(d.slack) == []
        d.post_heartbeat(now=now + 3 * 3600 + 1)
        assert len(_reminders(d.slack)) == 1
```
RED run:
```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_blocked_tasks.py -q -k TestHardening
```
Expected: 2 failed, 1 passed.
- `test_idle_enforcement_survives_non_string_description` fails with `TypeError: 'NoneType' object is not subscriptable`, from `main.py:5280`.
- `test_task_id_is_escaped_in_heartbeat_and_reminder` fails, because the raw `<!channel>` is present.
- `test_first_reminder_waits_for_interval_since_blocked` passes before the change. It guards the existing `status_set_at`-based delay: it would fail if the timer reminded on first sight. That would happen, for example, if `_maybe_remind_blocked`'s `self._blocked_reminded.get(key, task.get("since_epoch"))` defaulted to `now - interval`.

Record the measured result.

### Step 2: Implementation (GREEN)

1. In `main.py`, change line `:5280` from `t.get("description", "unknown")[:40] for t in pending_tasks[:5]` to:
```python
                    str(t.get("description") or "unknown")[:40] for t in pending_tasks[:5]
```
2. In `notifications.py:163`, change `lines.append(f"  • {task.get('id', '?')} — {desc}{age_txt}{link_txt}")` to:
```python
            lines.append(f"  • {_escape_mrkdwn(str(task.get('id', '?')))} — {desc}{age_txt}{link_txt}")
```
3. In `notifications.py:238`, change `return f"⏳ *Still waiting on {operator_name}:* {task.get('id', '?')} — {desc}{age_txt}{link_txt}"` to:
```python
    return f"⏳ *Still waiting on {operator_name}:* {_escape_mrkdwn(str(task.get('id', '?')))} — {desc}{age_txt}{link_txt}"
```

### Step 3: GREEN plus the full commander suite

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_blocked_tasks.py tests/test_notifications.py tests/test_idle_enforcement.py -q > /private/tmp/claude-502/bth.out 2>&1; echo "t=$?"; tail -n 3 /private/tmp/claude-502/bth.out; PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/ -q > /private/tmp/claude-502/bth-suite.out 2>&1; echo "suite=$?"; tail -n 4 /private/tmp/claude-502/bth-suite.out
```
Expected:
- `t=0`, with 0 failed;
- `suite=1`, where the only failure is the known pre-existing `tests/test_executing_plans_skill.py::test_execution_complete_restage_command_stages_from_non_root_cwd`.

Any other failure must be shown pre-existing by running it in a temporary `git worktree` of `HEAD` (never `git stash`), or else fixed.

### Step 4: Stage

```bash
git -C /Users/roberthyatt/Code/ironclaude add commander/src/ironclaude/main.py commander/src/ironclaude/notifications.py commander/tests/test_blocked_tasks.py; git -C /Users/roberthyatt/Code/ironclaude add -f docs/plans/2026-10-15-blocked-task-hardening-design.md docs/plans/2026-10-15-blocked-task-hardening.md docs/plans/2026-10-15-blocked-task-hardening.plan.json
```
Expected: no output.
