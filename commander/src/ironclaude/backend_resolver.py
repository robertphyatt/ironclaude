"""Shared LLM-backend resolution for Commander's five spots.

Implements the single resolution rule documented in
`worker/config-schema/llm-backend.md` (steps 1-3). The shared conformance
fixture `worker/config-schema/resolution-cases.json` pins the deterministic
cases; per-consumer defaults are applied here for Commander (backend default
"ollama", ollama url default localhost).
"""
from __future__ import annotations

from dataclasses import dataclass

from .ollama_client import OllamaClient
from .openai_client import OpenAiClient

# Commander's default backend when nothing in the config resolves it (step 1).
_COMMANDER_DEFAULT_BACKEND = "ollama"
_OLLAMA_DEFAULT_URL = "http://localhost:11434"


@dataclass
class ResolvedBackend:
    backend: str
    model: str | None
    url: str | None
    fallback_url: str | None = None
    timeout: int | None = None
    max_tokens: int | None = None
    connect_timeout: int | None = None
    probe_timeout: int | None = None
    hook_validation_budget: int | None = None


def _legacy_alias(cfg: dict, spot: str) -> str | None:
    """Backend-agnostic legacy model alias for a spot (step 2)."""
    if spot == "shadow":
        return cfg.get("shadow_model")                       # TOP-LEVEL
    if spot == "summarization":
        return cfg.get("ollama", {}).get("summarization_model")  # NESTED under ollama
    return None


def resolve_backend(cfg: dict, spot: str) -> ResolvedBackend:
    """Resolve backend/model/connection for `spot` from config `cfg`.

    Rule (verbatim from llm-backend.md):
      backend = spots[spot].backend ?? cfg.backend ?? cfg.validation_backend ?? "ollama"
      model   = spots[spot].model ?? <legacy alias> ?? <resolved-backend-block>.model
      url/fallback/timeout/max_tokens come from the resolved backend block.
    """
    spots = cfg.get("spots") or {}
    spot_cfg = spots.get(spot) or {}

    backend = (
        spot_cfg.get("backend")
        or cfg.get("backend")
        or cfg.get("validation_backend")
        or _COMMANDER_DEFAULT_BACKEND
    )

    ollama_block = cfg.get("ollama") or {}
    openai_block = cfg.get("openai") or {}
    block = openai_block if backend == "openai" else ollama_block

    model = spot_cfg.get("model") or _legacy_alias(cfg, spot) or block.get("model")

    if backend == "openai":
        url = openai_block.get("base_url")
        fallback_url = openai_block.get("fallback_base_url")
        max_tokens = openai_block.get("max_tokens")
    else:
        url = ollama_block.get("url") or _OLLAMA_DEFAULT_URL
        fallback_url = ollama_block.get("fallback_url")
        max_tokens = None

    timeout = block.get("timeout_seconds") or cfg.get("timeout_seconds")
    connect_timeout = block.get("connect_timeout_seconds") or cfg.get("connect_timeout_seconds")
    probe_timeout = block.get("probe_timeout_seconds") or cfg.get("probe_timeout_seconds")
    hook_validation_budget = block.get("hook_validation_budget_seconds") or cfg.get(
        "hook_validation_budget_seconds"
    )

    return ResolvedBackend(
        backend=backend,
        model=model,
        url=url,
        fallback_url=fallback_url,
        timeout=timeout,
        max_tokens=max_tokens,
        connect_timeout=connect_timeout,
        probe_timeout=probe_timeout,
        hook_validation_budget=hook_validation_budget,
    )


def make_client(
    resolved: ResolvedBackend,
    timeout: int = 120,
    fallback: str | None = None,
    connect_timeout: int | None = None,
    probe_timeout: int | None = None,
):
    """Build the transport client for a resolved backend.

    Returns an OpenAiClient for the openai backend, else an OllamaClient.
    connect_timeout/probe_timeout: explicit value wins; else falls back to the
    resolved backend's config value; else defaults to 3.
    """
    fallback_url = fallback if fallback is not None else resolved.fallback_url
    ct = connect_timeout if connect_timeout is not None else (resolved.connect_timeout or 3)
    pt = probe_timeout if probe_timeout is not None else (resolved.probe_timeout or 3)
    if resolved.backend == "openai":
        return OpenAiClient(
            base_url=resolved.url,
            fallback_base_url=fallback_url,
            timeout=timeout,
            connect_timeout=ct,
            probe_timeout=pt,
        )
    return OllamaClient(
        url=resolved.url,
        fallback_url=fallback_url,
        timeout=timeout,
        connect_timeout=ct,
        probe_timeout=pt,
    )
