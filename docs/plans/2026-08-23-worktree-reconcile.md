# /reconcile verb Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** Add a human `/reconcile` verb that integrates a managed worktree's current HEAD into LOCAL main
and keeps the worktree alive (never pushes), extracting verb 3 from the fused `/commit`.

**Requirements:** docs/plans/2026-08-23-worktree-reconcile-requirements.md

**Design:** docs/plans/2026-08-23-worktree-reconcile-design.md

**Architecture:** New op `reconcile` in the human-intent lane with HEAD-pinning evidence; a new
`finalizeReconcile(db, authority)` = assert live HEAD == pinned HEAD, then `finalizeLocalCommit` +
`recycleFinalized` (integration shape borrowed from `finalizeCommanderLocalCommit`, not the commit path),
returning `{state:'reconciled', integratedCommit}`. A v3 `human_intents` migration admits `reconcile` +
`close-out`. Wired through hook-intent, a new `reconcile_worktree` MCP tool, and state-activator; a new
reconcile skill. Purely additive — deploys standalone.

**Tech Stack:** TypeScript (workspace-manager, vitest, better-sqlite3), bash (state-activator.sh), git.

**Execution invariants:** shell state does not persist (absolute paths, `git -C <repo-root>`); vitest is
git-heavy (~150s → background if over the foreground timeout); `docs/` gitignored (`git add -f`); never
author an unmeasured `expected:`; every guard must be able to fail. DEPLOY (separate operator step) is
atomic: rebuild dist → claude 1.1.6 cache + codex cache, and the MCP server restarts (verify path is a
persistent server). Guard bug #5: these filenames use "reconcile" (no commit/push/merge/rebase substring).

**Grounded source anchors (verified this planning pass; the two crux facts read directly):**
- git-authority.ts:563 `if (input.operation !== 'commit') usablePushAuthorizations.add(authority)` — VERIFIED; must also exclude `'reconcile'` (R2).
- integration.ts:811-838 `finalizeLocalCommit(db, repositoryPath, assignment, sourcePath, frozenCommit, hooks?)` — VERIFIED; requires assignment `active` (:819), its `worktreeHead===frozenCommit` check (:825) is vacuous when fed live HEAD → the finalizer must assert `worktreeHead===evidence.headOid` itself (R3); writes its own freeze (:828, no prior freeze); dirty gate :832-834; active→ready_for_integration :836.
- db.ts: schema_migrations version guard pattern; v2 recreate :165-190 (CHECK :170, INSERT..SELECT :181-182, bump :187); v1 CHECK :134.
- types.ts:93-98 HumanIntentOperation. git-authority.ts:7 DirectGitOperation; observeDirectEvidence :393-439; verifyDirectGitAuthority :479-565 (managed path :519-564; commitEvidence validator template :111-121). integration.ts recycleFinalized :568-596; finalizeAttestedCandidate :840-883 (descendant proof :858); FinalizationResult :31-41 (has integratedCommit? :37); finalizeCommanderLocalCommit :1124-1166. hook-intent.ts issueHumanIntentFromHook :28-85 (managed branch :62-73, op condition :66); issueDirectGitHumanIntent :441-476. index.ts PUBLIC_TOOL_NAMES :34-45, tool defs (commit map :116-131, sync_worktree_to_target :150-162), dispatch :188-202, deps (finalizeDirect :270-286, requireProviderRoot :214-218). state-activator.sh:54 verb loop. Tests: integration-cases.ts registerFinalizationTests :34, core gate :254, managed-commit happy path :256-271, remote-unmoved :284-298.

Each task re-verifies its anchors at RED (Step 1.6). vitest: `cd worker/mcp-servers/workspace-manager && npx vitest run <file>`.

---

## Task 1: v3 human_intents migration + HumanIntentOperation type

**Files:**
- Modify: `worker/mcp-servers/workspace-manager/src/db.ts` (add v3 migration after the v2 block ~:190)
- Modify: `worker/mcp-servers/workspace-manager/src/types.ts:93-98`
- Test: `worker/mcp-servers/workspace-manager/src/__tests__/db.test.ts`

**Depends on:** none.

**Step 1 (RED):** In `db.test.ts` add: after opening a DB (which runs migrations), inserting a `human_intents`
row with `operation='reconcile'` succeeds and with `operation='close-out'` succeeds; a row with
`operation='bogus'` throws (CHECK still bounded); a pre-seeded v2-era row survives the v3 migration
(migrate an in-memory/tmp DB with a v2 row, reopen, assert the row present). ALSO update the existing
`schema_migrations`-version assertions (they expect `[1, 2]` at ~:474 and ~:552) to expect `[1, 2, 3]`.
Run:
`cd worker/mcp-servers/workspace-manager && npx vitest run src/__tests__/db.test.ts` → FAIL (CHECK rejects reconcile).

**Step 2 (GREEN):** In `db.ts` immediately after the v2 block's closing `}` (~:190) add the v3 migration
guarded by `if (!db.prepare('SELECT 1 FROM schema_migrations WHERE version = 3').get())`, in a
`db.transaction`, that: creates `human_intents_v3` with the operation CHECK admitting the seven values
(`'use-primary-checkout','return-to-managed-worktree','commit','commit-and-push','push','reconcile','close-out'`)
and the same columns as v2 (`workspace_guid TEXT NOT NULL`, no FK); `INSERT INTO human_intents_v3 (...cols...)
SELECT ...cols... FROM human_intents` (NO WHERE filter — v2 already made workspace_guid NOT NULL);
`DROP TABLE human_intents; ALTER TABLE human_intents_v3 RENAME TO human_intents`; recreate
`human_intents_lookup_idx`; `INSERT OR IGNORE INTO schema_migrations(version) VALUES (3)`. (Exact column list
mirrors the v2 CREATE.) In `types.ts:93-98` add `| 'reconcile'` and `| 'close-out'` to `HumanIntentOperation`.

**Step 3:** Run the db.test.ts file → PASS.

**Step 4:** Stage: `git -C /Users/roberthyatt/Code/ironclaude add worker/mcp-servers/workspace-manager/src/db.ts worker/mcp-servers/workspace-manager/src/types.ts worker/mcp-servers/workspace-manager/src/__tests__/db.test.ts`

---

## Task 2: HEAD-pin evidence + observe/verify reconcile branch + push-exclusion (git-authority.ts)

**Files:**
- Modify: `worker/mcp-servers/workspace-manager/src/git-authority.ts`
- Test: `worker/mcp-servers/workspace-manager/src/__tests__/git-authority.test.ts`

**Depends on:** Task 1 (the `reconcile` op type).

**Step 1 (RED):** In `git-authority.test.ts` add: `observeDirectEvidence(managedCheckout, 'reconcile')`
returns `{checkoutMode:'managed', canonicalBranch, localRef, headOid}` (headOid = HEAD commit); a
non-managed checkout for `reconcile` is refused; `verifyDirectGitAuthority` with a minted `reconcile`
intent + matching live HEAD returns an authority whose `operation==='reconcile'` that is NOT usable for
push, asserted with the PINNED message:
`expect(() => pushExactAuthorizedRef(authority)).toThrow('Direct Git push authority is single-use')`. That
message fires only at :614 when the authority is excluded from `usablePushAuthorizations`, so the test
FAILS if the :563 exclusion is dropped. (A bare `.toThrow()` would pass even with the exclusion removed —
ReconcileEvidence's absent `stagedTree` incidentally trips `assertPostCommitPushState` — so :563 is NOT the
sole barrier and the message must be pinned.) A reconcile intent whose evidence headOid differs from live
HEAD fails the byte-match. Run the file → FAIL.

**Step 2 (GREEN):**
- `DirectGitOperation` (:7): add `'reconcile'`; widen the exported `DirectGitAuthorityEvidence` union
  (~:35, compiler-forced) to include `ReconcileEvidence`.
- `observeDirectEvidence` (:393-439): add a `reconcile` branch (after the managed-mode assertion) returning
  `{...branch, headOid: runGit(checkout.path, ['rev-parse','--verify','HEAD^{commit}']).trim()}` with NO
  stagedTree/remote; assert `checkout.mode === 'managed'` for reconcile (refuse otherwise).
- Add a `reconcileEvidence(...)` validator mirroring `commitEvidence` (:111-121):
  `exactKeys(['checkoutMode','canonicalBranch','localRef','headOid'])` + `branchEvidence(...)` + `oid(headOid)`.
- `verifyDirectGitAuthority` managed path (:519-564): add a `reconcile` arm in the legacy-evidence chain
  using `reconcileEvidence`; the non-legacy path (:524-525) already routes through `observeDirectEvidence`.
- **:563 — the push-exclusion (R2):** change to
  `if (input.operation !== 'commit' && input.operation !== 'reconcile') usablePushAuthorizations.add(authority);`
- **:613 — clean refusal:** add `'reconcile'` to the `pushExactAuthorizedRef` guard so a reconcile
  authority is refused with the single-use message (defense-in-depth; makes the pinned test honest).

**Step 3:** Run the git-authority.test.ts file → PASS.

**Step 4:** Stage the two files.

---

## Task 3: finalizeReconcile finalizer (integration.ts) + FinalizationResult

**Files:**
- Modify: `worker/mcp-servers/workspace-manager/src/integration.ts`
- Test: `worker/mcp-servers/workspace-manager/src/__tests__/integration-cases.ts`

**Depends on:** Task 2.

**Step 1 (RED):** In `integration-cases.ts` first widen the `issueAndVerify` helper's op union (:237-252) to
include `'reconcile'`. Then (inside the `part === 'core'` block, after :271) add reconcile cases (mirror the
:256-271 managed-commit happy path + :284-298 remote-unmoved): commit staged work in the managed worktree
so HEAD is a real commit, mint+verify a `reconcile` authority (new `reconcileEvidence` helper:
`{checkoutMode:'managed', canonicalBranch, localRef, headOid: git(source,'rev-parse','HEAD')}`), then
`finalizeReconcile(database, authority)` → `state==='reconciled'`, local main advanced to the HEAD, worktree
still exists, row back to `lifecycle_status:'active'` with `base_commit` = integrated commit; the remote main
ref is byte-unchanged (reconcile never pushes); a SECOND `finalizeReconcile` on the same authority throws
(own single-use); moving HEAD AFTER the mint+verify (extra commit) → `finalizeReconcile` throws (HEAD-pin,
R3); a dirty worktree → throws (R4); **REPAIR (C2):** a `ready_for_integration` row seeded from a
conflict-paused commit-and-push (the `seedIntegratedPushPending` :139-162 shape, which carries a push
disposition) → `finalizeReconcile` returns `state:'integrated-local'` and PRESERVES the disposition +
integration_records row (assert it does NOT recycle / NULL the disposition). Run the file → FAIL
(finalizeReconcile undefined).

**Step 2 (GREEN):** Export `finalizeReconcile(db, authority: AuthorizedDirectGitOperation): FinalizationResult`:
- own single-use WeakSet (declare `const usedReconcileAuthorities = new WeakSet()`; throw 'single-use' if present; add).
- assert `authority.operation === 'reconcile'` AND `authority.checkoutMode === 'managed'` (else throw).
- `revalidateAuthorizedCommitState(authority)` (branch/localRef recheck).
- `const exact = exactAssignment(db, authority.worktreePath, authority.workspaceGuid, authority.providerRootSessionId)`.
- **HEAD-pin (R3):** assert `worktreeHead(authority.worktreePath) === (authority.evidence as ReconcileEvidence).headOid` (else throw 'reconcile HEAD changed since issuance; re-run /reconcile').
- if `exact.assignment.lifecycle_status === 'active'`: `const local = finalizeLocalCommit(db, exact.primaryCheckoutPath, exact.assignment, authority.worktreePath, evidence.headOid); recycleFinalized(db, local.repositoryPath, local.assignment); return { state:'reconciled', integratedCommit: local.integratedCommit };`
- else if `=== 'ready_for_integration'` (repair, R5): require the durable frozen ref (mirror :1029-1032) and `worktreeHead === headOid`; `runGit(worktree,['update-ref', candidateRef(guid), headOid])`; `const local = finalizeAttestedCandidate(db, primary, assignment, headOid)`. **Preserve any push-pending obligation (C2):** if `decodePushDisposition(local.assignment.disposition)` is non-null, return `{state:'integrated-local', integratedCommit: local.integratedCommit, pushError:'Remote has not proved the exact integrated candidate'}` (mirror `finishLocalIntegration` :660-670 — do NOT recycle, or the durable push-pending proof from an interrupted commit-and-push is destroyed); only when the disposition is null: `recycleFinalized(...)` and return `{state:'reconciled', integratedCommit}`. Anything else → throw directing to `reconcile_finalization`.
- Add `'reconciled'` to `FinalizationResult.state` (:31-41; `integratedCommit?` already exists :37).

**Step 3:** Run the integration-cases.ts suite via its runner (`npx vitest run src/__tests__/integration-core.test.ts`) → PASS. Then the FULL suite (`npx vitest run`, background if >120s) → all green; paste the measured count.

**Step 4:** Stage the two files.

---

## Task 4: wiring — hook-intent + reconcile_worktree tool + state-activator

**Files:**
- Modify: `worker/mcp-servers/workspace-manager/src/hook-intent.ts`
- Modify: `worker/mcp-servers/workspace-manager/src/index.ts`
- Modify: `worker/hooks/state-activator.sh`
- Test: `worker/mcp-servers/workspace-manager/src/__tests__/tool-dispatch.test.ts`
- Test: `worker/mcp-servers/workspace-manager/src/__tests__/git-authority.test.ts` (the mint gate — `issueHumanIntentFromHook` is exercised there, :944-1009)

**Depends on:** Task 3.

**Step 1 (RED):** (a) In `tool-dispatch.test.ts` add: `reconcile_worktree` is in `PUBLIC_TOOL_NAMES`;
`dispatchPublicTool('reconcile_worktree', args, deps)` calls `deps.reconcileWorktree`; the schema requires
`repository_path` + `workspace_guid`, has NO `message`, and `additionalProperties:false`; the dependency
enforces `requireProviderRoot` (a non-provider-root identity throws). (b) In `git-authority.test.ts` (mint
gate, near :944-1009) add: `issueHumanIntentFromHook` with ONE managed assignment + a `/reconcile` hook
payload mints an intent with `operation='reconcile'`; with ZERO assignments it throws
`'exactly one active assignment'` (bounds the widening — the :49 unassigned branch stays closed to reconcile).
Run both files → FAIL.

**Step 2 (GREEN):**
- `hook-intent.ts:66` — extend the managed op condition to include `'reconcile'` so it routes through
  `issueDirectGitHumanIntent` (managed, exactly-one-assignment). Do NOT touch the length-0 unassigned branch.
- `index.ts` — `PUBLIC_TOOL_NAMES` (:34-45) add `'reconcile_worktree'`; `PublicToolDependencies` (:23-32)
  add `reconcileWorktree: PublicSingleArgumentDependency`; add a tool definition (template = sync_worktree_to_target
  :150-162, required `['repository_path','workspace_guid']`, no message, additionalProperties:false); dispatch
  (:188-202) `case 'reconcile_worktree': return dependencies.reconcileWorktree(args);`; dependency (mirror
  finalizeDirect :270-286): `requireProviderRoot(); const authority = verifyDirectGitAuthority(db, {repositoryPath:
  requiredString(args,'repository_path'), workspaceGuid: optionalString(args,'workspace_guid'),
  providerRootSessionId: identity.sessionId, humanChannel, operation:'reconcile'}); return finalizeReconcile(db, authority);`;
  import `finalizeReconcile` with the other `integration.js` imports (:15).
- `state-activator.sh:54` — add `reconcile` to the `for operation in ...` list.

**Step 3:** Run `tool-dispatch.test.ts` and `git-authority.test.ts` → PASS. Then the FULL suite → all green; paste the count.

**Step 4:** Stage the five files (hook-intent.ts, index.ts, state-activator.sh, tool-dispatch.test.ts, git-authority.test.ts).

---

## Task 5: reconcile skill

**Files:**
- Create: `worker/skills/reconcile/SKILL.md`

**Depends on:** Task 4.

No tests required: skill Markdown has no test framework; Tasks 1-4 prove the behavior. The verb is inert
without this skill (state-activator's codex-link regex references `skills/<op>/SKILL.md`).

**Step 1:** Read `worker/skills/commit/SKILL.md` for the exact human-only exact-form template.

**Step 2:** Create `worker/skills/reconcile/SKILL.md` mirroring it: use only on the exact human forms
`/reconcile`, `/ironclaude:reconcile`, `$ironclaude:reconcile`, or the codex link; require professional mode
on, provider-root, one matching assignment; call workspace-manager `reconcile_worktree` with
`repository_path` + `workspace_guid` only; NEVER push; require successful local-integration evidence; on
failure report the exact error and preserve the assignment. Do NOT promise a clean "refuse on divergence" —
a conflicting target advance pauses mid-rebase (parity with `/commit`); on such a failure report the exact
error and direct to `reconcile_finalization`.

**Step 3:** Stage: `git -C /Users/roberthyatt/Code/ironclaude add worker/skills/reconcile/SKILL.md`

---

**Post-execution:** DEPLOY is a separate operator step (atomic: dist → claude+codex caches + MCP server
restart; state-activator via the hooks dir). A 3a-style scratch live-proof of `/reconcile` against the
deployed dist is the recommended FOLLOW-ON before relying on it. Human commits; no push.
