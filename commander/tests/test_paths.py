"""Unit tests for the path-resolution seam.

Every assertion is an EXACT path, never a prefix: a startswith() assertion
against a redirected home passes for any path segment and would not fail if an
accessor pointed at the wrong directory.
"""
from ironclaude import paths


class TestHome:
    def test_honors_ironclaude_home(self, tmp_path, monkeypatch):
        monkeypatch.setenv("IRONCLAUDE_HOME", str(tmp_path / "fake"))
        assert paths.home() == tmp_path / "fake"

    def test_falls_back_to_path_home(self, tmp_path, monkeypatch):
        monkeypatch.delenv("IRONCLAUDE_HOME", raising=False)
        monkeypatch.setenv("HOME", str(tmp_path / "viahome"))
        assert paths.home() == tmp_path / "viahome"

    def test_ironclaude_home_wins_over_home(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path / "viahome"))
        monkeypatch.setenv("IRONCLAUDE_HOME", str(tmp_path / "explicit"))
        assert paths.home() == tmp_path / "explicit"


class TestHooksConfig:
    def test_default(self, tmp_path, monkeypatch):
        monkeypatch.delenv("IC_OLLAMA_CONFIG_PATH", raising=False)
        monkeypatch.setenv("IRONCLAUDE_HOME", str(tmp_path))
        assert paths.hooks_config() == str(
            tmp_path / ".claude" / "ironclaude-hooks-config.json"
        )

    def test_honors_ic_ollama_config_path(self, tmp_path, monkeypatch):
        monkeypatch.setenv("IRONCLAUDE_HOME", str(tmp_path))
        monkeypatch.setenv("IC_OLLAMA_CONFIG_PATH", "/somewhere/else.json")
        assert paths.hooks_config() == "/somewhere/else.json"


class TestBrainSessionsDir:
    def test_default(self, tmp_path, monkeypatch):
        monkeypatch.setenv("IRONCLAUDE_HOME", str(tmp_path))
        assert paths.brain_sessions_dir() == str(
            tmp_path / ".ironclaude" / "brain-sessions"
        )


class TestAllowedLogPrefixes:
    def test_includes_tmp_varlog_and_home(self, tmp_path, monkeypatch):
        monkeypatch.setenv("IRONCLAUDE_HOME", str(tmp_path))
        assert paths.allowed_log_prefixes() == (
            "/tmp/",
            "/var/log/",
            str(tmp_path) + "/",
        )

    def test_resolves_per_call_not_at_import(self, tmp_path, monkeypatch):
        """The defect this seam removes: a constant frozen at import time."""
        monkeypatch.setenv("IRONCLAUDE_HOME", str(tmp_path / "first"))
        first = paths.allowed_log_prefixes()
        monkeypatch.setenv("IRONCLAUDE_HOME", str(tmp_path / "second"))
        assert paths.allowed_log_prefixes() != first


def test_hooks_config_expands_a_tilde_override(monkeypatch):
    """orchestrator_mcp.py applied expanduser to this override; paths returned it
    verbatim. A single-quoted `~/custom.json` therefore worked through one consumer
    and stayed literal through the other."""
    monkeypatch.setenv("IC_OLLAMA_CONFIG_PATH", "~/custom.json")
    resolved = paths.hooks_config()
    assert "~" not in resolved
    assert resolved.endswith("/custom.json")
