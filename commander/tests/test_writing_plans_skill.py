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


def test_writing_plans_preserves_original_authority_above_requirements_file():
    text = " ".join(_skill_text().lower().split())
    assert "operator directives and full brainstorming are original authority" in text
    assert "derived, operator-reviewed contract" in text
    assert "audit" in text and "before using it" in text
    assert "professional blind review must evaluate original requirements" not in text


def test_writing_plans_requires_pre_ready_holistic_parity_audit():
    text = " ".join(_skill_text().lower().split())
    assert "operator → brainstorming → requirements/design → plan parity audit" in text
    assert "before" in text and "mark_plan_ready" in text
    assert "do not start from the derived requirements document" in text
    for contract in (
        "task ids",
        "depends_on",
        "allowed_files",
        "steps and commands",
        "tests and expected results",
    ):
        assert contract in text, f"plan parity audit lost '{contract}'"


def test_writing_plans_requires_live_source_grounding():
    text = _skill_text().lower()
    assert "ground every plan fact in live source" in text
    assert "verified rather than inferred" in text


def test_writing_plans_forbids_review_history_in_plan_artifacts():
    text = _skill_text().lower()
    assert "no review history in plan artifacts" in text
    # The prohibition must name the blind-review rationale it protects
    assert "blind-reviewer input" in text or "blind review" in text


def test_writing_plans_does_not_instruct_recording_review_rounds():
    # Negative (design Testing Strategy item 3): the skill must not instruct
    # authors to build the review-history antipatterns that contaminated a prior
    # plan. These exact foundation-plan table headers are NOT used by the
    # prohibition bullet (which names "reviewer-drift audits" and "round-by-round
    # obligation tables"), so asserting their absence is non-tautological and
    # catches reintroduction of the actual contamination vocabulary.
    text = _skill_text().lower()
    assert "regression obligations retained" not in text, \
        "writing-plans must not carry a foundation-style review-obligations table"
    assert "reviewer-driven correction" not in text, \
        "writing-plans must not carry a foundation-style reviewer-drift audit column"


def test_writing_plans_routes_human_and_machine_artifacts_to_profiles():
    text = _skill_text()
    assert "elements-of-style" in text
    assert "write-lossless-ai-messages" in text
    assert text.index("elements-of-style") < text.index("Create plan document")
    assert text.index("write-lossless-ai-messages") < text.index("Create machine-readable plan JSON")
    for protected in (
        "schema", "task IDs", "depends_on", "allowed_files", "ordered steps",
        "commands", "expected results", "paths", "authority", "actionable state",
    ):
        assert protected in text

    parity_section = text[text.index("Operator → brainstorming → requirements/design → plan parity audit"):]
    assert "same" in parity_section and "commands" in parity_section


def test_writing_plans_limits_json_compression_to_natural_language_fields():
    text = " ".join(_skill_text().lower().split())
    assert "natural-language fields only" in text
    assert "must not drop fields" in text
    assert "must not change exact technical values" in text
    assert "ic_lossless_ai_messages_active" in text
    assert "must not appear in plan artifacts" in text
