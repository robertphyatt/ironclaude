# /reconcile — integrate a worktree into local main, keep it alive Design

> **Created:** 2026-08-23
> **Status:** Design Complete
> **Scope mode:** selective
> **Epic:** Loop A (first) of the human worktree-lifecycle epic
> ([[project_human_worktree_lifecycle_model]]). Four human verbs: commit-keep, push, reconcile-keep,
> close-out. This loop adds verb 3 (/reconcile). Sequencing (Fable-advised, additive-first): A /reconcile
> → B flip /commit to commit-keep → C /close-out → D verify /push (already ships) → E conflict Q&A.

## Summary

Add a human `/reconcile` verb: integrate the current managed worktree's HEAD into LOCAL main and KEEP the
worktree alive (assignment stays usable). It never pushes (publishing to origin is the separate `/push`).
Today this behavior exists only FUSED inside the managed `/commit` (which commits AND integrates). This
loop extracts the integrate-and-keep half as its own verb, so a later loop can make `/commit`
worktree-local without losing the route to main. `/reconcile` is purely additive — it changes no existing
verb — so it deploys standalone with no regression window.

## Why this is verb 3 and why it is additive

- Today's managed `/commit` = `finalizeDirectAuthority` op==='commit' → `createExactCommit`
  (integration.ts:1042) → `finalizeLocalCommit` (:1065, integrate to local main) → `recycleFinalized`
  (:1069) → `{state:'cleaned'}`. `recycleFinalized` (:568-596) is NOT teardown — it deletes the
  integration record, sets `base_commit=integratedCommit`, transitions `integrated→active`, leaving the
  worktree ALIVE (integration-cases.ts:266-269). So the fused commit already reconciles-and-keeps.
- `/reconcile` = the same integrate-and-keep on the current worktree HEAD, MINUS the fresh commit. No
  existing verb changes; `/commit` keeps working exactly as today until Loop B.

## Verb semantics

- **Target:** managed worktree only. Refuse `primary` (primary IS main) and `primary-unassigned` (the
  unassigned lane already refuses non-commit/push/commit-and-push at issue git-authority.ts:446 and verify
  :484 — a new `reconcile` op is refused there for free).
- **Effect:** integrate the worktree's current HEAD into `targetRef(assignment)` (local main), ff/equality-
  proven, keep the worktree active on the integrated commit. NEVER pushes to any remote.
- **Precondition:** clean worktree (the integration path's dirty gate finalizeLocalCommit:832-834 + the
  rebase clean requirement); a dirty tree is refused with "commit first with /commit". Divergent main
  (merge-base guard :752-753) is refused directing to `sync_worktree_to_target` (the future conflict Q&A
  loop replaces that manual step).

## Authority and evidence (I1 + HEAD-pinning)

- **New operation `reconcile`** in the human-intent lane. `human_intents.operation` is CHECK-constrained
  to the five existing verbs in v1 AND v2 (db.ts:134,:170); a v3 migration recreates the table with the
  CHECK widened to add BOTH `reconcile` and `close-out` (close-out is Loop C, admitted now to avoid a v4
  recreate; an unused admitted enum has zero behavior). SQLite CHECK cannot be altered in place — recreate
  exactly as the v2 migration did (db.ts:165-190), carrying the v2 NULL-guard lesson (straight copy; v2
  already cleaned NULL guids).
- **HEAD-pinning evidence** (`ReconcileEvidence`): `{checkoutMode:'managed', canonicalBranch, localRef,
  headOid}` — no stagedTree (no commit is created), no remote fields (local only). The mint captures the
  exact worktree HEAD oid; consume re-observes and requires live HEAD === pinned headOid (falsifiable: move
  HEAD after issuance → refuse). The human's blessing of the current content is the act of issuing
  `/reconcile` after seeing the state — the same authority strength as today's stagedTree attestation, with
  no commit evidence in the reconcile shape.
- **I1 repair via the same verb:** a `ready_for_integration` worktree with a conflicted finalization is
  repaired today by a fresh `/commit` authority that integrates (finalizeAttestedCandidate :840,
  descendant proof :858; case-D routing :1228-1231). New model: the human resolves, runs `/commit`
  (commit-keep, allowed on ready_for_integration too — Loop B) to create the exact commit, then
  `/reconcile` integrates the pinned HEAD. So `/reconcile` handles BOTH the `active` row (normal reconcile)
  and the `ready_for_integration` row (repair) — both are "integrate the pinned current HEAD into local
  main." `/commit` stays unconditionally local; repair does not force `/commit` to integrate.

## Components / edit sites (verify each against source in planning, Step 1.6)

1. `src/db.ts` — v3 migration recreating `human_intents` with the CHECK admitting `reconcile` + `close-out`
   (mirror the v2 recreate :165-190).
2. `src/types.ts:93` — extend `HumanIntentOperation` with `reconcile` (and `close-out`).
3. `src/git-authority.ts` — add `reconcile` to the direct-git op type; `ReconcileEvidence`; an
   `observeDirectEvidence` (or a dedicated observer) branch capturing `headOid = rev-parse HEAD`; the verify
   branch asserting live HEAD === pinned headOid; issue + verify refuse non-managed modes.
4. `src/integration.ts` — export `finalizeReconcileAuthority(db, authority)`:
   - `active` row: require `worktreeHead === evidence.headOid`; `finalizeLocalCommit(db, primary,
     assignment, worktree, headOid)` (its HEAD===frozen check :825 holds; dirty gate :832 gives the clean-
     tree requirement) → `recycleFinalized` → return `{state:'reconciled', integratedCommit}` (a NEW honest
     state string — do NOT reuse 'cleaned').
   - `ready_for_integration` row (repair): require the durable frozen ref (mirror :1029-1032) +
     `worktreeHead === headOid`; `update-ref candidateRef` + `finalizeAttestedCandidate` + recycle (=
     `finishLocalIntegration` :660-670; no push disposition exists for reconcile). Anything else → error
     directing to `reconcile_finalization`.
5. `src/hook-intent.ts:66` — add `reconcile` to the direct-git issuance branch (HEAD-pinning evidence).
6. `worker/hooks/state-activator.sh:54` — add `reconcile` to the verb list.
7. `src/index.ts` — add the public tool (`publicToolDefinitions` + dispatch case + a dependency with
   `requireProviderRoot`, no `message` arg). MCP tool name `reconcile_worktree` (avoid adjacency with the
   existing `reconcile_finalization`); slash verb stays `/reconcile` (slash↔tool mapping is independent,
   index.ts:195-197).
8. `worker/skills/reconcile/SKILL.md` — new skill mirroring `worker/skills/commit` (human-only exact-form
   gate; call the reconcile tool; require successful local-integration evidence; NEVER push).

## Data Flow

Human in a managed worktree runs `/reconcile` → state-activator mints a `reconcile` intent pinning the
current HEAD oid (HEAD-pinning evidence, local-only) → `/reconcile` skill calls workspace-manager
`reconcile_worktree` → verify re-observes HEAD, matches the pinned oid, consumes the single-use intent →
`finalizeReconcileAuthority` integrates HEAD into local main (rebase/equality-proven) + recycleFinalized
keeps the worktree active on the integrated commit → returns `{state:'reconciled', integratedCommit}`. No
remote is touched.

## Error Handling

- Dirty worktree → refuse: "commit first with /commit" (dirty gate :832-834).
- HEAD moved after issuance → pin mismatch → refuse (single-use intent preserved for re-issue).
- Divergent main (not a fast-forward / merge-base guard :752-753) → refuse, direct to
  `sync_worktree_to_target` (Loop E later automates the Q&A).
- Non-managed mode (primary / unassigned) → refuse.
- `ready_for_integration` without a durable frozen ref → error directing to `reconcile_finalization`.

## Testing Strategy

TDD (vitest), then a 3a-style scratch live-proof. Cases:
- v2→v3 migration preserves rows; the v3 CHECK still REFUSES arbitrary operation strings (widened-guard
  negative cases) and now ADMITS `reconcile` + `close-out`.
- HEAD-pin: reconcile of an `active` worktree integrates HEAD into local main, worktree stays alive + row
  `active` + `base_commit=integratedCommit` (mirror integration-cases.ts:256-271); moving HEAD after
  issuance → refuse.
- The blast-radius invariant: reconcile NEVER pushes — remote bytes unmoved (mirror :284-289).
- dirty-tree refusal; non-managed / unassigned refusal; repair-branch (`ready_for_integration`)
  integration; multi-commit range integrates (cumulativeBinaryEffect + rebase --onto :755-757 already
  supports it).

## Implementation Notes

- **Deploys standalone:** nothing existing changes behavior; the v3 CHECK is strictly wider than v2 (no old
  writer violates it; old readers do not inspect it). No regression window.
- **State string:** return `reconciled`, not `cleaned` (honest naming; `FinalizationResult` :31-41 widened).
- **Naming adjacency risk:** MCP tool `reconcile_worktree` vs `reconcile_finalization` (recovery) — crisp
  descriptions; slash verb `/reconcile`.
- **Guard bug #5 / docs gitignored:** stage plan artifacts with `git add -f` (PM off) or bracket-pathspec.
- **Verify in planning (Step 1.6):** every line anchor above (finalizeLocalCommit :825/:832, finishLocalIntegration
  :660-670, finalizeAttestedCandidate :840/:858, recycleFinalized :568-596, the v2 migration :165-190,
  db.ts CHECK :134/:170, git-authority issue :446 / verify :484, index.ts tool mapping :195-197).

## Non-goals

- Loop B (flip /commit to commit-keep). Loop C (/close-out = reconcile + releaseFinalized teardown). Loop D
  (verify /push — already ships). Loop E (conflict Q&A). No change to any existing verb, to
  finalizeCommanderLocalCommit, to the unassigned lane, or to /commit-and-push. No push/remote in /reconcile.
