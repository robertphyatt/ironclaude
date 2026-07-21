"""The executing-plans skill must retain the relocated policy-gated tier-up review.

The tier-up plan review moved out of writing-plans Phase 4.5 into executing-plans
(Step 1.5, after create_plan, gated by tier_up_review_policy). This guards against
silent deletion of that step during future skill edits, mirroring
test_version_consistency.py's approach.
"""
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILL = REPO_ROOT / "worker" / "skills" / "executing-plans" / "SKILL.md"
README = REPO_ROOT / "README.md"
ROADMAP = REPO_ROOT / "docs" / "plans" / "2026-07-20-v1-1-overall-roadmap.md"


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


def test_blind_review_receives_original_requirements_and_no_revision_context():
    text = _read()
    for artifact in (
        "<REQUIREMENTS_MD_PATH>",
        "<DESIGN_MD_PATH>",
        "<PLAN_MD_PATH>",
        "<PLAN_JSON_PATH>",
    ):
        assert artifact in text, f"blind review packet lost {artifact}"
    assert "operator-approved requirements" in text.lower()
    for forbidden in (
        "author rationale",
        "prior-round findings",
        "repair explanation",
        "revision history",
        "previous reviewer",
    ):
        assert forbidden in text.lower(), f"blindness rule lost '{forbidden}'"


def test_review_order_and_finding_verification_preserve_operator_guidance():
    text = _read().lower()
    req_design = text.index("requirements → design")
    design_plan = text.index("design → plan")
    technical = text.index("technical executability")
    assert req_design < design_plan < technical
    assert "reviewer output is evidence, not authority" in text
    assert "independently verify" in text
    assert "unsupported findings" in text


def test_first_failure_forces_holistic_audit_not_patch_churn():
    text = _read()
    lower = text.lower()
    assert "first verified `has-issues`" in lower
    assert "holistic invariant audit" in lower
    assert "finding-by-finding" in lower and "forbidden" in lower
    assert "requirements/design" in lower and "retreat" in lower
    assert "plan-only" in lower and "regenerate" in lower
    assert "brand-new blind reviewer" in lower
    assert "Revise / Proceed / Abort" not in text
    assert "After 3 rounds" not in text


def _self_update_section() -> str:
    text = _read()
    heading = "### IronClaude self-update boundary"
    start = text.index(heading)
    end = text.find("\n### ", start + len(heading))
    return text[start:] if end == -1 else text[start:end]


def _normalized(text: str) -> str:
    return " ".join(text.lower().split())


def test_self_update_boundary_requires_same_task_runtime_and_behavioral_proof():
    section = _normalized(_self_update_section())
    for concept in (
        "installs or updates its own codex plugin",
        "cachebuster before the final build",
        "fully quit and relaunch codex",
        "reopen the same native task",
        "run_diagnostics.runtime",
        "provider-active manifest",
        "plugin_root",
        "plugin_version",
        "manifest_sha256",
        "bundle_sha256",
        "client",
        "expected_runtime",
        "startup hashes",
        "intended installed-cache hashes",
        "different-stage",
        "changed:true",
    ):
        assert concept in section, f"self-update boundary lost '{concept}'"
    assert "get_resume_state.session_id` to equal the preserved native task id" in section
    assert "require `runtime activation match ... pass`" in section
    assert (
        "missing runtime fields, any fingerprint/identity mismatch, or a failed "
        "behavioral transition must fail closed"
    ) in section


def test_self_update_boundary_rejects_installation_only_evidence_and_limits_recovery():
    section = _normalized(_self_update_section())
    assert "compaction or reinstall alone is insufficient" in section
    assert (
        "`codex plugin list`, reinstall success, filesystem parity, and source/cache "
        "hashes are installation evidence only; none proves that the current process "
        "loaded the intended runtime"
    ) in section
    assert "create a new task only if same-task verification fails" in section
    assert "main orchestrator" in section
    for forbidden_subsystem in (
        "do not add a workflow stage",
        "do not add an mcp tool",
        "do not add a cache copier",
        "do not add transcript migration",
    ):
        assert forbidden_subsystem in section


def test_codex_install_docs_default_to_full_restart_and_same_task_reopen():
    readme = _normalized(README.read_text())
    assert "fully quit and relaunch codex" in readme
    assert "reopen the same" in readme
    assert "start a new codex task" not in readme
    assert "runtime fingerprint" in readme and "changed:true" in readme
    assert "replacement task" in readme and "verification fails" in readme


def test_roadmap_names_wave_1r_and_removes_unconditional_new_task_boundary():
    roadmap = _normalized(ROADMAP.read_text())
    assert "| 1r |" in roadmap and "mp-w12" in roadmap
    assert "reopen the same" in roadmap
    assert "opening a new task" not in roadmap
    assert "runtime fingerprint" in roadmap and "changed:true" in roadmap
