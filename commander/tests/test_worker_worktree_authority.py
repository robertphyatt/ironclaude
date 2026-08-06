"""Commander-only reviewed worker finalization authority tests."""

from __future__ import annotations

import inspect
import sqlite3
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from ironclaude.orchestrator_mcp import OrchestratorTools
from ironclaude.workspace_client import WorkspaceClient


OWNER = "11111111-1111-4111-8111-111111111111"


def _worker(**overrides):
    worker = {
        "id": "worker-1",
        "client": "codex",
        "machine": None,
        "repo": "/repo",
        "native_session_id": OWNER,
        "workspace_guid": "22222222-2222-4222-8222-222222222222",
        "workspace_repository_identity": "machine:repo.git",
        "workspace_path": "/repo/.ironclaude/worktrees/2222",
        "workspace_branch": "ironclaude/2222",
        "workspace_base_commit": "a" * 40,
        "workspace_integration_target": "main",
    }
    worker.update(overrides)
    return worker


def _review_state(**overrides):
    state = {
        "workflow_stage": "execution_complete",
        "unfinished_tasks": 0,
        "latest_task_boundary_grade": "A",
    }
    state.update(overrides)
    return state


def _evidence(**overrides):
    evidence = {
        "canonicalBranch": "ironclaude/2222",
        "localRef": "refs/heads/ironclaude/2222",
        "stagedTree": "b" * 40,
        "parentOid": "c" * 40,
    }
    evidence.update(overrides)
    return evidence


@pytest.fixture
def authority_tools():
    tools = object.__new__(OrchestratorTools)
    tools.registry = MagicMock()
    tools.registry.get_worker.return_value = _worker()
    tools.registry.update_worker_status = MagicMock()
    tools._workspace_client = MagicMock()
    tools._workspace_client.discover_installed_plugin_root.return_value = "/installed/codex"
    tools._workspace_client.finalize.return_value = {
        "state": "cleaned", "integratedCommit": "d" * 40,
    }
    tools._read_worker_finalization_state = MagicMock(return_value=_review_state())
    tools._derive_workspace_commit_evidence = MagicMock(return_value=_evidence())
    tools._ensure_ssh_manager = MagicMock()
    tools._resolve_ssh_host = MagicMock(return_value=None)
    return tools


def test_worktree_authority_commit_worker_derives_all_authority(authority_tools):
    result = authority_tools.commit_worker("worker-1", "reviewed worker commit")

    assert result["state"] == "cleaned"
    payload = authority_tools._workspace_client.finalize.call_args.args[0]
    assert payload == {"command": {
        "repositoryPath": "/repo",
        "workspaceGuid": "22222222-2222-4222-8222-222222222222",
        "providerRootSessionId": OWNER,
        "message": "reviewed worker commit",
        **_evidence(),
    }}
    authority_tools._workspace_client.finalize.assert_called_once_with(
        payload, plugin_root="/installed/codex",
    )
    authority_tools.registry.update_worker_status.assert_called_once_with(
        "worker-1", "completed",
    )


def test_worktree_authority_public_signature_accepts_no_forged_authority_fields():
    parameters = set(inspect.signature(OrchestratorTools.commit_worker).parameters)
    assert parameters == {"self", "worker_id", "message"}
    assert not hasattr(OrchestratorTools, "integrate_worker")
    assert not hasattr(WorkspaceClient, "push")


@pytest.mark.parametrize(
    "state",
    [
        _review_state(workflow_stage="executing"),
        _review_state(unfinished_tasks=1),
        _review_state(latest_task_boundary_grade="C"),
        _review_state(latest_task_boundary_grade=None),
    ],
)
def test_worktree_authority_rejects_unfinished_or_unreviewed_state(
    authority_tools, state,
):
    authority_tools._read_worker_finalization_state.return_value = state

    result = authority_tools.commit_worker("worker-1", "reviewed worker commit")

    assert result["failure_phase"] == "authority"
    assert result["assignment_preserved"] is True
    authority_tools._workspace_client.finalize.assert_not_called()


def test_worktree_authority_ssh_uses_remote_state_git_and_private_transport(
    authority_tools,
):
    authority_tools.registry.get_worker.return_value = _worker(
        machine="worker-host", repo="/srv/repo", workspace_path="/srv/worktree",
    )
    authority_tools._resolve_ssh_host.return_value = "worker.example"
    authority_tools._workspace_client.discover_installed_plugin_root.return_value = (
        "/home/worker/.codex/plugins/cache/ironclaude/ironclaude/1.1.4"
    )

    authority_tools.commit_worker("worker-1", "remote reviewed commit")

    authority_tools._read_worker_finalization_state.assert_called_once_with(
        OWNER, ssh_host="worker.example",
    )
    authority_tools._derive_workspace_commit_evidence.assert_called_once_with(
        "/srv/worktree", "ironclaude/2222", ssh_host="worker.example",
    )
    assert authority_tools._workspace_client.finalize.call_args.kwargs == {
        "ssh_host": "worker.example",
        "remote_plugin_root": (
            "/home/worker/.codex/plugins/cache/ironclaude/ironclaude/1.1.4"
        ),
    }


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=cwd, text=True, capture_output=True, check=True,
    )
    return result.stdout.strip()


def test_worktree_authority_live_git_evidence_is_exact(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "work.txt").write_text("base\n")
    _git(repo, "add", "work.txt")
    _git(repo, "commit", "-m", "base")
    _git(repo, "switch", "-c", "ironclaude/test")
    (repo / "work.txt").write_text("reviewed\n")
    _git(repo, "add", "work.txt")

    tools = object.__new__(OrchestratorTools)
    evidence = tools._derive_workspace_commit_evidence(
        str(repo), "ironclaude/test", ssh_host=None,
    )

    assert evidence == {
        "canonicalBranch": "ironclaude/test",
        "localRef": "refs/heads/ironclaude/test",
        "stagedTree": _git(repo, "write-tree"),
        "parentOid": _git(repo, "rev-parse", "--verify", "HEAD^{commit}"),
    }


def test_worktree_authority_reads_persisted_completion_and_latest_review(tmp_path):
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    connection = sqlite3.connect(claude_dir / "ironclaude.db")
    connection.executescript("""
        CREATE TABLE sessions (
            terminal_session TEXT PRIMARY KEY,
            workflow_stage TEXT NOT NULL
        );
        CREATE TABLE wave_tasks (
            terminal_session TEXT NOT NULL,
            status TEXT NOT NULL
        );
        CREATE TABLE review_grades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            terminal_session TEXT NOT NULL,
            grade TEXT NOT NULL,
            task_boundary INTEGER NOT NULL
        );
    """)
    connection.execute(
        "INSERT INTO sessions VALUES (?, 'execution_complete')", (OWNER,),
    )
    connection.execute(
        "INSERT INTO wave_tasks VALUES (?, 'review_passed')", (OWNER,),
    )
    connection.execute(
        "INSERT INTO review_grades (terminal_session, grade, task_boundary) "
        "VALUES (?, 'B', 1)",
        (OWNER,),
    )
    connection.commit()
    connection.close()

    tools = object.__new__(OrchestratorTools)
    state = tools._read_worker_finalization_state(
        OWNER, _claude_dir=claude_dir,
    )

    assert state == {
        "workflow_stage": "execution_complete",
        "unfinished_tasks": 0,
        "latest_task_boundary_grade": "B",
    }
