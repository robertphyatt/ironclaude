"""R14: the Codex Brain's tool set matches the Claude Brain's.

The Claude Brain loads only project/local settings plus its own MCP servers
(brain_client.py). The Codex Brain's app-server instead inherits the operator's whole
~/.codex surface: every enabled plugin (apps, computer use, browser, ...), user MCP
servers (node_repl, ...) and the ironclaude plugin's worker MCP servers (state-manager,
workspace-manager — the latter can push). This builds `-c` overrides that turn all of
that off for the Brain process only. ~/.codex/config.toml is read, never written.

ironclaude@ironclaude itself stays enabled: the Brain's restriction hooks live in its
hooks.json, and disabling a plugin drops its hooks. Its MCP servers are disabled one by
one instead (plugins.<id>.mcp_servers.<name>.enabled=false), keeping episodic-memory.

`-c` keys are split on '.', so ids are written unquoted; an id that cannot be written
that way fails the Brain start rather than being skipped.
"""
from __future__ import annotations

import json
import tomllib
from pathlib import Path
from typing import Iterable

KEPT_PLUGIN = "ironclaude@ironclaude"
KEPT_PLUGIN_MCP_SERVERS = frozenset({"episodic-memory"})
FIXED_OVERRIDES = (
    "features.apps=false",
    "features.tool_suggest=false",
    "features.image_generation=false",
    "features.goals=false",
    'web_search="disabled"',
)


class BrainToolTrimError(ValueError):
    """The trim cannot be derived; the Brain must not start with an unknown tool set."""


def _segment(name: str, kind: str) -> str:
    if not name or any(ch in name for ch in '."= '):
        raise BrainToolTrimError(f"{kind} {name!r} cannot be expressed as an unquoted -c key segment")
    return name


def brain_tool_trim_overrides(
    config_path: Path, manifest_path: Path, brain_mcp_servers: Iterable[str]
) -> list[str]:
    """`-c` pairs that disable everything the Brain does not share with the Claude Brain."""
    keep_servers = frozenset(brain_mcp_servers)
    config: dict = {}
    if config_path.exists():
        try:
            config = tomllib.loads(config_path.read_text())
        except (OSError, ValueError) as exc:
            raise BrainToolTrimError(f"cannot read {config_path}: {exc}") from exc
    try:
        manifest = json.loads(manifest_path.read_text())
    except (OSError, ValueError) as exc:
        raise BrainToolTrimError(f"cannot read {manifest_path}: {exc}") from exc

    values = list(FIXED_OVERRIDES)
    for plugin_id in sorted(config.get("plugins") or {}):
        if plugin_id != KEPT_PLUGIN:
            values.append(f"plugins.{_segment(plugin_id, 'plugin')}.enabled=false")
    for server in sorted(config.get("mcp_servers") or {}):
        if server not in keep_servers:
            values.append(f"mcp_servers.{_segment(server, 'MCP server')}.enabled=false")
    for server in sorted(manifest.get("mcpServers") or {}):
        if server not in KEPT_PLUGIN_MCP_SERVERS:
            values.append(
                f"plugins.{KEPT_PLUGIN}.mcp_servers."
                f"{_segment(server, 'plugin MCP server')}.enabled=false"
            )
    return [part for value in values for part in ("-c", value)]
