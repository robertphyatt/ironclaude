"""Guards for the sonnet spawn bias (POST-v1.1.1 queue item 2).

These are contradiction tests, not decoration: each one fails if the specific
defect it exists to catch comes back.
"""

from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_SYSTEM_PROMPT = _REPO / "commander" / "src" / "brain" / "system_prompt.md"
_ORCHESTRATOR = _REPO / "commander" / "src" / "ironclaude" / "orchestrator_mcp.py"
_EXECUTING_PLANS = _REPO / "worker" / "skills" / "executing-plans" / "SKILL.md"

_NO_GATE_LINE = (
    "Tier choice alone never lowers the grade or affects approval "
    "— recommend, do not gate."
)
_SONNET_DEFAULT = "claude-sonnet — THE DEFAULT."


class TestBrainSystemPrompt:
    def test_no_asymmetric_recommendation_following(self):
        """:217 told the Brain to follow the grader only when it recommends UP.

        A sonnet recommendation was explicitly outside what must be followed,
        which contradicts workflow.md:692 ("Default choice for most
        implementation work"). Fails if that phrasing returns.
        """
        text = _SYSTEM_PROMPT.read_text()
        assert "when it recommends opus or fable" not in text

    def test_no_opus_leaning_cost_argument(self):
        """:217 argued compaction cost > the sonnet/opus price gap — an
        argument that runs toward opus in a paragraph about tier choice."""
        text = _SYSTEM_PROMPT.read_text()
        assert "often exceeds the cost difference between sonnet and opus" not in text

    def test_retry_escalation_fact_survives(self):
        """The auto-escalate-on-retry sentence is real behaviour
        (orchestrator_mcp.py:2713-2717), not a bias. It must NOT be collateral
        damage of removing the counter-bias."""
        text = _SYSTEM_PROMPT.read_text()
        assert "auto-escalates to opus on retry" in text


class TestGraderMenus:
    def test_no_gate_line_in_all_three_copies(self):
        """Three grader prompts carry this menu. A partial update is the
        change-together defect: exactly 3, not 'at least 1'."""
        text = _ORCHESTRATOR.read_text()
        assert text.count(_NO_GATE_LINE) == 3

    def test_sonnet_marked_default_in_all_three_copies(self):
        text = _ORCHESTRATOR.read_text()
        assert text.count(_SONNET_DEFAULT) == 3


class TestExecutingPlansSubagentTier:
    def test_subagent_model_tier_step_present(self):
        """The Subagent Prompt Construction Guide specified subagent_type and
        max_turns and said nothing about model tier."""
        text = _EXECUTING_PLANS.read_text()
        assert "**Subagent model tier:**" in text
        assert "the default for plan-task execution" in text

    def test_tier_step_anchors_on_a_deterministic_signal(self):
        """A conditioning step that names no signal is a condition nobody can
        evaluate. IC_ROLE=worker is exported on every spawn path."""
        text = _EXECUTING_PLANS.read_text()
        assert 'echo "${IC_ROLE:-}"' in text

    def test_tier_step_is_inform_not_gate(self):
        text = _EXECUTING_PLANS.read_text()
        assert "This is INFORM-only" in text
