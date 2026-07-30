"""Tests for SSHConnectionManager."""

import os
import subprocess
from unittest.mock import MagicMock, patch, call

import pytest

from ironclaude.ssh_manager import SSHConnectionManager, MachineConfig, HealthResult


@pytest.fixture
def ssh_mgr(tmp_path):
    mgr = SSHConnectionManager(socket_dir=str(tmp_path / "sockets"))
    return mgr


class TestRegisterMachines:
    def test_register_single_machine(self, ssh_mgr):
        machines = [{"name": "remote-worker", "host": "remote-worker", "purpose": "trading",
                     "claude_path": "~/.claude/local/claude", "repos": ["/home/r/Code/bot"]}]
        ssh_mgr.register_machines(machines)
        m = ssh_mgr.get_machine("remote-worker")
        assert m is not None
        assert m.host == "remote-worker"
        assert m.repos == ["/home/r/Code/bot"]

    def test_unknown_machine_returns_none(self, ssh_mgr):
        assert ssh_mgr.get_machine("nonexistent") is None

    def test_list_machine_names(self, ssh_mgr):
        machines = [
            {"name": "a", "host": "a", "claude_path": "/c", "repos": ["/r"]},
            {"name": "b", "host": "b", "claude_path": "/c", "repos": ["/r"]},
        ]
        ssh_mgr.register_machines(machines)
        assert sorted(ssh_mgr.list_machine_names()) == ["a", "b"]

    def test_defaults_applied(self, ssh_mgr):
        machines = [{"name": "x", "host": "x", "claude_path": "/c", "repos": ["/r"]}]
        ssh_mgr.register_machines(machines)
        m = ssh_mgr.get_machine("x")
        assert m.log_dir == "/tmp/ic-logs"
        assert m.max_workers is None
        assert m.env == {}

    def test_role_defaults_to_worker(self, ssh_mgr):
        machines = [{"name": "x", "host": "x", "claude_path": "/c", "repos": ["/r"]}]
        ssh_mgr.register_machines(machines)
        m = ssh_mgr.get_machine("x")
        assert m.role == "worker"

    def test_role_explicit_monitor(self, ssh_mgr):
        machines = [{"name": "x", "host": "x", "claude_path": "/c", "repos": ["/r"], "role": "monitor"}]
        ssh_mgr.register_machines(machines)
        m = ssh_mgr.get_machine("x")
        assert m.role == "monitor"

    def test_role_explicit_worker(self, ssh_mgr):
        machines = [{"name": "x", "host": "x", "claude_path": "/c", "repos": ["/r"], "role": "worker"}]
        ssh_mgr.register_machines(machines)
        m = ssh_mgr.get_machine("x")
        assert m.role == "worker"

    def test_normalized_clients_are_preserved(self, ssh_mgr):
        clients = {
            "claude": {"enabled": True, "path": "/opt/claude"},
            "codex": {"enabled": True, "path": "/opt/codex"},
        }
        ssh_mgr.register_machines([
            {"name": "dual", "host": "dual", "clients": clients, "repos": ["/r"]},
        ])

        machine = ssh_mgr.get_machine("dual")
        assert machine.clients == clients
        assert machine.client_path("claude") == "/opt/claude"
        assert machine.client_path("codex") == "/opt/codex"

    def test_codex_only_machine_needs_no_claude_path(self, ssh_mgr):
        ssh_mgr.register_machines([
            {
                "name": "codex-only",
                "host": "codex-only",
                "clients": {
                    "codex": {"enabled": True, "path": "~/.local/bin/codex"},
                },
                "repos": ["/r"],
            },
        ])

        machine = ssh_mgr.get_machine("codex-only")
        assert machine.claude_path is None
        assert machine.client_path("codex") == "~/.local/bin/codex"
        assert machine.client_path("claude") is None


class TestGetSSHArgs:
    def test_returns_correct_args(self, ssh_mgr):
        args = ssh_mgr.get_ssh_args("remote-worker")
        assert args[0] == "ssh"
        assert "remote-worker" == args[-1]
        joined = " ".join(args)
        assert "ControlMaster=auto" in joined
        assert "ControlPersist=600" in joined
        assert "ServerAliveInterval=30" in joined

    def test_socket_dir_in_control_path(self, ssh_mgr):
        args = ssh_mgr.get_ssh_args("remote-worker")
        joined = " ".join(args)
        assert ssh_mgr.socket_dir in joined


class TestHealthCheck:
    @patch("ironclaude.ssh_manager.subprocess.run")
    def test_healthy_machine(self, mock_run, ssh_mgr):
        machines = [{"name": "k", "host": "k", "claude_path": "/c", "repos": ["/r"]}]
        ssh_mgr.register_machines(machines)
        mock_run.return_value = MagicMock(returncode=0, stdout="ok\n")
        result = ssh_mgr.health_check("k")
        assert result.ok is True

    @patch("ironclaude.ssh_manager.subprocess.run")
    def test_unreachable_machine(self, mock_run, ssh_mgr):
        machines = [{"name": "k", "host": "k", "claude_path": "/c", "repos": ["/r"]}]
        ssh_mgr.register_machines(machines)
        mock_run.return_value = MagicMock(returncode=255, stderr=b"Connection refused")
        result = ssh_mgr.health_check("k")
        assert result.ok is False
        assert "connectivity" in result.details.lower() or "failed" in result.details.lower()

    def test_health_check_unknown_machine(self, ssh_mgr):
        result = ssh_mgr.health_check("ghost")
        assert result.ok is False

    @patch("ironclaude.ssh_manager.subprocess.run")
    def test_monitor_skips_tmux_check(self, mock_run, ssh_mgr):
        machines = [{"name": "k", "host": "k", "claude_path": "/c", "repos": ["/r"], "role": "monitor"}]
        ssh_mgr.register_machines(machines)
        mock_run.return_value = MagicMock(returncode=0, stdout="ok\n")
        result = ssh_mgr.health_check("k")
        assert result.ok is True
        assert mock_run.call_count == 1  # SSH only, no client or tmux probe

    @patch("ironclaude.ssh_manager.subprocess.run")
    def test_transport_only_worker_health_checks_ssh_and_tmux(self, mock_run, ssh_mgr):
        machines = [{"name": "k", "host": "k", "claude_path": "/c", "repos": ["/r"], "role": "worker"}]
        ssh_mgr.register_machines(machines)
        mock_run.return_value = MagicMock(returncode=0, stdout="ok\n")
        result = ssh_mgr.health_check("k")
        assert result.ok is True
        assert mock_run.call_count == 2
        remote_commands = [call.args[0][-1] for call in mock_run.call_args_list]
        assert remote_commands == ["true", "tmux -V"]

    @patch("ironclaude.ssh_manager.subprocess.run")
    def test_run_argv_neutralizes_metacharacters(self, mock_run, ssh_mgr):
        import shlex

        malicious_executable = "/opt/claude; rm -rf ~"
        malicious_arg = "value; touch /tmp/pwned"
        mock_run.return_value = MagicMock(
            returncode=0, stdout="ok\n", stderr="",
        )

        ssh_mgr.run_argv("k", [malicious_executable, malicious_arg])

        remote_cmd = mock_run.call_args.args[0][-1]
        assert shlex.quote(malicious_executable) in remote_cmd
        assert shlex.quote(malicious_arg) in remote_cmd
        assert "; rm" not in remote_cmd.replace(
            shlex.quote(malicious_executable), "",
        )
        assert "; touch" not in remote_cmd.replace(shlex.quote(malicious_arg), "")

    @patch("ironclaude.ssh_manager.subprocess.run")
    def test_run_argv_home_tilde_executable_still_expands(
        self, mock_run, ssh_mgr,
    ):
        mock_run.return_value = MagicMock(
            returncode=0, stdout="ok\n", stderr="",
        )

        ssh_mgr.run_argv("k", ["~/.claude/local/claude", "--version"])

        remote_cmd = mock_run.call_args.args[0][-1]
        assert "$HOME" in remote_cmd
        assert "~" not in remote_cmd
        assert remote_cmd.endswith(" --version")


class TestTeardown:
    @patch("ironclaude.ssh_manager.subprocess.run")
    def test_teardown_sends_exit(self, mock_run, ssh_mgr):
        machines = [{"name": "k", "host": "k", "claude_path": "/c", "repos": ["/r"]}]
        ssh_mgr.register_machines(machines)
        ssh_mgr.teardown("k")
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert "-O" in cmd
        assert "exit" in cmd
