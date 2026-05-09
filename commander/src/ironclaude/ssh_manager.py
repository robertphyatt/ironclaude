"""SSH connection management for remote worker machines."""

from __future__ import annotations

import logging
import os
import subprocess
from dataclasses import dataclass, field

logger = logging.getLogger("ironclaude.ssh_manager")


@dataclass
class MachineConfig:
    name: str
    host: str
    claude_path: str
    repos: list[str]
    purpose: str = ""
    log_dir: str = "/tmp/ic-logs"
    max_workers: int | None = None
    env: dict[str, str] = field(default_factory=dict)


@dataclass
class HealthResult:
    ok: bool
    details: str


class SSHConnectionManager:
    """Manages persistent SSH ControlMaster connections per remote host."""

    def __init__(self, socket_dir: str = "/tmp/ic-ssh"):
        self.socket_dir = socket_dir
        os.makedirs(socket_dir, exist_ok=True)
        self._machines: dict[str, MachineConfig] = {}
        self._healthy: dict[str, bool] = {}

    def register_machines(self, machines: list[dict]) -> None:
        for m in machines:
            cfg = MachineConfig(
                name=m["name"],
                host=m["host"],
                claude_path=m["claude_path"],
                repos=m.get("repos", []),
                purpose=m.get("purpose", ""),
                log_dir=m.get("log_dir", "/tmp/ic-logs"),
                max_workers=m.get("max_workers"),
                env=m.get("env", {}),
            )
            self._machines[cfg.name] = cfg

    def get_machine(self, name: str) -> MachineConfig | None:
        return self._machines.get(name)

    def list_machine_names(self) -> list[str]:
        return list(self._machines.keys())

    def get_ssh_args(self, host: str) -> list[str]:
        return [
            "ssh",
            "-o", f"ControlPath={self.socket_dir}/%r@%h:%p",
            "-o", "ControlMaster=auto",
            "-o", "ControlPersist=600",
            "-o", "ServerAliveInterval=30",
            "-o", "ServerAliveCountMax=3",
            "-o", "ConnectTimeout=10",
            host,
        ]

    def health_check(self, name: str) -> HealthResult:
        machine = self._machines.get(name)
        if not machine:
            return HealthResult(ok=False, details=f"Unknown machine '{name}'")

        checks = [
            (["true"], "SSH connectivity"),
            (["which", machine.claude_path.replace("~", "$HOME")], "Claude binary"),
            (["tmux", "-V"], "tmux available"),
        ]
        for cmd, label in checks:
            try:
                result = subprocess.run(
                    self.get_ssh_args(machine.host) + [" ".join(cmd)],
                    capture_output=True, timeout=15,
                )
                if result.returncode != 0:
                    self._healthy[name] = False
                    return HealthResult(
                        ok=False,
                        details=f"{label} check failed (rc={result.returncode})",
                    )
            except subprocess.TimeoutExpired:
                self._healthy[name] = False
                return HealthResult(ok=False, details=f"{label} check timed out")
            except OSError as e:
                self._healthy[name] = False
                return HealthResult(ok=False, details=f"{label} check error: {e}")

        self._healthy[name] = True
        return HealthResult(ok=True, details="All checks passed")

    def health_check_all(self) -> dict[str, HealthResult]:
        return {name: self.health_check(name) for name in self._machines}

    def is_healthy(self, name: str) -> bool:
        return self._healthy.get(name, False)

    def teardown(self, name: str) -> None:
        machine = self._machines.get(name)
        if not machine:
            return
        try:
            subprocess.run(
                ["ssh", "-O", "exit",
                 "-o", f"ControlPath={self.socket_dir}/%r@%h:%p",
                 machine.host],
                capture_output=True, timeout=5,
            )
        except (subprocess.TimeoutExpired, OSError):
            pass

    def teardown_all(self) -> None:
        for name in self._machines:
            self.teardown(name)
