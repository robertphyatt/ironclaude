"""Closed inventory for every approved LLM communication construction seam."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
import re

import pytest

from ironclaude.communication_profiles import CONSTRUCTION_PROFILES


REPO_ROOT = Path(__file__).resolve().parents[2]
DECLARATION_RE = re.compile(r"IRONCLAUDE_LLM_PATH:\s*([a-z][a-z0-9_]*)")

# Each tuple identifies one source construction seam, not merely one profile.
# Brain and worker profiles have multiple construction seams by design.
EXPECTED_DECLARATIONS = {
    "commander/src/ironclaude/main.py": Counter({
        "commander_brain": 2,
        "managed_worker": 1,
    }),
    "commander/src/ironclaude/orchestrator_mcp.py": Counter({
        "advisor": 1,
        "claude_grader": 1,
        "codex_grader": 1,
        "managed_worker": 1,
        "session_summarizer": 1,
    }),
    "commander/src/ironclaude/grader.py": Counter({"local_grader": 1}),
    "commander/src/ironclaude/shadow_grader.py": Counter({"shadow_grader": 1}),
    "worker/skills/activate-professional-mode/SKILL.md": Counter({
        "direct_professional_session": 1,
    }),
    "worker/skills/use-fable-subagent/scripts/run_fable_subagent.py": Counter({
        "actual_fable_subagent": 1,
    }),
    "worker/hooks/plan-validator.sh": Counter({"hook_validator": 1}),
}


def _discover_declarations(path: Path) -> Counter[str]:
    return Counter(DECLARATION_RE.findall(path.read_text(encoding="utf-8")))


def _validate_profile_ids(discovered_ids: set[str]) -> None:
    approved_ids = set(CONSTRUCTION_PROFILES)
    unknown = discovered_ids - approved_ids
    missing = approved_ids - discovered_ids
    if unknown or missing:
        raise ValueError(
            f"communication construction inventory mismatch: "
            f"unknown={sorted(unknown)} missing={sorted(missing)}"
        )


def test_source_declarations_match_closed_construction_inventory():
    discovered_ids: set[str] = set()
    for relative_path, expected in EXPECTED_DECLARATIONS.items():
        actual = _discover_declarations(REPO_ROOT / relative_path)
        assert actual == expected, relative_path
        discovered_ids.update(actual)

    _validate_profile_ids(discovered_ids)
    assert discovered_ids == set(CONSTRUCTION_PROFILES)


def test_unclassified_low_level_construction_seam_is_rejected(tmp_path):
    fixture = tmp_path / "unclassified_llm_transport.py"
    fixture.write_text(
        "# IRONCLAUDE_LLM_PATH: unclassified_low_level_seam\n"
        "subprocess.run(['model-cli', '--prompt', untrusted_input])\n",
        encoding="utf-8",
    )

    unclassified = set(CONSTRUCTION_PROFILES) | set(_discover_declarations(fixture))
    with pytest.raises(ValueError, match="unclassified_low_level_seam"):
        _validate_profile_ids(unclassified)
