# Worktree Allocate Failure — Investigation Design

> **Created:** 2026-10-02
> **Status:** Design Complete
> **Scope mode:** hold
> **Requirements:** docs/plans/2026-10-02-worktree-allocate-investigation-requirements.md

## Summary

After the operator rebooted to clear swap, the Commander cannot allocate managed worktrees for `roleplaying-agents`. `allocate` exceeds its 60s timeout, and each timeout orphans a worker-id reservation. This loop gathers the evidence the read-only guard blocks: the DB, the git internals, and the allocate code path. It then writes a findings note. It changes nothing. Repair and v1.1.16 are separate loops that rely on these findings.

## Approach

This is the only viable approach. The operator wants the mess untangled, and repairing it blind would risk destroying data, possibly including worktree `13cf1c2c`. Every command is read-only. The single exception is copying the DB into the session scratchpad, which leaves the original untouched.

## Components

1. **DB snapshot:** `cp` the `ironclaude-workspaces.db` file and its `-wal` and `-shm` files into the scratchpad.
2. **DB dump:** run `sqlite3 -readonly` on the snapshot for the schema, then for nonterminal, `d1501*` and dead-session rows, plus lease and lock tables.
3. **Git state:** worktree list, `ironclaude/*` branches with their ahead counts versus main, recovery refs, and the reflog around 10:07.
4. **13cf1c2c forensics:** DB rows for that GUID, its branch and refs, and any reaper or allocate log lines that mention it.
5. **Allocate path:** read the workspace-manager allocate implementation and the orchestrator's 60s timeout. Measure the repo with `git count-objects -vH`, a `git ls-files` count, `.gitattributes`, LFS and hooks.
6. **Findings note:** `docs/plans/2026-10-02-worktree-allocate-findings.md`.

## Testing Strategy

Investigation only, so there are no tests. Evidence is the command output quoted in the findings note.
