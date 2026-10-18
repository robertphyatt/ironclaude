"""Directive-heading parity across the repo's three instruction surfaces (v1.1.16 L2b-2 R2).

AGENTS.md (read by Codex), CLAUDE.md and .claude/rules/behavioral.md (read by Claude)
carry the same 15 canonical directives, numbered 1-15 in canonical order.
"Actual Claude Fable from Codex" is the one client-specific exception: it appears on
AGENTS.md only, as directive 16. The worker templates are checked in L2b-3, not here.
"""
import re
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
HEADING = re.compile(r"^(\d+)\. \*\*(.+?)\*\*", re.MULTILINE)
CANONICAL = (
    "Challenge Assumptions",
    "Verify with Evidence",
    "Refuse Impossible Requests",
    "Persistent Questioning",
    "No Premature Optimization",
    "Search Before Guessing",
    "Subagent Discipline",
    "No Sycophantic Responses",
    "Right-Size Every Subagent (delegate liberally, match model to difficulty)",
    "Advisor Fallback (advisor unavailable ≠ skip the advisor)",
    "No Workflow Avoidance Under Stage/Context Restrictions",
    "Boy Scout Rule — Leave It Better Than You Found It",
    "Recipient-Based Communication Profiles",
    "Professional-Mode-Off Operator Authority",
    "Managed Worktree — Missing Shared Data Is a Blocker to Report, Never to Hand-Fix",
)
CODEX_ONLY = ("Actual Claude Fable from Codex",)
SURFACES = {
    "AGENTS.md": CANONICAL + CODEX_ONLY,
    "CLAUDE.md": CANONICAL,
    ".claude/rules/behavioral.md": CANONICAL,
}


def _headings(relative_path: str) -> list[tuple[int, str]]:
    text = (ROOT / relative_path).read_text(encoding="utf-8")
    return [(int(number), title) for number, title in HEADING.findall(text)]


@pytest.mark.parametrize("relative_path", sorted(SURFACES))
def test_surface_carries_exactly_the_canonical_directives_in_order(relative_path):
    expected = list(enumerate(SURFACES[relative_path], start=1))
    assert _headings(relative_path) == expected
