"""Guard for the Brain directive-reading quick-ref lookback value (v1.1.9 obs 2).

The startup-lookback gate arms only at hours_back >= 48 (canonical 72,
workflow.md:727); the quick-ref must not show a value that leaves the Brain blocked.
"""
from pathlib import Path

ORCH = Path(__file__).resolve().parents[1] / "src" / "brain" / "orchestrator_claude.md"
WF = Path(__file__).resolve().parents[1] / "src" / "brain" / "rules" / "workflow.md"


def test_directive_read_quickref_arms_startup_gate():
    text = ORCH.read_text()
    assert "hours_back=72" in text
    assert "hours_back=24" not in text


def test_directive_workflow_read_arms_gate():
    """#3: the directive-read in workflow.md must use a lookback that arms the
    startup gate (>=48; canonical 72), so a Brain reading via it is not blocked on
    its first gated action.

    obs-6(d): the bare ``"hours_back=72" in text`` assert is satisfied by the
    lookback-strategy line (:727, ``limit=100``) regardless of the directive-read
    line (:760, ``limit=20``), so a :760 regression to e.g. ``hours_back=12`` would
    still pass. Anchor on the exact :760 line — its ``limit=20`` form is unique to
    that line — so the guard fails on any :760 lookback change."""
    text = WF.read_text()
    assert "hours_back=24" not in text
    assert (
        "`get_operator_messages(limit=20, hours_back=72)` to read raw Slack messages."
        in text
    )


def test_workflow_secret_block_operator_ask_rule_present():
    """The Brain must surface a secretBlocked shared-resource entry to the operator
    and only re-call with allow_secret_entries=true after approval."""
    text = WF.read_text()
    assert "secretBlocked" in text
    assert "allow_secret_entries=true" in text
