"""The writing-plans skill must NOT contain a Tier-Up Plan Review phase.

The tier-up review was RELOCATED to the executing-plans skill (it now runs after
create_plan loads the plan, gated by tier_up_review_policy). This guards against
the old soft Phase 4.5 silently reappearing in writing-plans. The presence of the
relocated step in executing-plans is guarded by test_executing_plans_skill.py.

Lives in commander/tests/ (the pytest suite root) and reaches into worker/ via
REPO_ROOT, exactly as test_version_consistency.py does.
"""
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILL = REPO_ROOT / "worker" / "skills" / "writing-plans" / "SKILL.md"


def _skill_text() -> str:
    return SKILL.read_text()


def test_writing_plans_no_longer_contains_tier_up_phase():
    text = _skill_text()
    assert "Phase 4.5: Tier-Up Plan Review" not in text, \
        "writing-plans SKILL.md still has the old Tier-Up Plan Review phase — it was relocated to executing-plans"


def test_writing_plans_requires_operator_requirements_in_both_artifacts():
    text = _skill_text().lower()
    assert "operator-approved requirements" in text
    assert "requirements_file" in text
    assert "human plan" in text and "machine plan" in text


def test_writing_plans_requires_pre_ready_holistic_parity_audit():
    text = _skill_text().lower()
    assert "requirements → design → plan parity audit" in text
    assert "before" in text and "mark_plan_ready" in text
    for contract in (
        "task ids",
        "depends_on",
        "allowed_files",
        "steps and commands",
        "tests and expected results",
    ):
        assert contract in text, f"plan parity audit lost '{contract}'"
