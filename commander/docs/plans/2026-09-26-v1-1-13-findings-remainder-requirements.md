# v1.1.13 Findings — Remainder — Requirements

> **Created:** 2026-09-26
> **Status:** Operator-approved
> **Design:** docs/plans/2026-09-26-v1-1-13-findings-remainder-design.md
> **Parent:** docs/plans/2026-09-26-v1-1-13-findings-requirements.md (lineage 132)

## Context

Lineage 132 ran Tasks 1, 2, 3, 4, 7, 8 and 9. Each passed its task-boundary code review, and all are staged. Task 5, the reaper repo list, exposed a plan gap. Its new skip-vanished behavior broke `test_daemon.py` `TestRunMaintenance`: that fixture seeds a nonexistent local `/repo`, and `test_daemon.py` was not in Task 5's allowed files. The operator chose "Retreat to brainstorming". This effort covers only the remaining work.

## Operator directives (this retreat)

- "Retreat to brainstorming". This replaced an in-place plan amendment.
- Scope: "Remainder-only design". The reviewed, staged tasks are kept as they are.
- Fixture fix: "A: real tmp_path dir". `_commander_conn_with_repo` uses a real temporary directory. The operator rejected monkeypatching `os.path.isdir`.
- Vitest gate: the operator invoked the Boy Scout rule ("What is my boy scout rule?"), so the pre-existing `onTaskUpdate` timeout must not simply be tolerated. Choice: "Bisect now, fix next loop". This loop bisects the culprit; a follow-up loop fixes it and restores a strict exit-0 gate.
- All lineage-132 directives still apply. These include: grammar enforcement with a liberal length; folding into the unpushed v1.1.13; no push or deploy without an explicit go; no commit trailers; local tests only; the seam owns all completion.

## Acceptance criteria

1. `_managed_repositories` normalizes worktree-path repos to the primary repo, skips vanished local paths, and dedupes local paths by git common dir. Remote entries are unchanged. This is original item 2.
2. All `test_worktree_reaper.py` and `test_daemon.py` tests pass with the new behavior. The three `TestRunMaintenance` tests that use `_commander_conn_with_repo` point at a real temporary directory and reach the reaper. A reverse check proves this: pointing the fixture at a nonexistent path makes the two posting tests fail.
3. The terminal-failure surface uses its own once-only set, pruned where its counter is pruned. A terminal outcome that is not a counted failure resets the consecutive count. These are original items 4 and 5.
4. Each vitest test file is run in isolation, repeatedly, with one log per run. The findings note records which files reproduce `Timeout calling "onTaskUpdate"` (or that none do in isolation), quoting each run's summary lines. No product or test code changes. The fix is a follow-up loop.
5. The v1.1.13 CHANGELOG and README cover every shipped item from lineage 132 and this remainder. The CHANGELOG notes the vitest defect's status: bisected, fix pending.
6. Full suites:
   - pytest reports 0 failed;
   - hook `test-openai-backend.sh` reports 0 failed;
   - vitest reports 0 failed tests, with every `Errors` entry being the documented `onTaskUpdate` timeout. Any other error fails the gate.
