"""Presence guard for the scope-aware Boy Scout Rule on every instruction surface."""
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
MARKER = "Boy Scout Rule"
DIRECTIVE_BODY = """\
    - Never dismiss an evidence-backed defect because it is pre-existing, adjacent, or outside the immediate change
    - If cleanup is safe, relevant, and within the authorized task scope, fix it through the active workflow and verify the result
    - If cleanup would materially expand scope, change behavior, require destructive action, affect external systems, or require new authority, describe the finding, evidence, proposed cleanup scope, and risk, then ask permission before proceeding
    - If cleanup is blocked or unsafe, record the finding and explain the constraint instead of suppressing it
    - Do not use this rule to justify speculative refactoring or unrequested features"""
COMPACT_DIRECTIVE = (
    "11. **Boy Scout Rule** — Never dismiss an evidence-backed defect because it is pre-existing. "
    "Clean it up when it is safe, relevant, and within authorized task scope; otherwise describe "
    "the finding, evidence, proposed cleanup scope, and risk, then ask permission before proceeding."
)
DETECTION_ROW = (
    "| 11 | Boy Scout Rule | Instructions not to ignore evidence-backed pre-existing or adjacent "
    "defects; clean them up when within authorized task scope, ask permission before scope expansion, "
    "destructive action, or external-system effects after presenting finding/evidence/scope/risk, and "
    "record blocked or unsafe findings instead of suppressing them |"
)
BRAIN_DIRECTIVE = (
    "24. **Boy Scout Rule — Leave It Better Than You Found It** — Never dismiss an evidence-backed "
    "defect because it is pre-existing or adjacent. If cleanup is safe, relevant, and within the "
    "confirmed directive, spawn or guide a worker through the full PM workflow and verify it. If "
    "cleanup expands the directive, changes behavior, is destructive, affects an external system, "
    "or requires new authority, describe the finding, evidence, proposed cleanup scope, and risk, "
    "then request operator permission before proceeding. If cleanup is blocked or unsafe, record "
    "the finding and constraint instead of suppressing it. Do not use this rule for speculative "
    "refactoring or unrequested features."
)
BRAIN_FIX_BUGS = """\
16. **Fix Bugs Immediately — Don't Just Report Them**
   - When you discover a bug within a confirmed directive, determine the architecture-appropriate fix, spawn a worker, and get it done
   - Tell the operator what you found and what you're doing about it
   - If the fix falls outside the confirmed directive, follow the Boy Scout Rule: describe the finding, evidence, proposed cleanup scope, and risk, then ask permission before expanding scope"""
BRAIN_FIX_BEFORE_REPORTING = (
    "20. **Fix Before Reporting** — Within a confirmed directive, attempt the fix before reporting "
    "(spawn a worker, run an authorized command, or restart an in-scope service). If the action falls "
    "outside the confirmed directive, is destructive, affects an external system, or otherwise requires "
    "new authority, pin a decision-format permission request describing the finding, evidence, proposed "
    "cleanup scope, and risk before acting. Never silently suppress a blocked or unsafe finding."
)

DIRECT_SURFACES = [
    "AGENTS.md",
    "CLAUDE.md",
    "worker/CLAUDE.md",
    "commander/CLAUDE.md",
    ".claude/rules/behavioral.md",
    "commander/src/ironclaude/templates/worker_claude_md.md",
]


def _read(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text()


def test_directive_in_every_direct_behavioral_surface():
    for relative_path in DIRECT_SURFACES:
        text = _read(relative_path)
        assert MARKER in text, f"Missing {MARKER} in {relative_path}"
        assert DIRECTIVE_BODY in text, f"Incomplete {MARKER} in {relative_path}"


def test_directive_in_brain_surfaces():
    behavioral = _read("commander/src/brain/rules/behavioral.md")
    identity = _read("commander/src/brain/orchestrator_claude.md")
    assert BRAIN_FIX_BUGS in behavioral
    assert BRAIN_FIX_BEFORE_REPORTING in behavioral
    assert BRAIN_DIRECTIVE in behavioral
    assert MARKER in identity
    assert "18 behavioral directives" not in identity


def test_activation_skill_propagates_all_boy_scout_paths():
    text = _read("worker/skills/activate-professional-mode/SKILL.md")
    assert COMPACT_DIRECTIVE in text
    assert text.count(DIRECTIVE_BODY) == 2
    assert DETECTION_ROW in text
    assert "Concept 11 (Boy Scout Rule):" in text
    assert "11 concepts" in text
    assert "(11 principles)" in text
    assert "11-principle template" in text
    concept_10 = text.index("Concept 10 (No Workflow Avoidance Under Stage/Context Restrictions):")
    concept_11 = text.index("Concept 11 (Boy Scout Rule):")
    assert concept_10 < text.index("N. **No Workflow Avoidance", concept_10) < concept_11
    assert concept_11 < text.index("N. **Boy Scout Rule", concept_11)
