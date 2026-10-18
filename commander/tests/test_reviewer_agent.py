"""Design 2026-10-17 v120-completeness R1: one locked, report-only reviewer agent replaces the
Explore and general-purpose reviewer dispatches."""
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
AGENT = REPO / "worker" / "agents" / "reviewer.md"
ADVISOR = REPO / "worker" / "skills" / "advisor-fallback" / "SKILL.md"
EXECUTING = REPO / "worker" / "skills" / "executing-plans" / "SKILL.md"
DISPATCH = 'subagent_type="ironclaude:reviewer"'


def _frontmatter(text):
    assert text.startswith("---\n"), "reviewer.md has no frontmatter"
    block = text.split("---\n", 2)[1]
    return {key.strip(): value.strip() for key, value in
            (line.split(":", 1) for line in block.strip().splitlines())}


def test_reviewer_agent_is_read_only_and_unpinned():
    text = AGENT.read_text()
    fields = _frontmatter(text)
    assert fields["tools"] == "Read, Grep, Glob"
    assert "model" not in fields  # callers pass model=
    assert fields["description"]
    assert "report-only" in text.lower()
    import json
    manifest = json.loads((REPO / "worker" / ".claude-plugin" / "plugin.json").read_text())
    assert manifest["agents"] == ["./agents/search-conversations.md", "./agents/reviewer.md"]


def test_reviewer_dispatches_use_the_locked_agent():
    advisor = ADVISOR.read_text()
    assert DISPATCH in advisor
    assert 'subagent_type="Explore"' not in advisor
    text = EXECUTING.read_text()
    plan_review = text[text.index("- **Claude Code:** use a fresh `Agent`"):
                       text.index("- **Codex:** call `run_codex_advisor_review`")]
    assert DISPATCH in plan_review
    end_review = text[text.index("**Run the review (high-blast-radius + confirmed):**"):
                      text.index("**Step 8: Final summary**")]
    assert DISPATCH in end_review
    assert "`git diff --staged` output" in end_review and "inline" in end_review
    # Execution subagents stay general-purpose (backlog: the parent-stage hijack), so exactly one remains.
    assert text.count('subagent_type="general-purpose"') == 1
    assert 'Dispatch one subagent per task (Task tool with subagent_type="general-purpose")' in text
