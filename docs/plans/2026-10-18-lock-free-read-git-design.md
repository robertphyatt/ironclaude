# Lock-Free Read-Only Git Design

> **Created:** 2026-10-18
> **Status:** Design Complete

Loop: full — hook code, workspace-manager git helpers and Commander git calls (git index operations in every user repository).

## Summary

On 2026-10-18 `git add` failed repeatedly in `/Users/roberthyatt/Code/roleplaying-agents` with `Unable to create '.git/index.lock': File exists`. Three sessions (ASR/startup, Portrait, Feature flipper) were executing in the same primary checkout. Every lock was 0 bytes, had no `lsof` holder, no running git process and no `index.stash.*` residue; mtimes 00:33, 10:37 and 11:58 MDT. Their own git calls were small and instant. That is a git process that took the lock and was killed before writing, not a long live hold. The security-guidance plugin (the 2026-09-27 cause) is disabled in that repo.

An inventory of IronClaude found that no git command it launches avoids the optional index lock (`GIT_OPTIONAL_LOCKS` / `--no-optional-locks` appear nowhere), and that read-only `git status` / `git diff` run constantly in the session's repository: `execution-drift-check.sh:152` on every Bash Pre/PostToolUse, `submit_task` and SubagentStop while executing or reviewing, and `subagent-drift-detector.sh:41` on SubagentStop. On a 27 MB / 121k-entry index each refresh rewrites the index under `index.lock`; a hook process killed mid-refresh leaves exactly the observed file.

Operator direction (2026-10-18): "make the git actions a lot more crisp"; "lock, then stage, then unlock in rapid succession"; "Let's fix the lock issue while the evidence is fresh"; scope "All three (Recommended)" (hooks, Commander, workspace-manager); design approved "Yes, plan it (Recommended)".

## Requirements

1. **R1 — confirm before changing.** On a scratch repository (never roleplaying-agents' index), show: (a) a `git status` killed with SIGKILL mid-refresh leaves a 0-byte `index.lock`; (b) `git --no-optional-locks status` and `GIT_OPTIONAL_LOCKS=0 git status` take no lock and leave the index file unmodified; (c) whether `GIT_OPTIONAL_LOCKS=0 git diff --name-only` takes the lock, and whether adding `-c diff.autoRefreshIndex=false` stops it without listing a stat-only-touched file (content unchanged) as changed. Also time `git --no-optional-locks status --porcelain=v1 -z --untracked-files=all` once in roleplaying-agents (read-only; takes no lock). Record results in a findings file.
2. **R2 — hooks never take the index lock for reads.** `execution-drift-check.sh:152` and `subagent-drift-detector.sh:41` run `git --no-optional-locks status …`.
3. **R3 — workspace-manager reads never take the optional lock.** `runGit` and `runGitEnv` (`worker/mcp-servers/workspace-manager/src/git.ts:67, :80`) and the separate `spawnSync` at `integration.ts:321` pass `GIT_OPTIONAL_LOCKS=0` in the child environment. Required locks for real writes (`add`, `commit`, `reset`, `rebase`, `read-tree -m -u`, `checkout`, `merge`) are unaffected by this variable. Read-only `diff` calls additionally get `-c diff.autoRefreshIndex=false` only if R1(c) shows `GIT_OPTIONAL_LOCKS=0` alone does not stop `diff` from taking the lock and the flag does not change `--name-only` results.
4. **R4 — Commander reads never take the optional lock.** `orchestrator_mcp.py:1935` (`git diff --stat HEAD`) and `shadow_grader.py:214` (`git … diff`) run with `GIT_OPTIONAL_LOCKS=0` in their environment (plus the R3 diff flag under the same condition).
5. **Writes stay one short command.** Staging remains a single `git add` (lock → write → unlock). No new locking layer, retry loop or lock deletion is added.

## Architecture

- One rule for all of IronClaude: a git command IronClaude launches only to read never writes the index. Status uses `--no-optional-locks` (hooks) or `GIT_OPTIONAL_LOCKS=0` (child environment); diff adds `diff.autoRefreshIndex=false` if R1 requires it.
- Choke points: the two hook lines; `runGit`/`runGitEnv` plus one stray `spawnSync` in workspace-manager; two Python subprocess calls in the Commander.

## Error Handling

No behavior change on success. A git failure surfaces exactly as today. If R1 shows the lock-free forms change command output, the plan stops and reports instead of applying them.

## Testing Strategy

- R1 is its own investigation task with recorded evidence.
- Hooks: a test that runs the drift hooks' status path against a scratch repo while `index.lock` already exists and proves the read succeeds (a locking `git status` fails or warns; a lock-free one does not), and that the index file's mtime is unchanged.
- workspace-manager: a vitest that the child environment passed by `runGit`/`runGitEnv` contains `GIT_OPTIONAL_LOCKS=0`, plus a behavioral test that a read-only status through `runGit` succeeds while a pre-existing `index.lock` is present.
- Commander: tests that the two subprocess calls pass `GIT_OPTIONAL_LOCKS=0`.
- Full Commander suite, `make test-hooks`, workspace-manager and state-manager suites.

## Implementation Notes

- Never touch roleplaying-agents' index or lock; only the read-only, lock-free timing command runs there.
- Backlog, not this loop: hook-side timing logs; stale-lock detection/reporting; the model's own `git status` calls (instruction-level).
