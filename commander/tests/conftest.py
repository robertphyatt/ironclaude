"""Pytest configuration and shared fixtures."""
from __future__ import annotations

import importlib.util
import os
import sys
import types
from pathlib import Path

import pytest

collect_ignore = ["test_signal_handler_destructive.py"]

# ── Fix ironclaude.plugins namespace shadowing ────────────────────────────────
# src/ironclaude/plugins.py (a module) shadows src/ironclaude/plugins/ (a package).
# Pre-register namespace entries in sys.modules so inline imports of
# ironclaude.plugins.scan.pipeline.* in test files resolve correctly.

_SRC = Path(__file__).parent.parent / "src"


def _ensure_namespace(name: str, path: Path) -> None:
    """Register a stub package in sys.modules if not already present."""
    if name not in sys.modules:
        mod = types.ModuleType(name)
        mod.__path__ = [str(path)]  # type: ignore[attr-defined]
        mod.__package__ = name
        sys.modules[name] = mod


# Load plugins.py as the actual module, then set __path__ to make it also a package
_plugins_path = _SRC / "ironclaude/plugins.py"
_plugins_spec = importlib.util.spec_from_file_location("ironclaude.plugins", _plugins_path)
_plugins_mod = importlib.util.module_from_spec(_plugins_spec)  # type: ignore[arg-type]
_plugins_mod.__path__ = [str(_SRC / "ironclaude/plugins")]  # type: ignore[attr-defined]
sys.modules["ironclaude.plugins"] = _plugins_mod
_plugins_spec.loader.exec_module(_plugins_mod)  # type: ignore[union-attr]

# Now set up subpackage namespaces
_ensure_namespace("ironclaude.plugins.scan", _SRC / "ironclaude/plugins/scan")
_ensure_namespace(
    "ironclaude.plugins.scan.pipeline", _SRC / "ironclaude/plugins/scan/pipeline"
)

# Load orphan_remediation directly so its inline import in tests resolves.
_orm_path = _SRC / "ironclaude/plugins/scan/pipeline/orphan_remediation.py"
if _orm_path.exists():
    _orm_spec = importlib.util.spec_from_file_location(
        "ironclaude.plugins.scan.pipeline.orphan_remediation", _orm_path
    )
    _orm_mod = importlib.util.module_from_spec(_orm_spec)  # type: ignore[arg-type]
    sys.modules["ironclaude.plugins.scan.pipeline.orphan_remediation"] = _orm_mod
    _orm_spec.loader.exec_module(_orm_mod)  # type: ignore[union-attr]


@pytest.fixture(autouse=True)
def _guard_os_kill(monkeypatch):
    def _blocked_kill(pid, sig):
        raise RuntimeError(
            f"os.kill({pid!r}, {sig!r}) called without a mock. "
            f"Real signals are banned in tests — a MagicMock PID converts to 0 via __index__ "
            f"and kills the entire process group. "
            f"Add: monkeypatch.setattr(os, 'kill', lambda pid, sig: None) "
            f"or: with patch('os.kill', ...)"
        )

    def _blocked_killpg(pgid, sig):
        raise RuntimeError(
            f"os.killpg({pgid!r}, {sig!r}) called without a mock — "
            f"same ban applies to killpg. Mock it explicitly."
        )

    monkeypatch.setattr(os, "kill", _blocked_kill)
    if hasattr(os, "killpg"):
        monkeypatch.setattr(os, "killpg", _blocked_killpg)


@pytest.fixture(autouse=True)
def _isolate_ic_env(monkeypatch):
    """Prevent the developer's real IC_* environment from leaking into tests.

    OrchestratorTools.__init__ resolves brain_cwd / ollama config / machines
    from IC_* env vars BEFORE falling back to the injected `config` dict. On a
    machine running a live brain (IC_BRAIN_CWD set), that override made wiki/
    ledger tests write to and git-commit into the REAL brain repo, and the
    tmp_path assertions failed. Unset them so injected config is honored.
    """
    for var in (
        "IC_BRAIN_CWD",
        "IC_OLLAMA_CONFIG_PATH",
        "IC_MACHINES_CONFIG",
        "IC_LEDGER_PATH",
        "IC_LOG_DIR",
    ):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture(autouse=True)
def _reset_ollama_breakers():
    """The Ollama circuit breaker is module-level global state with a real clock.
    Reset it before every test so no test contaminates another's URL breakers."""
    from ironclaude.ollama_client import _BREAKERS
    _BREAKERS.reset()
    yield
    _BREAKERS.reset()
