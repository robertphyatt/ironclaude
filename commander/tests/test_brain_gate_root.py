"""Design 2026-10-17 v120-completeness R3: one Brain gate root, ~/.ironclaude/state/brain-gate,
created 0700 by the Commander and passed to both Brain clients and both Brain hooks."""
import os
import re
import stat
from pathlib import Path
from unittest.mock import patch

from ironclaude import paths
from ironclaude.brain_client import BrainClient
from ironclaude.codex_brain_client import CodexBrainClient

REPO = Path(__file__).resolve().parents[2]
GATE_HOOK = REPO / "worker" / "hooks" / "brain-gated-actions.sh"
CANARY_HOOK = REPO / "commander" / "hooks" / "brain-hooks-canary.sh"


def test_gate_root_is_under_ironclaude_state():
    assert paths.brain_gate_root() == Path(os.environ["HOME"]) / ".ironclaude" / "state" / "brain-gate"


def test_ensure_gate_root_creates_and_tightens_to_0700():
    root = paths.brain_gate_root()
    assert not root.exists()
    assert paths.ensure_brain_gate_root() == root
    assert stat.S_IMODE(root.stat().st_mode) == 0o700
    os.chmod(root, 0o755)
    paths.ensure_brain_gate_root()
    assert stat.S_IMODE(root.stat().st_mode) == 0o700


def test_both_brain_clients_pass_the_same_root(monkeypatch):
    root = str(paths.brain_gate_root())
    codex = CodexBrainClient()
    assert codex._spawn_env()["IRONCLAUDE_BRAIN_GATE_ROOT"] == root
    codex._reset_brain_gate_startup_state()
    assert stat.S_IMODE(os.stat(root).st_mode) == 0o700
    # setenv records the originals so teardown restores them (start() mutates os.environ).
    monkeypatch.setenv("IC_ROLE", "restored-by-monkeypatch")
    monkeypatch.setenv("IRONCLAUDE_BRAIN_GATE_SESSION", "restored-by-monkeypatch")
    monkeypatch.setenv("IRONCLAUDE_BRAIN_GATE_ROOT", "restored-by-monkeypatch")
    with patch.object(BrainClient, "_kill_brain_subprocess"), \
         patch.object(BrainClient, "discover_episodic_memory_path", side_effect=RuntimeError("stop")):
        try:
            BrainClient().start("prompt", cwd=os.environ["HOME"])
        except RuntimeError:
            pass
    assert os.environ["IRONCLAUDE_BRAIN_GATE_ROOT"] == root


def _root_check(hook):
    body = re.search(r"^gate_root_problem\(\) \{\n(.*?)^\}", hook.read_text(), re.S | re.M)
    assert body is not None, f"{hook.name} defines no gate_root_problem"
    return body.group(1)


def test_both_brain_hooks_share_one_root_check_and_default():
    assert _root_check(GATE_HOOK) == _root_check(CANARY_HOOK)
    default = 'GATE_ROOT="${IRONCLAUDE_BRAIN_GATE_ROOT:-$HOME/.ironclaude/state/brain-gate}"'
    for hook in (GATE_HOOK, CANARY_HOOK):
        text = hook.read_text()
        assert default in text
        assert "/tmp/ic/codex-brain-gate" not in text
    for module in ("brain_client.py", "codex_brain_client.py"):
        assert "/tmp/ic/codex-brain-gate" not in (REPO / "commander" / "src" / "ironclaude" / module).read_text()
