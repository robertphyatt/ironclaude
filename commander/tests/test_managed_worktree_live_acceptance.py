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
        "unrelated_primary_owner_no_push",
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
    assert integration["recycled_heads"] == integration["integrated_commits"]
    assert integration["worktrees_recycled"] is True
    assert integration["recycled_clean"] is True
    conflict = payload["scenarios"]["conflict_recovery"]["evidence"]
    assert conflict["first_attempt_preserved"] is True
    assert len(conflict["repaired_commit"]) == 40
    assert conflict["repaired_worktree_recycled"] is True
    assert conflict["repaired_worktree_clean"] is True
    coexistence = payload["scenarios"]["unrelated_primary_owner_no_push"]["evidence"]
    assert coexistence["owner_row_observed"] is True
    assert coexistence["owner_row_unchanged"] is True
    assert len(coexistence["owner_workspace_guid"]) == 36
    assert len(coexistence["worker_integrated_commit"]) == 40
    assert coexistence["worker_recycled_clean"] is True
    assert coexistence["remote_count"] == 0


def test_live_acceptance_contains_no_mock_success_escape_hatch():
    source = RUNNER.read_text()
    assert "MagicMock" not in source
    assert "mock_success" not in source
    assert "commander_push_count = 0" in source
    assert "git worktree list --porcelain" in source
