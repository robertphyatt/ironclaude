# tests/test_brain_monitor.py
import time
import pytest
from unittest.mock import MagicMock, patch
from ironclaude.brain_monitor import BrainMonitor


@pytest.fixture
def monitor():
    tmux = MagicMock()
    tmux.get_log_mtime.return_value = None
    return BrainMonitor(
        tmux_manager=tmux,
        timeout_seconds=5,
        grace_period_seconds=30,
    )


class TestBrainMonitor:
    def test_is_alive_no_log_file(self, monitor):
        """No log file means brain is not alive."""
        monitor._tmux.get_log_mtime.return_value = None
        assert monitor.is_alive() is False

    def test_is_alive_fresh_log(self, monitor):
        """Recently modified log means brain is alive."""
        monitor._tmux.get_log_mtime.return_value = time.time()
        assert monitor.is_alive() is True

    def test_is_alive_stale_log(self, monitor):
        """Log modified long ago means brain is not alive."""
        monitor._tmux.get_log_mtime.return_value = time.time() - 60
        assert monitor.is_alive() is False

    def test_needs_restart_no_session(self, monitor):
        """No tmux session means restart needed."""
        monitor._tmux.has_session.return_value = False
        assert monitor.needs_restart() is True

    def test_no_restart_when_alive(self, monitor):
        """Session exists and log is fresh means no restart."""
        monitor._tmux.has_session.return_value = True
        monitor._tmux.get_log_mtime.return_value = time.time()
        assert monitor.needs_restart() is False

    def test_restart_increments_count(self, monitor):
        """Restart increments counter and records timestamp."""
        monitor._tmux.has_session.return_value = False
        monitor._tmux.spawn_session.return_value = True
        assert monitor.restart_count == 0
        monitor.restart("claude --dangerously-skip-permissions")
        assert monitor.restart_count == 1
        assert monitor._last_restart_time > 0
        monitor._tmux.spawn_session.assert_called_once()

    def test_grace_period_prevents_restart(self, monitor):
        """Within grace period after restart, stale log does not trigger restart."""
        monitor._tmux.has_session.return_value = True
        monitor._tmux.get_log_mtime.return_value = None  # no log yet
        # Simulate recent restart
        monitor._last_restart_time = time.time()
        assert monitor.needs_restart() is False
