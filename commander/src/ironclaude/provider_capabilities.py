"""Client capability probing without routing or lifecycle side effects."""
from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from typing import Callable, Mapping, Sequence

from ironclaude.provider_config import ProviderConfig
from ironclaude.provider_state import ProviderState


@dataclass(frozen=True)
class ProbeResult:
    returncode: int
    stdout: str
    stderr: str


@dataclass(frozen=True)
class ClientCapability:
    host: str
    client: str
    role: str
    tier: str
    configured: bool
    supported: bool
    installed: bool
    authenticated: bool | None
    available: bool
    reason: str | None = None

    @property
    def usable(self) -> bool:
        return (
            self.configured and self.supported and self.installed and self.available
            and self.authenticated is not False
        )


def _run(host: str, argv: Sequence[str]) -> ProbeResult:
    if host != "local":
        return ProbeResult(126, "", "remote executor required")
    completed = subprocess.run(
        list(argv), capture_output=True, text=True, timeout=10, check=False
    )
    return ProbeResult(completed.returncode, completed.stdout, completed.stderr)


class CapabilityProbe:
    def __init__(
        self,
        *,
        executor: Callable[[str, Sequence[str]], ProbeResult] = _run,
        executable_lookup: Callable[[str], str | None] = shutil.which,
    ):
        self._executor = executor
        self._lookup = executable_lookup

    def _execute(self, host: str, argv: Sequence[str]) -> ProbeResult:
        try:
            return self._executor(host, argv)
        except subprocess.TimeoutExpired:
            return ProbeResult(124, "", "probe timed out")
        except OSError as exc:
            return ProbeResult(126, "", f"os error: {exc}")

    def _scope(
        self,
        config: ProviderConfig,
        client: str,
        role: str,
        tier: str,
        machine_clients: Mapping[str, Mapping[str, object]] | None = None,
    ) -> tuple[bool, bool]:
        client_cfg = config.clients[client]
        role_cfg = config.roles[role]
        configured = client_cfg.enabled and client in role_cfg.clients
        if machine_clients is not None:
            host_cfg = machine_clients.get(client)
            configured = configured and isinstance(host_cfg, Mapping) \
                and host_cfg.get("enabled") is True
        supported = tier in client_cfg.models
        return configured, supported

    def _capability(
        self, host, client, role, tier, configured, supported,
        installed, authenticated, *, available, reason=None
    ) -> ClientCapability:
        return ClientCapability(
            host=host, client=client, role=role, tier=tier,
            configured=configured, supported=supported, installed=installed,
            authenticated=authenticated, reason=reason, available=available,
        )

    def _probe_auth(self, executable, host, client, role, tier) -> ClientCapability:
        if client == "claude":
            # No documented non-interactive Claude auth-status contract.
            return self._capability(
                host, client, role, tier, True, True, True, None, available=True
            )
        result = self._execute(host, (executable, "login", "status"))
        if result.returncode == 124 and result.stderr == "probe timed out":
            return self._capability(
                host, client, role, tier, True, True, True, False,
                available=False, reason="probe_timeout",
            )
        if result.returncode == 126 and result.stderr.startswith("os error:"):
            return self._capability(
                host, client, role, tier, True, True, False, None,
                available=False, reason="executable_error",
            )
        if result.returncode == 0 and "chatgpt" in result.stdout.casefold():
            return self._capability(
                host, client, role, tier, True, True, True, True, available=True
            )
        reason = "unsupported_auth_mode" if result.returncode == 0 else "not_authenticated"
        return self._capability(
            host, client, role, tier, True, True, True, False,
            available=False, reason=reason,
        )

    def probe_local(
        self, config: ProviderConfig, client: str, role: str, tier: str
    ) -> ClientCapability:
        client_cfg = config.clients[client]
        configured, supported = self._scope(config, client, role, tier)
        if not configured or not supported:
            return self._capability(
                "local", client, role, tier, configured, supported,
                False, None, available=False,
                reason="not_configured" if not configured else "unsupported",
            )
        executable = self._lookup(client_cfg.path)
        if executable is None:
            return self._capability(
                "local", client, role, tier, True, True,
                False, None, available=False, reason="missing_executable",
            )
        return self._probe_auth(executable, "local", client, role, tier)

    def probe_remote(
        self,
        config: ProviderConfig,
        machine_clients: Mapping[str, Mapping[str, object]],
        client: str,
        role: str,
        tier: str,
        host: str,
    ) -> ClientCapability:
        configured, supported = self._scope(
            config, client, role, tier, machine_clients
        )
        if not configured or not supported:
            return self._capability(
                host, client, role, tier, configured, supported,
                False, None, available=False,
                reason="not_configured" if not configured else "unsupported",
            )
        executable = str(machine_clients[client]["path"])
        version = self._execute(host, (executable, "--version"))
        if version.returncode == 124 and version.stderr == "probe timed out":
            return self._capability(
                host, client, role, tier, True, True, False, None,
                available=False, reason="probe_timeout",
            )
        if version.returncode == 126 and version.stderr.startswith("os error:"):
            return self._capability(
                host, client, role, tier, True, True, False, None,
                available=False, reason="executable_error",
            )
        if version.returncode == 127:
            return self._capability(
                host, client, role, tier, True, True, False, None,
                available=False, reason="missing_executable",
            )
        if version.returncode != 0:
            return self._capability(
                host, client, role, tier, True, True,
                False, None, available=False, reason="executable_probe_failed",
            )
        return self._probe_auth(executable, host, client, role, tier)


class CapabilityRegistry:
    def __init__(self, state: ProviderState):
        self._state = state

    def record(self, capability: ClientCapability) -> None:
        self._state.record_capability(
            capability.host, capability.client, capability.role, capability.tier,
            configured=capability.configured,
            supported=capability.supported,
            installed=capability.installed,
            authenticated=capability.authenticated,
            reason=capability.reason,
            available=capability.available,
        )

    def get(self, host, client, role, tier) -> ClientCapability | None:
        observed = self._state.capability_observation(host, client, role, tier)
        if observed is None:
            return None
        return ClientCapability(
            host=host, client=client, role=role, tier=tier,
            configured=observed["configured"],
            supported=observed["supported"],
            installed=observed["installed"],
            authenticated=observed["authenticated"],
            reason=observed["reason"],
            available=observed["available"],
        )
