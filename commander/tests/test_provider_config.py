from dataclasses import FrozenInstanceError

import pytest

from ironclaude.provider_config import (
    ProviderConfigError,
    provider_config_from_commander,
    parse_provider_config,
    normalize_machine_clients,
)
def test_default_is_claude_only_and_shadow_off(base_config):
    cfg = parse_provider_config(base_config())
    assert cfg.roles["brain"].clients == ("claude",)
    assert cfg.clients["codex"].enabled is False
    assert cfg.shadow_mode is False


def test_global_codex_only_configuration(base_config):
    raw = base_config()
    raw["clients"]["claude"]["enabled"] = False
    raw["clients"]["codex"]["enabled"] = True
    for role in raw["roles"].values():
        role.update(preferred="codex", clients=["codex"])
    cfg = parse_provider_config(raw)
    assert all(role.clients == ("codex",) for role in cfg.roles.values())
    assert cfg.model_for("codex", "opus") == "gpt-5.6-sol"


def test_dual_client_role_requires_explicit_enablement(base_config):
    raw = base_config()
    raw["clients"]["codex"]["enabled"] = True
    raw["roles"]["worker"] = {
        "preferred": "claude",
        "clients": ["claude", "codex"],
    }
    cfg = parse_provider_config(raw)
    assert cfg.roles["worker"].clients == ("claude", "codex")


def test_installed_codex_is_not_implicitly_configured(base_config):
    cfg = parse_provider_config(base_config())
    assert "codex" not in cfg.roles["worker"].clients


def test_codex_models_match_approved_tiers(base_config):
    cfg = parse_provider_config(base_config())
    assert cfg.model_for("codex", "haiku") == "gpt-5.6-luna"
    assert cfg.model_for("codex", "sonnet") == "gpt-5.6-terra"
    assert cfg.model_for("codex", "opus") == "gpt-5.6-sol"


def test_codex_has_no_fable_alias(base_config):
    cfg = parse_provider_config(base_config())
    with pytest.raises(ProviderConfigError, match="fable"):
        cfg.model_for("codex", "fable")


@pytest.mark.parametrize("role", ["brain", "worker", "grader", "advisor"])
def test_all_required_roles_exist(role, base_config):
    assert role in parse_provider_config(base_config()).roles


def test_role_cannot_enable_globally_disabled_client(base_config):
    raw = base_config()
    raw["roles"]["worker"]["clients"].append("codex")
    with pytest.raises(ProviderConfigError, match="disabled"):
        parse_provider_config(raw)


def test_preferred_client_must_be_enabled_for_role(base_config):
    raw = base_config()
    raw["roles"]["worker"] = {"preferred": "codex", "clients": ["claude"]}
    with pytest.raises(ProviderConfigError, match="preferred"):
        parse_provider_config(raw)


def test_unknown_client_is_rejected(base_config):
    raw = base_config()
    raw["clients"]["other"] = {"enabled": True, "path": "other", "models": {}}
    with pytest.raises(ProviderConfigError, match="other"):
        parse_provider_config(raw)


@pytest.mark.parametrize("field", ["enabled", "shadow_mode"])
def test_boolean_fields_reject_strings(field, base_config):
    raw = base_config()
    if field == "enabled":
        raw["clients"]["codex"]["enabled"] = "false"
    else:
        raw["shadow_mode"] = "false"
    with pytest.raises(ProviderConfigError, match="boolean"):
        parse_provider_config(raw)


@pytest.mark.parametrize(
    ("client", "mutate"),
    [
        ("claude", lambda models: models.pop("fable")),
        ("codex", lambda models: models.pop("sonnet")),
        ("codex", lambda models: models.__setitem__("fable", "gpt-5.6-sol")),
        ("codex", lambda models: models.__setitem__("opus", 56)),
    ],
)
def test_model_contracts_are_validated_at_startup(client, mutate, base_config):
    raw = base_config()
    mutate(raw["clients"][client]["models"])
    with pytest.raises(ProviderConfigError, match="models"):
        parse_provider_config(raw)


def test_codex_model_values_are_exact(base_config):
    raw = base_config()
    raw["clients"]["codex"]["models"]["opus"] = "wrong-model"
    with pytest.raises(ProviderConfigError, match="approved models"):
        parse_provider_config(raw)


def test_unknown_role_is_rejected(base_config):
    raw = base_config()
    raw["roles"]["other-helper"] = {"preferred": "claude", "clients": ["claude"]}
    with pytest.raises(ProviderConfigError, match="roles"):
        parse_provider_config(raw)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda raw: raw.__setitem__("shadow", False),
        lambda raw: raw["clients"]["codex"].__setitem__("enable", True),
        lambda raw: raw["roles"]["worker"].__setitem__("client", ["claude"]),
    ],
)
def test_unknown_nested_keys_are_rejected(mutate, base_config):
    raw = base_config()
    mutate(raw)
    with pytest.raises(ProviderConfigError, match="unknown"):
        parse_provider_config(raw)


def test_parsed_configuration_is_deeply_immutable(base_config):
    cfg = parse_provider_config(base_config())
    with pytest.raises(TypeError):
        cfg.clients["claude"].models["sonnet"] = "other"
    with pytest.raises(TypeError):
        cfg.clients["other"] = cfg.clients["claude"]
    with pytest.raises(TypeError):
        cfg.roles["brain"] = cfg.roles["worker"]
    with pytest.raises(TypeError):
        cfg.advisor_worker_models["claude-sonnet"] = "other"
    with pytest.raises(FrozenInstanceError):
        cfg.shadow_mode = True


def test_legacy_claude_models_become_role_tier_overrides(base_config):
    commander = {
        "providers": base_config(),
        "brain_model": "company-brain-alias",
        "grader_model": "claude-opus-4-7",
        "default_opus_model": "claude-opus-4-7",
        "advisor": {
            "advisor_model": "company-advisor-alias",
            "advisor_models": {
                "claude-sonnet": "company-opus-advisor",
                "claude-opus": "company-fable-advisor",
            },
        },
    }
    cfg = provider_config_from_commander(commander)
    assert cfg.model_for("claude", "sonnet", "brain") == "company-brain-alias"
    assert cfg.model_for("claude", "opus", "grader") == "claude-opus-4-7"
    assert cfg.model_for("claude", "opus", "worker") == "claude-opus-4-7"
    assert cfg.model_for("claude", "opus", "advisor") == "company-advisor-alias"
    assert cfg.advisor_model_for("claude", "claude-sonnet", "opus") == "company-opus-advisor"
    assert cfg.advisor_model_for("claude", "claude-opus", "fable") == "company-fable-advisor"


def test_ambiguous_legacy_alias_uses_historical_role_tier(base_config):
    commander = {
        "providers": base_config(),
        "brain_model": "corp-sonnet-opus-router",
        "grader_model": "grader-alias",
        "default_opus_model": "worker-alias",
        "advisor": {"advisor_model": "advisor-alias"},
    }
    cfg = provider_config_from_commander(commander)
    assert cfg.model_for("claude", "sonnet", "brain") == "corp-sonnet-opus-router"
    assert cfg.model_for("claude", "opus", "grader") == "grader-alias"
    assert cfg.model_for("claude", "opus", "worker") == "worker-alias"
    assert cfg.model_for("claude", "opus", "advisor") == "advisor-alias"


def test_embedded_tier_text_does_not_reclassify_opaque_alias(base_config):
    commander = {
        "providers": base_config(),
        "brain_model": "corp-opuslike-router",
        "grader_model": "grader-alias",
        "default_opus_model": "worker-alias",
        "advisor": {"advisor_model": "advisor-alias"},
    }
    cfg = provider_config_from_commander(commander)
    assert cfg.model_for("claude", "sonnet", "brain") == "corp-opuslike-router"


def test_legacy_machine_becomes_claude_only():
    machine = {
        "name": "legacy",
        "host": "legacy",
        "repos": ["/repo"],
        "claude_path": "~/.claude/local/claude",
    }
    clients = normalize_machine_clients(machine)
    assert clients == {
        "claude": {"enabled": True, "path": "~/.claude/local/claude"}
    }


def test_codex_only_machine_needs_no_claude_path():
    machine = {
        "name": "codex-only",
        "host": "codex-only",
        "repos": ["/repo"],
        "clients": {"codex": {"enabled": True, "path": "/usr/local/bin/codex"}},
    }
    assert normalize_machine_clients(machine)["codex"]["enabled"] is True


def test_machine_requires_one_enabled_client():
    machine = {
        "name": "empty",
        "host": "empty",
        "repos": ["/repo"],
        "clients": {"codex": {"enabled": False, "path": "codex"}},
    }
    with pytest.raises(ProviderConfigError, match="enabled client"):
        normalize_machine_clients(machine)
