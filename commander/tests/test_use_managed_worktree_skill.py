"""use-managed-worktree skill contract: step 3 must branch on effectiveRoot.

`list_active_assignments`'s count could not distinguish an ISOLATED session
(writing in its managed worktree) from one whose assignment exists but whose
writes actually land in the PRIMARY checkout. `get_workspace_status` now
returns `effectiveRoot` ('primary'|'managed') on the assigned payload; step 3
must branch on that field, not on how many assignments exist.
"""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
USE_MANAGED = ROOT / "worker/skills/use-managed-worktree/SKILL.md"


def _step_three(text: str) -> str:
    start = text.index("3. Call")
    end = text.index("4. Require a clean primary checkout")
    return text[start:end]


def test_step_three_calls_get_workspace_status_by_provider_root():
    text = USE_MANAGED.read_text(encoding="utf-8")
    step3 = _step_three(text)
    assert "get_workspace_status" in step3
    assert "provider_root" in step3
    assert "mcp__plugin_ironclaude_workspace-manager__get_workspace_status" in step3


def test_step_three_branches_on_effective_root_managed_case():
    text = USE_MANAGED.read_text(encoding="utf-8")
    step3 = _step_three(text)
    assert "effectiveRoot" in step3
    assert '"managed"' in step3
    flat = " ".join(step3.split())
    assert "correctly isolated" in flat


def test_step_three_reports_primary_effective_root_truthfully():
    """An assignment that exists but writes to primary must never be called isolated."""
    text = USE_MANAGED.read_text(encoding="utf-8")
    step3 = _step_three(text)
    assert '"primary"' in step3
    flat = " ".join(step3.split())
    assert "landing in the primary checkout" in flat
    assert 'Do not call this "isolated."' in flat
    assert "/return-to-managed-worktree" in step3


def test_step_three_unassigned_path_continues():
    text = USE_MANAGED.read_text(encoding="utf-8")
    step3 = _step_three(text)
    assert '"unassigned"' in step3


def test_step_three_count_only_branch_text_is_gone():
    """The old branch decided isolation by counting `list_active_assignments`.

    That count cannot tell an isolated session from one whose assignment
    exists but whose writes land in the primary checkout — this is exactly
    the bug effectiveRoot fixes. If either phrase below survives, step 3 is
    still deciding by count instead of by effectiveRoot.
    """
    text = USE_MANAGED.read_text(encoding="utf-8")
    assert "One assignment already bound to this provider root" not in text
    assert "More than one: stop and report the ambiguity" not in text
    step3 = _step_three(text)
    assert "list_active_assignments" not in step3
