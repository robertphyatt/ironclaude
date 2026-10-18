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
