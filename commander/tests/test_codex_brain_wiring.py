"""Brain-client selection honors persisted operator choice across restarts."""

from copy import deepcopy

from ironclaude.config import DEFAULTS
from ironclaude.brain_client import BrainClient
from ironclaude.codex_brain_client import CodexBrainClient
from ironclaude.db import init_db
from ironclaude.main import select_brain_class
from ironclaude.provider_state import ProviderState


class TestBrainClientSelection:
    def test_persisted_codex_survives_restart_without_env(self, monkeypatch):
        conn = init_db(":memory:")
        ProviderState(conn).set_current_client("brain", "codex")
        monkeypatch.delenv("BRAIN_CLIENT", raising=False)
        assert select_brain_class(deepcopy(DEFAULTS), conn) is CodexBrainClient

    def test_persisted_claude_overrides_stale_codex_env(self, monkeypatch):
        conn = init_db(":memory:")
        ProviderState(conn).set_current_client("brain", "claude")
        monkeypatch.setenv("BRAIN_CLIENT", "codex")
        assert select_brain_class(deepcopy(DEFAULTS), conn) is BrainClient

    def test_legacy_codex_env_seeds_sticky_state_under_stock_config(
        self, monkeypatch
    ):
        conn = init_db(":memory:")
        monkeypatch.setenv("BRAIN_CLIENT", "codex")
        assert select_brain_class(deepcopy(DEFAULTS), conn) is CodexBrainClient
        assert ProviderState(conn).get_current_client("brain") == "codex"

        monkeypatch.delenv("BRAIN_CLIENT")
        assert select_brain_class(deepcopy(DEFAULTS), conn) is CodexBrainClient

    def test_unknown_value_falls_back_to_claude(self, monkeypatch):
        conn = init_db(":memory:")
        monkeypatch.setenv("BRAIN_CLIENT", "banana")
        assert select_brain_class(deepcopy(DEFAULTS), conn) is BrainClient
        assert ProviderState(conn).get_current_client("brain") == "claude"

    def test_configured_preference_is_persisted(self, monkeypatch):
        conn = init_db(":memory:")
        config = deepcopy(DEFAULTS)
        config["providers"]["clients"]["codex"]["enabled"] = True
        config["providers"]["roles"]["brain"] = {
            "preferred": "codex",
            "clients": ["claude", "codex"],
        }
        monkeypatch.delenv("BRAIN_CLIENT", raising=False)
        assert select_brain_class(config, conn) is CodexBrainClient
        assert ProviderState(conn).get_current_client("brain") == "codex"

    def test_persisted_codex_survives_config_removal_and_stale_claude_env(
        self, monkeypatch
    ):
        conn = init_db(":memory:")
        ProviderState(conn).set_current_client("brain", "codex")
        monkeypatch.setenv("BRAIN_CLIENT", "claude")
        assert select_brain_class(deepcopy(DEFAULTS), conn) is CodexBrainClient


class TestDefaultsUnchanged:
    def test_brain_role_still_claude_only(self):
        assert DEFAULTS["providers"]["roles"]["brain"]["clients"] == ["claude"]

    def test_codex_client_still_disabled_by_default(self):
        assert DEFAULTS["providers"]["clients"]["codex"]["enabled"] is False
