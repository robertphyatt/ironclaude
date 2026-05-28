# tests/test_tmux_manager.py
import os
import subprocess
import time

import pytest
from unittest.mock import patch, MagicMock
from ironclaude.tmux_manager import TmuxManager


# --- Fixtures for SSH tests ---

@pytest.fixture
def ssh_manager():
    mgr = MagicMock()
    mgr.get_ssh_args.return_value = ["ssh", "-o", "ControlMaster=auto", "remote-worker"]
    return mgr


@pytest.fixture
def tmux_ssh(tmp_path, ssh_manager):
    return TmuxManager(log_dir=str(tmp_path), ssh_manager=ssh_manager)


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
    def test_list_sessions_filters_by_prefix(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="ic-abc\nic-def\nother-session\n")
        mgr = TmuxManager(log_dir="/tmp/ic-logs")
        result = mgr.list_sessions(prefix="ic-")
        assert result == ["ic-abc", "ic-def"]
        mock_run.assert_called_once_with(
            ["tmux", "list-sessions", "-F", "#{session_name}"],
            capture_output=True, text=True,
        )

    @patch("ironclaude.tmux_manager.subprocess.run")
    def test_list_sessions_returns_empty_on_nonzero_returncode(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="")
        mgr = TmuxManager(log_dir="/tmp/ic-logs")
        result = mgr.list_sessions(prefix="ic-")
        assert result == []

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


# ====================================================================
# SSH transport layer tests
# ====================================================================

class TestRunHelper:
    @patch("ironclaude.tmux_manager.subprocess.run")
    def test_local_passthrough(self, mock_run, tmux_ssh):
        """Without ssh_host, command is passed through unchanged."""
        mock_run.return_value = MagicMock(returncode=0)
        tmux_ssh._run(["tmux", "has-session", "-t", "test"])
        mock_run.assert_called_once_with(["tmux", "has-session", "-t", "test"])

    @patch("ironclaude.tmux_manager.subprocess.run")
    def test_ssh_prefixed(self, mock_run, tmux_ssh):
        """With ssh_host, command is wrapped in SSH args."""
        mock_run.return_value = MagicMock(returncode=0)
        tmux_ssh._run(["tmux", "has-session", "-t", "test"], ssh_host="remote-worker")
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "ssh"
        assert "remote-worker" in cmd

    @patch("ironclaude.tmux_manager.subprocess.run")
    def test_ssh_without_manager_falls_back_to_local(self, mock_run):
        """ssh_host is ignored when no ssh_manager is configured."""
        mock_run.return_value = MagicMock(returncode=0)
        mgr = TmuxManager(log_dir="/tmp/ic-logs")
        mgr._run(["echo", "hello"], ssh_host="remote-worker")
        mock_run.assert_called_once_with(["echo", "hello"])

    @patch("ironclaude.tmux_manager.subprocess.run")
    def test_kwargs_forwarded(self, mock_run, tmux_ssh):
        """Extra kwargs are passed through to subprocess.run."""
        mock_run.return_value = MagicMock(returncode=0)
        tmux_ssh._run(["echo", "hi"], capture_output=True, text=True)
        assert mock_run.call_args.kwargs["capture_output"] is True
        assert mock_run.call_args.kwargs["text"] is True


class TestHasSessionSSH:
    @patch("ironclaude.tmux_manager.subprocess.run")
    def test_local_unchanged(self, mock_run, tmux_ssh):
        """has_session without ssh_host works as before."""
        mock_run.return_value = MagicMock(returncode=0)
        assert tmux_ssh.has_session("test") is True

    @patch("ironclaude.tmux_manager.subprocess.run")
    def test_remote_via_ssh(self, mock_run, tmux_ssh):
        """has_session with ssh_host routes through SSH."""
        mock_run.return_value = MagicMock(returncode=0)
        assert tmux_ssh.has_session("test", ssh_host="remote-worker") is True
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "ssh"


class TestKillSessionSSH:
    @patch("ironclaude.tmux_manager.subprocess.run")
    def test_remote_via_ssh(self, mock_run, tmux_ssh):
        """kill_session with ssh_host routes through SSH."""
        mock_run.return_value = MagicMock(returncode=0)
        assert tmux_ssh.kill_session("test", ssh_host="remote-worker") is True
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "ssh"


class TestSpawnSessionSSH:
    @patch("ironclaude.tmux_manager.subprocess.run")
    def test_remote_uses_remote_log_dir(self, mock_run, tmux_ssh):
        """spawn_session uses remote_log_dir for pipe-pane when set."""
        mock_run.return_value = MagicMock(returncode=0)
        tmux_ssh.spawn_session("w1", "echo hi", ssh_host="remote-worker",
                               remote_log_dir="/remote/logs")
        # Second call is pipe-pane
        pipe_call = mock_run.call_args_list[1]
        # The remote command string should contain /remote/logs/w1.log
        remote_cmd = pipe_call[0][0][-1]  # last arg is the quoted remote command
        assert "/remote/logs/w1.log" in remote_cmd


class TestCapturePaneSSH:
    @patch("ironclaude.tmux_manager.subprocess.run")
    def test_local_preserves_check_true(self, mock_run, tmux_ssh):
        """capture_pane without ssh_host still passes check=True."""
        mock_run.return_value = MagicMock(returncode=0, stdout="output\n")
        tmux_ssh.capture_pane("test")
        assert mock_run.call_args.kwargs.get("check") is True

    @patch("ironclaude.tmux_manager.subprocess.run")
    def test_remote_no_check(self, mock_run, tmux_ssh):
        """capture_pane with ssh_host does not pass check=True."""
        mock_run.return_value = MagicMock(returncode=0, stdout="output\n")
        tmux_ssh.capture_pane("test", ssh_host="remote-worker")
        assert "check" not in mock_run.call_args.kwargs or mock_run.call_args.kwargs["check"] is not True


class TestReadLogTailSSH:
    @patch("ironclaude.tmux_manager.subprocess.run")
    def test_remote_uses_tail_command(self, mock_run, tmux_ssh):
        """read_log_tail with ssh_host uses tail over SSH."""
        mock_run.return_value = MagicMock(returncode=0, stdout="line1\nline2\n")
        result = tmux_ssh.read_log_tail("test", ssh_host="remote-worker",
                                        remote_log_dir="/remote/logs")
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "ssh"
        remote_cmd = cmd[-1]
        assert "tail" in remote_cmd
        assert "/remote/logs/test.log" in remote_cmd

    @patch("ironclaude.tmux_manager.subprocess.run")
    def test_remote_failure_returns_not_found(self, mock_run, tmux_ssh):
        """read_log_tail returns 'No log file found' on remote failure."""
        mock_run.return_value = MagicMock(returncode=1, stdout="")
        result = tmux_ssh.read_log_tail("test", ssh_host="remote-worker")
        assert "No log file found" in result


class TestGetLogMtimeSSH:
    @patch("ironclaude.tmux_manager.subprocess.run")
    def test_remote_parses_epoch(self, mock_run, tmux_ssh):
        """get_log_mtime parses epoch from remote stat output."""
        mock_run.return_value = MagicMock(returncode=0, stdout="1715180000\n")
        result = tmux_ssh.get_log_mtime("test", ssh_host="remote-worker",
                                        remote_log_dir="/remote/logs")
        assert result == 1715180000.0

    @patch("ironclaude.tmux_manager.subprocess.run")
    def test_remote_failure_returns_none(self, mock_run, tmux_ssh):
        """get_log_mtime returns None on remote failure."""
        mock_run.return_value = MagicMock(returncode=1, stdout="")
        result = tmux_ssh.get_log_mtime("test", ssh_host="remote-worker")
        assert result is None


class TestFileOperations:
    def test_file_exists_local(self, tmux_ssh, tmp_path):
        """file_exists checks local filesystem when no ssh_host."""
        f = tmp_path / "marker.done"
        f.write_text("done")
        assert tmux_ssh.file_exists(str(f)) is True
        assert tmux_ssh.file_exists(str(tmp_path / "nope")) is False

    @patch("ironclaude.tmux_manager.subprocess.run")
    def test_file_exists_remote(self, mock_run, tmux_ssh):
        """file_exists uses test -f over SSH."""
        mock_run.return_value = MagicMock(returncode=0)
        assert tmux_ssh.file_exists("/tmp/ic-logs/test.done", ssh_host="remote-worker") is True

    def test_read_file_local(self, tmux_ssh, tmp_path):
        """read_file reads local files."""
        f = tmp_path / "data.txt"
        f.write_text("hello")
        assert tmux_ssh.read_file(str(f)) == "hello"

    def test_read_file_local_missing(self, tmux_ssh, tmp_path):
        """read_file returns None for missing local files."""
        assert tmux_ssh.read_file(str(tmp_path / "nope.txt")) is None

    @patch("ironclaude.tmux_manager.subprocess.run")
    def test_read_file_remote(self, mock_run, tmux_ssh):
        """read_file uses cat over SSH."""
        mock_run.return_value = MagicMock(returncode=0, stdout="remote-data")
        result = tmux_ssh.read_file("/tmp/file", ssh_host="remote-worker")
        assert result == "remote-data"

    @patch("ironclaude.tmux_manager.subprocess.run")
    def test_write_file_remote(self, mock_run, tmux_ssh):
        """write_file pipes content via SSH."""
        mock_run.return_value = MagicMock(returncode=0)
        result = tmux_ssh.write_file("/tmp/test.txt", "content", ssh_host="remote-worker")
        assert result is True

    def test_write_file_local(self, tmux_ssh, tmp_path):
        """write_file writes to local filesystem."""
        path = str(tmp_path / "out.txt")
        assert tmux_ssh.write_file(path, "hello") is True
        assert open(path).read() == "hello"

    def test_remove_file_local(self, tmux_ssh, tmp_path):
        """remove_file deletes local files."""
        f = tmp_path / "trash.txt"
        f.write_text("bye")
        assert tmux_ssh.remove_file(str(f)) is True
        assert not f.exists()

    def test_remove_file_local_missing(self, tmux_ssh, tmp_path):
        """remove_file returns False for missing local files."""
        assert tmux_ssh.remove_file(str(tmp_path / "nope.txt")) is False

    def test_mkdir_p_local(self, tmux_ssh, tmp_path):
        """mkdir_p creates nested directories locally."""
        d = str(tmp_path / "a" / "b" / "c")
        assert tmux_ssh.mkdir_p(d) is True
        assert os.path.isdir(d)

    @patch("ironclaude.tmux_manager.subprocess.run")
    def test_mkdir_p_remote(self, mock_run, tmux_ssh):
        """mkdir_p uses mkdir -p over SSH."""
        mock_run.return_value = MagicMock(returncode=0)
        assert tmux_ssh.mkdir_p("/remote/path", ssh_host="remote-worker") is True
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "ssh"

    @patch("ironclaude.tmux_manager.subprocess.run")
    def test_run_sqlite_query_remote(self, mock_run, tmux_ssh):
        """run_sqlite_query executes sqlite3 over SSH."""
        mock_run.return_value = MagicMock(returncode=0, stdout="executing\n")
        result = tmux_ssh.run_sqlite_query(
            "~/.claude/ironclaude.db",
            "SELECT workflow_stage FROM sessions WHERE terminal_session='abc'",
            ssh_host="remote-worker",
        )
        assert result == "executing"

    def test_run_sqlite_query_local_returns_none(self, tmux_ssh):
        """run_sqlite_query returns None without ssh_host (remote-only operation)."""
        result = tmux_ssh.run_sqlite_query("/some/db", "SELECT 1")
        assert result is None


# --- Menu detection fixtures and tests ---

from ironclaude.tmux_manager import detect_ask_user_menu


MENU_WITH_OTHER = """\
──────────────────────────────────────────────────────────────────────────
 ☐ Approach

Which approach fits your needs?

❯ 1. Event-driven
     Loose coupling, handles failures.
  2. Direct calls
     Simple, easy to debug.
  3. Other

Enter to select · ↑/↓ to navigate · Esc to cancel
"""

MENU_WITH_TYPE_SOMETHING = """\
──────────────────────────────────────────────────────────────────────────
 ☐ Goal

What is the primary goal?

❯ 1. Improve performance
     Optimize existing code paths.
  2. Add new functionality
     Build something new.
  3. Type something.

Enter to select · ↑/↓ to navigate · Esc to cancel
"""

MENU_NO_FREE_TEXT = """\
──────────────────────────────────────────────────────────────────────────
 ☐ Features

Which features to enable?

❯ 1. Logging
     Enable debug logging.
  2. Metrics
     Enable performance metrics.

Enter to select · ↑/↓ to navigate · Esc to cancel
"""

MENU_CURSOR_ON_OPTION_2 = """\
──────────────────────────────────────────────────────────────────────────
 ☐ Approach

Which approach fits your needs?

  1. Event-driven
     Loose coupling, handles failures.
❯ 2. Direct calls
     Simple, easy to debug.
  3. Other

Enter to select · ↑/↓ to navigate · Esc to cancel
"""

NO_MENU_OUTPUT = """\
ironclaude v1.0.8 | Professional Mode: ON | Status: executing

 Reading file src/main.py...
 Editing src/main.py...

$
"""


class TestDetectAskUserMenu:
    def test_detects_menu_with_other_option(self):
        """Detects menu and identifies 'Other' as the free-text option."""
        result = detect_ask_user_menu(MENU_WITH_OTHER)
        assert result["detected"] is True
        assert result["free_text_option"] == 3
        assert result["current_selection"] == 1
        assert len(result["options"]) == 3

    def test_detects_menu_with_type_something(self):
        """Detects menu and identifies 'Type something.' as the free-text option."""
        result = detect_ask_user_menu(MENU_WITH_TYPE_SOMETHING)
        assert result["detected"] is True
        assert result["free_text_option"] == 3
        assert result["current_selection"] == 1

    def test_detects_menu_without_free_text(self):
        """Detects menu but free_text_option is None when no Other/Type option."""
        result = detect_ask_user_menu(MENU_NO_FREE_TEXT)
        assert result["detected"] is True
        assert result["free_text_option"] is None
        assert len(result["options"]) == 2

    def test_no_menu_returns_not_detected(self):
        """Normal terminal output returns detected=False."""
        result = detect_ask_user_menu(NO_MENU_OUTPUT)
        assert result["detected"] is False
        assert result["options"] == []
        assert result["free_text_option"] is None
        assert result["current_selection"] is None

    def test_current_selection_detected(self):
        """Cursor position (❯) is correctly identified."""
        result = detect_ask_user_menu(MENU_CURSOR_ON_OPTION_2)
        assert result["current_selection"] == 2

    def test_parses_option_labels(self):
        """All option labels are correctly extracted."""
        result = detect_ask_user_menu(MENU_WITH_OTHER)
        labels = [label for _, label in result["options"]]
        assert "Event-driven" in labels
        assert "Direct calls" in labels
        assert "Other" in labels
