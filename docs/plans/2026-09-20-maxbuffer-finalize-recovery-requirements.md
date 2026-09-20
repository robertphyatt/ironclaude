# maxBuffer + Deterministic-Finalize-Failure Recovery — Requirements

> **Created:** 2026-09-20
> **Status:** Operator-approved (brainstorming --scope=hold; "Full (all four)"; GIT_MAX_BUFFER=64MB; cap reuse=3; action name `reopen_for_edit`; Component A revised to stream+hash after the operator's legitimate->64MB-commit challenge).

Design: `docs/plans/2026-09-20-maxbuffer-finalize-recovery-design.md`. Closes the df69c3a4-class
finalize deadlock (a live production incident, NOT a Fable-review finding). Scope fixed (`hold`),
Full A+B+C+D. Local pytest + vitest only (no GitHub CI). No version bump. Commit/push
operator-gated; no trailers. **Invariant across every requirement: never auto-abandon, never
discard work — the reviewed frozen commit and worktree bytes are always preserved or restored.**

## R1 (Component A) — Content-size-independent finalize equality check + bounded auxiliary git buffers
The finalize integrity check `cumulativeBinaryEffect` (`worker/mcp-servers/workspace-manager/src/integration.ts:295-297`),
used for the byte-exact equality comparison at `integration.ts:849` (capture reviewed effect)
and `:858` (refuse if the rebased effect differs), MUST be made content-size independent so an
arbitrarily large legitimate commit finalizes: rewrite it to write each
`git diff --binary --full-index <a> <b>` to a temp file via a `spawnSync` stdout file
descriptor (git writes to disk, never Node memory), `sha256` each file with a streaming read,
compare the two hashes, delete the temp files (in a `finally`, never inside the worktree).
Byte-identical diffs MUST still be judged equal (behavior-preserving) and a divergent rebased
effect MUST still be refused. Separately, introduce one shared constant `GIT_MAX_BUFFER =
64 * 1024 * 1024` applied as `maxBuffer` on `runGit` (`git.ts:46`) and `runGitEnv` (`:57`), and
replace the three hardcoded `64 * 1024 * 1024` in `patchIdAggregateTellMerged`
(`git.ts:615/627/641`) with it — a safety net for the small auxiliary git calls only. A
`maxBuffer`/`ENOBUFS` overflow MUST throw a clear error naming the git argv and the overflow
cause. No shell (argv-only `spawnSync`; the redirect is an fd, not a shell `>`).

## R2 (Component C) — Seam-independent daemon surface for a persistently-failing finalize
`commit_worker` finalize failures MUST advance a per-worker **cumulative** finalize-failure
counter the daemon reads, incremented on **every** failure mode (mode-tagged
`drift`/`conflict`/`repair` AND untagged/`transient`) independent of the idle/reap/dead seams
(so a live, still-retrying worker is not starved). Once the counter crosses
`FINALIZE_DRIFT_RETRY_CAP` (3, `commander/src/ironclaude/main.py:894`), the daemon MUST fire the
existing `_finalize_recovery_alerted` one-shot Slack+Brain blocker even for a live, non-idle
worker. The `transient` branch (`main.py:1714-1715`) MUST surface-once instead of silent
do-nothing. The counter MUST be cleared when the finalize integrates or the worker leaves the
running set (mirror the cumulative-counter semantics at `main.py:890-893`). Daemon-only; no
git/worktree mutation.

## R3 (Component D) — Brain give-up bound + 6d wizard terminal branch (prompt only)
`commander/src/brain/rules/workflow.md` SHIP step 6 (`:440`) MUST gain: after K=3 consecutive
`commit_worker` failures with the same error for a worker, STOP calling `commit_worker`, pin a
decision-format blocker (machinery `:894-938`), and stop nudging /commit (count tracked in the
ledger). The "6d. Guided Integration-Recovery Wizard" (`:566-601`) MUST gain a terminal branch:
if rerebase/re-invoke returns the same failure, pin an operator blocker and stop (today's only
non-integrating exits leave the row frozen forever and re-attempt next cycle). Prompt-only; no
unit test (behavioral).

## R4 (Component B) — Sanctioned `reopen_for_edit` recovery action (the forward path)
A new reconcile recovery mode MUST atomically return a stuck finalization to editable, work
preserved: `reset --hard` the worktree to the reviewed frozen commit, transition
`ready_for_integration → active` (already an allowed transition, `worker/mcp-servers/workspace-manager/src/db.ts:30`),
delete the candidate and freeze refs (`integration.ts:210-214`), release the integration lock.
It MUST refuse when the row is not in a stuck-finalization state (wrong-state guard) and MUST
never discard the frozen commit. Expose it: a new `cli.ts` verb; a new action `reopen_for_edit`
in `_RECOVERY_ACTIONS` (`commander/src/ironclaude/orchestrator_mcp.py:4171`) routed through
`recover_worker_integration` (registry-derived authority, structured dicts, never raises); and a
6d wizard step offering it when other actions keep failing.

## R5 — TDD, falsifiability, dist, and consistency
Each executable component is TDD (RED before GREEN). A: vitest proving a >64MB-class diff still
finalizes (equality holds), a divergent rebased effect is still refused, the streamed temp-file
path is exercised and cleaned up, `runGit`/`runGitEnv` carry `GIT_MAX_BUFFER`, an over-cap
auxiliary output throws the clear error, and the three patchId sites reference the shared
constant. C: commander recovery-driver tests proving a deterministic finalize failure surfaces
exactly once for a live non-idle worker and for the transient/None case. B: vitest
`integration-cases.ts` for the ready→active rollback + ref cleanup + lock release +
worktree-at-frozen + wrong-state refusal, plus a commander test for the new action. D: no unit
test (behavioral; document "No tests required"). After the TS changes, rebuild
`worker/mcp-servers/workspace-manager/dist/` (`npm run build`) and stage it (`git add -f`) — the
daemon/Brain run `dist/`. Full commander pytest + workspace-manager vitest green (judge vitest by
"N passed | 0 failed", not exit code — the benign onTaskUpdate RPC timeout may flip the code).

## R6 — Out of scope (backlog)
Any TRANSITIONS-table change; version/release actions; streaming treatment of non-finalize
content-scaled git calls (code-review diffs, `git show`) — they degrade to the clean 64MB error,
not a deadlock. Also out: the separate F1 test-only finding (vacuous heartbeat-test assertions)
and the v1.1.11 review observations (dead `format_orphaned_unmerged`, CHANGELOG test-count
inconsistency) — those are a separate follow-up loop.
