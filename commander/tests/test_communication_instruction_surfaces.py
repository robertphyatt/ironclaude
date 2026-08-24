"""Communication-profile coverage for direct instruction and Stop-hook surfaces."""

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[2]
HEADING = "Recipient-Based Communication Profiles"
SKILLS = (
    "ironclaude:elements-of-style",
    "ironclaude:write-lossless-ai-messages",
)
INSTRUCTION_SURFACES = (
    ROOT / "AGENTS.md",
    ROOT / "CLAUDE.md",
    ROOT / ".claude/rules/behavioral.md",
    ROOT / "worker/CLAUDE.md",
    ROOT / "commander/CLAUDE.md",
    ROOT / "commander/src/ironclaude/templates/worker_agents.md",
    ROOT / "commander/src/ironclaude/templates/worker_claude_md.md",
)


def test_instruction_surfaces_declare_recipient_based_communication_profiles():
    for surface in INSTRUCTION_SURFACES:
        text = surface.read_text(encoding="utf-8")
        assert HEADING in text, surface
        for skill in SKILLS:
            assert skill in text, surface


def test_instruction_surface_profile_heading_uses_unique_next_ordinal():
    for surface in INSTRUCTION_SURFACES:
        text = surface.read_text(encoding="utf-8")
        headings = list(re.finditer(r"^(\d+)\. \*\*(.+?)\*\*", text, re.MULTILINE))
        profile = [match for match in headings if match.group(2) == HEADING]
        assert len(profile) == 1, surface
        ordinals = [int(match.group(1)) for match in headings]
        assert len(ordinals) == len(set(ordinals)), surface
        profile_ordinal = int(profile[0].group(1))
        prior_ordinals = [
            int(match.group(1))
            for match in headings
            if match.start() < profile[0].start()
        ]
        assert profile_ordinal == max(prior_ordinals) + 1, surface


def test_activation_declares_human_profile_before_first_provider_state_call():
    activation = (
        ROOT / "worker/skills/activate-professional-mode/SKILL.md"
    ).read_text(encoding="utf-8")
    declaration = "IRONCLAUDE_LLM_PATH: direct_professional_session"
    skill_load = "Before the first substantive human-facing response, load and apply\n`ironclaude:elements-of-style`."
    state_call = "Call the active client's provider-native `get_professional_mode`."
    assert declaration in activation
    assert skill_load in activation
    assert "If the skill is missing or unreadable, report\nincomplete installation and leave professional mode unchanged." in activation
    assert activation.index(declaration) < activation.index(skill_load) < activation.index(state_call)


def test_stop_hook_extends_existing_rigor_check_for_human_facing_prose():
    hook = (ROOT / "worker/hooks/get-back-to-work-impl.sh").read_text(encoding="utf-8")
    rigor_prompt = hook.split('RIGOR_PROMPT="', 1)[1].split('\n\nRIGOR_SCHEMA=', 1)[0]
    assert hook.count('run_check "rigor"') == 1
    assert 'run_check "style"' not in hook
    assert "HUMAN-FACING COMMUNICATION" in rigor_prompt
    assert "quoted source material" in rigor_prompt
    assert "code, commands, paths, errors, schemas, sentinels" in rigor_prompt


def test_plan_validator_declares_machine_construction_path_without_prompt_injection():
    validator = (ROOT / "worker/hooks/plan-validator.sh").read_text(encoding="utf-8")
    declaration = "IRONCLAUDE_LLM_PATH: hook_validator"
    assert declaration in validator
    assert "destination machine" in validator
    prompt_transport = validator.split("call_validation_llm() {", 1)[1]
    assert declaration not in prompt_transport
