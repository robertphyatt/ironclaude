"""Direct-human Git and checkout skills must preserve Claude/Codex parity."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SKILLS = {
    name: ROOT / "worker" / "skills" / name / "SKILL.md"
    for name in (
        "commit",
        "commit-and-push",
        "push",
        "use-primary-checkout",
        "return-to-managed-worktree",
    )
}


def _text(name: str) -> str:
    return SKILLS[name].read_text(encoding="utf-8")


def test_all_five_skills_require_exact_provider_native_human_invocation():
    for name in SKILLS:
        text = _text(name)
        assert f"/{name}" in text
        assert f"/ironclaude:{name}" in text
        assert f"$ironclaude:{name}" in text
        assert "absolute Markdown skill link" in text
        assert "UserPromptSubmit" in text
        assert "model-generated" in text
        assert "subagent" in text
        assert "programmatic" in text


def test_skills_consume_server_held_authority_without_nonce_or_evidence_inputs():
    for name in SKILLS:
        text = _text(name)
        assert "server-held" in text
        assert "Do not supply" in text
        assert "nonce" in text
        assert "expected_evidence" in text
        assert "human_channel" in text
        assert "never returned to model conversation" in text


def test_git_skills_use_only_the_matching_public_operation():
    mapping = {
        "commit": "workspace-manager `commit`",
        "commit-and-push": "workspace-manager `commit_and_push`",
        "push": "workspace-manager `push`",
    }
    for name, call in mapping.items():
        text = _text(name)
        assert call in text
        assert "repository_path" in text
        assert "workspace_guid" in text
        if name == "push":
            assert "Do not supply a commit message" in text
        else:
            assert "message" in text


def test_checkout_skills_preserve_truthful_verified_switches():
    primary = _text("use-primary-checkout")
    managed = _text("return-to-managed-worktree")
    assert "use_primary_checkout" in primary
    assert "Workspace isolation disabled by operator." in primary
    assert "return_to_managed_worktree" in managed
    assert "Workspace isolation restored." in managed
    assert "Managed worktree: <managed_worktree_path>" in managed
