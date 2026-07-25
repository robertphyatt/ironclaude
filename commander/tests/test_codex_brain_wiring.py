"""Brain-client selection: codex is reachable ONLY via an explicit opt-in.

The provider-router route is deliberately closed. `provider_config.py:153-154`
raises `ProviderConfigError` for a role listing a globally-disabled client, and
`config.py:187` runs that validator unguarded inside `load_config()` (called at
`main.py:2713`). Adding "codex" to `roles.brain.clients` while
`clients.codex.enabled` is False would therefore crash the daemon at startup on
the stock config — which is why `TestDefaultsUnchanged` exists.
"""

from ironclaude.config import DEFAULTS
from ironclaude.brain_client import BrainClient
from ironclaude.codex_brain_client import CodexBrainClient
from ironclaude.main import select_brain_class


class TestBrainClientSelection:
    def test_unset_env_selects_claude(self, monkeypatch):
        monkeypatch.delenv("BRAIN_CLIENT", raising=False)
        assert select_brain_class() is BrainClient

    def test_codex_env_selects_codex(self, monkeypatch):
        monkeypatch.setenv("BRAIN_CLIENT", "codex")
        assert select_brain_class() is CodexBrainClient

    def test_unknown_value_falls_back_to_claude(self, monkeypatch):
        # Never fail to start over an unrecognised opt-in value.
        monkeypatch.setenv("BRAIN_CLIENT", "banana")
        assert select_brain_class() is BrainClient


class TestDefaultsUnchanged:
    def test_brain_role_still_claude_only(self):
        assert DEFAULTS["providers"]["roles"]["brain"]["clients"] == ["claude"]

    def test_codex_client_still_disabled_by_default(self):
        assert DEFAULTS["providers"]["clients"]["codex"]["enabled"] is False
