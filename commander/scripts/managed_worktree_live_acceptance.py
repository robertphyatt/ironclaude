#!/usr/bin/env python3
"""Bounded real-Git acceptance for Claude/Codex managed worktrees."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import sqlite3
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Any

from ironclaude.workspace_client import WorkspaceClient, WorkspaceClientError


WORKTREE_LIST_PROOF = "git worktree list --porcelain"


def run(argv: list[str], *, cwd: str | Path | None = None,
        env: dict[str, str] | None = None, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv, cwd=cwd, env=env, text=True, capture_output=True,
        timeout=timeout, check=False,
    )


def checked(argv: list[str], *, cwd: str | Path | None = None,
            env: dict[str, str] | None = None) -> str:
    result = run(argv, cwd=cwd, env=env)
    if result.returncode != 0:
        raise RuntimeError(
            f"command failed ({result.returncode}): {argv!r}: "
            f"{result.stderr.strip() or result.stdout.strip()}"
        )
    return result.stdout.strip()


def git(cwd: str | Path, *args: str, env: dict[str, str]) -> str:
    return checked(["git", *args], cwd=cwd, env=env)


def provider_evidence(command: str, env: dict[str, str]) -> dict[str, Any]:
    argv = [*shlex.split(command), "--version"]
    result = run(argv, env=env, timeout=20)
    if result.returncode != 0:
        raise RuntimeError(
            f"provider command failed: {argv!r}: "
            f"{result.stderr.strip() or result.stdout.strip()}"
        )
    return {
        "argv": argv,
        "returncode": result.returncode,
        "output": (result.stdout.strip() or result.stderr.strip())[:500],
        "fresh_process": True,
    }


def assignment_evidence(client: WorkspaceClient, repository: str, worker_id: str,
                        owner: str | None = None) -> dict[str, Any]:
    guid = str(uuid.uuid4())
    payload: dict[str, Any] = {
        "repository_path": repository,
        "workspace_guid": guid,
        "worker_id": worker_id,
        "integration_target": "main",
    }
    if owner is not None:
        payload["owner_session_id"] = owner
        return client.allocate(payload)
    reserved = client.allocate(payload)
    return client.bind({
        "repository_path": repository,
        "repository_identity": reserved["repository_identity"],
        "workspace_guid": reserved["workspace_guid"],
        "worker_id": worker_id,
        "owner_session_id": str(uuid.uuid4()),
        "expected_lifecycle": reserved["lifecycle_status"],
        "expected_worktree_path": reserved["worktree_path"],
        "expected_branch": reserved["branch"],
        "expected_base_commit": reserved["base_commit"],
        "expected_current_head": reserved["current_head"],
    })


def commit_evidence(assignment: dict[str, Any], env: dict[str, str]) -> dict[str, str]:
    source = assignment["worktree_path"]
    branch = git(source, "symbolic-ref", "--quiet", "--short", "HEAD", env=env)
    head = git(source, "rev-parse", "--verify", "HEAD^{commit}", env=env)
    if branch != assignment["branch"]:
        raise RuntimeError("live checkout branch differs from assignment")
    local_ref = f"refs/heads/{branch}"
    if git(source, "rev-parse", "--verify", f"{local_ref}^{{commit}}", env=env) != head:
        raise RuntimeError("live checkout ref differs from HEAD")
    return {
        "canonicalBranch": branch,
        "localRef": local_ref,
        "stagedTree": git(source, "write-tree", env=env),
        "parentOid": head,
    }


def finalize(client: WorkspaceClient, repository: str, assignment: dict[str, Any],
             message: str, env: dict[str, str]) -> dict[str, Any]:
    return client.finalize({"command": {
        "repositoryPath": repository,
        "workspaceGuid": assignment["workspace_guid"],
        "providerRootSessionId": assignment["owner_session_id"],
        "message": message,
        **commit_evidence(assignment, env),
    }})


def write_file(worktree: str, name: str, content: str, env: dict[str, str]) -> None:
    path = Path(worktree) / name
    path.write_text(content)
    git(worktree, "add", "--", name, env=env)


def pass_scenario(evidence: dict[str, Any]) -> dict[str, Any]:
    return {"status": "PASS", "evidence": evidence}


def execute(claude_command: str, codex_command: str, plugin_root: Path) -> dict[str, Any]:
    scenarios: dict[str, dict[str, Any]] = {}
    commander_push_count = 0
    with tempfile.TemporaryDirectory(prefix="ironclaude-live-acceptance-") as temp:
        temp_root = Path(temp)
        home = temp_root / "home"
        (home / ".claude").mkdir(parents=True)
        env = {**os.environ, "HOME": str(home), "GIT_TERMINAL_PROMPT": "0"}

        scenarios["claude_provider"] = pass_scenario(
            provider_evidence(claude_command, env),
        )
        scenarios["codex_provider"] = pass_scenario(
            provider_evidence(codex_command, env),
        )

        repository = temp_root / "repository"
        repository.mkdir()
        git(repository, "init", "-b", "main", env=env)
        git(repository, "config", "user.email", "acceptance@example.com", env=env)
        git(repository, "config", "user.name", "IronClaude Acceptance", env=env)
        (repository / "shared.txt").write_text("base\n")
        git(repository, "add", "shared.txt", env=env)
        git(repository, "commit", "-m", "initial", env=env)

        def client_runner(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
            return subprocess.run(argv, env=env, **kwargs)

        client = WorkspaceClient(plugin_root, runner=client_runner)

        direct = [
            assignment_evidence(client, str(repository), "direct-claude", str(uuid.uuid4())),
            assignment_evidence(client, str(repository), "direct-codex", str(uuid.uuid4())),
        ]
        primary_before = git(repository, "rev-parse", "HEAD", env=env)
        write_file(direct[0]["worktree_path"], "claude.txt", "claude isolated\n", env)
        write_file(direct[1]["worktree_path"], "codex.txt", "codex isolated\n", env)
        index_paths = [
            git(item["worktree_path"], "rev-parse", "--git-path", "index", env=env)
            for item in direct
        ]
        staged_trees = [
            git(item["worktree_path"], "write-tree", env=env) for item in direct
        ]
        inventory = git(repository, "worktree", "list", "--porcelain", env=env)
        if not all(item["worktree_path"] in inventory for item in direct):
            raise RuntimeError(f"{WORKTREE_LIST_PROOF} omitted direct assignment")
        primary_after = git(repository, "rev-parse", "HEAD", env=env)
        for item in direct:
            git(item["worktree_path"], "reset", "--hard", env=env)
            git(item["worktree_path"], "clean", "-fd", env=env)
            client.abandon({
                "repository_path": str(repository),
                "workspace_guid": item["workspace_guid"],
                "owner_session_id": item["owner_session_id"],
            })
            git(repository, "worktree", "remove", item["worktree_path"], env=env)
            git(repository, "branch", "-D", item["branch"], env=env)
        direct_removed = all(not Path(item["worktree_path"]).exists() for item in direct)
        if len(set(index_paths)) != 2 or len(set(staged_trees)) != 2 \
                or primary_before != primary_after or not direct_removed:
            raise RuntimeError("direct isolation lacks matching real Git evidence")
        scenarios["direct_isolation"] = pass_scenario({
            "workspace_guids": [item["workspace_guid"] for item in direct],
            "index_paths": index_paths,
            "staged_trees": staged_trees,
            "primary_head_before": primary_before,
            "primary_head_after": primary_after,
            "worktree_inventory": inventory,
            "worktrees_removed": direct_removed,
        })

        workers = [
            assignment_evidence(client, str(repository), "worker-a"),
            assignment_evidence(client, str(repository), "worker-b"),
        ]
        write_file(workers[0]["worktree_path"], "worker-a.txt", "worker a\n", env)
        write_file(workers[1]["worktree_path"], "worker-b.txt", "worker b\n", env)
        integrated = [
            finalize(client, str(repository), workers[0], "integrate worker a", env),
            finalize(client, str(repository), workers[1], "integrate worker b", env),
        ]
        commits = [item.get("integratedCommit", "") for item in integrated]
        recycled_heads = [
            git(item["worktree_path"], "rev-parse", "HEAD", env=env)
            for item in workers
        ]
        workers_recycled = all(Path(item["worktree_path"]).is_dir() for item in workers)
        recycled_clean = all(
            git(item["worktree_path"], "status", "--porcelain", env=env) == ""
            for item in workers
        )
        if not all(len(oid) == 40 for oid in commits) \
                or recycled_heads != commits or not workers_recycled or not recycled_clean:
            raise RuntimeError("Commander integration lacks commit or recycle evidence")
        scenarios["commander_integration"] = pass_scenario({
            "integrated_commits": commits,
            "primary_head": git(repository, "rev-parse", "HEAD", env=env),
            "recycled_heads": recycled_heads,
            "worktrees_recycled": workers_recycled,
            "recycled_clean": recycled_clean,
        })

        conflict = assignment_evidence(client, str(repository), "worker-conflict")
        write_file(conflict["worktree_path"], "shared.txt", "worker conflict\n", env)
        (repository / "shared.txt").write_text("primary conflict\n")
        git(repository, "add", "shared.txt", env=env)
        git(repository, "commit", "-m", "advance conflicting target", env=env)
        conflict_error = ""
        try:
            finalize(client, str(repository), conflict, "conflicting worker", env)
        except WorkspaceClientError as exc:
            conflict_error = str(exc)
        first_attempt_preserved = bool(conflict_error) and Path(conflict["worktree_path"]).exists()
        if not first_attempt_preserved:
            raise RuntimeError("conflict did not preserve assignment")
        (Path(conflict["worktree_path"]) / "shared.txt").write_text(
            "primary conflict\nworker conflict resolved\n",
        )
        git(conflict["worktree_path"], "add", "shared.txt", env=env)
        git(conflict["worktree_path"], "-c", "core.editor=true", "rebase", "--continue", env=env)
        repaired = finalize(client, str(repository), conflict, "reviewed conflict repair", env)
        repaired_commit = repaired.get("integratedCommit", "")
        repair_recycled = Path(conflict["worktree_path"]).is_dir()
        repair_head = git(conflict["worktree_path"], "rev-parse", "HEAD", env=env)
        repair_clean = git(conflict["worktree_path"], "status", "--porcelain", env=env) == ""
        if len(repaired_commit) != 40 or repair_head != repaired_commit \
                or not repair_recycled or not repair_clean:
            raise RuntimeError("conflict repair lacks integration and recycle evidence")
        scenarios["conflict_recovery"] = pass_scenario({
            "first_attempt_preserved": first_attempt_preserved,
            "first_error": conflict_error[:500],
            "repaired_commit": repaired_commit,
            "repaired_worktree_recycled": repair_recycled,
            "repaired_worktree_clean": repair_clean,
        })

        owner = assignment_evidence(client, str(repository), "primary-owner")
        worker = assignment_evidence(client, str(repository), "worker-coexist")
        write_file(worker["worktree_path"], "coexist.txt", "coexisting worker\n", env)
        workspace_db = home / ".claude/ironclaude-workspaces.db"
        connection = sqlite3.connect(workspace_db)
        connection.execute(
            "INSERT INTO primary_checkout_owners "
            "(repository_identity, workspace_guid, owner_session_id) VALUES (?, ?, ?)",
            (
                owner["repository_identity"],
                owner["workspace_guid"],
                owner["owner_session_id"],
            ),
        )
        connection.commit()
        owner_row_before = connection.execute(
            "SELECT repository_identity, workspace_guid, owner_session_id "
            "FROM primary_checkout_owners "
            "WHERE repository_identity = ? AND workspace_guid = ? AND owner_session_id = ?",
            (
                owner["repository_identity"],
                owner["workspace_guid"],
                owner["owner_session_id"],
            ),
        ).fetchone()
        connection.close()
        finalized = finalize(client, str(repository), worker, "coexisting worker", env)
        integrated_commit = finalized.get("integratedCommit", "")
        connection = sqlite3.connect(workspace_db)
        owner_row_after = connection.execute(
            "SELECT repository_identity, workspace_guid, owner_session_id "
            "FROM primary_checkout_owners "
            "WHERE repository_identity = ? AND workspace_guid = ? AND owner_session_id = ?",
            (
                owner["repository_identity"],
                owner["workspace_guid"],
                owner["owner_session_id"],
            ),
        ).fetchone()
        connection.close()
        worker_recycled = (
            Path(worker["worktree_path"]).is_dir()
            and git(worker["worktree_path"], "rev-parse", "HEAD", env=env) == integrated_commit
            and git(worker["worktree_path"], "status", "--porcelain", env=env) == ""
        )
        owner_row_unchanged = owner_row_before is not None and owner_row_after == owner_row_before
        if len(integrated_commit) != 40 or not worker_recycled or not owner_row_unchanged:
            raise RuntimeError("unrelated primary owner did not coexist with finalization")
        scenarios["unrelated_primary_owner_no_push"] = pass_scenario({
            "owner_row_observed": owner_row_before is not None,
            "owner_row_unchanged": owner_row_unchanged,
            "owner_workspace_guid": owner["workspace_guid"],
            "worker_integrated_commit": integrated_commit,
            "worker_recycled_clean": worker_recycled,
            "remote_count": len(git(repository, "remote", env=env).splitlines()),
        })

    return {
        "status": "PASS",
        "scenarios": scenarios,
        "commander_push_count": commander_push_count,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--claude-command", required=True)
    parser.add_argument("--codex-command", required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument(
        "--plugin-root",
        type=Path,
        default=Path(__file__).resolve().parents[2] / "worker",
    )
    args = parser.parse_args()
    try:
        report = execute(args.claude_command, args.codex_command, args.plugin_root)
        exit_code = 0
    except Exception as exc:  # bounded report for acceptance diagnosis
        report = {"status": "FAIL", "error": str(exc)}
        exit_code = 1
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, indent=2, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
