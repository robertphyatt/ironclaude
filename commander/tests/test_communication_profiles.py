from pathlib import Path

import pytest

from ironclaude.communication_profiles import (
    CommunicationProfileError,
    CONSTRUCTION_PROFILES,
    apply_communication_profile,
    load_profile,
    skill_invocation,
)


def test_closed_inventory_has_every_approved_path():
    assert CONSTRUCTION_PROFILES == {
        "direct_professional_session": "human",
        "commander_brain": "mixed",
        "managed_worker": "ai",
        "advisor": "ai",
        "claude_grader": "ai",
        "codex_grader": "ai",
        "local_grader": "ai",
        "shadow_grader": "ai",
        "session_summarizer": "ai",
        "hook_validator": "machine",
    }


def test_unknown_construction_path_fails_closed():
    with pytest.raises(CommunicationProfileError, match="unclassified"):
        apply_communication_profile("new_llm_path", "system")


def test_missing_skill_fails_closed(tmp_path: Path):
    with pytest.raises(CommunicationProfileError, match="missing or unreadable"):
        load_profile("ai", skills_root=tmp_path)


def test_profiles_load_exact_canonical_skill_bytes():
    skills_root = Path(__file__).resolve().parents[2] / "worker" / "skills"
    canonical_human = (skills_root / "elements-of-style" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    canonical_ai = (
        skills_root / "write-lossless-ai-messages" / "SKILL.md"
    ).read_text(encoding="utf-8")
    human = load_profile("human")
    ai = load_profile("ai")
    mixed = load_profile("mixed")
    assert human == canonical_human
    assert ai == canonical_ai
    assert mixed == f"{canonical_human}\n\n{canonical_ai}"


def test_invalid_utf8_skill_fails_closed(tmp_path: Path):
    skill = tmp_path / "write-lossless-ai-messages" / "SKILL.md"
    skill.parent.mkdir()
    skill.write_bytes(b"\xff")

    with pytest.raises(CommunicationProfileError, match="missing or unreadable"):
        load_profile("ai", skills_root=tmp_path)


def test_machine_profile_loads_no_prose_text():
    assert load_profile("machine") == ""


def test_invalid_destination_fails_closed():
    with pytest.raises(CommunicationProfileError, match="unknown communication destination"):
        load_profile("unknown")


def test_programmatic_application_disables_session_marker_output():
    prompt = apply_communication_profile("claude_grader", "system")
    assert "Do not emit IC_LOSSLESS_AI_MESSAGES_ACTIVE" in prompt


@pytest.mark.parametrize(
    ("client", "expected"),
    [
        ("claude", "/write-lossless-ai-messages"),
        ("codex", "$ironclaude:write-lossless-ai-messages"),
    ],
)
def test_managed_worker_invocation_is_client_native(client: str, expected: str):
    assert skill_invocation("managed_worker", client) == expected


def test_invalid_client_fails_closed():
    with pytest.raises(CommunicationProfileError, match="unsupported communication client"):
        skill_invocation("managed_worker", "unknown")


def test_non_ai_path_cannot_invoke_ai_skill():
    with pytest.raises(CommunicationProfileError, match="does not select AI profile"):
        skill_invocation("hook_validator", "claude")
