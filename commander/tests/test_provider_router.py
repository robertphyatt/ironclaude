from dataclasses import FrozenInstanceError

import pytest

from ironclaude.db import init_db
from ironclaude.provider_capabilities import CapabilityRegistry, ClientCapability
from ironclaude.provider_config import provider_config_from_commander, parse_provider_config
from ironclaude.provider_router import NoCapabilityAvailable, ProviderRouter
from ironclaude.provider_state import ProviderState


def setup_router(tmp_path, base_config, *, dual=False):
    raw = base_config()
    if dual:
        raw["clients"]["codex"]["enabled"] = True
        for role in raw["roles"].values():
            role["clients"] = ["claude", "codex"]
    config = parse_provider_config(raw)
    state = ProviderState(init_db(str(tmp_path / "commander.db")))
    registry = CapabilityRegistry(state)
    return ProviderRouter(config, state, registry), state, registry


def cap(client, role, tier, *, usable=True, host="local"):
    return ClientCapability(
        host=host,
        client=client,
        role=role,
        tier=tier,
        configured=True,
        supported=True,
        installed=usable,
        authenticated=True if usable else False,
        reason=None if usable else "unavailable",
        available=usable,
    )


def record(registry, *capabilities):
    for capability in capabilities:
        registry.record(capability)


def test_handle_is_immutable(tmp_path, base_config):
    router, _, registry = setup_router(tmp_path, base_config)
    record(registry, cap("claude", "brain", "sonnet"))
    handle = router.resolve("brain", "sonnet", ["local"])
    with pytest.raises(FrozenInstanceError):
        handle.client = "codex"


def test_claude_only_default(tmp_path, base_config):
    router, _, registry = setup_router(tmp_path, base_config)
    record(registry, cap("claude", "brain", "sonnet"))
    handle = router.resolve("brain", "sonnet", ["local"])
    assert (handle.client, handle.model) == ("claude", "sonnet")


def test_router_uses_legacy_role_model_override(tmp_path, base_config):
    config = provider_config_from_commander({
        "providers": base_config(),
        "brain_model": "claude-sonnet-4-5",
        "grader_model": "opus",
        "default_opus_model": "opus",
        "advisor": {"advisor_model": "opus"},
    })
    state = ProviderState(init_db(str(tmp_path / "commander.db")))
    registry = CapabilityRegistry(state)
    record(registry, cap("claude", "brain", "sonnet"))
    handle = ProviderRouter(config, state, registry).resolve(
        "brain", "sonnet", ["local"]
    )
    assert handle.model == "claude-sonnet-4-5"


def test_router_uses_legacy_advisor_worker_model_override(tmp_path, base_config):
    config = provider_config_from_commander({
        "providers": base_config(),
        "brain_model": "sonnet",
        "grader_model": "opus",
        "default_opus_model": "opus",
        "advisor": {
            "advisor_model": "opus",
            "advisor_models": {"claude-sonnet": "company-opus-advisor"},
        },
    })
    state = ProviderState(init_db(str(tmp_path / "commander.db")))
    registry = CapabilityRegistry(state)
    record(registry, cap("claude", "advisor", "opus"))
    handle = ProviderRouter(config, state, registry).resolve(
        "advisor", "opus", ["local"], advisor_worker_type="claude-sonnet"
    )
    assert handle.model == "company-opus-advisor"


def test_codex_is_never_selected_when_not_explicitly_enabled(tmp_path, base_config):
    router, _, registry = setup_router(tmp_path, base_config)
    record(
        registry,
        cap("claude", "worker", "sonnet"),
        cap("codex", "worker", "sonnet"),
    )
    handle = router.resolve("worker", "sonnet", ["local"])
    assert handle.client == "claude"


def test_codex_only_role_routes_without_claude(tmp_path, base_config):
    raw = base_config()
    raw["clients"]["claude"]["enabled"] = False
    raw["clients"]["codex"]["enabled"] = True
    for role in raw["roles"].values():
        role["preferred"] = "codex"
        role["clients"] = ["codex"]
    config = parse_provider_config(raw)
    state = ProviderState(init_db(str(tmp_path / "commander.db")))
    registry = CapabilityRegistry(state)
    record(registry, cap("codex", "worker", "sonnet"))
    handle = ProviderRouter(config, state, registry).resolve(
        "worker", "sonnet", ["local"]
    )
    assert (handle.client, handle.model) == ("codex", "gpt-5.6-terra")


def test_configured_codex_preference_wins_without_sticky_state(tmp_path, base_config):
    raw = base_config()
    raw["clients"]["codex"]["enabled"] = True
    raw["roles"]["worker"] = {
        "preferred": "codex", "clients": ["claude", "codex"],
    }
    config = parse_provider_config(raw)
    state = ProviderState(init_db(str(tmp_path / "commander.db")))
    registry = CapabilityRegistry(state)
    record(
        registry,
        cap("claude", "worker", "sonnet"),
        cap("codex", "worker", "sonnet"),
    )
    handle = ProviderRouter(config, state, registry).resolve(
        "worker", "sonnet", ["local"]
    )
    assert (handle.client, handle.model) == ("codex", "gpt-5.6-terra")


def test_sticky_codex_assignment_wins_when_usable(tmp_path, base_config):
    router, state, registry = setup_router(tmp_path, base_config, dual=True)
    state.set_current_client("brain", "codex")
    record(
        registry,
        cap("claude", "brain", "sonnet"),
        cap("codex", "brain", "sonnet"),
    )
    handle = router.resolve("brain", "sonnet", ["local"])
    assert (handle.client, handle.model) == ("codex", "gpt-5.6-terra")


def test_unavailable_current_falls_through_without_mutating_state(tmp_path, base_config):
    router, state, registry = setup_router(tmp_path, base_config, dual=True)
    state.set_current_client("worker", "claude")
    record(
        registry,
        cap("claude", "worker", "sonnet"),
        cap("codex", "worker", "sonnet"),
    )
    state.mark_unavailable(
        "local", "claude", "worker", "sonnet", "usage_limit", "limit"
    )
    handle = router.resolve("worker", "sonnet", ["local"])
    assert handle.client == "codex"
    assert state.get_current_client("worker") == "claude"


def test_explicit_cutover_fresh_success_routes_selected_client(tmp_path, base_config):
    router, state, registry = setup_router(tmp_path, base_config, dual=True)
    state.mark_unavailable(
        "local", "codex", "worker", "sonnet", "usage_limit", "old limit"
    )

    state.set_current_client("worker", "codex", reset_capabilities=True)
    record(
        registry,
        cap("claude", "worker", "sonnet"),
        cap("codex", "worker", "sonnet"),
    )

    assert router.resolve("worker", "sonnet", ["local"]).client == "codex"


def test_explicit_cutover_fresh_failure_uses_existing_fallback(tmp_path, base_config):
    router, state, registry = setup_router(tmp_path, base_config, dual=True)
    state.mark_unavailable(
        "local", "codex", "worker", "sonnet", "usage_limit", "old limit"
    )

    state.set_current_client("worker", "codex", reset_capabilities=True)
    record(
        registry,
        cap("claude", "worker", "sonnet"),
        cap("codex", "worker", "sonnet", usable=False),
    )

    assert router.resolve("worker", "sonnet", ["local"]).client == "claude"
    assert state.get_current_client("worker") == "codex"


def test_router_selects_first_eligible_host_with_capability(tmp_path, base_config):
    router, _, registry = setup_router(tmp_path, base_config)
    record(
        registry,
        cap("claude", "worker", "sonnet", usable=False, host="host-a"),
        cap("claude", "worker", "sonnet", host="host-b"),
    )
    handle = router.resolve("worker", "sonnet", ["host-a", "host-b"])
    assert handle.host == "host-b"


def test_sticky_client_is_tried_across_hosts_before_fallback(tmp_path, base_config):
    router, state, registry = setup_router(tmp_path, base_config, dual=True)
    state.set_current_client("worker", "codex")
    record(
        registry,
        cap("claude", "worker", "sonnet", host="host-a"),
        cap("codex", "worker", "sonnet", host="host-b"),
    )
    handle = router.resolve("worker", "sonnet", ["host-a", "host-b"])
    assert (handle.client, handle.host) == ("codex", "host-b")


def test_single_eligible_host_is_an_explicit_host_constraint(tmp_path, base_config):
    router, _, registry = setup_router(tmp_path, base_config)
    record(
        registry,
        cap("claude", "worker", "sonnet", host="host-a"),
        cap("claude", "worker", "sonnet", host="host-b"),
    )
    assert router.resolve("worker", "sonnet", ["host-b"]).host == "host-b"


def test_sticky_claude_preserves_fable(tmp_path, base_config):
    router, _, registry = setup_router(tmp_path, base_config, dual=True)
    record(
        registry,
        cap("claude", "worker", "fable"),
        cap("claude", "worker", "opus"),
        cap("codex", "worker", "opus"),
    )
    handle = router.resolve("worker", "fable", ["local"])
    assert (handle.client, handle.effective_tier, handle.model) == (
        "claude", "fable", "fable"
    )


def test_sticky_claude_fable_degrades_to_claude_opus(tmp_path, base_config):
    router, state, registry = setup_router(tmp_path, base_config, dual=True)
    record(
        registry,
        cap("claude", "worker", "fable"),
        cap("claude", "worker", "opus"),
        cap("codex", "worker", "opus"),
    )
    state.mark_unavailable(
        "local", "claude", "worker", "fable", "model_unavailable", "fable down"
    )
    handle = router.resolve("worker", "fable", ["local"])
    assert (handle.client, handle.requested_tier, handle.effective_tier) == (
        "claude", "fable", "opus"
    )


def test_sticky_codex_fable_request_stays_codex_opus(tmp_path, base_config):
    router, state, registry = setup_router(tmp_path, base_config, dual=True)
    state.set_current_client("worker", "codex")
    record(
        registry,
        cap("claude", "worker", "fable"),
        cap("claude", "worker", "opus"),
        cap("codex", "worker", "opus"),
    )
    handle = router.resolve("worker", "fable", ["local"])
    assert (handle.client, handle.effective_tier, handle.model) == (
        "codex", "opus", "gpt-5.6-sol"
    )
    assert state.get_current_client("worker") == "codex"


def test_stale_sticky_client_normalizes_to_configured_preference(tmp_path, base_config):
    config = parse_provider_config(base_config())
    state = ProviderState(init_db(str(tmp_path / "commander.db")))
    registry = CapabilityRegistry(state)
    state.set_current_client("worker", "codex")
    record(registry, cap("claude", "worker", "fable"))
    router = ProviderRouter(config, state, registry)
    handle = router.resolve("worker", "fable", ["local"])
    assert (handle.client, handle.effective_tier) == ("claude", "fable")
    assert state.get_current_client("worker") == "codex"


def test_sticky_claude_fable_crosses_only_after_claude_opus_unavailable(
    tmp_path, base_config
):
    router, state, registry = setup_router(tmp_path, base_config, dual=True)
    record(
        registry,
        cap("claude", "worker", "fable"),
        cap("claude", "worker", "opus"),
        cap("codex", "worker", "opus"),
    )
    state.mark_unavailable(
        "local", "claude", "worker", "fable", "model_unavailable", "fable down"
    )
    state.mark_unavailable(
        "local", "claude", "worker", "opus", "usage_limit", "opus limited"
    )
    handle = router.resolve("worker", "fable", ["local"])
    assert (handle.client, handle.effective_tier) == ("codex", "opus")


def test_capability_auth_failure_falls_through(tmp_path, base_config):
    router, state, registry = setup_router(tmp_path, base_config, dual=True)
    state.set_current_client("grader", "codex")
    record(
        registry,
        cap("codex", "grader", "opus", usable=False),
        cap("claude", "grader", "opus"),
    )
    handle = router.resolve("grader", "opus", ["local"])
    assert handle.client == "claude"


def test_both_unavailable_raises_with_context(tmp_path, base_config):
    router, _, registry = setup_router(tmp_path, base_config, dual=True)
    record(
        registry,
        cap("claude", "brain", "sonnet", usable=False),
        cap("codex", "brain", "sonnet", usable=False),
    )
    with pytest.raises(NoCapabilityAvailable) as exc:
        router.resolve("brain", "sonnet", ["local"])
    assert exc.value.role == "brain"
    assert exc.value.tier == "sonnet"
    assert exc.value.eligible_hosts == ("local",)


def test_capability_role_or_tier_mismatch_is_rejected(tmp_path, base_config):
    router, _, registry = setup_router(tmp_path, base_config)
    record(registry, cap("claude", "grader", "sonnet"))
    with pytest.raises(NoCapabilityAvailable):
        router.resolve("worker", "sonnet", ["local"])
