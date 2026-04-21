# tests/test_signal_forensics.py
"""Tests for _logged_kill forensic wrapper."""

import os
import signal
from unittest.mock import patch, MagicMock
import pytest

from ironclaude.signal_forensics import _logged_kill


class TestLoggedKill:
    def test_calls_through_to_os_kill(self):
        """_logged_kill delegates the actual kill to os.kill with exact args."""
        with patch("ironclaude.signal_forensics.os.kill") as mock_kill, \
             patch("ironclaude.signal_forensics.subprocess.run") as mock_ps:
            mock_ps.return_value = MagicMock(stdout="1234  1  1  python", returncode=0)
            _logged_kill(1234, signal.SIGTERM, "test reason")
        mock_kill.assert_called_once_with(1234, signal.SIGTERM)

    def test_logs_reason_pid_and_signal_name(self):
        """Log message contains the reason, target pid, and signal name."""
        warnings = []
        with patch("ironclaude.signal_forensics.os.kill"), \
             patch("ironclaude.signal_forensics.subprocess.run") as mock_ps, \
             patch("ironclaude.signal_forensics.logger.warning",
                   side_effect=lambda m, *a, **k: warnings.append(m)):
            mock_ps.return_value = MagicMock(stdout="1234  1  1  python", returncode=0)
            _logged_kill(1234, signal.SIGTERM, "test reason")
        combined = " ".join(warnings)
        assert "test reason" in combined, f"reason not in log: {warnings}"
        assert "1234" in combined, f"target pid not in log: {warnings}"
        assert "SIGTERM" in combined, f"signal name not in log: {warnings}"

    def test_propagates_process_lookup_error(self):
        """ProcessLookupError from os.kill propagates unchanged."""
        with patch("ironclaude.signal_forensics.os.kill",
                   side_effect=ProcessLookupError("no such process")), \
             patch("ironclaude.signal_forensics.subprocess.run"):
            with pytest.raises(ProcessLookupError):
                _logged_kill(9999, signal.SIGTERM, "should raise")

    def test_ps_failure_does_not_block_kill(self):
        """ps subprocess failure is non-fatal — os.kill still proceeds."""
        with patch("ironclaude.signal_forensics.os.kill") as mock_kill, \
             patch("ironclaude.signal_forensics.subprocess.run",
                   side_effect=Exception("ps timed out")):
            _logged_kill(1234, signal.SIGTERM, "ps will fail")
        mock_kill.assert_called_once_with(1234, signal.SIGTERM)
