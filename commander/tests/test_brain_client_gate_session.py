"""The Claude Brain exports IRONCLAUDE_BRAIN_GATE_SESSION so brain-gated-actions.sh
can keep per-Brain gate state, and resets the startup lookback markers on start."""
import os
from unittest.mock import patch

from ironclaude.brain_client import BrainClient


def test_gate_session_is_unique_and_safe():
    a, b = BrainClient(), BrainClient()
    assert a._brain_gate_session != b._brain_gate_session
    assert a._brain_gate_session.isalnum()


def test_start_exports_gate_session_and_resets_lookback(monkeypatch):
    from ironclaude import paths
    client = BrainClient()
    state = paths.brain_gate_root() / client._brain_gate_session
    state.mkdir(parents=True)
    for marker in ("lookback-slack", "lookback-ledger", "memory-armed"):
        (state / marker).touch()
    # setenv records the originals so teardown restores them (start() mutates os.environ).
    monkeypatch.setenv("IC_ROLE", "restored-by-monkeypatch")
    monkeypatch.setenv("IRONCLAUDE_BRAIN_GATE_SESSION", "restored-by-monkeypatch")
    monkeypatch.setenv("IRONCLAUDE_BRAIN_GATE_ROOT", "restored-by-monkeypatch")
    with patch.object(BrainClient, "_kill_brain_subprocess"), \
         patch.object(BrainClient, "discover_episodic_memory_path", side_effect=RuntimeError("stop")):
        try:
            client.start("prompt", cwd=os.environ["HOME"])
        except RuntimeError:
            pass
    assert os.environ["IRONCLAUDE_BRAIN_GATE_SESSION"] == client._brain_gate_session
    assert os.environ["IRONCLAUDE_BRAIN_GATE_ROOT"] == str(paths.brain_gate_root())
    assert not (state / "lookback-slack").exists()
    assert not (state / "lookback-ledger").exists()
    assert (state / "memory-armed").exists()
