"""Strict Commander adapter for workspace-manager's internal CLI."""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from ironclaude.workspace_client import WorkspaceClient, WorkspaceClientError


def completed(stdout='{"ok":true}\n', stderr="", returncode=0):
    return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=returncode)


def test_local_commands_use_argv_and_strict_json(tmp_path: Path):
    runner = Mock(return_value=completed('{"workspace_guid":"w"}\n'))
    client = WorkspaceClient(tmp_path, runner=runner)
    payload = {"repository_path": "/tmp/repo;$(touch nope)", "owner_session_id": "owner"}

    assert client.allocate(payload) == {"workspace_guid": "w"}
    argv = runner.call_args.args[0]
    assert argv[:2] == ["node", str(tmp_path / "mcp-servers/workspace-manager/dist/cli.js")]
    assert argv[2] == "allocate"
    assert json.loads(argv[3]) == payload
    assert runner.call_args.kwargs["shell"] is False
    assert runner.call_args.kwargs["check"] is False


def test_remote_commands_delegate_an_argv_vector_to_ssh_manager(tmp_path: Path):
    ssh = Mock()
    ssh.run_argv.return_value = completed('{"workspace_guid":"w"}\n')
    client = WorkspaceClient(tmp_path, runner=Mock(), ssh_manager=ssh)
    payload = {"workspace_guid": "w", "owner_session_id": "owner; echo forged"}

    assert client.bind(payload, ssh_host="worker-host", remote_plugin_root="/opt/iron claude") == {
        "workspace_guid": "w"
    }
    host, argv = ssh.run_argv.call_args.args
    assert host == "worker-host"
    assert argv[:3] == ["node", "/opt/iron claude/mcp-servers/workspace-manager/dist/cli.js", "bind"]
    assert json.loads(argv[3]) == payload


@pytest.mark.parametrize("stdout", ["", "[]\n", "null\n", "{bad}\n", '{"ok":true}\nextra\n'])
def test_rejects_non_object_or_non_exact_json(stdout: str, tmp_path: Path):
    client = WorkspaceClient(tmp_path, runner=Mock(return_value=completed(stdout)))
    with pytest.raises(WorkspaceClientError, match="JSON"):
        client.allocate({})


def test_surfaces_nonzero_and_stderr_without_parsing(tmp_path: Path):
    client = WorkspaceClient(
        tmp_path,
        runner=Mock(return_value=completed('{"forged":true}\n', "denied", 7)),
    )
    with pytest.raises(WorkspaceClientError, match="exit 7"):
        client.allocate({})


def test_exposes_only_no_push_lifecycle_methods(tmp_path: Path):
    client = WorkspaceClient(tmp_path, runner=Mock(return_value=completed()))
    assert {name for name in ("allocate", "bind", "finalize", "abandon", "reconcile") if hasattr(client, name)} == {
        "allocate", "bind", "finalize", "abandon", "reconcile"
    }
    assert not hasattr(client, "push")
    with pytest.raises(WorkspaceClientError, match="not allowed"):
        client._invoke("push", {})


def test_each_public_method_uses_its_exact_internal_command(tmp_path: Path):
    runner = Mock(return_value=completed())
    client = WorkspaceClient(tmp_path, runner=runner)
    for name in ("allocate", "bind", "finalize", "abandon", "reconcile"):
        getattr(client, name)({"marker": name})
        assert runner.call_args.args[0][2] == name
        assert json.loads(runner.call_args.args[0][3]) == {"marker": name}


def test_worktree_authority_workspace_client_calls_private_finalize_outside_ai_hook(
    tmp_path: Path,
):
    runner = Mock(return_value=completed('{"state":"cleaned"}\n'))
    client = WorkspaceClient(tmp_path / "source", runner=runner)
    plugin_root = tmp_path / "installed"
    payload = {"command": {
        "repositoryPath": "/repo",
        "workspaceGuid": "22222222-2222-4222-8222-222222222222",
        "providerRootSessionId": "11111111-1111-4111-8111-111111111111",
        "message": "reviewed",
        "canonicalBranch": "ironclaude/2222",
        "localRef": "refs/heads/ironclaude/2222",
        "stagedTree": "b" * 40,
        "parentOid": "c" * 40,
    }}

    assert client.finalize(payload, plugin_root=plugin_root) == {"state": "cleaned"}
    argv = runner.call_args.args[0]
    assert argv[:3] == [
        "node",
        str(plugin_root / "mcp-servers/workspace-manager/dist/cli.js"),
        "finalize",
    ]
    assert json.loads(argv[3]) == payload
    assert runner.call_args.kwargs["shell"] is False


@pytest.mark.parametrize(
    ("client_name", "cache_dir", "manifest_dir"),
    [
        ("claude", ".claude", ".claude-plugin"),
        ("codex", ".codex", ".codex-plugin"),
    ],
)
def test_worktree_discovers_exact_local_provider_runtime(
    tmp_path: Path, client_name: str, cache_dir: str, manifest_dir: str,
):
    root = tmp_path / cache_dir / "plugins/cache/ironclaude/ironclaude/1.1.3"
    (root / manifest_dir).mkdir(parents=True)
    (root / manifest_dir / "plugin.json").write_text('{"version":"1.1.3"}')
    cli = root / "mcp-servers/workspace-manager/dist/cli.js"
    cli.parent.mkdir(parents=True)
    cli.write_text("// bundle")
    client = WorkspaceClient(tmp_path / "unused", commander_version="1.1.3", home_dir=tmp_path)

    assert client.discover_installed_plugin_root(client_name) == str(root)


def test_worktree_local_runtime_discovery_rejects_zero_or_ambiguous_matches_with_evidence(tmp_path: Path):
    cache = tmp_path / ".codex/plugins/cache/ironclaude/ironclaude"
    stale = cache / "1.1.2"
    (stale / ".codex-plugin").mkdir(parents=True)
    (stale / ".codex-plugin/plugin.json").write_text('{"version":"1.1.2"}')
    stale_cli = stale / "mcp-servers/workspace-manager/dist/cli.js"
    stale_cli.parent.mkdir(parents=True)
    stale_cli.write_text("// stale")
    client = WorkspaceClient(tmp_path / "unused", commander_version="1.1.3", home_dir=tmp_path)

    with pytest.raises(WorkspaceClientError) as zero:
        client.discover_installed_plugin_root("codex")
    assert str(stale) in str(zero.value)
    assert "1.1.2" in str(zero.value)

    for suffix in ("a", "b"):
        root = cache / f"1.1.3+{suffix}"
        (root / ".codex-plugin").mkdir(parents=True)
        (root / ".codex-plugin/plugin.json").write_text(
            json.dumps({"version": f"1.1.3+codex.{suffix}"}),
        )
        cli = root / "mcp-servers/workspace-manager/dist/cli.js"
        cli.parent.mkdir(parents=True)
        cli.write_text("// bundle")

    with pytest.raises(WorkspaceClientError) as ambiguous:
        client.discover_installed_plugin_root("codex")
    assert "ambiguous" in str(ambiguous.value)
    assert "1.1.3+a" in str(ambiguous.value)
    assert "1.1.3+b" in str(ambiguous.value)


@pytest.mark.parametrize(
    ("client_name", "cache_dir", "manifest_dir"),
    [
        ("claude", ".claude", ".claude-plugin"),
        ("codex", ".codex", ".codex-plugin"),
    ],
)
def test_worktree_discovers_exact_remote_provider_runtime_over_existing_ssh_transport(
    tmp_path: Path, client_name: str, cache_dir: str, manifest_dir: str,
):
    root = f"/home/worker/{cache_dir}/plugins/cache/ironclaude/ironclaude/1.1.3"
    ssh = Mock()
    ssh.run_argv.side_effect = [
        completed("/home/worker\n"),
        completed(f"{root}\n"),
        completed('{"version":"1.1.3"}\n'),
        completed(""),
    ]
    client = WorkspaceClient(
        tmp_path / "unused", ssh_manager=ssh, commander_version="1.1.3", home_dir=tmp_path,
    )

    assert client.discover_installed_plugin_root(client_name, ssh_host="worker-host") == root
    assert ssh.run_argv.call_args_list[0].args == (
        "worker-host", ["python3", "-c", "import os; print(os.path.expanduser('~'))"],
    )
    assert ssh.run_argv.call_args_list[1].args[1][0] == "find"
    assert ssh.run_argv.call_args_list[2].args == (
        "worker-host", ["cat", f"{root}/{manifest_dir}/plugin.json"],
    )
    assert ssh.run_argv.call_args_list[3].args == (
        "worker-host", ["test", "-f", f"{root}/mcp-servers/workspace-manager/dist/cli.js"],
    )
