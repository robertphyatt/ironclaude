"""Hermetic contract tests for _kill_orphan_workers selection behavior."""
from unittest.mock import MagicMock

from ironclaude.main import _kill_orphan_workers
from ironclaude.tmux_manager import TmuxManager
from ironclaude.worker_registry import WorkerRegistry


def _dependencies(sessions: list[str], registered: tuple[str, ...] = ()):
    tmux = MagicMock(spec=TmuxManager)
    tmux.list_sessions.return_value = sessions
    registry = MagicMock(spec=WorkerRegistry)
    registry.get_running_workers.return_value = [
        {"tmux_session": name} for name in registered
    ]
    return tmux, registry


class TestKillOrphanWorkers:
    def test_kills_unregistered_session(self):
        tmux, registry = _dependencies(["ic-test-orphan-unregistered"])

        _kill_orphan_workers(tmux, registry)

        tmux.list_sessions.assert_called_once_with(prefix="ic-")
        tmux.kill_session.assert_called_once_with("ic-test-orphan-unregistered")

    def test_preserves_registered_session(self):
        name = "ic-test-orphan-registered"
        tmux, registry = _dependencies([name], registered=(name,))

        _kill_orphan_workers(tmux, registry)

        tmux.kill_session.assert_not_called()

    def test_preserves_brain_session(self):
        tmux, registry = _dependencies(["ic-brain"])

        _kill_orphan_workers(tmux, registry)

        tmux.kill_session.assert_not_called()

    def test_requests_only_ic_prefix_sessions(self):
        tmux, registry = _dependencies([])

        _kill_orphan_workers(tmux, registry)

        tmux.list_sessions.assert_called_once_with(prefix="ic-")
        tmux.kill_session.assert_not_called()
