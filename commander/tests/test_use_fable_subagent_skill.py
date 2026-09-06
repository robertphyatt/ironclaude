"""Contract tests for the Codex actual-Claude-Fable consultation skill."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SKILL_ROOT = ROOT / "worker/skills/use-fable-subagent"
SKILL = SKILL_ROOT / "SKILL.md"
LAUNCHER = SKILL_ROOT / "scripts/run_fable_subagent.py"


def _text() -> str:
    return SKILL.read_text(encoding="utf-8")


def test_skill_is_packaged_and_discovers_explicit_actual_fable_requests():
    text = _text()
    assert text.startswith("---\nname: use-fable-subagent\n")
    assert "Use when a Codex user explicitly requests an actual Claude Fable subagent" in text
    assert "Native Codex subagents cannot satisfy" in text
    assert "Do not substitute" in text
    assert "Opus" in text and "Sonnet" in text and "Haiku" in text


def test_skill_routes_through_bundled_launcher_and_stops_on_failure():
    text = _text()
    assert "scripts/run_fable_subagent.py" in text
    assert "--prompt-file" in text
    assert "FABLE_SUBAGENT_ERROR" in text
    assert "nonzero" in text.lower()
    assert "stop" in text.lower()
    assert LAUNCHER.exists()


def test_skill_secures_and_always_cleans_exact_temporary_prompt():
    text = _text().lower()
    for required in (
        "owner-only",
        "mode 0700",
        "mode 0600",
        "regular file",
        "not a symlink",
        "success, failure, or interruption",
        "exact private prompt file",
    ):
        assert required in text


def test_skill_requires_lossless_complete_report_only_packet():
    text = _text()
    assert "ironclaude:write-lossless-ai-messages" in text
    for required in (
        "task",
        "evidence",
        "constraints",
        "requested deliverable",
        "report-only",
    ):
        assert required in text.lower()


def test_parent_retains_authority_and_verifies_fable_findings():
    text = _text().lower()
    for retained in (
        "orchestration",
        "workflow-state changes",
        "staging",
        "commits",
        "task sequencing",
    ):
        assert retained in text
    assert "repository writes" in text
    assert "independently verify" in text
    assert "effective model" in text and "message.model" in text


def test_skill_has_three_pressure_scenarios_with_no_live_inference():
    scenarios = sorted((SKILL_ROOT / "test-scenarios").glob("*.md"))
    assert [path.name for path in scenarios] == [
        "01-without-skill.md",
        "02-with-skill.md",
        "03-edge-cases.md",
    ]
    combined = "\n".join(path.read_text(encoding="utf-8") for path in scenarios)
    assert "RED" in combined
    assert "GREEN" in combined
    assert "live Claude" in combined
    assert "no fallback" in combined.lower()
