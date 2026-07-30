"""Plan-review prompts must preserve brainstorming authority for both clients."""

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = REPO_ROOT / "commander" / "src" / "brain" / "rules" / "workflow.md"
SYSTEM_PROMPT = REPO_ROOT / "commander" / "src" / "brain" / "system_prompt.md"


def _read(path: Path) -> str:
    return " ".join(path.read_text().lower().split())


def test_brain_workflow_has_plan_specific_blindness_contract():
    text = _read(WORKFLOW)
    assert "generic code and artifact review" in text
    assert "plan-review exception" in text
    assert "full scoped brainstorming" in text
    assert "blind to prior review" in text
    assert "not blind to operator intent" in text


def test_brain_plan_review_selects_reviewer_on_active_client():
    text = _read(WORKFLOW)
    assert "trusted active client" in text
    assert "claude code" in text
    assert "codex" in text
    assert "always opus" not in text


def test_brain_plan_review_audits_authority_before_mechanics():
    text = _read(WORKFLOW)
    section = text.split("### plan review checklist", 1)[1]
    authority = section.index("operator directives")
    brainstorming = section.index("full scoped brainstorming")
    derived = section.index("roadmap/design")
    requirements = section.index("requirements")
    human = section.index("human plan")
    machine = section.index("machine plan")
    mechanics = section.index("mechanical steps")
    assert authority < brainstorming < derived < requirements < human < machine < mechanics
    assert "requirements and design are derived evidence" in section
    assert "challenge the frame" in section


def test_brain_plan_review_forbids_token_saving_and_fails_closed():
    text = _read(WORKFLOW)
    assert "do not omit or summarize brainstorming to save tokens" in text
    assert "ironclaude:remembering-conversations" in text
    assert "fail closed" in text
    assert "complete relevant turns" in text


def test_brain_system_prompt_carries_same_plan_review_contract():
    text = _read(SYSTEM_PROMPT)
    for contract in (
        "full scoped brainstorming",
        "operator directives",
        "roadmap/design",
        "requirements",
        "human plan",
        "machine plan",
        "semantic merge",
        "authority inversion",
        "blind to prior review",
        "not blind to operator intent",
    ):
        assert contract in text


def test_plan_review_is_not_artifact_only():
    workflow = _read(WORKFLOW)
    checklist = workflow.split("### plan review checklist", 1)[1]
    assert "reviewer gets only the artifact" not in checklist
    assert "objective containing only" not in checklist
