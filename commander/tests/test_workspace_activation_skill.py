"""Managed-worktree activation and operator-switch skill contracts."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
ACTIVATE = ROOT / "worker/skills/activate-professional-mode/SKILL.md"
PRIMARY = ROOT / "worker/skills/use-primary-checkout/SKILL.md"
MANAGED = ROOT / "worker/skills/return-to-managed-worktree/SKILL.md"
USE_MANAGED = ROOT / "worker/skills/use-managed-worktree/SKILL.md"

EXPLANATION = (
    "Git worktrees are helpful when you want multiple sessions working on the "
    "same codebase at the same time without pollution between efforts."
)


def test_activation_never_allocates_a_worktree():
    """Isolation is opt-in. Activation must not change where files are written.

    Redirecting an operator's writes is their decision, not a side effect of
    turning on workflow discipline — a user who activates and then finds an
    empty `git status` in the checkout they were watching has been surprised by
    their own tooling.
    """
    text = ACTIVATE.read_text(encoding="utf-8")
    report_at = text.index("### Step 2: Report workspace mode")
    instruction_at = text.index("### Step 3: Establish the active client's instruction surface")
    enable_at = text.index('Call `set_professional_mode` with `value: "on"`')
    assert report_at < instruction_at < enable_at
    assert "Never call `activate_session_workspace` from this skill" in text
    assert "OPT-IN" in text
    assert "/use-managed-worktree" in text
    assert "same bound Step 1 `session_id`" in text


def test_activation_contains_no_default_on_guidance():
    """Presence assertions cannot catch a contradiction elsewhere in the file.

    This is model-facing instruction text: a leftover "isolated by default" line
    in Key Principles is enough for a model to allocate a worktree that Step 2
    just forbade. Assert the ABSENCE of the pre-flip phrasing.
    """
    text = ACTIVATE.read_text(encoding="utf-8")
    lower = text.lower()
    assert "isolated by default" not in lower
    assert "receives it automatically" not in lower
    assert "no opt-in prompt" not in lower
    # The effective-root statement must be conditional, not unconditional.
    flat = " ".join(text.split()).lower()
    assert "effective workspace root is the primary checkout unless" in flat


def test_activation_reports_an_existing_assignment_truthfully():
    """A session isolated earlier must not be reported as primary mode."""
    text = ACTIVATE.read_text(encoding="utf-8")
    assert "list_active_assignments" in text
    assert "call `get_workspace_status` with its `workspace_guid`" in text
    assert "No assignment is the normal case" in text


def test_activation_discloses_exact_assignment_and_reason():
    text = ACTIVATE.read_text(encoding="utf-8")
    assert EXPLANATION in text
    for field in (
        "Workspace assignment: <workspace_guid>",
        "Managed worktree: <worktree_path>",
        "Managed branch: <branch>",
        "Base commit: <base_commit>",
    ):
        assert field in text
    assert "Do not claim activation success before this disclosure" in text


def test_activation_discloses_the_consequence_not_just_the_benefit():
    """Redirecting writes is default-on, so the confirmation must say so.

    Naming the worktree path is not enough: a user who reads only "worktrees are
    helpful" will work for an hour and then find an empty `git status` in the
    checkout they were looking at. This is the only runtime warning they get.
    """
    text = ACTIVATE.read_text(encoding="utf-8")
    # Primary mode must say plainly that nothing moved.
    assert "Working in your primary checkout. Nothing is redirected." in text
    assert "/use-managed-worktree" in text
    # An already-isolated session must still get the full consequence.
    assert "NOT your" in text and "primary checkout" in text
    assert "`git status` will not show" in text
    assert "/use-primary-checkout" in text
    # Committing changed hands; the enforcement summary must not still imply
    # the agent can commit.
    assert "Commit and push are human-only" in text


def test_activation_non_git_and_dirty_checkout_paths_are_explicit():
    text = ACTIVATE.read_text(encoding="utf-8")
    flat = " ".join(text.split())
    assert "If the project root is not inside a Git worktree" in flat
    assert "Professional mode active; Git worktree isolation not applicable (non-Git project)." in text
    # Activation no longer moves anything, so a dirty checkout is not its
    # problem — but it must still never touch the operator's uncommitted work.
    assert "never stash, commit, copy, reset, clean, or move" in flat.lower()


def test_opt_in_skill_allocates_without_destroying_uncommitted_work():
    """The dirty-checkout protection moved to where allocation now happens."""
    use_managed = USE_MANAGED.read_text(encoding="utf-8")
    flat = " ".join(use_managed.split())
    assert "activate_session_workspace" in use_managed
    assert "get_workspace_status" in use_managed
    assert "Require a clean primary checkout" in flat
    assert "do not stash, commit, copy, reset, clean, or move" in flat.lower()
    # Entering isolation restricts reach, so unlike leaving it, no human intent
    # is consumed. That asymmetry must be stated, not implied.
    assert "consumes no human-authority intent" in flat
    assert "Managed worktree isolation ENABLED." in use_managed
    assert "/use-primary-checkout" in use_managed


def test_activation_failure_is_fail_closed_and_ai_assisted():
    text = ACTIVATE.read_text(encoding="utf-8")
    flat = " ".join(text.split())
    assert "AI-assisted worktree recovery" in text
    assert "professional mode remains at the exact Step 1 prior value" in text
    assert "Never silently fall back to the primary checkout" in text
    assert "Bounded recovery diagnostic" in text
    for field in (
        "Failed operation:",
        "Repository:",
        "Workspace assignment:",
        "Observed error:",
        "Next safe action:",
    ):
        assert field in text
    assert "retry only the same assignment and repository binding" in text
    assert "preserve the assignment and explain the exact blocker" in flat


def test_primary_and_managed_switches_are_human_gated_and_truthful():
    primary = PRIMARY.read_text(encoding="utf-8")
    managed = MANAGED.read_text(encoding="utf-8")

    assert "use-primary-checkout" in primary
    assert "exact human command" in primary
    assert "use_primary_checkout" in primary
    assert "Workspace isolation disabled by operator." in primary
    assert "may share files and staging state with other sessions" in primary

    assert "return-to-managed-worktree" in managed
    assert "exact human command" in managed
    assert "return_to_managed_worktree" in managed
    assert "Workspace isolation restored." in managed
    assert "Managed worktree: <managed_worktree_path>" in managed

    assert "bare repository" not in primary.lower()
    assert "bare repository" not in managed.lower()
    assert "model-generated" in primary
    assert "model-generated" in managed
