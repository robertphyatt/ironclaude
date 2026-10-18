import copy
from dataclasses import FrozenInstanceError

import pytest

from ironclaude.config import DEFAULTS

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
    assert cfg.model_for("codex", "opus", host="local") == "gpt-5.6-sol"


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
    assert cfg.model_for("codex", "haiku", host="local") == "gpt-5.6-luna"
    assert cfg.model_for("codex", "sonnet", host="local") == "gpt-5.6-terra"
    assert cfg.model_for("codex", "opus", host="local") == "gpt-5.6-sol"
    assert cfg.model_for("codex", "fable", host="local") == "gpt-6-astra"


def test_codex_fable_tier_resolves_to_configured_model(base_config):
    raw = base_config()
    raw["clients"]["codex"]["models"]["fable"] = "gpt-6-astra"
    cfg = parse_provider_config(raw)
    assert cfg.model_for("codex", "fable", host="local") == "gpt-6-astra"


def test_codex_models_missing_fable_is_rejected(base_config):
    raw = base_config()
    raw["clients"]["codex"]["models"].pop("fable", None)
    with pytest.raises(ProviderConfigError):
        parse_provider_config(raw)


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


def test_codex_model_values_must_match_tier_word(base_config):
    raw = base_config()
    raw["clients"]["codex"]["models"]["opus"] = "wrong-model"
    with pytest.raises(ProviderConfigError, match="gpt-<version>-<tier-word>"):
        parse_provider_config(raw)


def test_codex_accepts_gpt6_models(base_config):
    raw = base_config()
    raw["clients"]["codex"]["models"] = {
        "haiku": "gpt-6-luna", "sonnet": "gpt-5.6-terra", "opus": "gpt-6-sol", "fable": "gpt-6-astra",
    }
    cfg = parse_provider_config(raw)
    assert cfg.model_for("codex", "opus", host="local") == "gpt-6-sol"
    assert cfg.model_for("codex", "haiku", host="local") == "gpt-6-luna"


def test_codex_accepts_future_versions(base_config):
    raw = base_config()
    raw["clients"]["codex"]["models"]["opus"] = "gpt-7.1-sol"
    assert parse_provider_config(raw).model_for("codex", "opus", host="local") == "gpt-7.1-sol"


@pytest.mark.parametrize(
    ("tier", "value"),
    [
        ("sonnet", "gpt-6-sol"),
        ("opus", "gpt-6-sol-mini"),
        ("opus", "GPT-6-sol"),
        ("haiku", "gpt--luna"),
        ("fable", None),
        ("sonnet", "sol"),
        ("opus", "Sol"),
        ("opus", "sol-mini"),
        ("haiku", "gpt-luna"),
    ],
)
def test_codex_rejects_wrong_tier_word_or_shape(tier, value, base_config):
    raw = base_config()
    raw["clients"]["codex"]["models"][tier] = value
    with pytest.raises(ProviderConfigError, match="gpt-<version>-<tier-word>"):
        parse_provider_config(raw)


def test_codex_rejects_extra_tier(base_config):
    raw = base_config()
    raw["clients"]["codex"]["models"]["mega"] = "gpt-6-astra"
    with pytest.raises(ProviderConfigError, match="gpt-<version>-<tier-word>"):
        parse_provider_config(raw)


def test_claude_models_remain_exact(base_config):
    raw = base_config()
    raw["clients"]["claude"]["models"]["opus"] = "claude-opus-4-8"
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
    assert cfg.model_for("claude", "sonnet", "brain", host="local") == "company-brain-alias"
    assert cfg.model_for("claude", "opus", "grader", host="local") == "claude-opus-4-7"
    assert cfg.model_for("claude", "opus", "worker", host="local") == "claude-opus-4-7"
    assert cfg.model_for("claude", "opus", "advisor", host="local") == "company-advisor-alias"
    assert cfg.advisor_model_for("claude", "claude-sonnet", "opus", host="local") == "company-opus-advisor"
    assert cfg.advisor_model_for("claude", "claude-opus", "fable", host="local") == "company-fable-advisor"


def test_ambiguous_legacy_alias_uses_historical_role_tier(base_config):
    commander = {
        "providers": base_config(),
        "brain_model": "corp-sonnet-opus-router",
        "grader_model": "grader-alias",
        "default_opus_model": "worker-alias",
        "advisor": {"advisor_model": "advisor-alias"},
    }
    cfg = provider_config_from_commander(commander)
    assert cfg.model_for("claude", "sonnet", "brain", host="local") == "corp-sonnet-opus-router"
    assert cfg.model_for("claude", "opus", "grader", host="local") == "grader-alias"
    assert cfg.model_for("claude", "opus", "worker", host="local") == "worker-alias"
    assert cfg.model_for("claude", "opus", "advisor", host="local") == "advisor-alias"


def test_embedded_tier_text_does_not_reclassify_opaque_alias(base_config):
    commander = {
        "providers": base_config(),
        "brain_model": "corp-opuslike-router",
        "grader_model": "grader-alias",
        "default_opus_model": "worker-alias",
        "advisor": {"advisor_model": "advisor-alias"},
    }
    cfg = provider_config_from_commander(commander)
    assert cfg.model_for("claude", "sonnet", "brain", host="local") == "corp-opuslike-router"


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


def test_default_advisor_class_for_claude_opus_worker_is_opus():
    cfg = provider_config_from_commander(copy.deepcopy(DEFAULTS))
    assert cfg.advisor_model_for("claude", "claude-opus", "opus", host="local") == "opus"


def test_default_advisor_class_for_codex_opus_worker_is_sol():
    cfg = provider_config_from_commander(copy.deepcopy(DEFAULTS))
    assert cfg.advisor_model_for("codex", "claude-opus", "opus", host="local") == "gpt-5.6-sol"


# ── Codex family words, no pinned models (design 2026-10-17 v120-completeness R2) ──

_WORDS = {"haiku": "luna", "sonnet": "terra", "opus": "sol", "fable": "astra"}


def _codex_cache(directory, slugs):
    import json
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "models_cache.json").write_text(json.dumps(
        {"models": [{"slug": slug} for slug in slugs] + [{"slug": None}, {}]}))
    return directory


def test_codex_defaults_are_family_words():
    from ironclaude import provider_config
    assert DEFAULTS["providers"]["clients"]["codex"]["models"] == _WORDS
    assert "codex" not in provider_config.EXPECTED_MODELS


def test_codex_accepts_family_words_for_their_tier(base_config):
    raw = base_config()
    raw["clients"]["codex"]["models"] = dict(_WORDS)
    parse_provider_config(raw)
    raw["clients"]["codex"]["models"]["opus"] = "gpt-7.2-sol"  # a word and a slug may mix
    parse_provider_config(raw)


def test_codex_family_word_resolves_to_newest_installed_and_slug_wins(base_config, tmp_path, monkeypatch):
    home = _codex_cache(tmp_path / "codexhome", [
        "gpt-5.9-luna", "gpt-5.10-luna", "gpt-6-sol", "gpt-6.1-sol", "gpt-reserve", "codex-auto-review"])
    monkeypatch.setenv("CODEX_HOME", str(home))
    raw = base_config()
    raw["clients"]["codex"]["models"] = {**_WORDS, "fable": "gpt-6-astra"}
    cfg = parse_provider_config(raw)
    assert cfg.model_for("codex", "haiku", host="local") == "gpt-5.10-luna"  # 5.10 > 5.9 numerically
    assert cfg.model_for("codex", "opus", host="local") == "gpt-6.1-sol"
    assert cfg.advisor_model_for("codex", "claude-opus", "opus", host="local") == "gpt-6.1-sol"
    assert cfg.model_for("codex", "fable", host="local") == "gpt-6-astra"  # explicit slug, not installed: as-is
    assert cfg.model_for("claude", "haiku", host="local") == "haiku"  # Claude is untouched
    # An explicit slug wins over a newer installed model, locally and remotely.
    slugs = parse_provider_config(base_config())
    assert slugs.model_for("codex", "opus", host="local") == "gpt-5.6-sol"
    assert slugs.model_for("codex", "opus", host="box-1") == "gpt-5.6-sol"


def test_codex_family_with_no_installed_model_is_unavailable(base_config, tmp_path, monkeypatch):
    from ironclaude.provider_config import CodexModelUnavailable, resolve_codex_model
    home = _codex_cache(tmp_path / "codexhome", ["gpt-6-sol"])
    monkeypatch.setenv("CODEX_HOME", str(home))
    raw = base_config()
    raw["clients"]["codex"]["models"] = dict(_WORDS)
    cfg = parse_provider_config(raw)
    with pytest.raises(CodexModelUnavailable) as exc:
        cfg.model_for("codex", "sonnet", host="local")
    assert exc.value.word == "terra"
    assert str(home / "models_cache.json") in str(exc.value)
    for text in (None, "{not json", "[1, 2]", '{"models": "x"}', '{"models": [{"slug": "gpt-6-sol"}]}'):
        with pytest.raises(CodexModelUnavailable):
            resolve_codex_model("luna", text, "somewhere")
    assert resolve_codex_model("gpt-6-luna", None, "somewhere") == "gpt-6-luna"  # a slug needs no cache


def test_remote_family_word_resolves_from_the_remote_cache(base_config, tmp_path, monkeypatch):
    import json
    from ironclaude.provider_config import CodexModelUnavailable
    monkeypatch.setenv("CODEX_HOME", str(_codex_cache(tmp_path / "local", ["gpt-6.2-sol"])))
    raw = base_config()
    raw["clients"]["codex"]["models"] = dict(_WORDS)
    cfg = parse_provider_config(raw)
    seen = []

    def remote(host):
        seen.append(host)
        return json.dumps({"models": [{"slug": "gpt-6-sol"}, {"slug": "gpt-5.6-sol"}]})

    assert cfg.model_for("codex", "opus", host="box-1", codex_cache=remote) == "gpt-6-sol"
    assert seen == ["box-1"]
    with pytest.raises(CodexModelUnavailable) as exc:  # no remote reader: never this Mac's cache
        cfg.model_for("codex", "opus", host="box-1")
    assert "box-1:" in str(exc.value)


def test_local_codex_cache_defaults_to_home_dot_codex(tmp_path, monkeypatch):
    import os
    from pathlib import Path
    from ironclaude.provider_config import resolve_local_codex_model
    monkeypatch.delenv("CODEX_HOME", raising=False)
    _codex_cache(Path(os.environ["HOME"]) / ".codex", ["gpt-6-luna", "gpt-5.6-luna"])
    assert resolve_local_codex_model("luna") == "gpt-6-luna"


def test_codex_home_holds_only_the_fixture_cache():
    """conftest _redirect_home gives every test a CODEX_HOME holding only the fixture cache, so a
    developer's installed Codex models never change what the suite resolves."""
    import json
    import os
    home = os.environ["CODEX_HOME"]
    assert os.listdir(home) == ["models_cache.json"]
    with open(os.path.join(home, "models_cache.json")) as handle:
        assert [m["slug"] for m in json.load(handle)["models"]] == [
            "gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.6-sol", "gpt-6-astra"]
    cfg = provider_config_from_commander(copy.deepcopy(DEFAULTS))
    assert cfg.model_for("codex", "haiku", host="local") == "gpt-5.6-luna"


def test_model_for_requires_host_keyword(base_config):
    """Design 2026-10-17 v120-completeness R2: a caller that omits host fails instead of silently
    resolving on this Mac."""
    cfg = parse_provider_config(base_config())
    with pytest.raises(TypeError):
        cfg.model_for("codex", "opus")
    with pytest.raises(TypeError):
        cfg.advisor_model_for("codex", "claude-opus", "opus")
    assert cfg.model_for("codex", "opus", host="local") == "gpt-5.6-sol"  # positive control
