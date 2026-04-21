# src/ironclaude/signal_forensics.py
"""Forensic kill wrapper: logs full context before every os.kill() call."""
from __future__ import annotations

import logging
import os
import signal
import subprocess
import traceback

logger = logging.getLogger("ironclaude")


def _logged_kill(pid: int, sig: int, reason: str) -> None:
    """Send a signal with full forensic logging.

    Logs our PID, target PID, signal name, reason, 3-frame caller stack, and
    target process info from ps.  Then calls os.kill(pid, sig).

    Raises whatever os.kill() raises (ProcessLookupError, PermissionError, …).
    Does NOT wrap os.kill(pid, 0) probe calls — those are existence checks.
    All logging goes to the 'ironclaude' logger (→ /tmp/ironclaude-daemon.log).
    """
    try:
        sig_name = signal.Signals(sig).name
    except (ValueError, AttributeError):
        sig_name = f"signal({sig})"

    # Caller context: last 3 frames excluding this function itself
    try:
        caller = "".join(traceback.format_stack()[:-1][-3:]).strip()
    except Exception:
        caller = "<traceback unavailable>"

    # Target process identity via ps
    try:
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "pid=,ppid=,pgid=,comm="],
            capture_output=True, text=True, timeout=2,
        )
        target_info = result.stdout.strip() or "<not found>"
    except Exception:
        target_info = "<ps failed>"

    logger.warning(
        f"_logged_kill: our_pid={os.getpid()} -> target_pid={pid} "
        f"sig={sig_name}({sig}) reason={reason!r}  "
        f"target_ps=[{target_info}]  "
        f"caller:\n{caller}"
    )

    os.kill(pid, sig)
