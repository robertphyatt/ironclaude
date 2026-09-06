"""Pure request-time routing from role/tier to a concrete client handle."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from ironclaude.provider_capabilities import CapabilityRegistry
from ironclaude.provider_config import ProviderConfig
from ironclaude.provider_state import ProviderState


@dataclass(frozen=True)
class ProviderHandle:
    client: str
    requested_tier: str
    effective_tier: str
    model: str
    host: str


class NoCapabilityAvailable(RuntimeError):
    def __init__(self, role: str, tier: str, eligible_hosts: Sequence[str]):
        self.role = role
        self.tier = tier
        self.eligible_hosts = tuple(eligible_hosts)
        super().__init__(
            f"no configured capability for role={role} tier={tier} "
            f"hosts={self.eligible_hosts}"
        )


class ProviderRouter:
    def __init__(
        self, config: ProviderConfig, state: ProviderState,
        registry: CapabilityRegistry,
    ):
        self._config = config
        self._state = state
        self._registry = registry

    def resolve(
        self,
        role: str,
        tier: str,
        eligible_hosts: Sequence[str],
        *,
        advisor_worker_type: str | None = None,
    ) -> ProviderHandle:
        role_config = self._config.roles[role]
        current = self._state.current_client(role, role_config.preferred)
        if current not in role_config.clients:
            current = role_config.preferred
        other_clients = [c for c in role_config.clients if c != current]

        if tier == "fable":
            attempts = [(current, "fable"), (current, "opus")]
            for client in other_clients:
                attempts.extend(((client, "fable"), (client, "opus")))
        else:
            attempts = [(current, tier)]
            attempts.extend((client, tier) for client in other_clients)

        # Client/tier attempt is outer loop: sticky client is exhausted across
        # all eligible hosts before any fallback client can be selected.
        for client, effective_tier in attempts:
            for host in eligible_hosts:
                if client not in role_config.clients:
                    continue
                capability = self._registry.get(
                    host, client, role, effective_tier
                )
                if capability is None or not capability.usable:
                    continue
                if not self._state.is_available(
                    host, client, role, effective_tier
                ):
                    continue
                model = (
                    self._config.advisor_model_for(
                        client, advisor_worker_type, effective_tier
                    )
                    if role == "advisor" and advisor_worker_type is not None
                    else self._config.model_for(client, effective_tier, role)
                )
                return ProviderHandle(
                    client=client,
                    requested_tier=tier,
                    effective_tier=effective_tier,
                    model=model,
                    host=host,
                )

        raise NoCapabilityAvailable(role, tier, eligible_hosts)
