# Interactive Conflict Resolution (M7) — M7c Design

> **Created:** 2026-08-27
> **Status:** Design Complete (M7c — design only, NO code)
> **Roadmap:** docs/plans/2026-08-24-post-v117-roadmap.md (M7, split M7a/M7b/M7c)
> **Builds on:** M7a design (docs/plans/2026-08-27-conflict-qa-m7a-design.md); M7b detect+surface (committed `c6454c2`).
> **Deps:** M2, M5, M6, M7a, M7b (all shipped).

## Summary

M7b turned the raw "unmerged paths: …" rebase refusal into a plain-language,
classified surface, but the outcome stays refuse/preserve-and-defer. M7c makes the
resolution real: for each genuinely-ambiguous (class-2) hunk the verb asks the operator
for plain-language guidance, applies it, shows the result back, and — once the whole
conflict is resolved — lands the resolved content on LOCAL main under a **non-forgeable
operator authority that reuses the existing keystroke-minted human-intent primitive**.
It never pushes. Aborting at any point restores the frozen reviewed commit intact.

The central decision of M7c is **how the operator's resolution authorizes the landing**.
M7a named this as its hardest crux (C4a): the resolved content differs from the reviewed
frozen content, so the `cumulativeBinaryEffect` equality gate that protects every other
integration path rejects it by design. Substituting "the operator confirmed" for that gate
creates a new authority-minting path — the same evidence-light shape that produced M6's C1
critical, where the only proof of authority was something the agent could manufacture. M7c
closes this by **reusing the one confirmation channel in the system whose mint site is
physically unreachable from the agent's tool surface**: the slash-command keystroke that
mints a human intent through the trusted UserPromptSubmit hook.

## Operator decisions settled for M7c

- **D1 — Reuse the proven primitive; no new mechanism, no client split.** The landing
  authority is a keystroke-minted human intent, not a new PostToolUse-on-`AskUserQuestion`
  confirmation primitive. The keystroke mint is unreachable from the agent's tools
  (`issueDirectGitHumanIntent` is imported only by the `hook-intent.ts` hook binary, never by
  the MCP server `index.ts`), works identically on Claude and Codex through the one
  `state-activator.sh` gate, and gives the headless fallback for free. Operator directive:
  "use what already works — I really don't want a big split." Adjudicated REUSE-VIABLE by a
  Fable review that verified every anchor.
- **D2 — Authority of record is a final `/confirm-resolution` keystroke over the complete
  resolved diff (amends M7a C4).** M7a step 4 / C4 said "the operator's live **per-hunk**
  confirmation is the review of record." M7c keeps the per-hunk prose Q&A **for correctness**
  (get each resolution right, revise or abort as needed) but relocates the **authority to
  land** to a single `/confirm-resolution` keystroke over the cumulative final diff. This is a
  refinement — the operator reviews strictly more (the whole candidate, not per-hunk
  fragments) and the authority becomes non-forgeable rather than the "NEW authority-minting
  path" M7a itself flagged as the M6-C1 hazard — but it amends a settled M7a sentence and was
  **explicitly approved** by the operator on 2026-08-27.

## Operator constraints (inherited from M7a — unchanged authority)

- **C1 — Conflicts are never the operator's to resolve manually.** The system edits files;
  the operator gives prose guidance only, never hand-edits or "go fix it and re-run."
- **C2 — Never-lose-work is paramount.** The frozen reviewed commit is preserved until a
  fully-confirmed resolution lands; abort restores it exactly; the target is never corrupted
  mid-resolution.
- **C3 — Never push.** Reconcile/close-out land on LOCAL main only; resolution never pushes.
- **C4 — Never land unreviewed bytes silently.** Superseded in locus by D2: the review of
  record is the `/confirm-resolution` keystroke over the complete resolved diff; the resolved
  candidate lands through the existing isRepair fresh-commit channel
  (`finalizeAttestedCandidate`), never a new integration bypass.
- **C5 — Do not disturb the M2/M5/M6-proven lanes.** The ff-only / equality-gate /
  preserve-and-defer floor stays the fallback. Plain `/reconcile` and `/close-out` keep their
  current landing semantics **byte-unchanged**: the `cumulativeBinaryEffect` equality gate
  guards the active lane (`continueFrozenFinalization`) and close-out's ready lane, while
  reconcile's ready *repair* lane lands any descendant via descendant + CAS proof (no equality
  check) — by design. M7c adds resolution on a distinct verb and changes neither.

## Architecture

M7c extends the existing rebase-recovery state machine in `integration.ts`; it adds no
parallel machine and no new integration path that bypasses the descendant + CAS proofs of
`finalizeAttestedCandidate`. Because an MCP call is a single request/response, "synchronous
inside the verb" is **skill-orchestrated**, mirroring M6's handler-orchestrated recovery: the
verb skill drives an `AskUserQuestion` loop between MCP calls.

### The authority spine (non-forgeable landing)

The landing reuses the keystroke → intent → consume machinery verbatim, with two additive
guards:

1. **New operation `confirm-resolution`.** Added to the `state-activator.sh` operation loop
   (one entry; the same gate already serves `/commit`, `/reconcile`, `/close-out` and the
   Codex `$ironclaude:…` forms — so both clients are covered by one change). A distinct verb,
   not an overload of `/reconcile`. The reason is candidate *provenance*, not an equality gate:
   `/reconcile`'s repair branch (`finalizeReconcile`, `integration.ts:1253-1262`) already lands
   *any* descendant HEAD via `finalizeAttestedCandidate` with descendant + CAS proof and **no**
   `cumulativeBinaryEffect` check — that is the shipped M6 repair contract (an existing test,
   `integration-cases.ts:871-908`, proves a repair reconcile integrates content that differs
   from the frozen commit), and C5 forbids changing it. So overloading `/reconcile` would add
   no protection, and narrowing it would break that contract. The distinct verb is what carries
   the genuinely-new M6-C1-class protection: the **registered-candidate pin** (guard 2) that
   plain `/reconcile` lacks and cannot gain without a semantics change. `/confirm-resolution`
   carries the explicit semantic "I ran the Q&A and reviewed the shown final diff," exactly as
   `/commit` means "I reviewed the staged diff," and is mintable only after the rebase completes
   and HEAD is back on the branch (a detached mid-rebase HEAD cannot mint) — exactly when the
   final diff is shown.
2. **Server-registered candidate OID.** The apply-resolution flow records the OID of the
   candidate it itself produced (a candidate ref plus a server-side record keyed to the
   assignment). The land tool requires this registered OID to equal live worktree HEAD before
   it will land — so only a candidate the apply flow produced, and that is still checked out,
   can land. A parked-but-unregistered HEAD refuses.

The mint already binds a specific commit OID as evidence (`reconcileEvidence.headOid`,
observed server-side at keystroke time), re-observed live at consume; the agent cannot mint
(hook-only), cannot forge the literal-prompt match, and cannot swap content after the
keystroke (evidence mismatch → refuse; `finalizeAttestedCandidate` independently re-proves
`worktreeHead === candidate`). Intent replay is dead by construction (short expiry,
atomic-once consumption, re-issuance supersedes). Mid-paused-rebase the HEAD is detached, so a
premature keystroke cannot mint at all — the mint is only possible after the rebase has
continued to completion and HEAD is back on the branch at the resolved candidate, exactly the
moment the operator is shown the final diff.

### The residual, and why it is the established floor

The one property no design provides is display integrity: the agent could misrelay the
server-returned diff before the operator keystrokes. Design R does not widen this — the shown
diff is the apply tool's server-side return, the registered OID is the OID the tool computed,
and the agent cannot substitute content between show-back and land (only lie in prose). This
is the identical residual every shipped lane already accepts: `/commit` trusts the operator
reviewed the staged tree, and the operator's recourse (inspect the worktree directly) is the
same. The rejected alternative (a PostToolUse-mint-on-`AskUserQuestion` primitive) still
contains an agent-relayed prose step and still rests on a client-side hook, so it removes no
residual R has — while costing a Claude-only mechanism Codex cannot replicate.

## Components (MCP tool surface)

- **detect (existing, M7b).** `classifyRebaseConflicts` / the `rebase-paused-conflict` return
  already deliver the classified class-2 hunks with plain-language two-sided summaries. M7c
  adds an **interactive-operator precondition** here: in a non-interactive context the verb
  refuses to enter the interactive loop and returns preserve-and-defer (C4b).
- **apply-resolution (new).** Input: (hunk identity, operator choice — keep-mine /
  take-target / prose-merged content). It stages the resolved path in the paused rebase,
  returns the resulting candidate hunk for show-back, and — once the last hunk is resolved and
  the rebase (which may span several commits) continues to TRUE completion — records the
  resolved candidate OID (candidate ref + server record). Because the integration rebase is
  `rebase --onto <target> <base>` on the reviewed-work branch (reviewed commits are *replayed*
  onto the target), git's stage `:2:`/`--ours` is the **integration target** and stage
  `:3:`/`--theirs` is the **reviewed work** — the reverse of a merge. So keep-mine is
  `git checkout --theirs -- <path>` and take-target is `git checkout --ours -- <path>`;
  prose-merge is the system authoring the merged file content from the operator's description,
  staged and shown back. A revise re-applies; an abort runs `recoverRebaseInProgress('abort')`.
  (The shipped M7b `classifyRebaseConflicts` two-sided summary labels these sides backwards;
  M7c corrects it so the operator authorizes from an accurate description.)
- **land (new).** Consumes the `confirm-resolution` intent via `verifyDirectGitAuthority`,
  additionally requires the registered candidate OID to equal live HEAD, then routes the
  candidate through `finalizeAttestedCandidate` — the isRepair channel — bypassing the
  equality gate **for this operation only** because the keystroke over the shown diff is the
  review. Local main advances; never pushes.

## Data flow (per conflict, oldest-hunk-first)

1. Verb hits a class-2 conflict → detect returns the classified hunks (M7b). Non-interactive
   context → preserve-and-defer, stop.
2. For each ambiguous hunk: skill presents the plain-language two-sided summary via
   `AskUserQuestion`; operator gives keep-mine / take-target / prose guidance (never
   hand-edits, C1).
3. apply-resolution produces the candidate resolved hunk and the skill shows it back.
4. Operator confirms (for correctness), revises (return to step 2), or aborts (Error
   Handling).
5. Repeat until every hunk is confirmed; the rebase continues to completion; HEAD is the
   resolved candidate; apply-resolution registers its OID.
6. Skill shows the **complete cumulative diff** and instructs the operator to type
   **`/confirm-resolution`**. The keystroke mints the intent; the land tool consumes it,
   checks registered-OID === live HEAD, and lands via `finalizeAttestedCandidate`. Local main
   advances. Never pushes (C3).

## Error handling (abort / rollback)

- **Abort at any prompt** → `recoverRebaseInProgress('abort')` → frozen pre-rebase reviewed
  commit restored exactly, target unchanged, verb returns "aborted; nothing landed; reviewed
  work preserved" (C2).
- **No partial resolution** — all-confirmed-or-abort; M7c never lands a subset of hunks and
  defers the rest.
- **No keystroke / headless** → no intent minted → land refuses → preserve-and-defer floor
  (C4b). Falls out for free; identical on both clients.
- **Frozen commit is the invariant** — it exists on its durable ref until the fully-confirmed
  resolution lands; every intermediate step is recoverable to it.
- **Session interruption mid-Q&A** leaves the preserved paused-rebase state; a re-run resumes
  classification from the same worktree. No work lost.
- **Malformed / offline / proof-failure at landing** preserves the worktree and surfaces the
  exact blocker (existing preserve-and-throw discipline) — never a silent integrate.

## Testing strategy

- **Taxonomy regression:** M7b's class-1/2/3 guards still hold; a non-overlapping /
  identical-effect rebase still integrates via the equality-gate path with no interaction.
- **Interactive resolve:** a seeded overlap conflict → each of keep-mine / take-target /
  prose-merge produces the asserted resolved bytes; the confirmed candidate lands via the
  `confirm-resolution` intent; local main advances; NO push; the equality gate is **replaced**
  by the keystroke authority for this path (not silently bypassed on other paths).
- **Non-forgeability:** through the `land_resolved_conflict` tool, refuse when (a) no
  `confirm-resolution` intent exists, or (b) the registered candidate OID ≠ live HEAD. The
  post-keystroke HEAD-move case (c) refuses at intent-consume (`verifyDirectGitAuthority`
  observes evidence live, so a moved HEAD yields "requires a matching human intent"); the
  finalize-level guard "Resolution HEAD changed since /confirm-resolution" is defense-in-depth
  for the verify→finalize window and is falsified by a *direct* `finalizeConfirmResolution`
  test (mint→verify→move HEAD→finalize), not through the tool.
- **Cross-verb non-interchange + registered-candidate (the real M6-C1 guard):** a `reconcile`
  intent cannot be consumed by `land_resolved_conflict` and a `confirm-resolution` authority
  cannot drive `finalizeReconcile` (operation guards); a parked-but-unregistered HEAD refuses
  at the land tool (registered candidate ≠ HEAD); and the existing reconcile repair suite
  (incl. `integration-cases.ts:871-908`) passes byte-unchanged. This — not a nonexistent
  reconcile equality gate — is what proves the agent cannot land content the operator did not
  see through the new surface.
- **Abort:** abort at the first prompt → frozen commit restored byte-identical, target
  unchanged, nothing landed.
- **No-partial:** confirm hunk 1, abort at hunk 2 → nothing landed, frozen restored.
- **C4b negative test:** a non-interactive caller cannot land a resolution (no keystroke →
  refuse).
- **Never-push falsifier:** no `git push` is reachable from any resolution path.
- **Never-manual falsifier:** the operator is never returned a raw conflict / "resolve and
  re-run" message on a class-2 conflict once M7c is live.

## Implementation notes (the M7c build order)

- **Build the authority spine first, prove it against a mechanically-seeded candidate.** The
  verb + intent plumbing + land tool + server-registered candidate are separable from the
  interactive apply/skill UI, and they carry the security weight. Prove them (including the
  cross-verb non-interchange and registered-candidate guards) against a candidate produced by a
  plain mechanical resolution before wiring the interactive Q&A on top. This is a suggestion
  for planning, not a fixed task split.
- **Additive change list (each small, through the existing primitive):**
  1. `state-activator.sh` — add `confirm-resolution` to the operation loop (one entry; both
     clients).
  2. `hook-intent.ts` — admit `confirm-resolution` (reuses the active-assignment query;
     `ready_for_integration` rows already qualify).
  3. `git-authority.ts` — admit the operation in `observeDirectEvidence`'s reconcile/close-out
     evidence branch and `verifyDirectGitAuthority`'s reconcile branch; add it to the
     `DirectGitOperation` union; and **exclude it from `usablePushAuthorizations`** so a
     confirm-resolution authority can never drive a push (defense-in-depth for C3/never-push).
  4. `db.ts` — a **v5 migration** widening the `human_intents.operation` CHECK allowlist to
     include `'confirm-resolution'` (the v3 migration is the exact precedent). Without it the
     mint fails at insert with a CHECK-constraint error.
  5. `index.ts` / `integration.ts` — the new land tool (consume → registered-OID check →
     `finalizeAttestedCandidate`) and the apply-resolution tool. The apply tool corrects the
     rebase side mapping (keep-mine → `--theirs`, take-target → `--ours`), registers the
     candidate **only on TRUE rebase completion** (a multi-commit rebase can pause again after
     `--continue`; check the `rebase-merge` dir is gone and HEAD is re-attached before
     registering), and also **corrects the shipped M7b `classifyRebaseConflicts` inverted
     side labels** so the surfaced two-sided summary is accurate.
  6. Wire the `reconcile` and `close-out` skills to orchestrate the `AskUserQuestion` loop
     between detect and apply, instruct the `/confirm-resolution` keystroke at the end, and
     add a **`worker/skills/confirm-resolution/SKILL.md`** so the slash command is a typeable,
     mintable surface on both clients (every mintable verb ships this file).
- **Do NOT** modify `resolveEffectiveCheckout`, the push lanes, the M5/M6 commit/close-out
  landing proofs, or plain `/reconcile`/`/close-out` landing semantics. The
  free-text-never-minted invariant stays intact — the gate remains an exact slash-command
  match; adding one operation entry widens nothing about what free text can do.

## Non-goals (M7c scope=hold)

- No AI that guesses a resolution without the operator's prose and the final keystroke.
- No partial / per-hunk deferred landing (all-or-abort).
- No new confirmation-authority mechanism and no per-client confirmation code (D1).
- No async/Slack medium (synchronous in-verb); a Commander-orchestrated async variant is a
  possible future milestone, out of scope here.
- No conflict resolution for the pure `/push` lane (push is human-only, ff-only + lease; it
  never rebases).
- No change to plain `/reconcile` / `/close-out` landing behavior — those lanes stay exactly
  as shipped (equality-gated on the active + close-out-ready lanes; descendant + CAS on
  reconcile's ready repair lane). M7c only adds the distinct `/confirm-resolution` surface.
