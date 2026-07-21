"""The project version must be identical across every place it is declared.

Guards against the recurring release foot-gun where one of the hand-edited
version sources is missed and silently drifts (e.g. the installed package
metadata lagging `pyproject.toml`).
"""
import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def _pyproject_version() -> str:
    text = (REPO_ROOT / "commander" / "pyproject.toml").read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    assert m, "version not found in commander/pyproject.toml"
    return m.group(1)


def _claude_plugin_json_version() -> str:
    data = json.loads((REPO_ROOT / "worker" / ".claude-plugin" / "plugin.json").read_text())
    return data["version"]


def _release_version_from_codex_cachebuster(version: str) -> str:
    match = re.fullmatch(r"(?P<release>[^+]+)(?:\+codex\.[a-z0-9-]+)?", version)
    if match is None:
        raise ValueError(f"invalid Codex cachebuster version: {version!r}")
    return match.group("release")


def _codex_plugin_json_version() -> str:
    data = json.loads((REPO_ROOT / "worker" / ".codex-plugin" / "plugin.json").read_text())
    return _release_version_from_codex_cachebuster(data["version"])


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


@pytest.mark.parametrize(
    ("version", "expected_release"),
    [
        ("1.0.24", "1.0.24"),
        ("1.0.24+codex.20260720062826", "1.0.24"),
    ],
)
def test_codex_cachebuster_release_version(version, expected_release):
    assert _release_version_from_codex_cachebuster(version) == expected_release


@pytest.mark.parametrize(
    "version",
    [
        "1.0.24+codex.",
        "1.0.24+codex.first+codex.second",
        "1.0.24+build.20260720062826",
        "1.0.24+codex.CACHEBUSTER",
        "1.0.24+codex.cache_buster",
        "1.0.24+codex.cache.buster",
        "1.0.24+codex.cache!buster",
    ],
)
def test_codex_cachebuster_release_version_rejects_malformed_metadata(version):
    with pytest.raises(ValueError, match="invalid Codex cachebuster version"):
        _release_version_from_codex_cachebuster(version)


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
