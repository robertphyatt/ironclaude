# tests/test_worker_claude_md_template.py
"""Tests for provider-native worker instruction templates."""

import re
from pathlib import Path


TEMPLATE_PATH = Path(__file__).parent.parent / "src" / "ironclaude" / "templates" / "worker_claude_md.md"
AGENTS_TEMPLATE_PATH = Path(__file__).parent.parent / "src" / "ironclaude" / "templates" / "worker_agents.md"
ACTIVATION_SKILL_PATH = (
    Path(__file__).parents[2]
    / "worker"
    / "skills"
    / "activate-professional-mode"
    / "SKILL.md"
)


def _markdown_fence_after(content: str, marker: str) -> str:
    remainder = content.split(marker, 1)[1]
    return remainder.split("```markdown\n", 1)[1].split("\n```", 1)[0] + "\n"


def _concept_headings(content: str) -> list[str]:
    headings = re.findall(r"^\d+\. \*\*(.+?)\*\*", content, re.MULTILINE)
    return [
        "Advisor Fallback" if heading.startswith("Advisor Fallback") else
        "Boy Scout Rule" if heading.startswith("Boy Scout Rule") else heading
        for heading in headings
    ]


class TestWorkerClaudeMdTemplate:
    def test_template_file_exists(self):
        """Template file must exist at expected path."""
        assert TEMPLATE_PATH.exists(), f"Template not found at {TEMPLATE_PATH}"

    def test_template_contains_workflow_requirement(self):
        """Template must contain the workflow requirement blockquote."""
        content = TEMPLATE_PATH.read_text()
        assert "WORKFLOW REQUIREMENT" in content

    def test_claude_worker_template_matches_activation_canonical_contract(self):
        """Packaged CLAUDE.md composes canonical workflow + full Claude rules."""
        activation = ACTIVATION_SKILL_PATH.read_text()
        compact = _markdown_fence_after(
            activation, "If `CLAUDE.md` is absent in `update` mode"
        )
        rules = _markdown_fence_after(
            activation, "create it with the\nfull canonical template"
        )
        workflow = compact.split("\n\n", 1)[0]
        assert TEMPLATE_PATH.read_text() == f"{workflow}\n\n{rules}"

    def test_codex_worker_template_matches_activation_canonical_contract(self):
        """Packaged AGENTS.md is the exact canonical Codex template."""
        activation = ACTIVATION_SKILL_PATH.read_text()
        canonical = _markdown_fence_after(
            activation,
            "When root `AGENTS.md` is absent, create it with this canonical template",
        )
        assert AGENTS_TEMPLATE_PATH.read_text() == canonical

    def test_worker_templates_have_semantic_concept_parity(self):
        """Claude keeps native rules; Codex adds actual-Fable routing."""
        claude = TEMPLATE_PATH.read_text()
        codex = AGENTS_TEMPLATE_PATH.read_text()
        workflow = (
            "> **WORKFLOW REQUIREMENT (when professional mode is active):**"
        )
        assert claude.count(workflow) == 1
        assert codex.count(workflow) == 1
        expected = [
            "Challenge Assumptions",
            "Verify with Evidence",
            "Refuse Impossible Requests",
            "Persistent Questioning",
            "No Premature Optimization",
            "Search Before Guessing",
            "Subagent Discipline",
            "No Sycophantic Responses",
            "Advisor Fallback",
            "No Workflow Avoidance Under Stage/Context Restrictions",
            "Boy Scout Rule",
            "Recipient-Based Communication Profiles",
        ]
        assert _concept_headings(claude) == expected + [
            "Managed Worktree — Missing Shared Data Is a Blocker to Report, Never to Hand-Fix"
        ]
        assert _concept_headings(codex) == expected + [
            "Actual Claude Fable from Codex",
            "Managed Worktree — Missing Shared Data Is a Blocker to Report, Never to Hand-Fix",
        ]
        assert "`Agent` tool (`model=fable`" in claude
        assert "`model=opus`" in claude
        assert "`run_codex_advisor_review`" in codex
        assert "`requester_model`" in codex
        assert 'review_tier: "one-up"' in codex
        assert "`luna → terra → sol → astra`" in codex
        assert "`gpt-5.6-luna → gpt-5.6-terra → gpt-5.6-sol → gpt-6-astra`" in codex
        assert "nested `codex exec`" in codex
        assert "ironclaude:use-fable-subagent" in codex
        assert "Native Codex subagents cannot satisfy" in codex

    def test_activation_existing_file_gate_requires_managed_worktree_concept(self):
        """I1: the existing-file semantic-check table, the verify-only NAME list, and the
        read-back counts must require the 13th (Claude) / 14th (Codex) 'Managed Worktree'
        concept, or activation on an EXISTING standalone project reports complete at the old
        count and never writes it."""
        skill = ACTIVATION_SKILL_PATH.read_text()
        # The existing-file concept table lists the Managed-Worktree concept.
        assert "| Managed Worktree — Missing Shared Data" in skill
        # Read-back counts are reconciled (Claude 13 / Codex 14).
        assert "fourteen for Codex, thirteen for Claude" in skill
        # No stale Claude=twelve / Codex=thirteen read-back count remains.
        assert "thirteen for Codex, twelve for Claude" not in skill
        # verify-only enumerates uncovered concepts by exact name from the list between
        # these two markers; it must name Managed Worktree, directly before the
        # Codex-only concept so "preceding thirteen" (line 236) is literally true.
        name_list = skill.split("its exact name from this list:", 1)[1].split(
            "The enumerated name set must equal", 1
        )[0]
        assert (
            "- Managed Worktree — Missing Shared Data Is a Blocker to Report, Never to Hand-Fix\n"
            "- Actual Claude Fable from Codex"
        ) in name_list
