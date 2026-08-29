# M6 `/close-out` Remediation Plan (post-end-review)

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** Fix the 2 confirmed critical + 2 important defects the tier-up end-review found in M6
`/close-out` (already staged): never integrate a rejected-rebase resolution (C1), preserve carried
work across same-session row reuse via a `preserved_work` table + per-lifecycle recovery refs (C2,
subsumes I1), auto-heal a recoverable integrated state (I2), plus cleanups.

**Requirements:** docs/plans/2026-08-26-m6-close-out-requirements.md (R10–R12)

**Architecture:** Additive to the staged M6 code. `reuseTerminalAssignment` and the reaper/tombstone
lanes stay BYTE-UNTOUCHED. Design remediation: docs/plans/2026-08-26-m6-close-out-design.md
("## Remediation").

**Tech Stack:** TypeScript, vitest, better-sqlite3, git.

**Execution invariants:** absolute paths; `docs/` gitignored (`git add -f`); vitest
`--testTimeout=30000`; test git helper `git()`; inline SELECT reads; distinctive test names so a
`-t` RED filter does not sweep existing green tests; the suite run is each task's falsifier; baseline
277 green.

---

## Task 1: C1 — never integrate a rejected-rebase resolution

**Files:** `index.ts`, `integration.ts`, `__tests__/integration-cases.ts` (all under
worker/mcp-servers/workspace-manager/src).

The security-load-bearing change is the `cumulativeBinaryEffect` equality gate — close-out's authority
is evidence-light (the human attested no commit), so this proof is the ONLY review guarantee. It MUST
have a falsifying test: a seed whose HEAD is a **descendant** of the advanced target with **altered
content** (the existing `finalizeAttestedCandidate` descendant proof passes it; only the equality clause
catches it).

**Step 1 — RED-a (isAncestor disjunct):** seed a `ready_for_integration` frozen-attached row (mirror
`seedFrozenReadyNoRebase(true)` :198-222; HEAD===frozen, NOT a descendant of the advanced target).
`finalizeCloseOut` → assert `state==='rebase-recovery-repair-required'`, root main UNCHANGED, worktree
preserved, row still `ready_for_integration`. (Today this THROWS at `finalizeAttestedCandidate:927` —
red via throw ≠ the asserted structured state.) Run `-t "C1 close-out ready"`.

**Step 2 — RED-b (the SECURITY falsifier):** from `seedFrozenReadyNoRebase(false)`, in the worktree
`git reset --hard <root HEAD (=expectedTarget)>`, write ALTERED content, `git add`+`commit` → HEAD is a
DESCENDANT of expectedTarget whose `cumulativeBinaryEffect` DIFFERS from the frozen review.
`finalizeCloseOut` → assert `rebase-recovery-repair-required`, root main UNCHANGED, worktree preserved,
row ready. (Today this INTEGRATES the altered content — main ADVANCES; deleting the
`cumulativeBinaryEffect` comparison from the Step-4 gate turns this green→red — its only falsifier.) Run
`-t "C1 close-out altered"`.

**Step 3 — GREEN positive control:** `seedFrozenReadyNoRebase(false)`, `reset --hard <expectedTarget>`,
`cherry-pick <frozen>` (non-conflicting → identical `--binary --full-index` patch → equal effect) →
`finalizeCloseOut` assert `state==='closed-out'`, root main advanced, worktree removed. Guards against an
over-broad/inverted gate.

**Step 4 — GREEN gate:** in `finalizeCloseOut`'s `ready_for_integration` branch, before
`update-ref candidateRef`/`finalizeAttestedCandidate`: `frozen`=freezeRef commit (dedupe the branch's
existing rev-parse); `target`=`targetRef`; `expectedTarget`=target commit;
`reviewedEffect=cumulativeBinaryEffect(worktree, base_commit, frozen)`; if
`!isAncestor(primaryCheckoutPath, expectedTarget, headOid) || cumulativeBinaryEffect(worktree,
expectedTarget, headOid)!==reviewedEffect` return `{state:'rebase-recovery-repair-required', detail:
'Close-out: the rebase resolution changed the reviewed content; preserved for automated resolution (M7).
Not an operator task.'}`. Mirrors integration.ts:1499-1501; `headOid` in scope at :1372.

**Step 5 — GREEN handler:** `index.ts` `closeOutWorktree`: `cont=reconcileFinalization('continue')` in
try/catch; on throw re-probe `reconcileFinalization('status')` — if `probe.state` is
`rebase-paused-conflict` OR `rebase-paused-clean` return `{state: probe.state, detail: '… pending
automated resolution … Not an operator task. (msg)'}` (use the PROBE's own state); else rethrow. If
`cont.state ∉ {cleaned, integrated-local}` return `cont` (preserve-and-defer).

**Step 6:** run suite; **Step 7:** stage (`index.ts integration.ts integration-cases.ts`).

---

## Task 2: C2 (+I1) — `preserved_work` table + per-lifecycle recovery refs survive row reuse

**Depends on:** Task 1. **Files:** `db.ts`, `integration.ts`, `index.ts`, `integration-cases.ts`,
`db.test.ts` (schema_migrations v4 bumps the migration chain to `[1,2,3,4]`, which its three
migration-version canary assertions at :474/:552/:607 must track).

**Step 1 — RED:** (A) Case-A close-out carries pending-push, then `ensureSessionWorktree` again for the
same session (reuse) → `listPreservedWork` STILL returns the pending-push entry. (B) two dirty close-outs
on the same GUID → two distinct `refs/ironclaude/recovery/<guid>-<oid>`, both resolve, both listed. (C)
an ABANDONED row with `recovery_ref` (no table row) is STILL listed (I-1 UNION). (D) after (A), a
matching `/push` then drain → `listPreservedWork` empty for that guid (I-2). (E) a close-out whose
disposition phase is `push-succeeded` → cleared, no carry, no insert (obs 2). Run `-t "C2 preserved-work"`.

**Step 2 — GREEN db.ts:** `migrateSchema` after v3 — version-4 guard: `CREATE TABLE IF NOT EXISTS
preserved_work (id PK AUTOINCREMENT, workspace_guid NOT NULL, repository_identity NOT NULL,
owner_session_id, kind CHECK IN ('pending-push','recovery'), payload NOT NULL, created_at, resolved_at)`
+ index on `repository_identity` + `INSERT OR IGNORE schema_migrations(4)`. Export `insertPreservedWork`
(IDEMPOTENT on an unresolved `(workspace_guid, kind, payload)` — skip if such a row exists),
`listUnresolvedPreservedWork(db, repositoryIdentity, ownerSessionId)` (`resolved_at IS NULL`),
`resolvePreservedWork(db, {workspaceGuid?, kind, predicate})`. Do NOT touch `reuseTerminalAssignment`.
Update `db.test.ts`'s three migration-version canary assertions `[1,2,3]`→`[1,2,3,4]` (sites
:474/:552/:607) — v4 is a legitimate new migration those assertions exist to track.

**Step 3 — GREEN integration.ts writers (ordering matters):**
- `snapshotResidualIfDirty`: mint `refs/ironclaude/recovery/<guid>-<snapshot>` CREATE-ONLY
  (`update-ref <ref> <oid> ''`) with TOLERATE-SAME-OID (catch → `rev-parse --verify <ref>^{commit}` ===
  snapshot ? proceed : rethrow — I-3); set `assignments.recovery_ref` AND `insertPreservedWork(kind:
  'recovery', payload {ref, residualFiles})` BEFORE the rev-parse re-verify + `reset --hard` (I-4).
- `closeOutRelease`: if `decodePushDisposition` phase `=== 'push-succeeded'` → `setDisposition(null)`, no
  carry/insert (obs 2); when carrying pending-push, wrap `transitionAssignment('integrated','cleaned')` +
  `insertPreservedWork(kind:'pending-push', payload JSON.stringify(pendingPush))` in ONE `db.transaction`
  (I-4).
- `drainCarriedObligations`: keep the matched-row resolve AND add a direct query of unresolved
  `pending-push` `preserved_work` rows for the `repositoryIdentity` — parse payload, match
  `remoteUrl`/`destinationRef`, `isAncestor(primary, payload.candidateCommit, pushedLocalOid)` →
  `resolvePreservedWork` (I-2).

**Step 4 — GREEN index.ts `listPreservedWork`:** `listUnresolvedPreservedWork(db, repo.repositoryIdentity,
identity.sessionId)` mapped to `{workspace_guid, kind, destinationRef?|ref?}`, UNION the EXISTING
assignments cleaned/abandoned query WITH DEDUPE: emit all table rows first; then the assignments query,
appending a pending-push leg only when no unresolved pending-push table row exists for that guid, and a
recovery leg only when that exact ref value wasn't already emitted (I-1). Keep the existing C7 test shape.

**Step 5:** run suite; **Step 6:** stage (`db.ts integration.ts index.ts integration-cases.ts`).

---

## Task 3: I2 — auto-heal a recoverable integrated state in `closeOutRelease`

**Depends on:** Task 2. **Files:** `integration.ts`, `integration-cases.ts`.

**Step 1 — RED:** `seedIntegratedPushPending` then `git reset --hard <frozen>` so worktree
HEAD===frozenCommit≠candidate. `finalizeCloseOut` → assert `closed-out` (NOT a throw), worktree removed,
row cleaned, `pendingPush` carried. (RED: closeOutRelease throws at `actualHead!==integrated_commit`.) Run
`-t "I2 close-out frozen-head"`.

**Step 2 — GREEN:** `closeOutRelease` — after resolving `current`, before the clean/integration-proof
block: `candidate`=candidateRef commit; if `candidate!==integrated_commit` throw 'Close-out candidate
proof differs; preserving worktree'; in the existing disposition block require
`disposition.candidateCommit===integrated_commit && freezeRef===disposition.frozenCommit` else throw
'Close-out push refs differ; preserving worktree'; self-heal: `head0=worktreeHead`; if
`head0!==integrated_commit && disposition && head0===disposition.frozenCommit && worktreeIsClean` →
`reset --hard integrated_commit`. No-disposition head-mismatch keeps the existing refusal.

**Step 3:** run suite; **Step 4:** stage.

---

## Task 4: Cleanups — `pushPendingSummary` phase filter + comment

**Depends on:** Task 3. **Files:** `integration.ts`, `integration-cases.ts`.

**Step 1 — RED:** `pushPendingSummary(JSON.stringify({phase:'push-succeeded',...}))` → `undefined`;
`push-pending`/`push-failed` → defined. Run `-t "pushPendingSummary"`.

**Step 2 — GREEN:** `pushPendingSummary` returns the summary only when `decoded.phase` is `'push-pending'`
or `'push-failed'`, else `undefined`. `finalizeCloseOut`: reword the "HEAD changed since issuance" throw
to "Close-out HEAD changed since verification; re-run /close-out".

**Step 3:** run full suite; **Step 4:** stage.
