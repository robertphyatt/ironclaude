# tests/test_singleton.py
"""Unit tests for ironclaude daemon singleton enforcement."""

import fcntl
import os
import signal

import pytest
from unittest.mock import patch

import ironclaude.main as main_module


@pytest.fixture(autouse=True)
def reset_pid_lock_fd():
    """Reset _pid_lock_fd before/after each test to avoid leaked fds."""
    original = main_module._pid_lock_fd
    yield
    if main_module._pid_lock_fd is not None and main_module._pid_lock_fd != original:
        try:
            os.close(main_module._pid_lock_fd)
        except OSError:
            pass
    main_module._pid_lock_fd = original


@pytest.fixture
def pid_file(tmp_path, monkeypatch):
    """Override _PID_FILE to use a temp path for isolation."""
    path = str(tmp_path / "ic-daemon-test.pid")
    monkeypatch.setattr(main_module, "_PID_FILE", path)
    return path


class TestAcquireSingletonLock:
    def test_fresh_start_creates_pid_file(self, pid_file):
        """No existing PID file: acquires lock and writes current PID."""
        main_module._acquire_singleton_lock()
        assert os.path.exists(pid_file)
        with open(pid_file) as f:
            assert int(f.read().strip()) == os.getpid()
        assert main_module._pid_lock_fd is not None

    def test_stale_pid_logs_warning_and_proceeds(self, pid_file):
        """Existing PID file with dead process PID: logs warning and overwrites."""
        with open(pid_file, "w") as f:
            f.write("99999999\n")
        with patch("os.kill", side_effect=ProcessLookupError), \
             patch.object(main_module.logger, "warning") as mock_warn:
            main_module._acquire_singleton_lock()
        mock_warn.assert_called_once()
        assert "stale" in mock_warn.call_args[0][0].lower()
        with open(pid_file) as f:
            assert int(f.read().strip()) == os.getpid()

    def test_alive_pid_exits_with_error(self, pid_file):
        """PID file contains alive PID (no lock held): exits 1."""
        with open(pid_file, "w") as f:
            f.write("99999\n")
        with patch("os.kill", return_value=None), \
             pytest.raises(SystemExit) as exc:
            main_module._acquire_singleton_lock()
        assert exc.value.code == 1

    def test_permission_error_treated_as_alive(self, pid_file):
        """PermissionError from os.kill: process exists but unsignable — exit 1."""
        with open(pid_file, "w") as f:
            f.write("99999\n")
        with patch("os.kill", side_effect=PermissionError), \
             pytest.raises(SystemExit) as exc:
            main_module._acquire_singleton_lock()
        assert exc.value.code == 1

    def test_lock_held_exits_with_error(self, pid_file):
        """Another process holds flock: exits 1 with informative message."""
        with patch("fcntl.flock", side_effect=BlockingIOError), \
             patch.object(main_module.logger, "error") as mock_err, \
             pytest.raises(SystemExit) as exc:
            main_module._acquire_singleton_lock()
        assert exc.value.code == 1
        mock_err.assert_called_once()
        assert "already running" in mock_err.call_args[0][0]

    def test_corrupt_pid_file_overwritten_silently(self, pid_file):
        """Corrupt PID file content: overwrites without error."""
        with open(pid_file, "w") as f:
            f.write("not-a-pid\n")
        main_module._acquire_singleton_lock()
        with open(pid_file) as f:
            assert int(f.read().strip()) == os.getpid()

    def test_fd_cloexec_set_on_lock_fd(self, pid_file):
        """Lock fd has FD_CLOEXEC set so it is not inherited across os.execvp."""
        main_module._acquire_singleton_lock()
        fd = main_module._pid_lock_fd
        assert fd is not None
        flags = fcntl.fcntl(fd, fcntl.F_GETFD)
        assert flags & fcntl.FD_CLOEXEC, "FD_CLOEXEC must be set on lock fd"


class TestHandleRestartReleasesLock:
    def test_sighup_truncates_and_closes_fd_before_execvp(self, monkeypatch):
        """_handle_restart cleans up children and releases PID lock before os.execvp."""
        from unittest.mock import MagicMock

        fake_fd = 99
        main_module._pid_lock_fd = fake_fd

        # Set up fake _daemon with brain and _db
        fake_daemon = MagicMock()
        fake_daemon._db = MagicMock()
        monkeypatch.setattr(main_module, "_daemon", fake_daemon)

        truncated = []
        closed = []
        pkill_called = []

        def fake_ftruncate(fd, size):
            truncated.append(fd)

        def fake_close(fd):
            closed.append(fd)

        def fake_execvp(path, args):
            raise SystemExit(0)

        def fake_subprocess_run(cmd, **kwargs):
            pkill_called.append(list(cmd))
            return MagicMock(returncode=0)

        monkeypatch.setattr(os, "ftruncate", fake_ftruncate)
        monkeypatch.setattr(os, "close", fake_close)
        monkeypatch.setattr(os, "execvp", fake_execvp)
        monkeypatch.setattr(main_module.subprocess, "run", fake_subprocess_run)
        monkeypatch.setattr(main_module.time, "sleep", lambda _: None)

        with pytest.raises(SystemExit):
            main_module._handle_restart(signal.SIGHUP, None)

        fake_daemon.shutdown.assert_called_once()
        fake_daemon.brain.shutdown.assert_called_once()
        fake_daemon._db.close.assert_called_once()
        assert any(cmd[0] == "pkill" for cmd in pkill_called), "pkill -P should be called"
        assert fake_fd in truncated, "fd should be truncated before exec"
        assert fake_fd in closed, "fd should be closed before exec"
        assert main_module._pid_lock_fd is None, "_pid_lock_fd should be cleared"

    def test_handle_restart_close_runs_even_if_ftruncate_raises(self, monkeypatch):
        """os.close is called even when os.ftruncate raises OSError — regression for production outage."""
        from unittest.mock import MagicMock

        fake_fd = 99
        main_module._pid_lock_fd = fake_fd
        monkeypatch.setattr(main_module, "_daemon", None)

        ftruncate_called = []
        close_called = []

        def fake_ftruncate(fd, size):
            ftruncate_called.append(fd)
            raise OSError("simulated disk error")

        def fake_close(fd):
            close_called.append(fd)

        def fake_execvp(*a, **kw):
            raise SystemExit(0)

        def fake_subprocess_run(cmd, **kwargs):
            m = MagicMock()
            m.stdout = ""
            m.returncode = 0
            return m

        monkeypatch.setattr(os, "ftruncate", fake_ftruncate)
        monkeypatch.setattr(os, "close", fake_close)
        monkeypatch.setattr(os, "execvp", fake_execvp)
        monkeypatch.setattr(main_module.subprocess, "run", fake_subprocess_run)
        monkeypatch.setattr(main_module.time, "sleep", lambda _: None)

        with pytest.raises(SystemExit):
            main_module._handle_restart(signal.SIGHUP, None)

        assert fake_fd in ftruncate_called, "ftruncate should have been attempted"
        assert fake_fd in close_called, "close must run even when ftruncate raises OSError"
        assert main_module._pid_lock_fd is None
