"""The project version must be identical across every place it is declared.

Guards against the recurring release foot-gun where one of the hand-edited
version sources is missed and silently drifts (e.g. the installed package
metadata lagging `pyproject.toml`).
"""
import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _pyproject_version() -> str:
    text = (REPO_ROOT / "commander" / "pyproject.toml").read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    assert m, "version not found in commander/pyproject.toml"
    return m.group(1)


def _claude_plugin_json_version() -> str:
    data = json.loads((REPO_ROOT / "worker" / ".claude-plugin" / "plugin.json").read_text())
    return data["version"]


def _codex_plugin_json_version() -> str:
    data = json.loads((REPO_ROOT / "worker" / ".codex-plugin" / "plugin.json").read_text())
    return data["version"]


def _marketplace_version() -> str:
    data = json.loads((REPO_ROOT / ".claude-plugin" / "marketplace.json").read_text())
    return data["plugins"][0]["version"]


def _makefile_pinned_version():
    """The Makefile no longer pins a version in the plugin-cache path (it derives
    the installed version at runtime). If a hardcoded version is ever
    reintroduced there, fold it back into the equality check."""
    text = (REPO_ROOT / "Makefile").read_text()
    m = re.search(r"/ironclaude/ironclaude/(\d+\.\d+\.\d+)/hooks", text)
    return m.group(1) if m else None


def test_version_sources_match():
    versions = {
        "commander/pyproject.toml": _pyproject_version(),
        "worker/.claude-plugin/plugin.json": _claude_plugin_json_version(),
        "worker/.codex-plugin/plugin.json": _codex_plugin_json_version(),
        ".claude-plugin/marketplace.json": _marketplace_version(),
    }
    makefile_version = _makefile_pinned_version()
    if makefile_version is not None:
        versions["Makefile"] = makefile_version

    distinct = set(versions.values())
    assert len(distinct) == 1, f"version mismatch across declared sources: {versions}"
