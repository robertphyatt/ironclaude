# maxBuffer + Deterministic-Finalize-Failure Recovery — Design

> **Created:** 2026-09-20
> **Status:** Design Complete
> **Type:** Bug fix + recovery hardening. Closes the df69c3a4-class finalize deadlock.
> **Scope mode:** hold (Full A+B+C+D, operator-confirmed).

## Summary

A managed worker (`df69c3a4`) deadlocked during finalize. Root cause traced end-to-end
(pre-compaction investigation subagent `ab08f666`, all paths read directly): `commit_worker`
→ `finalize` verb → `finalizeCommanderLocalCommit` → `continueFrozenFinalization` →
`cumulativeBinaryEffect` runs `runGit(['diff','--binary','--full-index',base,head])`
(`integration.ts:295-297`) through `runGit`, which calls `spawnSync` with **no `maxBuffer`**
(`git.ts:45-50`; `runGitEnv` identical `:56-61`) → Node's **1MB default** → **ENOBUFS** on a
14k-line binary `decisions.json` diff.

Decisively, that throw is at `integration.ts:849`, **after** the durable
`active → ready_for_integration` transition at `:930` (a single autocommitting `UPDATE`,
`db.ts:456-459`, **not** inside a `db.transaction`). So the assignment is durably stuck at
`ready_for_integration`; the finalize **deterministically re-fails every retry**; the Brain
"6d. Guided Integration-Recovery Wizard" (`workflow.md:566-601`) loops forever
(rerebase → re-invoke `commit_worker` → same ENOBUFS) with **no give-up bound and no terminal
branch**; and the daemon's bounded one-shot surface is **starved** because it fires only from
idle/reap seams a still-retrying worker never reaches. The operator was forced into raw
`assignments.db` CAS surgery (the "[Modify Shared Resources]" prompt is Claude Code's own
permission classifier, not an IronClaude rule).

This is **not** a Fable-review finding; it is a live production incident.

Two failure surfaces, four coordinated changes. The proximate cause (a content-scaled git diff
overflowing a fixed buffer) is fixed by making the finalize equality check content-size
independent, plus a bounded-buffer net on the auxiliary git calls (A). The systemic cause (a
deterministic finalize failure
has no bounded, surfaced, or sanctioned-forward path) is closed by a daemon bound (C), a Brain
bound (D), and a first-class rollback tool (B).

**Invariant across all four:** never auto-abandon, never discard work — the reviewed frozen
commit and worktree bytes are always preserved or restored, never dropped.

## Architecture

Component A is independent and fixes *this* ENOBUFS. Components B/C/D close the *general*
deterministic-finalize deadlock and are useful even without A (any future deterministic
finalize failure — not just ENOBUFS — now surfaces, bounds, and has a forward path). Settled
knobs (operator-confirmed): `GIT_MAX_BUFFER = 64MB`; cap reuses the existing
`FINALIZE_DRIFT_RETRY_CAP = 3`; the Component-B action/verb is named `reopen_for_edit`.

## Components

### Component A — Content-size-independent finalize equality check + bounded git buffers (`worker/mcp-servers/workspace-manager/src/integration.ts` + `git.ts`)

**Why a fixed cap is insufficient (operator challenge).** `cumulativeBinaryEffect`
(`integration.ts:295-297`) exists solely for a **byte-exact equality comparison**:
`continueFrozenFinalization` captures `reviewedEffect = cumulativeBinaryEffect(base_commit,
frozenCommit)` at `:849`, then at `:858` refuses if
`cumulativeBinaryEffect(expectedTarget, integratedCommit) !== reviewedEffect` — the security
invariant that the rebase introduced **no un-reviewed content**. Its output therefore scales
with commit content size, so **any fixed `maxBuffer` cap merely relocates the deadlock**: a
legitimate large-asset commit (base85 `--binary` inflation pushes even a ~50MB file over 64MB)
would ENOBUFS at finalize, and `reopen_for_edit` is no recourse for a single legitimate large
asset (nothing to "split"). The fix must make the check **content-size independent**, not just
raise the ceiling.

- **Stream+hash the equality check (behavior-preserving).** Rewrite the two
  `cumulativeBinaryEffect` uses so each `git diff --binary --full-index <a> <b>` is written to a
  temp file (`spawnSync` with `stdio` stdout redirected to an open file descriptor, so git
  writes to disk — never buffered in Node memory), then `sha256`-hashed with a **streaming**
  read; compare the two 32-byte hashes; delete the temp files (in a `finally`, and never inside
  the worktree). Byte-identical diffs produce identical hashes, so the invariant is unchanged —
  but memory is O(1) regardless of commit size, so an arbitrarily large legitimate commit
  finalizes. Keep the no-shell contract (argv-only `spawnSync`; the redirect is an fd, not a
  shell `>`).
- **Boy-Scout net for the other, inherently-small git calls.** Introduce one shared constant
  `GIT_MAX_BUFFER = 64 * 1024 * 1024` (64MB); apply it as `maxBuffer` on the `spawnSync` in
  `runGit` (`git.ts:46`) and `runGitEnv` (`:57`); replace the three hardcoded
  `64 * 1024 * 1024` in `patchIdAggregateTellMerged` (`:615/627/641`) with it. These calls
  (merge-base, rev-parse, porcelain status, worktree list, rebase, update-ref) are bounded-small;
  the cap is a safety net, not the primary fix. On a `maxBuffer`/`ENOBUFS` overflow, throw a
  clear error naming the git argv and the overflow cause instead of Node's opaque spawn error.

Rationale for the 64MB net: `maxBuffer` is a per-call ceiling, not a reservation (Node grows
the buffer as bytes arrive, then aborts) — no N×cap OOM risk; memory equals actual output size.
64MB is the already-proven in-repo value. The finalize equality check no longer depends on it
(streamed), so 64MB caps only the small auxiliary calls, where it will never be reached in
normal operation and degrades to a clean recoverable error if it ever is.

### Component C — Seam-independent daemon surface (`commander/src/ironclaude/main.py` + hook from `orchestrator_mcp.py`)

Today `commit_worker` failures never advance the daemon's drift counter — it turns only from
the reap/idle/dead seams (`main.py:4079/4335/4364`), which a still-retrying worker never
reaches, so the counter never hits the cap and `held`/surface never fires (starvation). And
the `transient` branch (`main.py:1714-1715`) is a silent do-nothing.

- Record every finalize failure returned to the Brain (hook from `orchestrator_mcp.py:3417`,
  where `commit_worker` catches and classifies) into a per-worker **cumulative**
  finalize-failure counter the daemon can read, incremented independent of the idle/reap
  cadence and on **every** failure mode (mode-tagged `drift`/`conflict`/`repair` AND
  untagged/`transient`).
- Once that counter crosses `FINALIZE_DRIFT_RETRY_CAP` (3), fire the **existing**
  `_finalize_recovery_alerted` one-shot Slack+Brain blocker **even for a live, non-idle
  worker** (the machinery already exists at `main.py:1636-1715`; this only changes what can
  reach it).
- Extend the `transient` branch (`:1714-1715`) to surface-once instead of pure do-nothing, so
  a probe-fail/None (broader-outage) case also reaches the operator exactly once.
- Clear the counter when the finalize integrates or the worker leaves the running set (mirror
  the existing cumulative-counter clearing semantics documented at `main.py:890-893`).

Blast radius: daemon only; no git/worktree mutation; reuses proven one-shot machinery.

### Component D — Brain give-up bound + 6d terminal branch (`commander/src/brain/rules/workflow.md`, prompt only)

- SHIP checklist step 6 (`:440`, which today unconditionally says "call `commit_worker`"):
  after **K = 3** consecutive `commit_worker` failures with the same error for a worker, STOP
  calling `commit_worker`, pin a decision-format blocker (machinery at `:894-938`), and stop
  nudging /commit. Track the count in the ledger.
- The "6d. Guided Integration-Recovery Wizard" (`:566-601`): add a **terminal branch** — if
  rerebase / re-invoke returns the same failure, pin an operator blocker and stop (today its
  only non-integrating exits, Decline `:573` and restore_frozen `:578`, leave the row frozen
  at `ready_for_integration` forever and re-attempt next cycle — the unbounded re-pin that
  also starves Component C).

Blast radius: smallest (prompt only); LLM-behavioral, not deterministically enforceable — which
is why it pairs with Component C's deterministic daemon surface.

### Component B — Sanctioned `reopen_for_edit` recovery action (`integration.ts` + `cli.ts` + `orchestrator_mcp.py` + `workflow.md`)

The forward path that replaces DB surgery. A new reconcile recovery mode mirroring
`restoreFrozen` (`integration.ts:1884-1893`) but adding the lifecycle transition + lock
release:

1. `reset --hard` the worktree to the reviewed **frozen** commit (work preserved — this is the
   already-reviewed content, never discarded).
2. Transition `ready_for_integration → active` (**already an allowed transition**, `db.ts:30`
   — no TRANSITIONS-table change needed).
3. Delete the candidate and freeze refs
   (`refs/ironclaude/finalization/<guid>/candidate` and `/frozen`, `integration.ts:210-214`).
4. Release the integration lock.

Exposure:
- New verb accepted by `cli.ts`.
- New action `reopen_for_edit` added to `_RECOVERY_ACTIONS` (`orchestrator_mcp.py:4171`) so it
  routes through `recover_worker_integration` (derives authority from the registry; no
  caller-supplied refs; returns structured dicts, never raises).
- A 6d wizard step offering `reopen_for_edit` when the wizard's other actions keep failing.

Result: the Brain/operator gets an authenticated tool to return a deterministically-stuck
assignment to an editable `active` state so the worker can re-stage (e.g. split the giant
file) and re-commit — exactly what the hand-CAS did, but sanctioned and work-preserving.

Blast radius: largest (crosses TS/Python/Brain), but no TRANSITIONS change and no work
discarded (reset target is the reviewed frozen commit).

## Data Flow

Finalize failure → `commit_worker` classifies (`orchestrator_mcp.py:3081-3134`) → returns
mode dict to Brain **and** records the failure into the per-worker cumulative counter (C) →
daemon reads counter; ≥3 → one-shot `_finalize_recovery_alerted` blocker (C). In parallel, the
Brain's own ledger count ≥3 → stop calling `commit_worker`, pin blocker (D). Operator (or 6d
wizard) invokes `reopen_for_edit` → `recover_worker_integration` → reconcile mode: reset to
frozen, `ready_for_integration → active`, delete refs, release lock (B) → worker re-stages and
re-commits; with A, the diff no longer ENOBUFS at all for sub-64MB output.

## Error Handling

- A (ENOBUFS): clear, argv-naming error instead of opaque spawn failure; over-cap output is a
  loud recoverable error, not a wedge.
- B: `recover_worker_integration` already returns structured dicts and never raises; the new
  mode preserves that. Refuse the action if the row is not in a stuck-finalization state
  (wrong-state guard) so it cannot corrupt a healthy assignment. Never discard the frozen
  commit.
- C: counter is cumulative and cleared only on integrate/leave-running — a worker cannot reset
  the cap by going idle; the one-shot flag prevents alert spam.
- D: terminal branch pins a blocker (durable) rather than silently looping.

## Testing Strategy

TDD per task.
- **A:** vitest — (1) the finalize equality check: a large synthetic diff that would exceed
  64MB still finalizes (equality holds) and a divergent rebased effect is still refused, with
  the streamed hash path exercised (temp file written + cleaned up, no in-memory buffering of
  the diff); (2) `runGit`/`runGitEnv` set `GIT_MAX_BUFFER` and an over-cap auxiliary output
  throws the new clear error; (3) the three patchId sites reference the shared constant. RED
  before the stream+hash rewrite lands.
- **C:** extend the commander recovery-driver unit tests (existing ~3209-test suite) — assert a
  deterministic `commit_worker` finalize failure surfaces **exactly once** for a **live,
  non-idle** worker, and that the `transient`/None case surfaces once (RED against the current
  starved/silent behavior).
- **B:** vitest `worker/mcp-servers/workspace-manager/src/__tests__/integration-cases.ts` — the
  `ready_for_integration → active` rollback, candidate/freeze ref cleanup, lock release, and
  worktree-reset-to-frozen assertions; plus a commander test for the new
  `recover_worker_integration` action + the wrong-state refusal.
- **D:** Brain-rule / behavioral review only (prompt change; not unit-testable). State this
  explicitly in the plan task ("No tests required: Brain-prompt rule change, behavioral").
- **dist:** rebuild `worker/mcp-servers/workspace-manager/dist/` (esbuild `npm run build`) after
  the TS changes — the daemon/Brain run `dist/`, so a stale dist is a live defect (the C1 class
  from v1.1.11). Stage `dist/` with `git add -f` (tracked-but-gitignored).

## Implementation Notes

- Local pytest + vitest only (no GitHub CI). `vitest` may emit a benign `onTaskUpdate` RPC
  timeout under the heavy real-git suite — judge by "N passed | 0 failed", not exit code.
- Bash cwd is `commander/`; use `git -C <repo-root>` and absolute paths. `docs/` is gitignored
  (`git add -f`). No version bump implied by this design (operator decides at release);
  commit/push operator-gated; no trailers.
- No behavioral-mirror or skill-file changes here — this is runtime/recovery code, so the
  body-7 parity test is unaffected.
- Component A now includes the `cumulativeBinaryEffect` stream+hash rewrite (operator-confirmed:
  a fixed cap would break legitimate large commits). Out of scope (hold): any TRANSITIONS-table
  change; version/release actions; extending the streaming treatment to non-finalize content-scaled
  git calls (code-review diffs, `git show`) — they degrade to the clean 64MB error, not a deadlock.
