"""ironclaude CLI entry point."""
import argparse
import os
import re
import shlex
import signal
import subprocess
import sys
import time

_PID_FILE = "/tmp/ic-daemon.pid"

_PYTHON_EXECUTABLE_RE = re.compile(r"python(?:[0-9]+(?:\.[0-9]+)*)?")
_POSITIVE_ASCII_DECIMAL_RE = re.compile(r"[0-9]+")
_PYTHON_FLAGS_WITHOUT_VALUES = frozenset(
    {"-B", "-E", "-I", "-O", "-OO", "-P", "-q", "-s", "-S", "-u", "-v", "-V", "-x"}
)
# Local shutdown envelope: workspace command (60s), Codex cleanup (20s),
# Socket Mode join (5s), final Slack request (30s), and scheduling margin (5s).
# Remote-machine teardown is not covered by this bound.
_STOP_TIMEOUT_SECONDS = 120.0


def _process_cmdline(pid):
    """Return the command line of process `pid` via `ps`, or None if unavailable.

    Cross-platform (macOS has no /proc): uses `ps -o command= -p <pid>`.
    """
    try:
        result = subprocess.run(
            ["ps", "-o", "command=", "-p", str(pid)],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def _pid_is_daemon(pid):
    """True if `pid`'s command line identifies it as the ironclaude daemon.

    Guards against signaling an unrelated process when the OS has reused a PID
    after the daemon crashed.
    """
    cmdline = _process_cmdline(pid)
    if not cmdline:
        return False
    try:
        tokens = shlex.split(cmdline)
    except ValueError:
        return False
    if not tokens or _PYTHON_EXECUTABLE_RE.fullmatch(os.path.basename(tokens[0])) is None:
        return False
    index = 1
    while index < len(tokens):
        token = tokens[index]
        if token == "-m":
            return index + 1 < len(tokens) and tokens[index + 1] == "ironclaude.main"
        if token in ("-c", "--") or not token.startswith("-"):
            return False
        if token in _PYTHON_FLAGS_WITHOUT_VALUES:
            index += 1
            continue
        if token in ("-W", "-X"):
            if index + 1 >= len(tokens):
                return False
            index += 2
            continue
        if (token.startswith("-W") or token.startswith("-X")) and len(token) > 2:
            index += 1
            continue
        return False
    return False


def _read_positive_pid():
    try:
        with open(_PID_FILE) as f:
            raw = f.read().strip()
    except FileNotFoundError:
        return None, "missing"
    if _POSITIVE_ASCII_DECIMAL_RE.fullmatch(raw) is None:
        return None, "corrupt"
    pid = int(raw)
    if pid <= 0:
        return None, "corrupt"
    return pid, None


def _cmd_restart():
    try:
        with open(_PID_FILE) as f:
            raw = f.read().strip()
    except FileNotFoundError:
        print("Daemon not running")
        return 1
    try:
        pid = int(raw)
    except ValueError:
        print("Daemon PID file corrupt")
        return 1
    if not _pid_is_daemon(pid):
        print(f"Daemon PID {pid} no longer belongs to ironclaude")
        return 1
    try:
        os.kill(pid, signal.SIGHUP)
    except ProcessLookupError:
        print("Daemon PID stale")
        return 1
    except PermissionError:
        print(f"No permission to signal daemon PID {pid}")
        return 1
    print(f"Restart signal sent to daemon PID {pid}")
    return 0


def _cmd_stop(timeout_seconds=_STOP_TIMEOUT_SECONDS):
    pid, error = _read_positive_pid()
    if error == "missing":
        print("Daemon not running")
        return 0
    if error == "corrupt":
        print("Daemon PID file corrupt")
        return 1
    if not _pid_is_daemon(pid):
        print(f"Daemon PID {pid} no longer belongs to ironclaude")
        return 1
    if not _pid_is_daemon(pid):
        print(f"Daemon PID {pid} changed identity before signal")
        return 1
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        print("Daemon PID stale")
        return 1
    except PermissionError:
        print(f"No permission to signal daemon PID {pid}")
        return 1

    deadline = time.monotonic() + max(0.0, timeout_seconds)
    while time.monotonic() < deadline:
        if not _pid_is_daemon(pid):
            print(f"Daemon (PID {pid}) stopped")
            return 0
        time.sleep(0.05)
    if not _pid_is_daemon(pid):
        print(f"Daemon (PID {pid}) stopped")
        return 0
    print(f"Daemon (PID {pid}) still running after SIGTERM")
    return 1


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="ironclaude", description="IronClaude daemon control"
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("restart", help="Restart the daemon (sends SIGHUP)")
    sub.add_parser("stop", help="Stop the daemon cleanly (sends SIGTERM)")
    args = parser.parse_args(argv)
    if args.cmd == "restart":
        return _cmd_restart()
    if args.cmd == "stop":
        return _cmd_stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
