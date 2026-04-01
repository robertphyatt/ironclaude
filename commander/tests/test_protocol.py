# tests/test_protocol.py
import json
import logging
import os
import unittest.mock as mock
import pytest
from ironclaude.protocol import (
    write_decision,
    read_pending_decisions,
    read_task_ledger,
    write_worker_spec,
    read_worker_spec,
)


@pytest.fixture
def tmp_dirs(tmp_path):
    decisions_dir = tmp_path / "decisions"
    specs_dir = tmp_path / "specs"
    decisions_dir.mkdir()
    specs_dir.mkdir()
    return {"decisions": str(decisions_dir), "specs": str(specs_dir), "base": str(tmp_path)}


class TestDecisions:
    def test_write_and_read_decision(self, tmp_dirs):
        decision = {"action": "spawn_worker", "worker_id": "w-1"}
        write_decision(tmp_dirs["decisions"], decision)
        pending = read_pending_decisions(tmp_dirs["decisions"])
        assert len(pending) == 1
        assert pending[0]["action"] == "spawn_worker"

    def test_decisions_cleared_after_read(self, tmp_dirs):
        write_decision(tmp_dirs["decisions"], {"action": "test"})
        read_pending_decisions(tmp_dirs["decisions"])
        assert read_pending_decisions(tmp_dirs["decisions"]) == []

    def test_multiple_decisions(self, tmp_dirs):
        write_decision(tmp_dirs["decisions"], {"action": "a"})
        write_decision(tmp_dirs["decisions"], {"action": "b"})
        pending = read_pending_decisions(tmp_dirs["decisions"])
        assert len(pending) == 2


class TestTaskLedger:
    def test_read_nonexistent_ledger(self, tmp_dirs):
        ledger = read_task_ledger(tmp_dirs["base"] + "/ledger.json")
        assert ledger is None

    def test_read_valid_ledger(self, tmp_dirs):
        path = tmp_dirs["base"] + "/ledger.json"
        data = {"objective": "Test", "tasks": [], "current_task": 0, "total_tasks": 0}
        with open(path, "w") as f:
            json.dump(data, f)
        ledger = read_task_ledger(path)
        assert ledger["objective"] == "Test"


class TestWorkerSpecs:
    def test_write_and_read_spec(self, tmp_dirs):
        spec = {"id": "w-1", "type": "claude-max", "repo": "/tmp"}
        write_worker_spec(tmp_dirs["specs"], spec)
        read = read_worker_spec(tmp_dirs["specs"], "w-1")
        assert read is not None
        assert read["type"] == "claude-max"

    def test_read_nonexistent_spec(self, tmp_dirs):
        assert read_worker_spec(tmp_dirs["specs"], "nope") is None


class TestWorkerSpecPathTraversal:
    def test_write_rejects_path_traversal(self, tmp_dirs):
        """write_worker_spec rejects worker IDs containing path separators."""
        spec = {"id": "../evil", "type": "claude-max", "repo": "/tmp"}
        with pytest.raises(ValueError):
            write_worker_spec(tmp_dirs["specs"], spec)

    def test_read_rejects_path_traversal(self, tmp_dirs):
        """read_worker_spec rejects worker IDs containing path separators."""
        with pytest.raises(ValueError):
            read_worker_spec(tmp_dirs["specs"], "../../../etc/passwd")

    def test_valid_id_accepted(self, tmp_dirs):
        """Valid IDs (alphanumeric, hyphen, underscore) are accepted."""
        spec = {"id": "worker-1_abc", "type": "claude-max", "repo": "/tmp"}
        write_worker_spec(tmp_dirs["specs"], spec)
        result = read_worker_spec(tmp_dirs["specs"], "worker-1_abc")
        assert result is not None
        assert result["type"] == "claude-max"


class TestDecisionSecurity:
    def test_write_decision_creates_dir_mode_0o700(self, tmp_path):
        """write_decision creates the decisions directory with mode 0o700."""
        decisions_dir = str(tmp_path / "new_decisions")
        write_decision(decisions_dir, {"action": "test"})
        assert (os.stat(decisions_dir).st_mode & 0o777) == 0o700

    def test_read_pending_creates_dir_mode_0o700(self, tmp_path):
        """read_pending_decisions creates the decisions directory with mode 0o700."""
        decisions_dir = str(tmp_path / "new_decisions")
        read_pending_decisions(decisions_dir)
        assert (os.stat(decisions_dir).st_mode & 0o777) == 0o700

    def test_read_pending_skips_foreign_owned_files(self, tmp_dirs, caplog):
        """read_pending_decisions skips and warns on files not owned by current user."""
        write_decision(tmp_dirs["decisions"], {"action": "mine"})

        real_stat = os.stat

        def fake_stat(path, **kwargs):
            if str(path).endswith(".json"):
                fake = mock.MagicMock()
                fake.st_uid = os.getuid() + 1
                return fake
            return real_stat(path, **kwargs)

        with mock.patch("ironclaude.protocol.os.stat", side_effect=fake_stat):
            with caplog.at_level(logging.WARNING, logger="ironclaude.protocol"):
                decisions = read_pending_decisions(tmp_dirs["decisions"])

        assert decisions == []
        assert any("not owned" in r.message for r in caplog.records)
