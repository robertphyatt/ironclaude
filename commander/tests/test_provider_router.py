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


def test_resolve_excludes_source_client_without_changing_default(tmp_path, base_config):
    router, state, registry = setup_router(tmp_path, base_config, dual=True)
    registry.record(cap("claude", "worker", "opus"))
    registry.record(cap("codex", "worker", "opus"))
    assert router.resolve("worker", "opus", ["local"], excluded_clients=("claude",)).client == "codex"
    assert state.current_client("worker", "claude") == "claude"


def test_handoff_exclusion_both_directions_and_fable_fallback(tmp_path, base_config):
    router, state, registry = setup_router(tmp_path, base_config, dual=True)
    record(
        registry,
        cap("claude", "worker", "fable"),
        cap("claude", "worker", "opus"),
        cap("codex", "worker", "opus"),
    )
    assert router.resolve(
        "worker", "fable", ["local"], excluded_clients=("claude",)
    ).client == "codex"
    state.set_current_client("worker", "codex")
    target = router.resolve(
        "worker", "fable", ["local"], excluded_clients=("codex",)
    )
    assert (target.client, target.effective_tier) == ("claude", "fable")
    assert state.get_current_client("worker") == "codex"
    with pytest.raises(NoCapabilityAvailable):
        router.resolve(
            "worker", "fable", ["local"], excluded_clients=("claude", "codex")
        )


def test_worker_account_usage_quarantine_blocks_fable_opus_fallback(tmp_path, base_config):
    router, state, registry = setup_router(tmp_path, base_config, dual=True)
    record(
        registry,
        cap("claude", "worker", "fable"),
        cap("claude", "worker", "opus"),
        cap("codex", "worker", "opus"),
    )
    state.mark_unavailable(
        "local", "claude", "worker", state.worker_usage_scope_tier("worker", "fable"),
        "usage", "account quota", usage_generation="native-old", usage_cursor=1,
        usage_event_key="quota", usage_event_timestamp="2026-10-06T12:00:02Z",
    )
    resolved = router.resolve("worker", "fable", ["local"])
    assert (resolved.client, resolved.effective_tier) == ("codex", "opus")


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


def test_sticky_codex_fable_request_selects_astra(tmp_path, base_config):
    router, state, registry = setup_router(tmp_path, base_config, dual=True)
    state.set_current_client("worker", "codex")
    record(
        registry,
        cap("claude", "worker", "fable"),
        cap("claude", "worker", "opus"),
        cap("codex", "worker", "fable"),
        cap("codex", "worker", "opus"),
    )
    handle = router.resolve("worker", "fable", ["local"])
    assert (handle.client, handle.effective_tier, handle.model) == (
        "codex", "fable", "gpt-6-astra"
    )
    assert state.get_current_client("worker") == "codex"


def test_unavailable_codex_astra_falls_to_codex_sol_before_claude(tmp_path, base_config):
    router, state, registry = setup_router(tmp_path, base_config, dual=True)
    state.set_current_client("worker", "codex")
    record(
        registry,
        cap("codex", "worker", "fable"),
        cap("codex", "worker", "opus"),
        cap("claude", "worker", "fable"),
        cap("claude", "worker", "opus"),
    )
    state.mark_unavailable(
        "local", "codex", "worker", "fable", "model_unavailable", "astra down"
    )
    handle = router.resolve("worker", "fable", ["local"])
    assert (handle.client, handle.effective_tier, handle.model) == (
        "codex", "opus", "gpt-5.6-sol"
    )


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


def test_sticky_claude_fable_crosses_to_other_provider_fable_before_opus(
    tmp_path, base_config
):
    router, state, registry = setup_router(tmp_path, base_config, dual=True)
    record(
        registry,
        cap("claude", "worker", "fable"),
        cap("claude", "worker", "opus"),
        cap("codex", "worker", "fable"),
        cap("codex", "worker", "opus"),
    )
    state.mark_unavailable(
        "local", "claude", "worker", "fable", "model_unavailable", "fable down"
    )
    state.mark_unavailable(
        "local", "claude", "worker", "opus", "usage_limit", "opus limited"
    )
    handle = router.resolve("worker", "fable", ["local"])
    assert (handle.client, handle.effective_tier, handle.model) == (
        "codex", "fable", "gpt-6-astra"
    )


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


# ── Codex family words resolve where Codex runs (design 2026-10-17 v120-completeness R2) ──

def test_codex_family_words_resolve_where_codex_runs_and_unavailable_falls_through(
    tmp_path, base_config, monkeypatch, caplog,
):
    home = tmp_path / "codexhome"
    home.mkdir()
    (home / "models_cache.json").write_text('{"models": [{"slug": "gpt-6.1-sol"}, {"slug": "gpt-6-sol"}]}')
    monkeypatch.setenv("CODEX_HOME", str(home))
    raw = base_config()
    raw["clients"]["codex"]["enabled"] = True
    raw["clients"]["codex"]["models"] = {"haiku": "luna", "sonnet": "terra", "opus": "sol", "fable": "astra"}
    for role in raw["roles"].values():
        role["clients"] = ["claude", "codex"]
    state = ProviderState(init_db(str(tmp_path / "commander.db")))
    registry = CapabilityRegistry(state)
    router = ProviderRouter(parse_provider_config(raw), state, registry)
    state.set_current_client("worker", "codex")
    state.set_current_client("advisor", "codex")
    record(
        registry,
        cap("codex", "worker", "opus"),
        cap("codex", "worker", "opus", host="box-1"),
        cap("codex", "advisor", "opus"),
        cap("codex", "worker", "sonnet"),
        cap("claude", "worker", "sonnet"),
    )
    local = router.resolve("worker", "opus", ["local"])
    remote = router.resolve("worker", "opus", ["box-1"],
                            codex_cache=lambda host: '{"models": [{"slug": "gpt-6-sol"}]}')
    assert (local.client, local.host, local.model) == ("codex", "local", "gpt-6.1-sol")
    assert (remote.client, remote.host, remote.model) == ("codex", "box-1", "gpt-6-sol")
    assert router.resolve("advisor", "opus", ["local"], advisor_worker_type="claude-opus").model == "gpt-6.1-sol"
    # No Terra installed: Codex is unavailable for the call and routing falls to Claude.
    fallback = router.resolve("worker", "sonnet", ["local"])
    assert (fallback.client, fallback.model) == ("claude", "sonnet")
    assert "'terra'" in caplog.text and str(home / "models_cache.json") in caplog.text
