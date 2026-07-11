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
    assert "tier-up" not in text.lower(), \
        "writing-plans SKILL.md still references 'tier-up' — the review belongs in executing-plans now"
