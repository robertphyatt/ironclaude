"""Presence guard: the writing-plans skill must carry the plan-authoring invariants —
the verification rules (verify the artifact that RAN; absence is not evidence) and the
execution-invariants checklist plans must include — so a future edit cannot silently
drop guidance that exists because these defects recurred across many review rounds."""
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILL = "worker/skills/writing-plans/SKILL.md"

VERIFY_RAN = "Verify the artifact that RAN"
ABSENCE = "Absence is not evidence"
INVARIANTS = "Execution invariants (carry these into the plan)"
WRAPPER_LOG = "ironclaude-mcp-state-manager.log"   # the PPID -> executed-path evidence source
FINGERPRINT = "runtime-fingerprint"                 # plugin_root/bundle_path/bundle_sha256
GIT_LOG_S = "git log -S"                            # before calling missing code a defect


def _read(rel: str) -> str:
    return (REPO_ROOT / rel).read_text()


def test_verification_rules_present():
    text = _read(SKILL)
    assert VERIFY_RAN in text
    assert ABSENCE in text
    # the decisive evidence sources must be named, not left to be rediscovered
    assert WRAPPER_LOG in text
    assert FINGERPRINT in text
    assert GIT_LOG_S in text


def test_execution_invariants_section_present():
    text = _read(SKILL)
    assert INVARIANTS in text
    # the invariants that actually bit, each named (exact case as written in the skill)
    for marker in ("does NOT persist", "nomatch", "git -C", "Foreground `sleep`", "add -f"):
        assert marker in text, marker


def test_rationalization_rows_present():
    text = _read(SKILL)
    assert "deployed build is obviously" in text
    assert "Empty output means" in text


def test_verification_quality_invariants_present():
    """The author-side counterpart to the reviewer's 'cannot fail' archetype.

    Each marker is copied character-for-character from the bullet it guards — the rule
    the fourth marker states applies to this test itself.
    """
    text = _read(SKILL)
    for marker in ("you have not measured",
                   "Prove every verification can fail",
                   "must not perturb its own measurement",
                   "EXACT case and spacing"):
        assert marker in text, marker
