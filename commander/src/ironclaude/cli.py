"""ironclaude CLI entry point."""
import argparse
import os
import signal
import subprocess
import sys

_PID_FILE = "/tmp/ic-daemon.pid"

# Marker present in the daemon's command line (launched as `python -m ironclaude.main`,
# matching the existing `pgrep -f ironclaude.main` convention in main.py).
_DAEMON_MARKER = "ironclaude.main"


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
    return bool(cmdline) and _DAEMON_MARKER in cmdline


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


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="ironclaude", description="IronClaude daemon control"
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("restart", help="Restart the daemon (sends SIGHUP)")
    args = parser.parse_args(argv)
    if args.cmd == "restart":
        return _cmd_restart()
    return 0


if __name__ == "__main__":
    sys.exit(main())
