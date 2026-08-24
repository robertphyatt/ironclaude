# /reconcile verb Requirements (operator-approved)

> **Created:** 2026-08-23
> **Source:** the operator-confirmed worktree-lifecycle model ([[project_human_worktree_lifecycle_model]])
> and the Fable advisor's sequencing/decisions (the operator said "follow the fable agent guidance").
> Loop A of the epic. Design: docs/plans/2026-08-23-worktree-reconcile-design.md.

## Why

The operator's model has four human verbs (commit-keep, push, reconcile-keep, close-out). Today the
integrate-into-local-main-and-keep-the-worktree behavior (verb 3) exists ONLY fused inside the managed
`/commit`. This loop extracts it as a standalone human `/reconcile` verb so a later loop can make
`/commit` worktree-local without losing the route to main. `/reconcile` is purely additive.

## Approved requirements

- **R1 — `/reconcile` integrates the current worktree HEAD into LOCAL main and keeps the worktree alive.**
  Managed worktree only. After it runs, the worktree survives and the assignment returns to `active` on the
  integrated commit (mirrors the shipped `recycleFinalized` "keep alive" behavior). Returns a distinct
  honest state `reconciled` (never reuse `cleaned`).

- **R2 — `/reconcile` NEVER pushes.** Integration is local ref motion only; publishing to origin is the
  separate `/push`. A `/reconcile` authority must be structurally incapable of pushing (excluded from the
  push-authorization set). Commander parity: the commander does the same integrate-locally step and never
  pushes.

- **R3 — HEAD-pinning authority.** The minted intent pins the exact worktree HEAD oid at issuance; the
  finalizer MUST assert the live worktree HEAD equals the pinned oid before integrating (the shipped
  finalizeLocalCommit HEAD check goes vacuous when fed the live HEAD, so this explicit assertion is the
  enforcement). A HEAD that drifted between mint and consume is refused. Human-only, provider-root,
  single-use, UserPromptSubmit gate — all unchanged from the existing lanes.

- **R4 — clean tree required.** `/reconcile` integrates the committed HEAD; a dirty worktree is refused
  ("commit first with /commit"). Divergent-main handling stays the shipped behavior (ordinary target
  advancement is absorbed by the rebase; a corrupted base is refused directing to `sync_worktree_to_target`).

- **R5 — repair case.** A `ready_for_integration` worktree (a paused/rebased finalization) is reconciled
  by `/reconcile` too, integrating the already-rebased pinned HEAD (finalizeAttestedCandidate requires the
  HEAD to already descend from the current target, so the sequence is rerebase-via-reconcile_finalization
  then `/reconcile`). This is what lets a later loop make `/commit` worktree-local without orphaning the
  repair path. `/commit` is NOT made to conditionally integrate.

- **R6 — schema.** A v3 `human_intents` migration widens the operation CHECK to admit BOTH `reconcile` and
  `close-out` (close-out is admitted now to avoid a later v4 recreate; it is CHECK+type only this loop, no
  wiring). The migration mirrors the v2 recreate pattern and must not roll back on legacy rows (v2 already
  made workspace_guid NOT NULL, so no extra filter is needed). Negative test: the v3 CHECK still refuses an
  arbitrary operation string.

- **R7 — surface.** New public MCP tool `reconcile_worktree` (slash verb `/reconcile`; the distinct tool
  name avoids adjacency with the existing recovery tool `reconcile_finalization`), required
  `{repository_path, workspace_guid}`, no `message`, `additionalProperties:false`, `requireProviderRoot`.
  A new `worker/skills/reconcile/SKILL.md` (human-only exact-form gate) instructs the tool call and NEVER
  pushes; without it the verb is inert (the hook's codex-link regex references `skills/<op>/SKILL.md`).

- **R8 — additive, deploys standalone.** No existing verb changes behavior. The v3 CHECK is strictly wider
  than v2. No regression window. TDD (vitest) each task; a 3a-style scratch live-proof is the FOLLOW-ON.
  Deploy (local): rebuild dist → claude 1.1.6 cache + codex cache; the codex hook cache is not covered by
  `make deploy-hooks`.

## Non-goals

- Loop B (flip `/commit` to worktree-local). Loop C (`/close-out` = reconcile + releaseFinalized teardown).
- Loop D (verify `/push` — already ships). Loop E (conflict Q&A). `sync_worktree_to_target` (already ships;
  it is the opposite direction — update a worktree TO main). No change to `/commit`, `/commit-and-push`,
  `finalizeCommanderLocalCommit`, or the unassigned lane. No push/remote in `/reconcile`.
