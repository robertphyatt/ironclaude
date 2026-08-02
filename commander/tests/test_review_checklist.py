"""Presence guard: the review checklist code-review Step 4.5 loads must carry the
detectors that exist because these defects recurred.

Each marker is copied character-for-character from the heading it guards. A marker
recalled rather than copied turns green on write and red at the task boundary — the
rule this test's own third marker states applies to this test itself.

LIMIT, stated deliberately: this proves the text is PRESENT. It does not prove any
reviewer behaves differently. The behavioural evidence is the review output naming
these checks instead of the generic fallback.
"""
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CHECKLIST = REPO_ROOT / ".claude" / "rules" / "review-checklist.md"


def _read() -> str:
    return CHECKLIST.read_text()


def test_checklist_exists():
    assert CHECKLIST.is_file(), f"{CHECKLIST} missing — code-review falls back to generic checks"


def test_checklist_carries_the_three_detectors():
    text = _read()
    for marker in ("Falsifiability at the end state",
                   "Provenance for factual claims",
                   "Widened-guard scope"):
        assert marker in text, marker


def test_checklist_uses_the_step_4_5_procedure_format():
    """Step 4.5 applies "detection steps" and respects "DO NOT flag" suppressions.
    Without both, the checks are applied loosely or over-fire."""
    text = _read()
    assert "Detection steps" in text
    assert "DO NOT flag" in text


CANONICAL = REPO_ROOT / "worker" / "rules" / "review-checklist.md"


def _headings(path: Path, prefix: str) -> set[str]:
    """Check names. The transitional file marks each check with `## `; the canonical
    file marks checks with `### ` and reserves `## ` for the CRITICAL/INFORMATIONAL
    groupings. The prefix is therefore per-file, not shared."""
    return {
        line[len(prefix):].strip()
        for line in path.read_text().splitlines()
        if line.startswith(prefix)
    }


def test_transitional_checklist_is_a_subset_of_canonical():
    """The `.claude/` copy is transitional and will be deleted. Set EQUALITY is the
    wrong assertion — canonical carries more checks. What must hold is that the
    transitional file contains nothing canonical lacks, so its deletion cannot drop
    a check. Fails the moment a check is added to the transitional copy only, or
    removed from canonical.
    """
    transitional = _headings(CHECKLIST, "## ")
    canonical = _headings(CANONICAL, "### ")
    assert transitional, "no ## headings found in the transitional checklist"
    assert transitional <= canonical, f"only in transitional: {sorted(transitional - canonical)}"


def test_canonical_checklist_carries_the_three_detectors():
    """The canonical checklist is worker/rules/review-checklist.md — it ships in the
    plugin at <plugin_root>/rules/ and is what code-review Step 4.5 loads once its
    reference is repointed.

    The `.claude/rules/` assertions above are TRANSITIONAL. That file is a project-local
    duplicate; it stays only until a relaunched session is observed loading this canonical
    file, then it and those assertions are deleted together.
    """
    text = CANONICAL.read_text()
    for marker in ("Falsifiability at the end state",
                   "Provenance for factual claims",
                   "Widened-guard scope"):
        assert marker in text, marker
