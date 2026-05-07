# tests/test_tmux_manager.py
import os
import subprocess
import time

import pytest
from unittest.mock import patch, MagicMock
from ironclaude.tmux_manager import TmuxManager


class TestTmuxManager:
    def test_init(self):
        mgr = TmuxManager(log_dir="/tmp/ic-logs")
        assert mgr.log_dir == "/tmp/ic-logs"

    @patch("ironclaude.tmux_manager.subprocess.run")
    def test_has_session_true(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        mgr = TmuxManager(log_dir="/tmp/ic-logs")
        assert mgr.has_session("test") is True
        mock_run.assert_called_once_with(
            ["tmux", "has-session", "-t", "test"], capture_output=True
        )

    @patch("ironclaude.tmux_manager.subprocess.run")
    def test_has_session_false(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1)
        mgr = TmuxManager(log_dir="/tmp/ic-logs")
        assert mgr.has_session("test") is False

    @patch("ironclaude.tmux_manager.subprocess.run")
    def test_spawn_session(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        mgr = TmuxManager(log_dir="/tmp/ic-logs")
        mgr.spawn_session("worker-1", "echo hello", cwd="/tmp")
        calls = mock_run.call_args_list
        assert len(calls) == 2  # new-session + pipe-pane
        assert "worker-1" in calls[0].args[0]

    @patch("ironclaude.tmux_manager.subprocess.run")
    def test_kill_session(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        mgr = TmuxManager(log_dir="/tmp/ic-logs")
        mgr.kill_session("worker-1")
        mock_run.assert_called_once_with(
            ["tmux", "kill-session", "-t", "worker-1"], capture_output=True
        )

    @patch("ironclaude.tmux_manager.subprocess.run")
    def test_send_keys(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        mgr = TmuxManager(log_dir="/tmp/ic-logs")
        mgr.send_keys("worker-1", "hello world")
        calls = mock_run.call_args_list
        assert len(calls) == 2  # send-keys text + send-keys Enter

    @patch("ironclaude.tmux_manager.subprocess.run")
    def test_send_keys_enter_uses_list_form_not_shell(self, mock_run):
        """Enter keypress must use list form, not shell=True, to prevent injection."""
        mock_run.return_value = MagicMock(returncode=0)
        mgr = TmuxManager(log_dir="/tmp/ic-logs")
        mgr.send_keys("worker-1", "hello world")
        calls = mock_run.call_args_list
        enter_call = calls[1]
        assert isinstance(enter_call.args[0], list), "Enter keypress must use list form, not shell string"
        assert enter_call.kwargs.get("shell", False) is False

    @patch("ironclaude.tmux_manager.subprocess.run")
    def test_send_raw_keys_single_call(self, mock_run):
        """send_raw_keys sends all keys in a single subprocess call."""
        mock_run.return_value = MagicMock(returncode=0)
        mgr = TmuxManager(log_dir="/tmp/ic-logs")
        mgr.send_raw_keys("worker-1", ["Down", "Space", "Enter"])
        assert mock_run.call_count == 1
        call_args = mock_run.call_args[0][0]
        assert call_args == ["tmux", "send-keys", "-t", "worker-1", "Down", "Space", "Enter"]

    @patch("ironclaude.tmux_manager.subprocess.run")
    def test_send_raw_keys_list_form_not_shell(self, mock_run):
        """send_raw_keys must use list form, not shell=True, to prevent injection."""
        mock_run.return_value = MagicMock(returncode=0)
        mgr = TmuxManager(log_dir="/tmp/ic-logs")
        mgr.send_raw_keys("worker-1", ["Down"])
        assert isinstance(mock_run.call_args[0][0], list), "Must use list form, not shell string"
        assert mock_run.call_args.kwargs.get("shell", False) is False


class TestSpawnSessionPathTraversal:
    @patch("ironclaude.tmux_manager.subprocess.run")
    def test_rejects_path_traversal_name(self, mock_run):
        """spawn_session rejects session names containing path separators."""
        mgr = TmuxManager(log_dir="/tmp/ic-logs")
        with pytest.raises(ValueError):
            mgr.spawn_session("../evil", "echo hello")

    @patch("ironclaude.tmux_manager.subprocess.run")
    def test_rejects_dotdot_name(self, mock_run):
        """spawn_session rejects names with .. sequences."""
        mgr = TmuxManager(log_dir="/tmp/ic-logs")
        with pytest.raises(ValueError):
            mgr.spawn_session("ic-w1/../etc/passwd", "echo hello")

    @patch("ironclaude.tmux_manager.subprocess.run")
    def test_accepts_valid_name(self, mock_run):
        """spawn_session accepts valid alphanumeric-hyphen-underscore names."""
        mock_run.return_value = MagicMock(returncode=0)
        mgr = TmuxManager(log_dir="/tmp/ic-logs")
        result = mgr.spawn_session("ic-worker-1", "echo hello")
        assert result is True


class TestCapturePane:
    @patch("ironclaude.tmux_manager.subprocess.run")
    def test_capture_pane_returns_stdout(self, mock_run):
        """capture_pane returns rendered terminal output."""
        mock_run.return_value = MagicMock(returncode=0, stdout="Clean rendered output\n")
        mgr = TmuxManager(log_dir="/tmp/ic-logs")
        result = mgr.capture_pane("test-session", lines=50)
        assert result == "Clean rendered output\n"
        mock_run.assert_called_once_with(
            ["tmux", "capture-pane", "-p", "-t", "test-session", "-S", "-50"],
            capture_output=True, text=True, check=True,
        )

    @patch("ironclaude.tmux_manager.subprocess.run")
    def test_capture_pane_raises_on_dead_session(self, mock_run):
        """capture_pane raises CalledProcessError when session doesn't exist."""
        mock_run.side_effect = subprocess.CalledProcessError(1, "tmux")
        mgr = TmuxManager(log_dir="/tmp/ic-logs")
        with pytest.raises(subprocess.CalledProcessError):
            mgr.capture_pane("dead-session")


@pytest.fixture
def tmux(tmp_path):
    """Create a TmuxManager with a temp log directory."""
    return TmuxManager(log_dir=str(tmp_path))


class TestReadLogTailAnsiStripping:
    def test_strips_ansi_color_codes(self, tmux):
        """ANSI CSI color codes are removed from output."""
        log_path = tmux.get_log_path("test-session")
        with open(log_path, "w") as f:
            f.write("\x1b[32mProfessional Mode: ON\x1b[0m\n")
        result = tmux.read_log_tail("test-session", lines=5)
        assert "Professional Mode: ON" in result
        assert "\x1b" not in result

    def test_strips_carriage_returns(self, tmux):
        """Carriage returns from tmux pipe-pane are removed."""
        log_path = tmux.get_log_path("test-session")
        with open(log_path, "w") as f:
            f.write("line one\r\nline two\r\n")
        result = tmux.read_log_tail("test-session", lines=5)
        assert "\r" not in result
        assert "line one" in result
        assert "line two" in result

    def test_preserves_clean_text(self, tmux):
        """Clean text without ANSI codes passes through unchanged."""
        log_path = tmux.get_log_path("test-session")
        with open(log_path, "w") as f:
            f.write("ironclaude v1.0.34 | Professional Mode: ON | Status: idle\n")
        result = tmux.read_log_tail("test-session", lines=5)
        assert result == "ironclaude v1.0.34 | Professional Mode: ON | Status: idle\n"

    def test_strips_spinner_lines(self, tmux):
        """Spinner animation frames (e.g. Gallivanting...) are removed from output."""
        log_path = tmux.get_log_path("test-session")
        with open(log_path, "w") as f:
            f.write(
                "\u2736Gallivanting\u2026\n"
                "\u00b7Gallivanting\u2026\n"
                "\u2733Gallivanting\u2026\n"
                "Thinking\u2026\n"
                "  Reasoning\u2026\n"
                "Real output: edited src/main.py\n"
                "\u2722Gallivanting\u2026\n"
                "Task complete. 3 files changed.\n"
            )
        result = tmux.read_log_tail("test-session", lines=20)
        assert "Gallivanting" not in result
        assert "Thinking\u2026" not in result
        assert "Reasoning" not in result
        assert "Real output: edited src/main.py" in result
        assert "Task complete. 3 files changed." in result

    def test_returns_only_last_n_lines_from_large_file(self, tmux):
        """read_log_tail returns only the last N lines from a file with many more lines."""
        log_path = tmux.get_log_path("test-session")
        total_lines = 1000
        requested_lines = 10
        with open(log_path, "w") as f:
            for i in range(total_lines):
                f.write(f"line {i}\n")
        result = tmux.read_log_tail("test-session", lines=requested_lines)
        result_lines = result.splitlines()
        assert len(result_lines) == requested_lines
        assert result_lines[0] == f"line {total_lines - requested_lines}"
        assert result_lines[-1] == f"line {total_lines - 1}"


class TestCleanupOldLogs:
    def test_removes_old_log_files(self, tmux):
        """Log files older than max_age_days are removed."""
        old_log = os.path.join(tmux.log_dir, "old-session.log")
        with open(old_log, "w") as f:
            f.write("old log data")
        old_time = time.time() - (10 * 86400)
        os.utime(old_log, (old_time, old_time))
        removed = tmux.cleanup_old_logs(max_age_days=7)
        assert removed == 1
        assert not os.path.exists(old_log)

    def test_keeps_recent_log_files(self, tmux):
        """Log files newer than max_age_days are kept."""
        recent_log = os.path.join(tmux.log_dir, "recent-session.log")
        with open(recent_log, "w") as f:
            f.write("recent log data")
        removed = tmux.cleanup_old_logs(max_age_days=7)
        assert removed == 0
        assert os.path.exists(recent_log)

    def test_removes_old_done_and_brain_contact(self, tmux):
        """Old .done and .brain_contact marker files are also removed."""
        old_time = time.time() - (10 * 86400)
        for ext in (".done", ".brain_contact"):
            path = os.path.join(tmux.log_dir, f"old-session{ext}")
            with open(path, "w") as f:
                f.write("marker")
            os.utime(path, (old_time, old_time))
        removed = tmux.cleanup_old_logs(max_age_days=7)
        assert removed == 2

    def test_ignores_non_matching_extensions(self, tmux):
        """Files with other extensions are not touched."""
        old_time = time.time() - (10 * 86400)
        other_file = os.path.join(tmux.log_dir, "config.json")
        with open(other_file, "w") as f:
            f.write("{}")
        os.utime(other_file, (old_time, old_time))
        removed = tmux.cleanup_old_logs(max_age_days=7)
        assert removed == 0
        assert os.path.exists(other_file)

    def test_missing_log_dir_returns_zero(self):
        """Missing log directory returns 0, no crash."""
        mgr = TmuxManager(log_dir="/tmp/nonexistent-ic-test-dir")
        os.rmdir("/tmp/nonexistent-ic-test-dir")
        removed = mgr.cleanup_old_logs(max_age_days=7)
        assert removed == 0
