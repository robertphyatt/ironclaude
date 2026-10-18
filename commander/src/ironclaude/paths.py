"""Single seam for user-home path resolution.

Every accessor resolves at CALL time. This module must contain ZERO
module-level path constants — a constant freezes the home at import, before any
test fixture can redirect it, which is the defect this seam exists to remove
(see docs/plans/2026-07-31-paths-seam-design.md).

Imports are limited to os and pathlib so every ironclaude module can import
this one without a cycle.
"""
from __future__ import annotations

import os
from pathlib import Path


def home() -> Path:
    """Resolution root. IRONCLAUDE_HOME overrides; else Path.home()."""
    override = os.environ.get("IRONCLAUDE_HOME")
    return Path(override) if override else Path.home()


def _claude_dir() -> Path:
    return home() / ".claude"


def _ironclaude_dir() -> Path:
    return home() / ".ironclaude"


def hooks_config() -> str:
    """The ollama/hooks config. IC_OLLAMA_CONFIG_PATH overrides."""
    override = os.environ.get("IC_OLLAMA_CONFIG_PATH")
    if override:
        return os.path.expanduser(override)
    return str(_claude_dir() / "ironclaude-hooks-config.json")


def brain_sessions_dir() -> str:
    return str(_ironclaude_dir() / "brain-sessions")


def allowed_log_prefixes() -> tuple[str, ...]:
    return ("/tmp/", "/var/log/", str(home()) + "/")


def brain_gate_root() -> Path:
    """Per-Brain gate state root for brain-gated-actions.sh and brain-hooks-canary.sh (design
    2026-10-17 v120-completeness R3). Both Brain clients pass it as IRONCLAUDE_BRAIN_GATE_ROOT."""
    return _ironclaude_dir() / "state" / "brain-gate"


def ensure_brain_gate_root() -> Path:
    """Create the gate root (and parents) and force mode 0700. The Brain hooks fail closed unless
    it is a real directory owned by this user with no group or other permission bits."""
    root = brain_gate_root()
    root.mkdir(parents=True, exist_ok=True)
    os.chmod(root, 0o700)
    return root
