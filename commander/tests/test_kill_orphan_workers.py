"""Integration tests for _kill_orphan_workers — real tmux sessions, no mocks."""
import subprocess
import pytest
from ironclaude.tmux_manager import TmuxManager
from ironclaude.worker_registry import WorkerRegistry
from ironclaude.db import init_db
from ironclaude.main import _kill_orphan_workers


def _has_session(name: str) -> bool:
    result = subprocess.run(["tmux", "has-session", "-t", name], capture_output=True)
    return result.returncode == 0


def _kill_session(name: str) -> None:
    subprocess.run(["tmux", "kill-session", "-t", name], capture_output=True)


@pytest.fixture
def real_tmux(tmp_path):
    return TmuxManager(log_dir=str(tmp_path))


@pytest.fixture
def real_registry(tmp_path):
    conn = init_db(str(tmp_path / "test.db"))
    return WorkerRegistry(conn)


@pytest.fixture
def protected_registry(real_tmux, real_registry):
    """Pre-register all existing ic-* sessions so they aren't killed as orphans."""
    for name in real_tmux.list_sessions(prefix="ic-"):
        real_registry.register_worker(name, "worker", name)
    return real_registry


class TestKillOrphanWorkers:
    def test_kills_unregistered_session(self, real_tmux, protected_registry):
        name = "ic-test-orphan-unregistered"
        subprocess.run(["tmux", "new-session", "-d", "-s", name], check=True)
        try:
            _kill_orphan_workers(real_tmux, protected_registry)
            assert not _has_session(name), f"Orphan session {name!r} should have been killed"
        finally:
            _kill_session(name)

    def test_preserves_registered_session(self, real_tmux, protected_registry):
        name = "ic-test-orphan-registered"
        subprocess.run(["tmux", "new-session", "-d", "-s", name], check=True)
        protected_registry.register_worker(name, "worker", name)
        try:
            _kill_orphan_workers(real_tmux, protected_registry)
            assert _has_session(name), f"Registered session {name!r} should not have been killed"
        finally:
            _kill_session(name)

    def test_preserves_brain_session(self, real_tmux, protected_registry):
        name = "ic-brain"
        created_here = not _has_session(name)
        if created_here:
            subprocess.run(["tmux", "new-session", "-d", "-s", name], check=True)
        try:
            _kill_orphan_workers(real_tmux, protected_registry)
            assert _has_session(name), "ic-brain session must never be killed by orphan cleanup"
        finally:
            if created_here:
                _kill_session(name)

    def test_kills_only_ic_prefix_sessions(self, real_tmux, protected_registry):
        orphan = "ic-test-orphan-killme"
        other = "not-ic-test-shouldlive"
        subprocess.run(["tmux", "new-session", "-d", "-s", orphan], check=True)
        subprocess.run(["tmux", "new-session", "-d", "-s", other], check=True)
        try:
            _kill_orphan_workers(real_tmux, protected_registry)
            assert not _has_session(orphan), f"ic- orphan {orphan!r} should have been killed"
            assert _has_session(other), f"Non-ic- session {other!r} should not have been killed"
        finally:
            _kill_session(orphan)
            _kill_session(other)
