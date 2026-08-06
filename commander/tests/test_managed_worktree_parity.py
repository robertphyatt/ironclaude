"""Static parity constraints for the bounded live acceptance runner."""

from pathlib import Path

from ironclaude.workspace_client import WorkspaceClient


RUNNER = Path(__file__).parents[1] / "scripts/managed_worktree_live_acceptance.py"


def test_parity_runner_names_both_providers_and_commander_transport():
    source = RUNNER.read_text()
    assert "claude_provider" in source
    assert "codex_provider" in source
    assert "WorkspaceClient" in source
    assert "client.allocate" in source
    assert "client.bind" in source
    assert "client.finalize" in source


def test_parity_commander_transport_has_no_push_capability():
    assert not hasattr(WorkspaceClient, "push")
