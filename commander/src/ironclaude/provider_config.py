"""Validated static configuration for Claude Code and Codex clients."""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from types import MappingProxyType
from typing import Callable, Mapping

CLIENT_NAMES = ("claude", "codex")
# Inspected external-client lifecycle roles: Brain, spawned workers, tool-free
# grader subprocess, and /advisor helper. Local Ollama/shadow graders are not
# Claude/Codex client roles.
ROLE_NAMES = ("brain", "worker", "grader", "advisor")
TIER_NAMES = ("haiku", "sonnet", "opus", "fable")
PROVIDER_KEYS = frozenset(("clients", "roles", "shadow_mode"))
CLIENT_KEYS = frozenset(("enabled", "path", "models"))
ROLE_KEYS = frozenset(("preferred", "clients"))
MACHINE_CLIENT_KEYS = frozenset(("enabled", "path"))
EXPECTED_MODELS = {
    "claude": {
        "haiku": "haiku", "sonnet": "sonnet", "opus": "opus", "fable": "fable"
    },
}
# Codex models are family words (design 2026-10-17 v120-completeness R2; operator: "we shouldn't be
# doing any pinned defaults in ironclaude anymore"). Like Claude's aliases, a word means the newest
# model of that family where Codex runs; an explicit gpt-<version>-<word> slug is used as-is.
CODEX_TIER_WORDS = {"haiku": "luna", "sonnet": "terra", "opus": "sol", "fable": "astra"}
CODEX_MODEL_RE = re.compile(r"^gpt-(\d+(?:\.\d+)*)-(luna|terra|sol|astra)$")
# Read on a remote worker host through SSHConnectionManager.run_argv, which shell-quotes every
# argument, so `sh -c` expands the cache path on that host.
REMOTE_CODEX_CACHE_ARGV = ("sh", "-c", 'cat "${CODEX_HOME:-$HOME/.codex}/models_cache.json"')


class CodexModelUnavailable(ValueError):
    """No installed model of a Codex family word: Codex is unavailable for this call."""

    def __init__(self, word: str, cache_location: str):
        self.word = word
        self.cache_location = cache_location
        super().__init__(
            f"Codex model family {word!r} has no installed model in {cache_location}; "
            "Codex is unavailable for this call"
        )


def local_codex_cache_path() -> str:
    home = os.environ.get("CODEX_HOME") or os.path.join(os.path.expanduser("~"), ".codex")
    return os.path.join(home, "models_cache.json")


def remote_codex_cache_location(host: str) -> str:
    return f"{host}:${{CODEX_HOME:-$HOME/.codex}}/models_cache.json"


def read_local_codex_cache() -> str | None:
    try:
        with open(local_codex_cache_path(), encoding="utf-8") as handle:
            return handle.read()
    except OSError:
        return None


def newest_codex_slug(word: str, cache_text: str | None) -> str | None:
    """Newest ``gpt-<version>-<word>`` listed in a Codex models_cache.json text, ordered by numeric
    version like plan-validator.sh ``_newest_codex_model`` and advisor-review.ts ``newestAtTier``.
    None when the text is missing or malformed or lists no model of the family."""
    if cache_text is None:
        return None
    try:
        models = json.loads(cache_text).get("models")
    except (ValueError, AttributeError):
        return None
    best = None
    for entry in models if isinstance(models, list) else ():
        slug = entry.get("slug") if isinstance(entry, dict) else None
        match = CODEX_MODEL_RE.fullmatch(slug) if isinstance(slug, str) else None
        if match is None or match.group(2) != word:
            continue
        version = tuple(int(part) for part in match.group(1).split("."))
        if best is None or version > best[0]:
            best = (version, slug)
    return best[1] if best else None


def resolve_codex_model(value: str, cache_text: str | None, cache_location: str) -> str:
    """An explicit ``gpt-<version>-<word>`` slug is used as-is. A family word resolves to the newest
    installed model of that family in ``cache_text``, else CodexModelUnavailable."""
    if CODEX_MODEL_RE.fullmatch(value):
        return value
    newest = newest_codex_slug(value, cache_text)
    if newest is None:
        raise CodexModelUnavailable(value, cache_location)
    return newest


def resolve_local_codex_model(value: str) -> str:
    """resolve_codex_model against this Mac's ``$CODEX_HOME/models_cache.json`` (default ~/.codex)."""
    return resolve_codex_model(value, read_local_codex_cache(), local_codex_cache_path())


def _codex_models_valid(models: Mapping[str, object]) -> bool:
    if set(models) != set(TIER_NAMES):
        return False
    for tier, value in models.items():
        word = CODEX_TIER_WORDS[tier]
        if value == word:
            continue
        match = CODEX_MODEL_RE.fullmatch(value) if isinstance(value, str) else None
        if match is None or match.group(2) != word:
            return False
    return True


class ProviderConfigError(ValueError):
    """Raised when provider configuration is unsafe or contradictory."""


@dataclass(frozen=True)
class ClientConfig:
    name: str
    enabled: bool
    path: str
    models: Mapping[str, str]


@dataclass(frozen=True)
class RoleConfig:
    preferred: str
    clients: tuple[str, ...]


@dataclass(frozen=True)
class ProviderConfig:
    clients: Mapping[str, ClientConfig]
    roles: Mapping[str, RoleConfig]
    shadow_mode: bool
    claude_role_models: Mapping[tuple[str, str], str]
    advisor_worker_models: Mapping[str, str]

    def model_for(
        self, client: str, tier: str, role: str | None = None, *, host: str,
        codex_cache: Callable[[str], str | None] | None = None,
    ) -> str:
        if client == "claude" and role is not None:
            override = self.claude_role_models.get((role, tier))
            if override is not None:
                return override
        try:
            model = self.clients[client].models[tier]
        except KeyError as exc:
            raise ProviderConfigError(
                f"client {client!r} has no model mapping for tier {tier!r}"
            ) from exc
        # Design 2026-10-17 v120-completeness R2: Claude aliases and explicit Codex slugs are used
        # as-is, locally and remotely. A Codex family word resolves where Codex runs: this Mac's
        # cache on host "local", the remote box's own cache through codex_cache(host) otherwise.
        # No model of the family -> CodexModelUnavailable (the router tries the next client).
        if client != "codex" or CODEX_MODEL_RE.fullmatch(model):
            return model
        if host == "local":
            return resolve_local_codex_model(model)
        return resolve_codex_model(
            model, codex_cache(host) if codex_cache is not None else None,
            remote_codex_cache_location(host),
        )

    def advisor_model_for(
        self, client: str, worker_type: str, tier: str, *, host: str,
        codex_cache: Callable[[str], str | None] | None = None,
    ) -> str:
        if client == "claude":
            override = self.advisor_worker_models.get(worker_type)
            if override is not None:
                return override
        return self.model_for(client, tier, "advisor", host=host, codex_cache=codex_cache)


def parse_provider_config(
    raw: Mapping[str, object],
    *,
    claude_role_models: Mapping[tuple[str, str], str] | None = None,
    advisor_worker_models: Mapping[str, str] | None = None,
) -> ProviderConfig:
    unknown_provider_keys = set(raw) - PROVIDER_KEYS
    if unknown_provider_keys:
        raise ProviderConfigError(
            f"unknown provider keys: {sorted(unknown_provider_keys)}"
        )
    raw_clients = raw.get("clients")
    raw_roles = raw.get("roles")
    if not isinstance(raw_clients, Mapping) or not isinstance(raw_roles, Mapping):
        raise ProviderConfigError("providers.clients and providers.roles are required")

    unknown = set(raw_clients) - set(CLIENT_NAMES)
    if unknown:
        raise ProviderConfigError(f"unknown provider client(s): {sorted(unknown)}")
    if set(raw_roles) != set(ROLE_NAMES):
        raise ProviderConfigError(f"providers.roles must exactly match {sorted(ROLE_NAMES)}")

    clients: dict[str, ClientConfig] = {}
    for name in CLIENT_NAMES:
        value = raw_clients.get(name)
        if not isinstance(value, Mapping):
            raise ProviderConfigError(f"providers.clients.{name} is required")
        unknown_client_keys = set(value) - CLIENT_KEYS
        if unknown_client_keys:
            raise ProviderConfigError(
                f"unknown keys for client {name!r}: {sorted(unknown_client_keys)}"
            )
        path = value.get("path")
        models = value.get("models")
        if not isinstance(path, str) or not path:
            raise ProviderConfigError(f"providers.clients.{name}.path is required")
        if not isinstance(models, Mapping):
            raise ProviderConfigError(f"providers.clients.{name}.models is required")
        enabled = value.get("enabled", False)
        if not isinstance(enabled, bool):
            raise ProviderConfigError(f"providers.clients.{name}.enabled must be boolean")
        model_values = dict(models)
        if name == "codex":
            if not _codex_models_valid(model_values):
                raise ProviderConfigError(
                    "providers.clients.codex.models must map each tier to its Codex family word "
                    "or a gpt-<version>-<tier-word> model (haiku→luna, sonnet→terra, "
                    "opus→sol, fable→astra)"
                )
        elif model_values != EXPECTED_MODELS[name]:
            raise ProviderConfigError(
                f"providers.clients.{name}.models must exactly match approved models"
            )
        clients[name] = ClientConfig(
            name=name,
            enabled=enabled,
            path=path,
            models=MappingProxyType(model_values),
        )

    roles: dict[str, RoleConfig] = {}
    for role in ROLE_NAMES:
        value = raw_roles.get(role)
        if not isinstance(value, Mapping):
            raise ProviderConfigError(f"providers.roles.{role} is required")
        unknown_role_keys = set(value) - ROLE_KEYS
        if unknown_role_keys:
            raise ProviderConfigError(
                f"unknown keys for role {role!r}: {sorted(unknown_role_keys)}"
            )
        preferred = value.get("preferred")
        enabled_for_role = value.get("clients")
        if preferred not in CLIENT_NAMES:
            raise ProviderConfigError(f"invalid preferred client for role {role!r}")
        if not isinstance(enabled_for_role, list) or not enabled_for_role:
            raise ProviderConfigError(f"role {role!r} requires at least one client")
        if len(enabled_for_role) != len(set(enabled_for_role)):
            raise ProviderConfigError(f"role {role!r} contains duplicate clients")
        for name in enabled_for_role:
            if name not in CLIENT_NAMES:
                raise ProviderConfigError(f"unknown client {name!r} for role {role!r}")
            if not clients[name].enabled:
                raise ProviderConfigError(f"client {name!r} is disabled for role {role!r}")
        if preferred not in enabled_for_role:
            raise ProviderConfigError(f"preferred client is not enabled for role {role!r}")
        roles[role] = RoleConfig(preferred, tuple(enabled_for_role))

    shadow_mode = raw.get("shadow_mode", False)
    if not isinstance(shadow_mode, bool):
        raise ProviderConfigError("providers.shadow_mode must be boolean")
    overrides = dict(claude_role_models or {})
    for (role, tier), model in overrides.items():
        if role not in ROLE_NAMES or tier not in TIER_NAMES or not isinstance(model, str):
            raise ProviderConfigError("invalid legacy Claude role model override")
    advisor_overrides = dict(advisor_worker_models or {})
    if not all(
        isinstance(worker_type, str) and worker_type
        and isinstance(model, str) and model
        for worker_type, model in advisor_overrides.items()
    ):
        raise ProviderConfigError("invalid advisor.advisor_models override")
    return ProviderConfig(
        MappingProxyType(clients), MappingProxyType(roles), shadow_mode,
        MappingProxyType(overrides), MappingProxyType(advisor_overrides),
    )


def _semantic_tier(model: object, field: str, fallback: str) -> str:
    if not isinstance(model, str) or not model:
        raise ProviderConfigError(f"{field} must be a non-empty model name")
    tokens = set(re.split(r"[^a-z0-9]+", model.casefold()))
    matches = [tier for tier in TIER_NAMES if tier in tokens]
    return matches[0] if len(matches) == 1 else fallback


def provider_config_from_commander(config: Mapping[str, object]) -> ProviderConfig:
    advisor = config.get("advisor")
    if not isinstance(advisor, Mapping):
        raise ProviderConfigError("advisor configuration is required")
    legacy = {
        "brain": config.get("brain_model"),
        "grader": config.get("grader_model"),
        "worker": config.get("default_opus_model"),
        "advisor": advisor.get("advisor_model"),
    }
    overrides = {
        (role, _semantic_tier(model, field, fallback)): model
        for role, model, field, fallback in (
            ("brain", legacy["brain"], "brain_model", "sonnet"),
            ("grader", legacy["grader"], "grader_model", "opus"),
            ("worker", legacy["worker"], "default_opus_model", "opus"),
            ("advisor", legacy["advisor"], "advisor.advisor_model", "opus"),
        )
    }
    providers = config.get("providers")
    if not isinstance(providers, Mapping):
        raise ProviderConfigError("providers configuration is required")
    advisor_models = advisor.get("advisor_models", {})
    if not isinstance(advisor_models, Mapping):
        raise ProviderConfigError("advisor.advisor_models must be an object")
    return parse_provider_config(
        providers,
        claude_role_models=overrides,
        advisor_worker_models=advisor_models,
    )


def normalize_machine_clients(machine: Mapping[str, object]) -> dict[str, dict]:
    raw = machine.get("clients")
    if raw is None:
        claude_path = machine.get("claude_path")
        if not isinstance(claude_path, str) or not claude_path:
            raise ProviderConfigError(
                f"machine {machine.get('name', '?')!r} requires claude_path or clients"
            )
        return {"claude": {"enabled": True, "path": claude_path}}
    if not isinstance(raw, Mapping):
        raise ProviderConfigError("machine clients must be an object")

    normalized: dict[str, dict] = {}
    for name, value in raw.items():
        if name not in CLIENT_NAMES or not isinstance(value, Mapping):
            raise ProviderConfigError(f"invalid machine client {name!r}")
        unknown_machine_keys = set(value) - MACHINE_CLIENT_KEYS
        if unknown_machine_keys:
            raise ProviderConfigError(
                f"unknown machine client keys for {name!r}: {sorted(unknown_machine_keys)}"
            )
        path = value.get("path")
        if not isinstance(path, str) or not path:
            raise ProviderConfigError(f"machine client {name!r} requires path")
        enabled = value.get("enabled", False)
        if not isinstance(enabled, bool):
            raise ProviderConfigError(f"machine client {name!r} enabled must be boolean")
        normalized[name] = {"enabled": enabled, "path": path}
    if not any(value["enabled"] for value in normalized.values()):
        raise ProviderConfigError("machine requires at least one enabled client")
    return normalized
