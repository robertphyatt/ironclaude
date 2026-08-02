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


def test_plan_review_receives_derived_artifacts_and_no_revision_context():
    text = _read()
    lower = text.lower()
    for artifact in (
        "<REQUIREMENTS_MD_PATH>",
        "<DESIGN_MD_PATH>",
        "<PLAN_MD_PATH>",
        "<PLAN_JSON_PATH>",
    ):
        assert artifact in text, f"blind review packet lost {artifact}"
    assert "derived requirements" in lower
    for forbidden in (
        "prior reviewer findings",
        "repair coaching",
        "revision history",
        "reviewer identities",
    ):
        assert forbidden in lower, f"blindness rule lost '{forbidden}'"


def test_plan_review_receives_full_brainstorming_authority_chain():
    text = _read().lower()
    normalized = " ".join(text.split())
    markers = (
        "operator directives",
        "full scoped brainstorming",
        "roadmap/design",
        "derived requirements",
        "human plan",
        "machine plan",
    )
    positions = [text.index(marker) for marker in markers]
    assert positions == sorted(positions), "plan-review authority chain is out of order"
    assert "operator directives" in text
    assert "settled brainstorming decisions outrank every derived" in text
    assert "must not omit or compress" in normalized
    assert "save tokens" in normalized


def test_plan_review_blindness_keeps_intent_but_excludes_review_history():
    text = _read().lower()
    assert "blind to prior reviewer findings" in text
    assert "not blind to operator intent" in text
    for included in ("rationale", "alternatives", "approvals", "clarifications"):
        assert included in text
    for excluded in (
        "prior reviewer findings",
        "verdicts",
        "repair coaching",
        "reviewer identities",
        "revision history",
    ):
        assert excluded in text


def test_plan_review_recovers_compacted_context_and_fails_closed():
    text = _read().lower()
    assert "ironclaude:remembering-conversations" in text
    assert "compacted" in text
    assert "complete relevant turns" in text
    assert "token-saving synopsis" in text
    assert "fail closed" in text
    assert "do not substitute" in text


def test_plan_review_dispatch_is_provider_native_with_shared_contract():
    text = _read()
    normalized = " ".join(text.split())
    assert "get_professional_mode" in text
    assert "Claude Code" in text and "`Agent`" in text
    assert "Codex" in text and "`codex exec" in text
    assert "complete current artifact contents inline" in normalized
    assert "same authority order" in normalized
    assert "same materiality" in normalized


def test_plan_review_hunts_semantic_frame_drift_before_executability():
    text = _read().lower()
    frame = text.index("challenge the derived frame")
    technical = text.index("technical executability")
    assert frame < technical
    for archetype in (
        "semantic merge",
        "semantic collapse",
        "substitution",
        "lost independence",
        "authority inversion",
    ):
        assert archetype in text


def test_review_order_and_finding_verification_preserve_operator_guidance():
    text = _read().lower()
    req_design = text.index("roadmap/design → derived requirements")
    design_plan = text.index("derived requirements → human")
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
    # One review per plan lineage: a HAS-ISSUES verdict now dispatches a mandatory
    # tier-up advisor instead of a second blind review.
    assert "mandatory tier-up fix advisor" in lower
    assert "advisor-remediated" in lower
    assert "exactly one blind review" in lower
    assert "brand-new blind reviewer" not in lower
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


def test_executing_plans_regeneration_carries_no_review_history():
    text = _read().lower()
    assert "carries no prior-review content forward" in text


def test_reviewer_archetypes_cover_verification_quality():
    """The detector half of the verification-quality rules.

    An author-side invariant only warns; a reviewer archetype detects, and the reviewer
    has source access. Neither an unmeasured `expected:` nor a guard the change itself
    moves was covered by the original five archetypes.

    The last two markers were added 2026-07-31 for the same reason, from six defects in
    four loops that a blind reviewer caught and the author did not: a guard evaluated at
    writing time rather than against the state after every task lands, and a count taken
    from an agent's prose summary rather than from a file the author opened.
    """
    text = _read()
    for marker in ("predicted rather than measured",
                   "the change itself moves",
                   "defused by a later step of the same plan",
                   "provenance is an agent summary"):
        assert marker in text, marker
