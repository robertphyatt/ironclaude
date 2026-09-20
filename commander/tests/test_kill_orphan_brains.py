"""Tests for _kill_orphan_brains: must kill the RECORDED brain PID only.

pgrep output must never drive a kill selection (operator directive: never
pattern-kill; scope to recorded PIDs). pgrep is retained solely for
log-only residue diagnostics after the targeted kill.
"""

from unittest.mock import MagicMock, patch

import ironclaude.main as main_module


def _make_pgrep_result(stdout: str) -> MagicMock:
    result = MagicMock()
    result.stdout = stdout
    result.returncode = 0
    return result


def test_kills_recorded_pid_not_pgrep_output():
    """pgrep returns two fake pids; only the recorded pid must be killed."""
    recorded_pids = []

    def fake_logged_kill(pid, sig, reason):
        recorded_pids.append(pid)

    with patch.object(main_module, "_logged_kill", side_effect=fake_logged_kill) as mock_kill, \
         patch.object(main_module.subprocess, "run", return_value=_make_pgrep_result("111\n222\n")), \
         patch.object(main_module.time, "sleep", return_value=None):
        main_module._kill_orphan_brains(recorded_brain_pid=98765)

    assert 98765 in recorded_pids, f"expected recorded pid 98765 to be killed, got {recorded_pids}"
    assert 111 not in recorded_pids, "pgrep-discovered pid 111 must never be passed to _logged_kill"
    assert 222 not in recorded_pids, "pgrep-discovered pid 222 must never be passed to _logged_kill"
    mock_kill.assert_called_once()


def test_no_recorded_pid_means_no_kill():
    """With no recorded pid, nothing is killed even though pgrep finds candidates."""
    with patch.object(main_module, "_logged_kill") as mock_kill, \
         patch.object(main_module.subprocess, "run", return_value=_make_pgrep_result("111\n222\n")), \
         patch.object(main_module.time, "sleep", return_value=None):
        main_module._kill_orphan_brains(recorded_brain_pid=None)

    mock_kill.assert_not_called()
