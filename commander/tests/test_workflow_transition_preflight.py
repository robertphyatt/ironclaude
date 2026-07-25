"""Regression contracts for workflow-transition caller preflight.

These tests intentionally inspect the shipped skill contracts.  The skills are
the transition callers; keeping their preflight discipline explicit prevents a
future documentation edit from reintroducing redundant MCP transition calls.
"""
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SKILLS = {
    "brainstorming": REPO_ROOT / "worker" / "skills" / "brainstorming" / "SKILL.md",
    "writing-plans": REPO_ROOT / "worker" / "skills" / "writing-plans" / "SKILL.md",
    "executing-plans": REPO_ROOT / "worker" / "skills" / "executing-plans" / "SKILL.md",
}


def _read(name: str) -> str:
    return SKILLS[name].read_text().lower()


def test_every_transition_caller_requires_resume_preflight_and_single_call():
    for name in SKILLS:
        text = _read(name)
        assert "get_resume_state" in text, f"{name} lost its resume-state preflight"
        assert "provider-native root session" in text, f"{name} lost native session validation"
        assert "equal-target" in text, f"{name} lost same-target suppression"
        assert "one different-target" in text, f"{name} no longer limits transition calls"
        assert "changed:true" in text, f"{name} does not require a changed transition result"
        assert "unexpected `changed:false`" in text, f"{name} does not distinguish an unexpected no-op"
        assert "fresh `get_resume_state` read" in text, \
            f"{name} permits retrying without a fresh state read"
        assert "no blind retry" in text, f"{name} permits unsafe transition retries"


def test_preflight_fails_closed_when_identity_cannot_be_confirmed():
    for name in SKILLS:
        text = _read(name)
        assert "fail closed" in text, f"{name} must stop on missing or mismatched identity"


def test_create_plan_reload_remains_a_domain_operation_not_a_skipped_transition():
    text = _read("executing-plans")
    assert "create_plan reload" in text
    assert "exempt" in text
    assert "create_plan" in text


def test_brainstorming_delegates_debugging_stage_entry_to_skill_bridge():
    text = _read("brainstorming")
    assert "skill-state-bridge" in text
    assert "do not call `mark_debugging`" in text


def _assert_local_preflight(text: str, action: str, target: str, occurrence: int = 0) -> None:
    """Require the preflight directly before an actionable transition call."""
    positions = []
    start = 0
    while True:
        position = text.find(action, start)
        if position < 0:
            break
        positions.append(position)
        start = position + len(action)

    assert len(positions) > occurrence, f"missing actionable transition {action}"
    position = positions[occurrence]
    local = text[max(0, position - 500): position + 500]
    assert "mandatory direct transition preflight" in local, \
        f"{action} must have nearby preflight, not only a document-global rule"
    assert f"target `{target}`" in local, \
        f"{action} preflight must name target {target}"
    assert "different-target result" in local and "once" in local, \
        f"{action} must skip equal targets and invoke once when distinct"


def test_named_transition_calls_have_local_preflight_coverage():
    brainstorming = _read("brainstorming")
    writing = _read("writing-plans")
    executing = _read("executing-plans")

    _assert_local_preflight(brainstorming, "mcp__plugin_ironclaude_state-manager__mark_brainstorming", "brainstorming")
    _assert_local_preflight(brainstorming, "mcp__plugin_ironclaude_state-manager__mark_design_ready", "design_ready")
    _assert_local_preflight(writing, "mcp__plugin_ironclaude_state-manager__mark_plan_ready", "plan_ready")
    _assert_local_preflight(executing, "mcp__plugin_ironclaude_state-manager__start_execution", "executing")
    _assert_local_preflight(executing, "mcp__plugin_ironclaude_state-manager__mark_executing", "executing")

    retreat_action = "mcp__plugin_ironclaude_state-manager__retreat"
    _assert_local_preflight(executing, retreat_action, "brainstorming", occurrence=0)
    _assert_local_preflight(executing, retreat_action, "brainstorming", occurrence=1)
