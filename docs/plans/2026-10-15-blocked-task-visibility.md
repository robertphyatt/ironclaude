# Blocked-Task Visibility Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** Make a ledger task that is blocked on the operator visible in every heartbeat, with a top-level reminder every 4 hours, by having the daemon read the real wiki ledger.

**Requirements:** `docs/plans/2026-10-15-blocked-task-visibility-design.md` (§Requirements R1–R3)

**Architecture:**
- The ledger parser moves to `protocol.py`. `read_task_ledger` reads both the wiki `.md` page and the legacy JSON file.
- The daemon's `_ledger_path` uses `<brain_cwd>/wiki/tasks.md` when `brain_cwd` is configured. Production always has it, because `config.DEFAULTS` sets it. Raw test configs without it keep the legacy `tmp_dir/task-ledger.json`. The Slack `status` command computes progress from the ledger's `tasks` list (completed/total).
- `post_heartbeat` lists blocked tasks through `format_heartbeat(blocked_tasks=...)`, then calls `_maybe_remind_blocked`. That posts a top-level reminder every `blocked_reminder_interval_seconds` (default 14400) for each blocked task that has an `escalation_ts`.

**Tech Stack:** Python 3.11, pytest (commander venv).

Loop: full. This adds daemon heartbeat and Slack posting behaviour.

## Execution invariants

- Literal absolute paths. Use `git -C /Users/roberthyatt/Code/ironclaude`.
- Run pytest with `PYTHONUNBUFFERED=1` and `commander/.venv/bin/python`, from `/Users/roberthyatt/Code/ironclaude/commander`.
- Never stash, commit or push.
- Tests must never read `~/.ironclaude`. Every daemon in the new tests sets `brain_cwd` to a `tmp_path` dir.

**Verified at planning time:**

| What | Where |
|---|---|
| `_extract_ledger_json` staticmethod | `orchestrator_mcp.py:7382-7403`; `orchestrator_mcp.py` has no `ironclaude.protocol` import |
| `read_task_ledger` | `protocol.py:65-71`; imports `json, logging, os, re, threading, time, Path` |
| `_ledger_path` | `main.py:1564`, next to `self._last_heartbeat = 0.0` at `:1560` |
| `datetime, timezone` import | `main.py:27` |
| `read_task_ledger` import | `main.py:49` |
| `post_heartbeat` / `format_heartbeat` call | `main.py:5479` / `:5542-5556` |
| `format_heartbeat` | `notifications.py:115-215`; `_escape_mrkdwn` `:12`, `_fmt_duration` `:106` |
| `SlackInterface.get_permalink(message_ts)` | returns `str` or `None` |
| `DEFAULTS["heartbeat_interval_seconds"]` | `config.py` |
| `tests/test_blocked_tasks.py` | does not exist |
| Live ledger `~/.ironclaude/brain/wiki/tasks.md` | statuses `completed`, `completed`, `in_progress`; no `current_task`/`total_tasks` keys |
| `_handle_status` | `main.py:3520-3533`, reads `ledger['current_task']`/`ledger['total_tasks']`; called unguarded from `poll_slack_commands` (`:2496`), which `run()` calls without a try (`:5679`) |
| `read_task_ledger` callers in `src` | only `main.py:3523` (status) and `:5222` (idle enforcement, already try-wrapped and `.get`-based) |
| `SlackBot.post_message` on failure | enqueues the text; `flush_queue` (`slack_interface.py:120-131`) keeps retrying it |

Switching `_ledger_path` makes idle enforcement start nudging the Brain about `pending`/`in_progress` ledger tasks as soon as this ships. That is that code's intended behaviour.

---

## Task 1: Shared ledger reader, and the daemon reads the wiki ledger

TDD.

**Files:**
- Modify: `commander/src/ironclaude/protocol.py` (`:65-71`)
- Modify: `commander/src/ironclaude/orchestrator_mcp.py` (`:7382-7403`)
- Modify: `commander/src/ironclaude/main.py` (`:1564`, `_handle_status` `:3524`)
- Create: `commander/tests/test_blocked_tasks.py`

### Step 1: Tests (RED)

Create `commander/tests/test_blocked_tasks.py`:
```python
# tests/test_blocked_tasks.py
"""Blocked ledger tasks surface in the heartbeat and get periodic reminders."""
import json
import os
import time
from datetime import datetime, timezone
from unittest.mock import MagicMock

from ironclaude.main import IroncladeDaemon
from ironclaude.protocol import read_task_ledger


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _write_ledger(brain_dir, tasks):
    wiki = brain_dir / "wiki"
    wiki.mkdir(parents=True, exist_ok=True)
    body = (
        "---\ntitle: Task Ledger\n---\n**Objective:** x\n\n## Data\n\n```json\n"
        + json.dumps({"objective": "x", "tasks": tasks})
        + "\n```\n"
    )
    (wiki / "tasks.md").write_text(body)


def _daemon(tmp_path, **extra):
    config = {"tmp_dir": str(tmp_path), "brain_cwd": str(tmp_path / "brain"),
              "operator_name": "Robert", **extra}
    slack = MagicMock()
    registry = MagicMock()
    registry.get_recent_workers.return_value = []
    tmux = MagicMock()
    tmux.log_dir = str(tmp_path / "logs")
    os.makedirs(tmux.log_dir, exist_ok=True)
    brain = MagicMock()
    brain.get_token_usage.return_value = {
        "total_tokens": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0,
        "seconds_since_last_activity": None,
    }
    d = IroncladeDaemon(config, slack, None, registry, tmux, brain)
    d._state_manager_db_path = str(tmp_path / "state-manager.db")
    return d


class TestLedgerReader:
    def test_reads_wiki_page_data_block(self, tmp_path):
        _write_ledger(tmp_path / "brain", [{"id": "t14", "status": "blocked"}])
        ledger = read_task_ledger(str(tmp_path / "brain" / "wiki" / "tasks.md"))
        assert ledger["tasks"] == [{"id": "t14", "status": "blocked"}]

    def test_legacy_json_and_missing(self, tmp_path):
        p = tmp_path / "task-ledger.json"
        p.write_text(json.dumps({"objective": "y", "tasks": [{"id": "a"}]}))
        assert read_task_ledger(str(p))["objective"] == "y"
        assert read_task_ledger(str(tmp_path / "nope.md")) is None

    def test_daemon_ledger_path_uses_brain_wiki_when_configured(self, tmp_path):
        d = _daemon(tmp_path)
        assert d._ledger_path == str(tmp_path / "brain" / "wiki" / "tasks.md")

    def test_daemon_ledger_path_legacy_without_brain_cwd(self, tmp_path):
        d = IroncladeDaemon({"tmp_dir": str(tmp_path)}, MagicMock(), None, MagicMock(),
                            MagicMock(log_dir=str(tmp_path)), MagicMock())
        assert d._ledger_path == str(tmp_path / "task-ledger.json")


class TestStatusCommand:
    def test_status_progress_from_wiki_ledger(self, tmp_path):
        _write_ledger(tmp_path / "brain", [
            {"id": "t1", "status": "completed"}, {"id": "t2", "status": "blocked"}])
        d = _daemon(tmp_path)
        d.registry.get_running_workers.return_value = []
        d.registry.get_active_objective.return_value = None
        d.brain.is_alive.return_value = True
        d._handle_status()
        assert "Progress: 1/2" in d.slack.post_message.call_args[0][0]
```
RED run:
```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_blocked_tasks.py -q
```
Expected: 3 failed, 2 passed.
- `test_reads_wiki_page_data_block` fails: today's `read_task_ledger` JSON-parses the `.md`, returns `None`, and subscripting gives a `TypeError`.
- `test_daemon_ledger_path_uses_brain_wiki_when_configured` fails: `_ledger_path` is the legacy path.
- `test_status_progress_from_wiki_ledger` fails: the legacy file is absent, so status shows `Progress: N/A`.
- The other two pass.

Record the measured result.

### Step 2: Implementation (GREEN)

1. In `protocol.py`, replace `read_task_ledger` (`:65-71`) with:
```python
def extract_ledger_json(body: str) -> dict:
    """Extract ledger JSON from the ## Data code fence in a wiki page body."""
    parts = body.split("## Data")
    if len(parts) < 2:
        return {"objective": None, "tasks": []}
    data_section = parts[1]
    fence_start = data_section.find("```")
    if fence_start == -1:
        return {"objective": None, "tasks": []}
    after_fence = data_section[fence_start + 3:]
    first_newline = after_fence.find("\n")
    if first_newline == -1:
        return {"objective": None, "tasks": []}
    json_content = after_fence[first_newline + 1:]
    fence_end = json_content.find("```")
    if fence_end == -1:
        return {"objective": None, "tasks": []}
    try:
        return json.loads(json_content[:fence_end].strip())
    except (json.JSONDecodeError, ValueError):
        return {"objective": None, "tasks": []}


def read_task_ledger(path: str) -> dict | None:
    """Read the task ledger. A .md path is the wiki ledger page (its ## Data JSON
    block); anything else is the legacy JSON file. Returns None if missing or
    unparseable."""
    try:
        with open(path) as f:
            raw = f.read()
    except (OSError, UnicodeDecodeError):
        return None
    if path.endswith(".md"):
        data = extract_ledger_json(raw)
        if not isinstance(data, dict):
            return None
        return data if (data.get("objective") or data.get("tasks")) else None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None
```
2. In `orchestrator_mcp.py`, replace the body of `_extract_ledger_json` (`:7383-7403`, keeping `@staticmethod` and the `def` line) with:
```python
        """Extract ledger JSON from the ## Data code fence in a wiki page body."""
        from ironclaude.protocol import extract_ledger_json
        return extract_ledger_json(body)
```
3. In `main.py`, replace line `:1564` (`self._ledger_path = os.path.join(config.get("tmp_dir", "/tmp/ic"), "task-ledger.json")`) with:
```python
        # The Brain's update_ledger writes <brain_cwd>/wiki/tasks.md; read that. Raw test
        # configs without brain_cwd keep the legacy JSON path (DEFAULTS always set it).
        _brain_cwd = config.get("brain_cwd")
        self._ledger_path = (
            os.path.join(os.path.expanduser(_brain_cwd), "wiki", "tasks.md")
            if _brain_cwd else os.path.join(config.get("tmp_dir", "/tmp/ic"), "task-ledger.json")
        )
```
4. In `main.py` `_handle_status`, replace the line `progress = f"{ledger['current_task']}/{ledger['total_tasks']}" if ledger else "N/A"` (`:3524`) with:
```python
        tasks = ledger.get("tasks") if isinstance(ledger, dict) else None
        tasks = [t for t in (tasks or []) if isinstance(t, dict)]
        done = sum(1 for t in tasks if t.get("status") == "completed")
        progress = f"{done}/{len(tasks)}" if tasks else "N/A"
```
The wiki ledger has no `current_task`/`total_tasks` keys. The old subscript would raise `KeyError` inside the unguarded Slack command loop and stop the daemon.

### Step 3: GREEN

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_blocked_tasks.py tests/test_enforcement.py tests/test_idle_enforcement.py tests/test_orchestrator_mcp.py -q > /private/tmp/claude-502/bt1.out 2>&1; echo "t1=$?"; tail -n 3 /private/tmp/claude-502/bt1.out
```
Expected: `t1=0`, with 0 failed.

### Step 4: Stage

```bash
git -C /Users/roberthyatt/Code/ironclaude add commander/src/ironclaude/protocol.py commander/src/ironclaude/orchestrator_mcp.py commander/src/ironclaude/main.py commander/tests/test_blocked_tasks.py
```
Expected: no output.

---

## Task 2: Heartbeat section and 4-hourly reminder

TDD. Depends on Task 1.

**Files:**
- Modify: `commander/src/ironclaude/notifications.py` (`format_heartbeat` signature and empty check; new section; new `format_blocked_reminder`)
- Modify: `commander/src/ironclaude/main.py` (`__init__` near `:1564`; `post_heartbeat` `:5525-5556`; new methods)
- Modify: `commander/src/ironclaude/config.py` (`DEFAULTS`)
- Modify: `commander/tests/test_blocked_tasks.py` (append)
- Modify: `docs/plans/2026-10-15-blocked-task-visibility-design.md` (documentation sync, Step 2 item 7)
- Stage only: `docs/plans/2026-10-15-blocked-task-visibility.md`, `docs/plans/2026-10-15-blocked-task-visibility.plan.json`

### Step 1: Tests (RED)

Append to `commander/tests/test_blocked_tasks.py`:
```python


from ironclaude.notifications import format_heartbeat

H = 5 * 3600  # blocked five hours ago


def _reminders(slack):
    return [c.args[0] for c in slack.post_message.call_args_list
            if c.args and str(c.args[0]).startswith("⏳ *Still waiting on Robert:*")]


class TestHeartbeatSection:
    def test_format_heartbeat_blocked_only(self):
        text = format_heartbeat([], operator_name="Robert", blocked_tasks=[
            {"id": "t14", "description": "Fix Loop 7 — blocked: A/B decision",
             "age_seconds": 3600 * 27, "permalink": "https://slack/p/1"}])
        assert "No active workers" not in text
        assert "🚧 *Blocked tasks" in text
        assert "t14" in text and "27h" in text and "https://slack/p/1" in text

    def test_format_heartbeat_without_blocked_unchanged(self):
        assert "Blocked tasks" not in format_heartbeat([], blocked_tasks=[])

    def test_post_heartbeat_lists_blocked_task(self, tmp_path):
        now = time.time()
        _write_ledger(tmp_path / "brain", [
            {"id": "t14", "description": "blocked: A/B", "status": "blocked",
             "status_set_at": _iso(now - H), "escalation_ts": "1791922986.893029"},
            {"id": "t13", "description": "done", "status": "completed"}])
        d = _daemon(tmp_path)
        d.slack.get_permalink.return_value = "https://slack/p/1"
        d.post_heartbeat(now=now)
        heartbeat = d.slack.post_message.call_args_list[0].args[0]
        assert "t14" in heartbeat and "https://slack/p/1" in heartbeat
        assert "t13" not in heartbeat


class TestReminder:
    def _setup(self, tmp_path, now, escalated=True):
        task = {"id": "t14", "description": "blocked: A/B", "status": "blocked",
                "status_set_at": _iso(now - H)}
        if escalated:
            task["escalation_ts"] = "1791922986.893029"
        _write_ledger(tmp_path / "brain", [task])
        d = _daemon(tmp_path)
        d.slack.get_permalink.return_value = "https://slack/p/1"
        d.slack.post_message.return_value = "ts"
        return d

    def test_reminds_then_waits_four_hours(self, tmp_path):
        now = time.time()
        d = self._setup(tmp_path, now)
        d.post_heartbeat(now=now)
        assert len(_reminders(d.slack)) == 1
        d.post_heartbeat(now=now + 3600)
        assert len(_reminders(d.slack)) == 1
        d.post_heartbeat(now=now + 4 * 3600 + 1)
        assert len(_reminders(d.slack)) == 2

    def test_no_reminder_without_escalation(self, tmp_path):
        now = time.time()
        d = self._setup(tmp_path, now, escalated=False)
        d.post_heartbeat(now=now)
        assert _reminders(d.slack) == []

    def test_failed_post_is_not_reposted_within_interval(self, tmp_path):
        # post_message queues a failed post for later flush, so a second attempt
        # inside the interval would deliver a duplicate.
        now = time.time()
        d = self._setup(tmp_path, now)
        d.slack.post_message.side_effect = (
            lambda text, *a, **k: None if text.startswith("⏳ *Still") else "ts")
        d.post_heartbeat(now=now)
        d.post_heartbeat(now=now + 1000)
        assert len(_reminders(d.slack)) == 1
        d.post_heartbeat(now=now + 4 * 3600 + 1)
        assert len(_reminders(d.slack)) == 2

    def test_unblocked_task_is_pruned(self, tmp_path):
        now = time.time()
        d = self._setup(tmp_path, now)
        d.post_heartbeat(now=now)
        assert "t14" in d._blocked_reminded
        _write_ledger(tmp_path / "brain", [{"id": "t14", "status": "completed"}])
        d.post_heartbeat(now=now + 1000)
        assert "t14" not in d._blocked_reminded
```
RED run:
```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_blocked_tasks.py -q -k "TestHeartbeatSection or TestReminder"
```
Expected: 6 failed, 1 passed.
- The 2 `format_heartbeat` tests fail with a `TypeError`, because there is no `blocked_tasks` keyword.
- `test_post_heartbeat_lists_blocked_task` fails, because the heartbeat lacks `t14`.
- 3 reminder tests fail, because the daemon posts no reminder and has no `_blocked_reminded`.
- `test_no_reminder_without_escalation` passes trivially before the change. It guards the escalation filter after GREEN.

Record the measured result.

### Step 2: Implementation (GREEN)

1. **`notifications.py`, `format_heartbeat`:**
   - Add a final parameter `blocked_tasks: list[dict] | None = None,` after `mem_line: str | None = None,`.
   - After `blocked_directives = blocked_directives or []`, add `blocked_tasks = blocked_tasks or []`.
   - Change the empty-check line to `if not workers and not waits and not brain_waits and not blocked_directives and not blocked_tasks:`.
   - Immediately after the `blocked_directives` section (the `for block in blocked_directives:` loop), add:
```python
    if blocked_tasks:
        lines.append(f"🚧 *Blocked tasks (waiting on {operator_name} where pinned):*")
        for task in blocked_tasks:
            desc = _escape_mrkdwn(str(task.get("description") or ""))
            if len(desc) > 120:
                desc = desc[:120] + "..."
            age = task.get("age_seconds")
            age_txt = f" (blocked {_fmt_duration(float(age))})" if age is not None else ""
            link = task.get("permalink")
            link_txt = f" — <{link}|decision>" if link else ""
            lines.append(f"  • {task.get('id', '?')} — {desc}{age_txt}{link_txt}")
```
2. **`notifications.py`, new function**, after `format_heartbeat`:
```python


def format_blocked_reminder(task: dict, operator_name: str = "Operator") -> str:
    """Top-level reminder for a ledger task blocked on a pinned operator decision."""
    desc = _escape_mrkdwn(str(task.get("description") or ""))
    age = task.get("age_seconds")
    age_txt = f" (blocked {_fmt_duration(float(age))})" if age is not None else ""
    link = task.get("permalink")
    link_txt = f"\nDecision: <{link}|pinned message>" if link else ""
    return f"⏳ *Still waiting on {operator_name}:* {task.get('id', '?')} — {desc}{age_txt}{link_txt}"
```
3. **`config.py`:** in `DEFAULTS`, after `"heartbeat_interval_seconds": 900,`, add `"blocked_reminder_interval_seconds": 14400,`.
4. **`main.py`, `__init__`:** immediately after the `self._ledger_path = (...)` block from Task 1, add `self._blocked_reminded: dict[str, float] = {}`.
5. **`main.py`, `post_heartbeat`:**
   - Immediately before `self._last_heartbeat_ts = self.slack.post_message(`, add:
```python
        try:
            blocked_tasks = self._blocked_ledger_tasks(now)
        except Exception as e:
            logger.warning(f"Heartbeat blocked-task read failed: {e}")
            blocked_tasks = []
```
   - Add `blocked_tasks=blocked_tasks,` as the last keyword argument of the `format_heartbeat(` call, after `mem_line=_format_mem_line(),`.
   - Immediately after that `self.slack.post_message(...)` statement closes, add:
```python
        try:
            self._maybe_remind_blocked(blocked_tasks, now)
        except Exception as e:
            logger.warning(f"Blocked-task reminder failed: {e}")
```
6. **`main.py`, new methods**, placed directly before `def post_heartbeat(`:
```python
    @staticmethod
    def _parse_iso_epoch(value) -> float | None:
        if not isinstance(value, str) or not value:
            return None
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None

    def _blocked_ledger_tasks(self, now: float) -> list[dict]:
        """Ledger tasks with status 'blocked' (R1), with age and escalation permalink."""
        ledger = read_task_ledger(self._ledger_path)
        if not ledger:
            return []
        blocked = []
        for task in ledger.get("tasks") or []:
            if not isinstance(task, dict) or task.get("status") != "blocked":
                continue
            since = self._parse_iso_epoch(task.get("status_set_at"))
            escalation_ts = task.get("escalation_ts")
            blocked.append({
                "id": task.get("id"),
                "description": task.get("description"),
                "escalation_ts": escalation_ts,
                "since_epoch": since,
                "age_seconds": (now - since) if since is not None else None,
                "permalink": self.slack.get_permalink(escalation_ts) if escalation_ts else None,
            })
        return blocked

    def _maybe_remind_blocked(self, blocked_tasks: list[dict], now: float) -> None:
        """Top-level reminder at most every blocked_reminder_interval_seconds for each
        blocked task with a pinned escalation (R2). The timer advances on every post
        attempt: SlackBot.post_message queues a failed post and flush_queue delivers
        it later, so retrying would duplicate it. Entries for tasks no longer
        blocked are pruned."""
        from ironclaude.notifications import format_blocked_reminder
        interval = float(self.config.get("blocked_reminder_interval_seconds", 14400))
        escalated = [t for t in blocked_tasks if t.get("escalation_ts")]
        live = {str(t.get("id")) for t in escalated}
        for key in list(self._blocked_reminded):
            if key not in live:
                del self._blocked_reminded[key]
        operator_name = self.config.get("operator_name", "Operator")
        for task in escalated:
            key = str(task.get("id"))
            base = self._blocked_reminded.get(key, task.get("since_epoch"))
            if base is None:
                self._blocked_reminded[key] = now
                continue
            if now - base < interval:
                continue
            self.slack.post_message(format_blocked_reminder(task, operator_name))
            self._blocked_reminded[key] = now
```

7. **`docs/plans/2026-10-15-blocked-task-visibility-design.md`, documentation sync** (no tests required: documentation). Make these exact replacements so the design matches this plan:
   - **Idle-enforcement sentence.**
     - Replace: "Today the live ledger holds only `completed` and `blocked` tasks. Idle enforcement's `pending`/`in_progress` ledger check therefore gains no new nudges right away; it becomes correct for when those statuses appear."
     - With: "Once the daemon reads the wiki ledger, idle enforcement's `pending`/`in_progress` ledger check starts nudging the Brain about such tasks. That is the check's intended behaviour; the restart after this change is when it becomes observable."
   - **Reader formats.**
     - Replace: "for a `.md` path it strips the wiki front matter and parses the `## Data` block; JSON stays for compatibility. It returns `None` when the file is missing or unparseable."
     - With: "for a `.md` path it parses the JSON in the `## Data` block (the front matter comes before it and is ignored); JSON stays for compatibility. It returns `None` when the file is missing, undecodable or unparseable."
   - **Ledger path.**
     - Replace the whole bullet that begins "`_ledger_path` becomes `os.path.join(os.path.expanduser(config.get(\"brain_cwd\"".
     - With two bullets:
       - "`_ledger_path` becomes `os.path.join(os.path.expanduser(brain_cwd), \"wiki\", \"tasks.md\")` when `brain_cwd` is configured, the same derivation as `orchestrator_mcp.py:516`. Production always has `brain_cwd`, through DEFAULTS. Configs without it keep the legacy JSON path."
       - "The Slack `status` command computes progress as completed/total from the ledger's `tasks` list, because the wiki ledger has no `current_task`/`total_tasks` keys."
   - **Reminder formatter.**
     - Replace: "New `format_blocked_reminder(task, operator_name, age, permalink)` builds the reminder."
     - With: "New `format_blocked_reminder(task, operator_name)` builds the reminder; the age and permalink come from the task dict."
   - **Failed post.**
     - Replace: "A failed reminder post is logged and retried at the next heartbeat; `last_reminded` advances only when the post succeeds."
     - With: "A failed reminder post is queued by `SlackBot.post_message` and delivered later by `flush_queue`. `last_reminded` therefore advances on every attempt, because posting again would duplicate the reminder."
   - **Failed-post test bullet.**
     - Replace: "  - a failed post leaves the timer unchanged."
     - With two lines: "  - a failed post is not posted again within the interval." and "- **Status command:** with a wiki ledger, `status` shows completed/total progress and does not crash."

### Step 3: GREEN plus the full commander suite

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_blocked_tasks.py tests/test_notifications.py -q > /private/tmp/claude-502/bt2.out 2>&1; echo "t2=$?"; tail -n 3 /private/tmp/claude-502/bt2.out; PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/ -q > /private/tmp/claude-502/bt-suite.out 2>&1; echo "suite=$?"; tail -n 4 /private/tmp/claude-502/bt-suite.out
```
Expected:
- `t2=0`, with 0 failed;
- `suite=1`, where the only failure is the known pre-existing `tests/test_executing_plans_skill.py::test_execution_complete_restage_command_stages_from_non_root_cwd`.

Any other failure must be shown pre-existing by running it in a temporary `git worktree` of `HEAD` (never `git stash`), or else fixed.

### Step 4: Stage

```bash
git -C /Users/roberthyatt/Code/ironclaude add commander/src/ironclaude/notifications.py commander/src/ironclaude/main.py commander/src/ironclaude/config.py commander/tests/test_blocked_tasks.py; git -C /Users/roberthyatt/Code/ironclaude add -f docs/plans/2026-10-15-blocked-task-visibility.md docs/plans/2026-10-15-blocked-task-visibility.plan.json docs/plans/2026-10-15-blocked-task-visibility-design.md
```
Expected: no output.
