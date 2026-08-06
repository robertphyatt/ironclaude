"""Real-Git acceptance harness tests for managed worktrees."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).parents[2]
RUNNER = REPO_ROOT / "commander/scripts/managed_worktree_live_acceptance.py"


def _provider(tmp_path: Path, name: str) -> Path:
    command = tmp_path / name
    command.write_text(f"#!/bin/sh\nprintf '{name} acceptance provider\\n'\n")
    command.chmod(0o755)
    return command


def test_live_acceptance_uses_real_git_evidence_and_six_scenarios(tmp_path):
    report = tmp_path / "report.json"
    result = subprocess.run(
        [
            sys.executable,
            str(RUNNER),
            "--claude-command", str(_provider(tmp_path, "claude")),
            "--codex-command", str(_provider(tmp_path, "codex")),
            "--plugin-root", str(REPO_ROOT / "worker"),
            "--report", str(report),
        ],
        text=True,
        capture_output=True,
        timeout=120,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(report.read_text())
    assert payload["status"] == "PASS"
    assert payload["commander_push_count"] == 0
    assert set(payload["scenarios"]) == {
        "claude_provider",
        "codex_provider",
        "direct_isolation",
        "commander_integration",
        "conflict_recovery",
        "primary_fencing_no_push",
    }
    assert all(item["status"] == "PASS" for item in payload["scenarios"].values())
    isolation = payload["scenarios"]["direct_isolation"]["evidence"]
    assert len(set(isolation["workspace_guids"])) == 2
    assert len(set(isolation["index_paths"])) == 2
    assert len(set(isolation["staged_trees"])) == 2
    assert isolation["primary_head_before"] == isolation["primary_head_after"]
    assert isolation["worktrees_removed"] is True
    integration = payload["scenarios"]["commander_integration"]["evidence"]
    assert all(len(oid) == 40 for oid in integration["integrated_commits"])
    assert integration["worktrees_removed"] is True
    conflict = payload["scenarios"]["conflict_recovery"]["evidence"]
    assert conflict["first_attempt_preserved"] is True
    assert len(conflict["repaired_commit"]) == 40
    fencing = payload["scenarios"]["primary_fencing_no_push"]["evidence"]
    assert fencing["ownership_row_observed"] is True
    assert fencing["fenced_attempt_preserved"] is True
    assert "fenced while primary checkout is owned" in fencing["fenced_error"]
    assert fencing["ownership_released"] is True
    assert fencing["finalized_after_resume"] is True


def test_live_acceptance_contains_no_mock_success_escape_hatch():
    source = RUNNER.read_text()
    assert "MagicMock" not in source
    assert "mock_success" not in source
    assert "commander_push_count = 0" in source
    assert "git worktree list --porcelain" in source
