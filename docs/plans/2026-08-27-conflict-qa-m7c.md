# M7c Interactive Conflict Resolution Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** Land operator-resolved conflict content on LOCAL main under a non-forgeable authority that reuses the existing keystroke-minted human-intent primitive (`/confirm-resolution`), with per-hunk prose Q&A for correctness, all-or-abort semantics, and no push.

**Requirements:** docs/plans/2026-08-27-conflict-qa-m7c-requirements.md

**Design:** docs/plans/2026-08-27-conflict-qa-m7c-design.md

**Architecture:** A new `confirm-resolution` direct-git operation is added across the proven layers (mint gate, hook admission, authority/evidence, and the `human_intents` CHECK-constraint schema). A land tool consumes that intent and requires the server-registered candidate ref (written by the apply tool on TRUE rebase completion) to equal the intent-bound live HEAD before routing through the existing `finalizeAttestedCandidate` (isRepair) channel — the operator's keystroke over the shown diff is the review of record. An apply-resolution tool turns per-hunk operator prose into staged resolved bytes with the correct rebase side mapping (`--theirs` = reviewed work, `--ours` = target). The verb skills orchestrate the loop; a `confirm-resolution` skill provides the typeable command surface.

**Tech Stack:** TypeScript (workspace-manager MCP server), esbuild bundle, vitest, bash hook, SQLite (better-sqlite3).

**Execution invariants** (the blind reviewer checks commands against these): shell state does NOT persist between steps (literal absolute paths); Bash cwd is `commander/`, so writes use `git -C <repo-root>`; quote globs; foreground `sleep` blocked; `docs/` gitignored; empty result distinguishable from failure; every `expected:` for a NEW test is RED-first (the suite cannot run at plan-writing time). Current test baseline: **299 passing** (post-QW-CAP `0cc7e11`).

---

## Task 1: Add `confirm-resolution` to the mint + authority + schema layers

**Files:**
- Modify: `worker/mcp-servers/workspace-manager/src/types.ts:93-100` (HumanIntentOperation)
- Modify: `worker/mcp-servers/workspace-manager/src/git-authority.ts:7` (DirectGitOperation), `:441` (observeDirectEvidence), `:589` (verify legacy branch), `:625` (usablePushAuthorizations exclusion)
- Modify: `worker/mcp-servers/workspace-manager/src/hook-intent.ts:92`
- Modify: `worker/hooks/state-activator.sh:54`
- Modify: `worker/mcp-servers/workspace-manager/src/db.ts` (v5 migration after the v4 block)
- Test: `worker/mcp-servers/workspace-manager/src/__tests__/git-authority.test.ts`
- Test: `worker/mcp-servers/workspace-manager/src/__tests__/db.test.ts` (schema-migration version-list canary)

**Step 1 (RED):** Mirror an existing reconcile mint→consume test, grounded on a `repository(true)` fixture (origin remote present). Add `'a confirm-resolution intent mints and consumes, binding the live managed HEAD'`: `issueDirectGitHumanIntent` then `verifyDirectGitAuthority` with `operation: 'confirm-resolution' as unknown as DirectGitOperation`; assert `authority.operation === 'confirm-resolution'` and `(authority.evidence as ReconcileEvidence).headOid === <worktree HEAD>`. Run `-t "confirm-resolution intent mints"`. Expected: FAIL — the op is rejected (origin-backed fixture → the `:479` allowlist throw; without an origin remote it fails earlier at `remote get-url` `:451`).

**Step 2 (GREEN A — operation plumbing):** `types.ts:100` append `| 'confirm-resolution'`; `git-authority.ts:7` append it to `DirectGitOperation`; `:441` `if (operation === 'reconcile' || operation === 'close-out' || operation === 'confirm-resolution')` (LIVE headOid, like reconcile); `:589` legacy branch add `|| input.operation === 'confirm-resolution'`; leave the `matchEvidence` close-out condition at `:598` UNCHANGED; `hook-intent.ts:92` add `|| operation === 'confirm-resolution'`; `state-activator.sh:54` add `confirm-resolution` to the for-loop list.

**Step 3 (GREEN B — schema migration, C1):** In `db.ts`, immediately after the v4 block (~`:219+`), add a **v5 migration** copying the v3 precedent (`:192-217`) verbatim in shape: `CREATE TABLE human_intents_v5` with `operation … CHECK (operation IN (…the seven existing ops PLUS 'confirm-resolution'))`, `INSERT..SELECT` all rows from `human_intents`, `DROP TABLE human_intents`, `ALTER TABLE human_intents_v5 RENAME TO human_intents`, recreate `human_intents_lookup_idx`, `INSERT OR IGNORE INTO schema_migrations(version) VALUES (5)`. Without it the confirm-resolution insert fails with `CHECK constraint failed`. The Step 1 test is the falsifier (delete the migration → CHECK error). **Canary:** `db.test.ts` hardcodes the `schema_migrations` version list in three tests (`toEqual([1,2,3,4])` at ~lines 474/552/607); the v5 migration makes it `[1,2,3,4,5]` — update those three assertions accordingly (leave the row-survival + index assertions unchanged).

**Step 4 (GREEN C — never-push defense-in-depth, ObsA):** `git-authority.ts:625` add `&& input.operation !== 'confirm-resolution'` to the `usablePushAuthorizations` guard. Add a negative test: `pushExactAuthorizedRef(<a confirm-resolution authority>)` throws (never-push falsifier, S7).

**Step 5:** Run the full suite (baseline 299 + the new mint/consume + push-refusal tests; the db.test.ts canary must be green after the `[1,2,3,4,5]` update). Typecheck. Stage the seven files. `state-activator.sh` has no test framework: it joins the byte-identical exact-slash-match loop, so free text still cannot mint (S2 by construction).

---

## Task 2: Land tool + registered-candidate check (authority spine)

**Depends on:** Task 1. **Files:** `integration.ts`, `index.ts`, `__tests__/integration-cases.ts`, `__tests__/tool-dispatch.test.ts`.

Proves the spine against a **mechanically-seeded** candidate. The genuinely-new M6-C1-class protection is candidate **provenance** (registered-candidate pin), NOT a reconcile equality gate — `finalizeReconcile`'s repair branch (`integration.ts:1253-1262`) lands any descendant by shipped design, and that stays unchanged.

**Step 1 (RED):** With `seedRebaseConflictKind` (`:2141`) reach a `ready_for_integration` paused row, hand-resolve + `rebase --continue` so HEAD is a resolved candidate, write `candidateRef(guid)`=HEAD. Deps via `createPublicToolDependencies(db, {client, sessionId: OWNER, invocationThreadId: OWNER, source})`. Assert:
- **HAPPY:** intent present, `headOid===candidateRef===worktreeHead` → `land_resolved_conflict` returns `state:'reconciled'`, local main advances, NO origin ref moved.
- **S4/C4b:** no intent → the matching-human-intent refusal, nothing lands.
- **S1 content-pin:** `candidateRef ≠ live HEAD` → `'No confirmed resolution candidate matches the authorized HEAD'`, nothing lands.
- **S1 TOCTOU (split):** (a) **tool-level** — mint, move HEAD, call the tool → intent-consume observes evidence live → the matching-human-intent refusal (`:612`), nothing lands; (b) **direct-level** — `issueAndVerify(…,'confirm-resolution',…)` for a pre-verified authority, then move HEAD, then call `finalizeConfirmResolution` directly → `'Resolution HEAD changed since /confirm-resolution'` (keeps the finalize guard falsifiable).
- **S3 (cross-verb non-interchange + provenance):** a `reconcile` intent cannot be consumed by `land_resolved_conflict` (`:612` refusal); a `confirm-resolution` authority cannot drive `finalizeReconcile` (its operation guard throws); the existing reconcile repair suite (incl. `integration-cases.ts:871-908`) passes byte-unchanged.
- `tool-dispatch.test.ts`: `publicToolDefinitions` has `land_resolved_conflict` with `required: ['repository_path','workspace_guid']`.

Run `-t "land_resolved_conflict"`. Expected: FAIL — `landResolvedConflict is not a function` / tool absent.

**Step 2 (GREEN — integration.ts):** module-level `const usedConfirmResolutionAuthorities = new WeakSet<AuthorizedDirectGitOperation>();` and export `finalizeConfirmResolution(db, authority)`: guard `operation==='confirm-resolution'` + `checkoutMode==='managed'` + single-use; `revalidateAuthorizedCommitState`; `exactAssignment`; require `lifecycle_status==='ready_for_integration'`; require durable `freezeRef`; `headOid=(evidence as ReconcileEvidence).headOid`; `registered=rev-parse candidateRef(guid)` (throw `'No confirmed resolution candidate matches the authorized HEAD; nothing landed'` if absent OR ≠ headOid); require `worktreeHead===headOid` (throw `'Resolution HEAD changed since /confirm-resolution; re-run'`); `local=finalizeAttestedCandidate(...)`; push-disposition → `integrated-local`; else `recycleFinalized` + `{state:'reconciled', integratedCommit}`. Verify every symbol against current source (`finalizeReconcile :1233-1265`, `finalizeAttestedCandidate :929-972`).

**Step 3 (GREEN — index.ts):** add `land_resolved_conflict` to `PUBLIC_TOOL_NAMES`; add its definition (isRepair land; consumes `/confirm-resolution` intent; requires registered candidate == authorized HEAD; keeps worktree; never pushes; `required: repository_path + workspace_guid`); dispatch `→ dependencies.landResolvedConflict(args)`; handler mirroring `reconcileWorktree` (`:343-353`) with `operation: 'confirm-resolution'` → `finalizeConfirmResolution`; add to `PublicToolDependencies`; import the function.

**Step 4:** Full suite + typecheck; stage the four files.

---

## Task 3: `resolve_conflict_hunk` apply tool + M7b label fix

**Depends on:** Task 2. **Files:** `integration.ts`, `index.ts`, `__tests__/integration-cases.ts`, `__tests__/tool-dispatch.test.ts`.

**Correct rebase side mapping:** in `rebase --onto <target> <base>` the reviewed commits are *replayed*, so `--ours` = target, `--theirs` = reviewed work. keep-mine → `git checkout --theirs -- <path>`; take-target → `git checkout --ours -- <path>`. Also fixes shipped **M7b**'s inverted `classifyRebaseConflicts` labels (`integration.ts:1657`).

**Step 1 (RED):** With `seedRebaseConflictKind` seeding a class-2 overlap conflict, assert REAL bytes: `keep-mine` → file holds the reviewed-side content; `take-target` → target-side content; `prose`+`content` → that exact content. **Multi-commit completion (I3):** seed TWO worktree commits both conflicting; resolving the first path returns the SECOND commit's conflict with NO candidate; resolving the second registers `candidateRef` and returns the candidate. **S5 abort:** `choice:'abort'` → `recoverRebaseInProgress('abort')` restores frozen byte-identical, target unchanged. **S6 no-partial:** resolve then abort → nothing landed, `candidateRef` absent/unchanged. **M7b label:** seed asymmetric line counts and assert `classifyRebaseConflicts` labels the stage-3 count `'your reviewed work'` and the stage-2 count `'the integration target'` (re-swapping fails it). Run `-t "resolve_conflict_hunk"`. Expected: FAIL — function absent + the label assertion fails against current inverted labels.

**Step 2 (GREEN — integration.ts):** (a) swap the labels at `classifyRebaseConflicts:1657` (stage-3 = reviewed work; stage-2 = target). (b) `resolveConflictHunk(db, {…, path, choice, content?})`: re-validate a `ready_for_integration` paused rebase; apply the choice to the single path (keep-mine → `--theirs`; take-target → `--ours`; prose → write `content`), `git add <path>`; when `--diff-filter=U` is empty run `git -c core.editor=true rebase --continue` **inside try/catch** (a failing continue = a NEW conflict → return the fresh `classifyRebaseConflicts` set + remaining count, NO candidate); after a successful continue, verify the `rebase-merge` dir (`rev-parse --git-path rebase-merge`, `:1667` pattern) is GONE **and** `symbolic-ref HEAD` succeeds before writing `candidateRef(guid)`=HEAD; return `{path, staged, remaining, candidate?}` (candidate only on TRUE completion); `abort` → `recoverRebaseInProgress('abort')`. A delete-modify path makes `checkout --ours/--theirs` throw on the absent stage → refuse that path explicitly. NEVER lands, NEVER pushes.

**Step 3 (GREEN — index.ts):** register `resolve_conflict_hunk` (`PUBLIC_TOOL_NAMES` + definition with `properties` repository_path, workspace_guid, path, `choice` enum `['keep-mine','take-target','prose','abort']`, optional `content`; required repository_path, workspace_guid, path, choice + dispatch + handler with `requireProviderRoot`; add to `PublicToolDependencies`).

**Step 4:** Full suite + typecheck; stage the four files.

---

## Task 4: `confirm-resolution` skill surface + wire the reconcile/close-out skills

**Depends on:** Task 3. **Files:** `worker/skills/confirm-resolution/SKILL.md` (create), `worker/skills/reconcile/SKILL.md`, `worker/skills/close-out/SKILL.md`.

**No tests required:** skill markdown is orchestration prose with no test framework; the behavior it drives is proven by Tasks 1-3.

**Step 1:** Create `worker/skills/confirm-resolution/SKILL.md` mirroring `worker/skills/reconcile/SKILL.md`: frontmatter name/description; the four exact human forms (`/confirm-resolution`, `/ironclaude:confirm-resolution`, `$ironclaude:confirm-resolution`, the Codex command-link); authority is server-held (the keystroke mints the intent); the skill calls `land_resolved_conflict` with only `repository_path` + `workspace_guid`; never push. Without this file neither client offers the command and the mint never fires (I2).

**Step 2:** In `reconcile/SKILL.md` and `close-out/SKILL.md`, at the `rebase-paused-conflict` surface (M7b): (1) non-interactive → M7b preserve-and-defer, stop (S4/C4b); (2) per class-2 hunk oldest-first: `AskUserQuestion` two-sided summary → `resolve_conflict_hunk` → show back → confirm/revise/abort; (3) on a candidate: show the COMPLETE cumulative diff → operator types `/confirm-resolution` → `land_resolved_conflict`; (4) close-out only: after `reconciled`, instruct a re-run of `/close-out` to tear down the now-integrated row (never auto-mint). Add constraints: operator never hand-edits or is told to "resolve and re-run" (S8); resolution never pushes (S7); abort restores the frozen commit (S5). Stage the three files.
