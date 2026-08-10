"""Contract tests for the Brain's guided integration-recovery wizard block in workflow.md.

`commit_worker` can fail and return `{"recovery": {"reconcile": {"mode": "conflict" | "drift"}}}`
when a worker's managed rebase drifts (worktree frozen, nothing mutated yet) or pauses on
unmerged paths. These tests assert the guided wizard block that teaches the Brain how to walk
the operator through recovery — one step at a time, never auto-resolving — is present in
workflow.md.

Assertions are scoped to the wizard's own section (from its heading to the next top-level
heading), anchored on the section's own heading, so a pass proves the wizard block exists
rather than matching incidental occurrences of these words elsewhere in the file.
"""

from pathlib import Path

import pytest

WORKFLOW_PATH = (
    Path(__file__).resolve().parents[1] / "src" / "brain" / "rules" / "workflow.md"
)

SECTION_HEADING = "### 6c. Guided Integration-Recovery Wizard"


def _read_wizard_section() -> str:
    text = WORKFLOW_PATH.read_text()
    start = text.find(SECTION_HEADING)
    assert start != -1, f"{SECTION_HEADING!r} heading not found in workflow.md"
    body_start = start + len(SECTION_HEADING)
    next_heading = text.find("\n### ", body_start)
    end = next_heading if next_heading != -1 else len(text)
    return text[start:end]


@pytest.fixture(scope="module")
def wizard_section() -> str:
    return _read_wizard_section()


def test_wizard_section_exists():
    assert _read_wizard_section()


def test_recover_worker_integration_tool_named(wizard_section):
    assert "recover_worker_integration" in wizard_section


def test_rerebase_action_present(wizard_section):
    assert 'recover_worker_integration(worker_id, "rerebase")' in wizard_section


def test_restore_frozen_action_present(wizard_section):
    assert 'recover_worker_integration(worker_id, "restore_frozen")' in wizard_section


def test_drift_decline_keeps_worktree_frozen(wizard_section):
    # The keep-frozen/decline path for mode == "drift": no tool call, worktree stays frozen.
    assert "the worktree stays frozen exactly as-is" in wizard_section


def test_abort_branch_present(wizard_section):
    assert 'recover_worker_integration(worker_id, "abort")' in wizard_section


def test_conflict_mode_branch_present(wizard_section):
    assert 'mode == "conflict"' in wizard_section


def test_commit_worker_reinvoke_mentioned(wizard_section):
    assert "re-invoke `commit_worker`" in wizard_section


def test_never_auto_resolve_phrase_present(wizard_section):
    assert "NEVER auto-resolves a semantic conflict" in wizard_section


CONFLICT_SUBHEADING = '**`mode == "conflict"`**'
DO_NOT_MARKER = "**Do NOT:**"


def _read_conflict_branch() -> str:
    section = _read_wizard_section()
    start = section.find(CONFLICT_SUBHEADING)
    assert start != -1, f"{CONFLICT_SUBHEADING!r} not found in wizard section"
    end = section.find(DO_NOT_MARKER, start)
    assert end != -1, f"{DO_NOT_MARKER!r} not found after conflict subheading"
    return section[start:end]


def test_conflict_branch_continues_via_recover_worker_integration():
    assert 'recover_worker_integration(worker_id, "continue")' in _read_conflict_branch()


def test_conflict_branch_routes_repair_required_to_commit_worker():
    branch = _read_conflict_branch()
    assert "rebase-recovery-repair-required" in branch
    assert "re-invoke `commit_worker`" in branch


def test_conflict_branch_commit_worker_only_for_repair_required():
    # I2 removed the paused-rebase -> commit_worker dead-end. commit_worker may
    # appear in the conflict slice ONLY as the repair-required result's isRepair
    # routing, which necessarily follows the repair-required mention. If a
    # paused-rebase -> commit_worker instruction were re-added ahead of it, or
    # commit_worker appeared with no repair-required context, .index would fail.
    branch = _read_conflict_branch()
    assert branch.index("rebase-recovery-repair-required") < branch.index("re-invoke `commit_worker`")


REPAIR_SUBHEADING = '**`mode == "repair"`**'


def _read_repair_branch() -> str:
    # The repair branch sits between the drift branch and the conflict subheading,
    # so it is OUTSIDE _read_conflict_branch()'s slice. Bound it by the concrete
    # CONFLICT_SUBHEADING rather than a generic ** scan (its own **Approve**/
    # **Decline** bullets start with ** and would truncate a naive slice).
    section = _read_wizard_section()
    start = section.find(REPAIR_SUBHEADING)
    assert start != -1, f"{REPAIR_SUBHEADING!r} not found in wizard section"
    end = section.find(CONFLICT_SUBHEADING, start)
    assert end != -1, f"{CONFLICT_SUBHEADING!r} not found after repair subheading"
    return section[start:end]


def test_repair_mode_branch_present(wizard_section):
    assert 'mode == "repair"' in wizard_section


def test_repair_branch_routes_approve_to_commit_worker():
    branch = _read_repair_branch()
    assert "re-invoke `commit_worker`" in branch
    assert 'recover_worker_integration(worker_id, "restore_frozen")' in branch


def test_conflict_branch_decline_uses_restore_frozen():
    # I1: after `continue` returns repair-required the rebase has completed, so the
    # conflict step-3 Decline must reset via restore_frozen, not abort (which throws
    # 'no rebase in progress'). Scope to the step-3 **Decline** line form — step 2's
    # abort has no **Decline** prefix and must stay untouched.
    branch = _read_conflict_branch()
    assert '**Decline** → call `recover_worker_integration(worker_id, "restore_frozen")`' in branch
    assert '**Decline** → call `recover_worker_integration(worker_id, "abort")`' not in branch
