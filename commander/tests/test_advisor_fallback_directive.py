"""Presence guard: the Advisor Fallback directive must exist in every IronClaude
behavioral surface, spelling out the TIER-RELATIVE fallback (Fable if available, else
Opus) — so a future edit cannot silently drop it or reintroduce a hard-coded model.

The daemon Brain surface is the ONE exception: it has no `advisor` tool and
brain-task-gate.sh bans every Agent/Task subagent except ironclaude:search-conversations,
so its rule 23 pins a blind report-only `spawn_worker` reviewer instead of the
worker-side tier-relative Agent-tool fallback (see test_directive_in_brain_behavioral)."""
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MARKER = "Advisor Fallback"
OPUS_CLASS = "model=opus"   # the substitute reviewer (Opus class)
TOP_TIER = "model=fable"    # named only as the explicit-operator-request exception
TOP_TIER_RULE = "explicitly requests the top tier"
RETIRED = "if Fable is available"
SAME_TIER = 'review_tier: "same"'
BRAIN_MARKER = "Independent Review (no advisor tool, no subagents)"
CODEX_MARKER = "run_codex_advisor_review"
CODEX_SEMANTIC_LADDER = "luna (haiku) → terra (sonnet) → sol (opus) → astra (fable)"
CODEX_MODEL_LADDER = "gpt-<version>-luna → gpt-<version>-terra → gpt-<version>-sol → gpt-<version>-astra"
ONE_UP_TIER = 'review_tier: "one-up"'


def _read(rel: str) -> str:
    return (REPO_ROOT / rel).read_text()


def test_directive_in_canonical_behavioral():
    text = _read(".claude/rules/behavioral.md")
    assert MARKER in text
    assert "advisor" in text.lower()
    # both the preferred tier AND its fallback must be named (no hard-coded model)
    assert TOP_TIER in text and OPUS_CLASS in text
    assert TOP_TIER_RULE in text
    assert SAME_TIER in text
    assert RETIRED not in text


def test_directive_in_brain_behavioral():
    """Brain has no `advisor` tool and brain-task-gate.sh bans every Agent/Task
    subagent except ironclaude:search-conversations — rule 23 routes the review
    obligation through a blind report-only reviewer WORKER, never the worker-side
    Agent-tool fallback."""
    text = _read("commander/src/brain/rules/behavioral.md")
    assert BRAIN_MARKER in text
    assert "spawn_worker" in text
    assert "worker_type=claude-opus" in text
    assert "report-only" in text
    assert "just reason it through" in text  # no-self-review obligation retained
    # the worker-side tier-relative Agent fallback must NOT leak into the Brain surface
    assert MARKER not in text
    assert TOP_TIER not in text and OPUS_CLASS not in text
    assert "Spawn a top-tier subagent via the `Agent` tool" not in text


def test_directive_in_agents_md():
    """Codex workers load AGENTS.md (root), NOT .claude/rules/behavioral.md. The advisor
    directive must exist there AND name the fixed-function broker, so a codex worker
    is not left advisor-less and a future edit cannot silently drop codex advisor parity."""
    text = _read("AGENTS.md")
    assert MARKER in text
    assert CODEX_MARKER in text
    assert "luna → terra → sol → astra" in text
    assert CODEX_MODEL_LADDER in text
    assert ONE_UP_TIER in text
    assert "Never run nested `codex exec`" in text
    assert SAME_TIER in text
    assert "capped at the Opus class" in text
    assert "only on explicit operator request" in text
    assert "tiered-up" not in text


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
    # Communication Profiles adds a 12th concept after Boy Scout Rule. Count
    # strings track the whole rule set, not Advisor Fallback specifically.
    # Keep these pins so future concept additions must update every count.
    assert "13 concepts" in text
    assert "(13 principles)" in text
    assert "13-principle template" in text
    assert TOP_TIER in text and OPUS_CLASS in text                # tier-relative fallback named
    assert text.count(CODEX_MARKER) >= 3
    assert text.count("luna→terra→sol→astra") >= 2
    assert "gpt-<version>-luna→gpt-<version>-terra→gpt-<version>-sol→gpt-<version>-astra" in text
    assert text.count(ONE_UP_TIER) >= 3
    assert RETIRED not in text
    assert "Fable if available" not in text
    assert text.count(SAME_TIER) >= 3
    assert text.count(TOP_TIER_RULE) >= 3


def test_codex_advisor_ladder_names_astra_requester_and_ceiling_without_claiming_fable():
    text = _read("worker/skills/advisor-fallback/SKILL.md")
    assert "`gpt-6-astra`" in text
    assert CODEX_SEMANTIC_LADDER in text
    assert CODEX_MODEL_LADDER in text
    assert "`gpt-6-sol` → `gpt-6-astra`" in text
    assert "`gpt-6-astra` → `gpt-6-astra`" in text
    assert "Astra remains a Codex model and does not satisfy an actual Claude Fable request." in text
    assert ONE_UP_TIER in text
    assert "gpt-<version>-<luna|terra|sol|astra>" in text
    assert SAME_TIER in text
    assert "capped at the Opus class" in text
    assert "opus → fable" not in text
    assert "one-tier-up" not in text


def test_directive_in_root_claude_md_matches_behavioral():
    claude = _read("CLAUDE.md")
    rules = _read(".claude/rules/behavioral.md")

    def block(text):
        return text.split("10. **Advisor Fallback", 1)[1].split("11. **No Workflow", 1)[0]

    assert block(claude) == block(rules)
    assert OPUS_CLASS in block(claude) and TOP_TIER_RULE in block(claude)
    assert SAME_TIER in block(claude)
    assert RETIRED not in block(claude)


def test_claude_advisor_reviewer_is_the_locked_reviewer_agent():
    """Design 2026-10-17 v120-completeness R1: the report-only Claude reviewer is ironclaude:reviewer."""
    text = _read("worker/skills/advisor-fallback/SKILL.md")
    assert 'subagent_type="ironclaude:reviewer"' in text
    assert 'subagent_type="Explore"' not in text
    assert "general-purpose" not in text
