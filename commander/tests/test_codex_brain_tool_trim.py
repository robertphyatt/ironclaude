"""R14: the Codex Brain's tool set matches the Claude Brain's — inherited plugins, user
MCP servers and ironclaude worker MCP servers are disabled per start, read-only."""
import json
from pathlib import Path

import pytest

from ironclaude.codex_brain_tool_trim import (
    FIXED_OVERRIDES,
    BrainToolTrimError,
    brain_tool_trim_overrides,
)

BRAIN = frozenset({"orchestrator", "research", "ollama"})
REPO_MANIFEST = Path(__file__).parents[2] / "worker" / ".codex-plugin" / "plugin.json"


def _manifest(tmp_path, servers=("episodic-memory", "state-manager", "workspace-manager")):
    path = tmp_path / "plugin.json"
    path.write_text(json.dumps({"mcpServers": {name: {"command": "node"} for name in servers}}))
    return path


def _values(pairs):
    assert pairs[0::2] == ["-c"] * (len(pairs) // 2)
    return pairs[1::2]


def test_missing_config_gives_fixed_and_manifest_overrides(tmp_path):
    values = _values(brain_tool_trim_overrides(tmp_path / "absent.toml", _manifest(tmp_path), BRAIN))
    assert values == [
        *FIXED_OVERRIDES,
        "plugins.ironclaude@ironclaude.mcp_servers.state-manager.enabled=false",
        "plugins.ironclaude@ironclaude.mcp_servers.workspace-manager.enabled=false",
    ]


def test_fixed_overrides_turn_off_apps_web_image_goals():
    assert FIXED_OVERRIDES == (
        "features.apps=false",
        "features.tool_suggest=false",
        "features.image_generation=false",
        "features.goals=false",
        'web_search="disabled"',
    )


def test_every_plugin_except_ironclaude_is_disabled(tmp_path):
    config = tmp_path / "config.toml"
    config.write_text(
        '[plugins."slack@openai-curated"]\nenabled = true\n'
        '[plugins."ironclaude@ironclaude"]\nenabled = true\n'
        '[plugins."ironclaude@ironclaude".mcp_servers.state-manager.tools.create_plan]\n'
        'approval_mode = "approve"\n'
        '[plugins."unified-computer-use@openai-bundled"]\nenabled = true\n'
    )
    values = _values(brain_tool_trim_overrides(config, _manifest(tmp_path, ("episodic-memory",)), BRAIN))
    assert "plugins.slack@openai-curated.enabled=false" in values
    assert "plugins.unified-computer-use@openai-bundled.enabled=false" in values
    assert not any(v.startswith("plugins.ironclaude@ironclaude.enabled") for v in values)


def test_user_mcp_servers_outside_brain_set_are_disabled(tmp_path):
    config = tmp_path / "config.toml"
    config.write_text(
        '[mcp_servers.node_repl]\ncommand = "node"\n'
        '[mcp_servers.computer-use]\ncommand = "x"\n'
        '[mcp_servers.orchestrator]\ncommand = "python"\n'
        '[mcp_servers.ollama]\ncommand = "python"\n'
    )
    values = _values(brain_tool_trim_overrides(config, _manifest(tmp_path, ()), BRAIN))
    assert "mcp_servers.node_repl.enabled=false" in values
    assert "mcp_servers.computer-use.enabled=false" in values
    assert "mcp_servers.orchestrator.enabled=false" not in values
    assert "mcp_servers.ollama.enabled=false" not in values


def test_episodic_memory_plugin_server_is_kept(tmp_path):
    values = _values(brain_tool_trim_overrides(tmp_path / "absent.toml", _manifest(tmp_path), BRAIN))
    assert not any("episodic-memory" in v for v in values)


def test_repo_manifest_disables_exactly_the_worker_servers(tmp_path):
    values = _values(brain_tool_trim_overrides(tmp_path / "absent.toml", REPO_MANIFEST, BRAIN))
    assert [v for v in values if v.startswith("plugins.ironclaude@ironclaude.")] == [
        "plugins.ironclaude@ironclaude.mcp_servers.state-manager.enabled=false",
        "plugins.ironclaude@ironclaude.mcp_servers.workspace-manager.enabled=false",
    ]


@pytest.mark.parametrize("bad_id", ["evil.plugin@x", 'q"uote@x', "a=b@x"])
def test_unexpressible_plugin_id_fails_closed(tmp_path, bad_id):
    config = tmp_path / "config.toml"
    config.write_text(f"[plugins.'{bad_id}']\nenabled = true\n")
    with pytest.raises(BrainToolTrimError, match="cannot be expressed"):
        brain_tool_trim_overrides(config, _manifest(tmp_path), BRAIN)


def test_invalid_config_fails_closed(tmp_path):
    config = tmp_path / "config.toml"
    config.write_text("[plugins\nbroken")
    with pytest.raises(BrainToolTrimError, match="cannot read"):
        brain_tool_trim_overrides(config, _manifest(tmp_path), BRAIN)


def test_missing_manifest_fails_closed(tmp_path):
    with pytest.raises(BrainToolTrimError, match="cannot read"):
        brain_tool_trim_overrides(tmp_path / "absent.toml", tmp_path / "no-manifest.json", BRAIN)


def test_trim_error_is_a_value_error():
    """start() catches ValueError around Popen(self._app_server_argv()) and fails the start."""
    assert issubclass(BrainToolTrimError, ValueError)
