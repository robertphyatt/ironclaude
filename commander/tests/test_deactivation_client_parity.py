"""Provider-aware professional-mode deactivation reporting contract."""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / "worker/skills/deactivate-professional-mode/SKILL.md"


def _section(text: str, start: str, end: str) -> str:
    return text.split(start, 1)[1].split(end, 1)[0]


def test_deactivation_accepts_both_human_invocation_surfaces():
    text = SKILL.read_text()
    assert "/deactivate-professional-mode" in text
    assert "/ironclaude:deactivate-professional-mode" in text
    assert "$ironclaude:deactivate-professional-mode" in text
    assert "programmatically" in text
    assert "subagent" in text
    assert "Then STOP" in text


def test_deactivation_uses_provider_native_state_check():
    text = SKILL.read_text()
    assert "## Provider-native state-manager calls" in text
    assert "mcp__plugin_ironclaude_state-manager__get_professional_mode" in text
    assert "Codex `state-manager` `get_professional_mode`" in text
    assert "active client's provider-native `get_professional_mode`" in text


def test_deactivation_has_three_outcomes_without_false_failure():
    text = SKILL.read_text()
    success = _section(text, "**If state is 'off'", "**If state is 'on' or 'undecided'")
    failure = _section(text, "**If state is 'on' or 'undecided'", "**If verification is unavailable")
    unknown = _section(text, "**If verification is unavailable", "### Step 4: STOP")

    assert "deactivation confirmed" in success
    assert "workflow_stage='idle'" not in success
    assert "Direct code changes permitted" not in success
    assert "Git write operations permitted" not in success
    assert "Safety guardrails removed" not in success
    assert "Other user, project, sandbox, and approval controls remain in force" in success
    assert "deactivation FAILED" in failure
    assert "DEACTIVATION STATUS UNKNOWN" in unknown
    assert "Do not report deactivation as failed" in unknown
    assert "Missing or invalid Codex thread_source" in unknown
    assert "exact tool error" in unknown
    assert "manual SQLite" not in unknown


def test_deactivation_never_supplies_broad_manual_sql_or_self_deactivates():
    text = SKILL.read_text()
    assert "UPDATE sessions SET professional_mode='off'" not in text
    assert "Always show sqlite" not in text
    assert "Do not try to call `set_professional_mode` with value 'off'" in text
