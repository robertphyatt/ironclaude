"""Pytest configuration and shared fixtures."""
from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

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


_ensure_namespace("ironclaude.plugins", _SRC / "ironclaude/plugins")
_ensure_namespace("ironclaude.plugins.scan", _SRC / "ironclaude/plugins/scan")
_ensure_namespace(
    "ironclaude.plugins.scan.pipeline", _SRC / "ironclaude/plugins/scan/pipeline"
)

# Load orphan_remediation directly so its inline import in tests resolves.
_orm_path = _SRC / "ironclaude/plugins/scan/pipeline/orphan_remediation.py"
_orm_spec = importlib.util.spec_from_file_location(
    "ironclaude.plugins.scan.pipeline.orphan_remediation", _orm_path
)
_orm_mod = importlib.util.module_from_spec(_orm_spec)  # type: ignore[arg-type]
sys.modules["ironclaude.plugins.scan.pipeline.orphan_remediation"] = _orm_mod
_orm_spec.loader.exec_module(_orm_mod)  # type: ignore[union-attr]
