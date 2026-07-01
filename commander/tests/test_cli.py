"""Tests for ironclaude CLI entry point."""
import os
import signal
import subprocess
import sys
from unittest.mock import MagicMock, patch

import pytest

from ironclaude.cli import main


def _fake_ps(cmdline, returncode=0):
    """Build a fake subprocess.run result for the `ps` identity check."""
    return MagicMock(returncode=returncode, stdout=cmdline)


# A command line that identifies the process as the ironclaude daemon.
_DAEMON_CMDLINE = "/usr/bin/python3 -m ironclaude.main --no-respawn"

# Captured at import time — before _guard_os_kill autouse patches os.kill.
# Integration tests that need real signal delivery restore this via monkeypatch.
_real_os_kill = os.kill


@pytest.fixture
def pid_file(tmp_path, monkeypatch):
    """Temp PID file path; patches _PID_FILE in cli module."""
    f = tmp_path / "ic-daemon.pid"
    monkeypatch.setattr("ironclaude.cli._PID_FILE", str(f))
    return f


def test_restart_sends_sighup(pid_file, capsys):
    pid_file.write_text("12345")
    with patch("subprocess.run", return_value=_fake_ps(_DAEMON_CMDLINE)), \
            patch("os.kill") as mock_kill:
        rc = main(["restart"])
    mock_kill.assert_called_once_with(12345, signal.SIGHUP)
    assert rc == 0
    assert "12345" in capsys.readouterr().out


def test_restart_no_pid_file(pid_file, capsys):
    # pid_file fixture patches _PID_FILE but does NOT write the file
    rc = main(["restart"])
    assert rc == 1
    assert "Daemon not running" in capsys.readouterr().out


def test_restart_stale_pid(pid_file, capsys):
    # Identity check passes, but the process exits between the check and the
    # signal, so os.kill raises ProcessLookupError.
    pid_file.write_text("99999")
    with patch("subprocess.run", return_value=_fake_ps(_DAEMON_CMDLINE)), \
            patch("os.kill", side_effect=ProcessLookupError):
        rc = main(["restart"])
    assert rc == 1
    assert "Daemon PID stale" in capsys.readouterr().out


def test_restart_refuses_when_identity_check_fails(pid_file, capsys):
    # PID file points at a live PID, but `ps` shows a non-daemon command line
    # (OS reused the PID after the daemon crashed). Restart must NOT signal it.
    pid_file.write_text("4242")
    with patch("subprocess.run", return_value=_fake_ps("/usr/sbin/sshd -D")), \
            patch("os.kill") as mock_kill:
        rc = main(["restart"])
    mock_kill.assert_not_called()
    assert rc == 1
    out = capsys.readouterr().out
    assert "4242" in out
    assert "no longer belongs to ironclaude" in out


def test_restart_permission_error_is_caught(pid_file, capsys):
    # Identity check passes, but os.kill is denied — must return non-zero
    # cleanly without raising a traceback.
    pid_file.write_text("4243")
    with patch("subprocess.run", return_value=_fake_ps(_DAEMON_CMDLINE)), \
            patch("os.kill", side_effect=PermissionError) as mock_kill:
        rc = main(["restart"])
    mock_kill.assert_called_once_with(4243, signal.SIGHUP)
    assert rc == 1
    assert "4243" in capsys.readouterr().out


def test_restart_e2e_real_sighup(pid_file, monkeypatch, capsys):
    # Override the autouse guard — this test sends a real signal to its own subprocess.
    monkeypatch.setattr(os, "kill", _real_os_kill)

    script = (
        "import signal, sys, os, time\n"
        "signal.signal(signal.SIGHUP, lambda s, f: sys.exit(0))\n"
        "print(os.getpid(), flush=True)\n"
        "time.sleep(5)\n"
        "sys.exit(1)\n"
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", script],
        stdout=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        pid = int(proc.stdout.readline().strip())
        pid_file.write_text(str(pid))

        # The helper subprocess runs `python -c ...`, not `ironclaude.main`, so
        # force the identity check to pass while keeping the REAL os.kill (this
        # test verifies real SIGHUP delivery).
        with patch("ironclaude.cli._pid_is_daemon", return_value=True):
            rc = main(["restart"])

        assert rc == 0
        assert str(pid) in capsys.readouterr().out
        exit_code = proc.wait(timeout=2)
        assert exit_code == 0, f"Subprocess did not receive SIGHUP (exit_code={exit_code})"
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
