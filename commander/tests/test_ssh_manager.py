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
        assert mock_run.call_count == 2  # SSH + claude only, no tmux

    @patch("ironclaude.ssh_manager.subprocess.run")
    def test_worker_checks_tmux(self, mock_run, ssh_mgr):
        machines = [{"name": "k", "host": "k", "claude_path": "/c", "repos": ["/r"], "role": "worker"}]
        ssh_mgr.register_machines(machines)
        mock_run.return_value = MagicMock(returncode=0, stdout="ok\n")
        result = ssh_mgr.health_check("k")
        assert result.ok is True
        assert mock_run.call_count == 3  # SSH + claude + tmux

    @patch("ironclaude.ssh_manager.subprocess.run")
    def test_claude_path_metacharacters_are_neutralized(self, mock_run, ssh_mgr):
        # A malicious/mistyped claude_path with shell metacharacters must not
        # break out of the remote `which ...` command.
        malicious = "/opt/claude; rm -rf ~"
        machines = [{"name": "k", "host": "k", "claude_path": malicious, "repos": ["/r"]}]
        ssh_mgr.register_machines(machines)
        mock_run.return_value = MagicMock(returncode=0, stdout="ok\n")
        ssh_mgr.health_check("k")

        # Find the remote command string that runs `which` for the claude binary.
        which_cmds = [
            c.args[0][-1]
            for c in mock_run.call_args_list
            if isinstance(c.args[0][-1], str) and c.args[0][-1].startswith("which ")
        ]
        assert len(which_cmds) == 1
        remote_cmd = which_cmds[0]
        # The metacharacters must appear only inside the shlex-quoted form,
        # never as a bare, executable `; rm` sequence.
        import shlex
        assert shlex.quote(malicious) in remote_cmd
        assert "; rm" not in remote_cmd.replace(shlex.quote(malicious), "")

    @patch("ironclaude.ssh_manager.subprocess.run")
    def test_home_tilde_path_still_expands(self, mock_run, ssh_mgr):
        # A legitimate ~/path must still resolve $HOME on the remote host.
        machines = [{"name": "k", "host": "k",
                     "claude_path": "~/.claude/local/claude", "repos": ["/r"]}]
        ssh_mgr.register_machines(machines)
        mock_run.return_value = MagicMock(returncode=0, stdout="ok\n")
        ssh_mgr.health_check("k")

        which_cmds = [
            c.args[0][-1]
            for c in mock_run.call_args_list
            if isinstance(c.args[0][-1], str) and c.args[0][-1].startswith("which ")
        ]
        assert len(which_cmds) == 1
        remote_cmd = which_cmds[0]
        # $HOME stays unquoted so the remote shell expands it; the rest is quoted.
        assert "$HOME" in remote_cmd
        assert "~" not in remote_cmd


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
