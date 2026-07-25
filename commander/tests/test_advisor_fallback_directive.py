"""Presence guard: the Advisor Fallback directive must exist in every IronClaude
behavioral surface, spelling out the TIER-RELATIVE fallback (Fable if available, else
Opus) — so a future edit cannot silently drop it or reintroduce a hard-coded model."""
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MARKER = "Advisor Fallback"
PRIMARY = "model=fable"   # preferred subagent tier
FALLBACK = "model=opus"   # required fallback when Fable is unavailable
CODEX_MARKER = "codex exec"   # the codex-native advisor branch (codex has no /advisor or Agent tool)


def _read(rel: str) -> str:
    return (REPO_ROOT / rel).read_text()


def test_directive_in_canonical_behavioral():
    text = _read(".claude/rules/behavioral.md")
    assert MARKER in text
    assert "advisor" in text.lower()
    # both the preferred tier AND its fallback must be named (no hard-coded model)
    assert PRIMARY in text and FALLBACK in text


def test_directive_in_brain_behavioral():
    text = _read("commander/src/brain/rules/behavioral.md")
    assert MARKER in text
    assert PRIMARY in text and FALLBACK in text


def test_directive_in_agents_md():
    """Codex workers load AGENTS.md (root), NOT .claude/rules/behavioral.md. The advisor
    directive must exist there AND name the codex-native path (codex exec), so a codex worker
    is not left advisor-less and a future edit cannot silently drop codex advisor parity."""
    text = _read("AGENTS.md")
    assert MARKER in text
    assert CODEX_MARKER in text


def test_directive_in_activation_skill():
    text = _read("worker/skills/activate-professional-mode/SKILL.md")
    # assert each of the FIVE distinct Advisor-Fallback edits individually.
    # '9. **Advisor Fallback**' alone is shared by the compact one-liner AND the
    # full block, so guard them apart: the compact one-liner via its em-dash
    # form, and the two full-form blocks (full template + Concept 9) via a
    # bulleted phrase they alone carry.
    assert "**Advisor Fallback** — If the" in text            # compact CLAUDE.md one-liner
    assert text.count("just reason it through") >= 2          # full template block + Concept 9 block
    assert "| 9 | Advisor Fallback |" in text                 # concept-detection table row (propagation vector)
    assert "Concept 9 (Advisor Fallback):" in text            # append-path canonical block header
    # v1.0.24 added an 11th concept (Boy Scout Rule) after No Workflow Avoidance Under Stage/Context
    # Restrictions) — count strings track the whole rule set, not the Advisor
    # Fallback rule specifically. Kept as pins so a future edit that adds a
    # concept but forgets to bump the count is caught.
    assert "11 concepts" in text                               # count string bumped 10 -> 11
    assert "(11 principles)" in text                           # count string bumped 10 -> 11
    assert "11-principle template" in text                     # cross-references bumped 10 -> 11
    assert PRIMARY in text and FALLBACK in text                # tier-relative fallback named
