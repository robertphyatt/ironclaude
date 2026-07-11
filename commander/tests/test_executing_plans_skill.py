"""The executing-plans skill must retain the relocated policy-gated tier-up review.

The tier-up plan review moved out of writing-plans Phase 4.5 into executing-plans
(Step 1.5, after create_plan, gated by tier_up_review_policy). This guards against
silent deletion of that step during future skill edits, mirroring
test_version_consistency.py's approach.
"""
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILL = REPO_ROOT / "worker" / "skills" / "executing-plans" / "SKILL.md"


def _read() -> str:
    return SKILL.read_text()


def test_executing_plans_has_policy_gated_tier_up_step():
    text = _read()
    assert "Tier-up plan review (policy-gated)" in text, \
        "executing-plans SKILL.md lost the policy-gated tier-up review step"
    assert "tier_up_review_policy" in text, \
        "tier-up step no longer reads the tier_up_review_policy config"
    assert "submit_tier_up_review" in text, \
        "tier-up step no longer records the review via submit_tier_up_review"


def test_executing_plans_tier_up_is_blind_and_policy_aware():
    text = _read().lower()
    assert "blind" in text, "tier-up review no longer describes a blind reviewer"
    for token in ("enforced", "commander-choice", "off"):
        assert token in text, f"tier-up policy branch lost '{token}'"
    assert "fable_unavailable.json" in text, \
        "tier-up review lost the on-disk Fable-availability check"
