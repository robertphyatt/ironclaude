"""Tests for ironclaude CLI entry point."""
import os
import signal
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import ironclaude.cli as cli_module
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
        if proc.stdout:
            proc.stdout.close()


@pytest.mark.parametrize("raw", ["0", "-1", "+1", "1.5", "abc", "１２３"])
def test_stop_refuses_non_positive_or_non_ascii_decimal_pid(
    pid_file, raw, capsys
):
    pid_file.write_text(raw)
    with patch("os.kill") as mock_kill:
        rc = main(["stop"])
    assert rc == 1
    mock_kill.assert_not_called()
    assert "PID file corrupt" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("cmdline", "expected"),
    [
        ("/usr/bin/python3 -u -m ironclaude.main --no-respawn", True),
        ("/usr/bin/notpython -m ironclaude.main", False),
        ("/usr/bin/python3 -m ironclaude.main.evil", False),
        ("/usr/bin/python3 worker.py --label ironclaude.main", False),
        ("/usr/bin/python3 worker.py -m ironclaude.main", False),
        ("/usr/bin/python3 -c pass -m ironclaude.main", False),
        ("/usr/bin/python3 -m evil -m ironclaude.main", False),
    ],
)
def test_daemon_identity_uses_exact_executable_and_module_tokens(
    cmdline, expected
):
    with patch("subprocess.run", return_value=_fake_ps(cmdline)):
        assert cli_module._pid_is_daemon(4242) is expected


def test_stop_refuses_stale_or_reused_pid_before_signal(pid_file, capsys):
    pid_file.write_text("4242")
    with patch("ironclaude.cli._pid_is_daemon", return_value=False), \
            patch("os.kill") as mock_kill:
        rc = main(["stop"])
    assert rc == 1
    mock_kill.assert_not_called()
    assert "no longer belongs to ironclaude" in capsys.readouterr().out


def test_stop_revalidates_identity_immediately_before_signal(pid_file, capsys):
    pid_file.write_text("4242")
    with patch("ironclaude.cli._pid_is_daemon", side_effect=[True, False]), \
            patch("os.kill") as mock_kill:
        rc = main(["stop"])
    assert rc == 1
    mock_kill.assert_not_called()
    assert "changed identity before signal" in capsys.readouterr().out


def test_stop_permission_error_is_nonzero(pid_file, capsys):
    pid_file.write_text("4242")
    with patch("ironclaude.cli._pid_is_daemon", return_value=True), \
            patch("os.kill", side_effect=PermissionError) as mock_kill:
        rc = main(["stop"])
    assert rc == 1
    mock_kill.assert_called_once_with(4242, signal.SIGTERM)
    assert "No permission" in capsys.readouterr().out


def test_stop_success_requires_pid_to_stop_identifying_as_daemon(pid_file, capsys):
    pid_file.write_text("4242")
    with patch(
        "ironclaude.cli._pid_is_daemon", side_effect=[True, True, False]
    ), patch("os.kill") as mock_kill:
        rc = main(["stop"])
    assert rc == 0
    mock_kill.assert_called_once_with(4242, signal.SIGTERM)
    assert "stopped" in capsys.readouterr().out


def test_stop_timeout_is_nonzero_and_never_claims_success(pid_file, capsys):
    pid_file.write_text("4242")
    with patch("ironclaude.cli._pid_is_daemon", return_value=True), \
            patch("os.kill") as mock_kill:
        rc = cli_module._cmd_stop(timeout_seconds=0)
    assert rc == 1
    mock_kill.assert_called_once_with(4242, signal.SIGTERM)
    out = capsys.readouterr().out
    assert "still running" in out
    assert "stopped" not in out


def test_stop_checks_identity_once_more_at_deadline(pid_file, capsys):
    pid_file.write_text("4242")
    with patch(
        "ironclaude.cli._pid_is_daemon", side_effect=[True, True, False]
    ), patch("os.kill") as mock_kill:
        rc = cli_module._cmd_stop(timeout_seconds=0)
    assert rc == 0
    mock_kill.assert_called_once_with(4242, signal.SIGTERM)
    assert "stopped" in capsys.readouterr().out


def test_canonical_stop_allows_local_shutdown_beyond_five_seconds(
    pid_file, capsys
):
    pid_file.write_text("4242")
    with patch(
        "ironclaude.cli._pid_is_daemon",
        side_effect=[True, True, True, True, False],
    ), patch("ironclaude.cli.time.monotonic", side_effect=[0.0, 0.0, 6.0, 7.0]), \
            patch("ironclaude.cli.time.sleep"), patch("os.kill") as mock_kill:
        rc = main(["stop"])
    assert rc == 0
    mock_kill.assert_called_once_with(4242, signal.SIGTERM)
    assert "stopped" in capsys.readouterr().out


def test_canonical_stop_times_out_at_local_120_second_bound(
    pid_file, capsys
):
    pid_file.write_text("4242")
    with patch("ironclaude.cli._pid_is_daemon", return_value=True), \
            patch("ironclaude.cli.time.monotonic", side_effect=[0.0, 0.0, 120.0]), \
            patch("ironclaude.cli.time.sleep"), patch("os.kill") as mock_kill:
        rc = main(["stop"])
    assert rc == 1
    mock_kill.assert_called_once_with(4242, signal.SIGTERM)
    assert mock_kill.call_args_list == [((4242, signal.SIGTERM),)]
    out = capsys.readouterr().out
    assert "still running" in out
    assert "stopped" not in out


def test_stop_e2e_real_same_uid_sigterm(pid_file, monkeypatch, capsys):
    monkeypatch.setattr(os, "kill", _real_os_kill)
    script = (
        "import signal, sys, os, time\n"
        "signal.signal(signal.SIGTERM, lambda s, f: sys.exit(0))\n"
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

        def still_target(candidate):
            assert candidate == pid
            return proc.poll() is None

        with patch("ironclaude.cli._pid_is_daemon", side_effect=still_target):
            rc = main(["stop"])

        assert rc == 0
        assert proc.wait(timeout=2) == 0
        assert "stopped" in capsys.readouterr().out
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        if proc.stdout:
            proc.stdout.close()


def test_stop_real_siginfo_handler_suppresses_respawn(
    pid_file, tmp_path, monkeypatch, capsys
):
    """A real same-UID SIGTERM must reach the SA_SIGINFO clean-shutdown path."""
    monkeypatch.setattr(os, "kill", _real_os_kill)
    respawn_marker = tmp_path / "respawned"
    script = (
        "import os, sys, time\n"
        "from pathlib import Path\n"
        "import ironclaude.main as daemon_main\n"
        "daemon_main._daemon = None\n"
        "daemon_main._clean_shutdown = False\n"
        "daemon_main._sigterm_trusted = False\n"
        f"marker = Path({str(respawn_marker)!r})\n"
        "daemon_main._spawn_respawner = lambda: marker.write_text('respawned')\n"
        "daemon_main._install_sigaction_handler()\n"
        "print(os.getpid(), flush=True)\n"
        "deadline = time.monotonic() + 5\n"
        "while not daemon_main._clean_shutdown and time.monotonic() < deadline:\n"
        "    time.sleep(0.01)\n"
        "if not daemon_main._clean_shutdown:\n"
        "    daemon_main._spawn_respawner()\n"
        "print(f'clean={daemon_main._clean_shutdown}', flush=True)\n"
        "sys.exit(0 if daemon_main._clean_shutdown else 2)\n"
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", script],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        pid = int(proc.stdout.readline().strip())
        pid_file.write_text(str(pid))

        def still_target(candidate):
            assert candidate == pid
            return proc.poll() is None

        with patch("ironclaude.cli._pid_is_daemon", side_effect=still_target):
            rc = main(["stop"])

        stdout, stderr = proc.communicate(timeout=2)
        assert rc == 0
        assert proc.returncode == 0, stderr
        assert "clean=True" in stdout
        assert not respawn_marker.exists()
        assert "stopped" in capsys.readouterr().out
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        if proc.stdout:
            proc.stdout.close()
        if proc.stderr:
            proc.stderr.close()


def test_make_stop_delegates_without_pid_kill_or_sleep_logic():
    makefile = Path(__file__).resolve().parents[1] / "Makefile"
    text = makefile.read_text()
    stop_recipe = text.split("\nstop:\n", 1)[1].split("\nfollow-run:\n", 1)[0]
    assert "$(PYTHON) -m ironclaude.cli stop" in stop_recipe
    assert "kill" not in stop_recipe
    assert "sleep" not in stop_recipe
    assert "PID_FILE" not in stop_recipe
