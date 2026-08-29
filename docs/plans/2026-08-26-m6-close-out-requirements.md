# M6 — `/close-out` Requirements

> **Created:** 2026-08-26 · Derived from operator directives + the M6 brainstorm (Fable-coached).
> Design: `docs/plans/2026-08-26-m6-close-out-design.md`.

Authority order: operator directives → brainstorming decisions → this requirements file → plan.

## Operator directives (authority)
- **D1.** `/close-out` = reconcile (integrate managed worktree HEAD into LOCAL main) + FULL teardown
  (remove worktree + temp branch, terminal row). Verb 4 of the worktree-lifecycle model.
- **D2.** The worktree verbs must **JUST WORK — auto-resolve edge cases, never instruct the operator
  to "go push/commit first."** (Operator, this loop: "Why can't you just automatically resolve this?")
- **D3.** **Never push without an explicit per-action operator go.** close-out is local-only; it never
  pushes.
- **D4.** Never-lose-work is paramount; never bypass review by landing unreviewed bytes on main.
- **D5.** Do NOT disturb the M2/M5-proven reconcile / commit / commit-and-push / push lanes.
- **D6.** Scope: full auto-resolve including the push-lane drain (operator-confirmed).

## Functional requirements
- **R1.** A new `close-out` direct-Git operation is wired end-to-end (verb prompt-match,
  `DirectGitOperation` union, push-exclusion) with an **evidence-light, lifecycle-tolerant** intent path:
  hook-intent resolves the assignment by guid WITHOUT the active-only exclusion (admitting
  `active`/`ready_for_integration`/`integrated`) and issues an intent that observes NO commit/HEAD
  evidence at issuance; `verifyDirectGitAuthority` observes `ReconcileEvidence` LIVE at verify and
  consumes the intent by operation/repo/guid/session. A new `close_out_worktree` MCP tool (with
  handler-orchestrated recovery, R7) and a `worker/skills/close-out/SKILL.md` exist. The lifecycle-tolerant
  lookup is close-out-scoped: `/reconcile`/`/commit` on an integrated row still refuse (negative tests).
- **R1b (entry is total).** The evidence-light intent makes `/close-out` mintable at prompt time even
  when a rebase is paused (detached HEAD) or the row is `integrated` — no raw identity/missing-intent
  error reaches the operator on a legitimate close-out target. Security: the authority still binds the
  live worktree identity/commit observed at verify; only issuance defers commit observation.
- **R2.** On a clean worktree with no push-pending obligation: close-out integrates HEAD into local
  main, removes the worktree + temp branch, transitions the row to a terminal `cleaned` state, and
  returns a `closed-out` result. Local main advances; the reconcile lane is byte-untouched.
- **R3 (Case A — auto-resolve push-pending).** A push-pending obligation exists only on an `integrated`
  row. close-out must ENTER on such a row (via R1's lifecycle-tolerant intent + a `finalizeCloseOut`
  `integrated` branch, so the persistent state — a failed `/commit-and-push` whose worktree is still
  alive — is not dead-ended with "nothing to close out"), tear the worktree down anyway, and **carry the
  obligation forward on the terminal (`cleaned`) row** (disposition preserved; candidate/frozen refs
  kept). It performs a read-only `ls-remote` self-heal first (clearing the obligation for free if the
  remote already has the commit). It NEVER pushes and NEVER instructs the operator. An `integrated` row
  whose worktree is already gone (crash mid-teardown) completes DB-only (transition to `cleaned`, carry
  disposition), never losing work.
- **R4 (drain — smallest possible push-lane touch).** A later explicit `/push` of local main to the
  carried obligation's remote clears the obligation by **containment**
  (`isAncestor(candidateCommit, pushedOid)`), via a post-success sweep in the push finalizers. A
  non-matching remote/ref leaves the obligation intact. This sweep is **hygiene, not correctness**: the
  carried commit is already in local main, so a normal `/push` of main publishes the work regardless —
  the sweep only clears the now-stale disposition record on the `cleaned` row. It MUST be the minimal
  post-success clear and MUST NOT otherwise alter the M2/M5-proven push lanes (D5). A precondition to
  verify while planning: no recovery/reaper reader ACTS on a `cleaned` row's stale push disposition.
- **R5 (Case B — auto-resolve dirty tree).** When the worktree is dirty, close-out snapshots the full
  residual (tracked + untracked, `add -A` scope) to a durable `refs/ironclaude/recovery/<guid>` ref
  (setting `recovery_ref`) BEFORE cleaning, then integrates the committed HEAD and tears down. Residual
  is preserved on the ref (never on main, never auto-committed to a reviewed lane); the result names
  the recovery ref + residual count loudly. No instruction to the operator.
- **R6 (idempotent, no scary error).** A re-run of `/close-out` when there is no active/ready
  assignment to close out (e.g. already closed out) returns a CLEAR, non-error terminal message
  ("nothing to close out for this worktree") — never a hard failure, never the go-fix-it anti-pattern.
  Handled at the skill layer: the intent layer (`hook-intent.ts:44`) already excludes
  cleaned/integrated rows and the skill cannot distinguish "already closed out" from "no such
  assignment," so it reports one clear message covering both. (Narrowed from "returns success" — that
  was unreachable via the intent path.)
- **R7 (auto-continue recoverable rebases; conflicts NEVER handed to the operator).** When
  integration pauses on a rebase, close-out itself resolves what it can and never instructs the
  operator to fix it:
  - **(a) Mechanically-recoverable** (`rebase-paused-clean`): the `close_out_worktree` HANDLER, BEFORE
    minting authority, probes `reconcileFinalization('status')` and, on a clean paused rebase, invokes
    the existing **authority-free** `reconcileFinalization('continue')` (integration.ts:1246, unmodified)
    to drive it to completion with HEAD re-attached; it then verifies the close-out authority live and
    runs `finalizeCloseOut` → teardown. No operator action, no instruction; the reconcile lane is
    byte-untouched. (Recovery is handler-orchestrated, NOT inside the authority-gated finalizer — a
    paused rebase detaches HEAD, so no close-out authority could be minted on it.)
  - **(b) True conflict** (`rebase-paused-conflict`) or a content-changing resolution
    (`rebase-recovery-repair-required`, :1298): preserve the worktree and report it as PENDING
    AUTOMATED resolution (M7 conflict Q&A). It is NEVER surfaced as an operator action item ("go
    resolve it and re-run"), per the standing directive that conflicts are auto-resolved or deferred,
    with the operator asked only PROSE guidance on genuinely ambiguous spots. Interactive assisted
    resolution itself is M7 scope; M6 stops at preserve-and-defer.
  - close-out does not inherit reconcile's "run reconcile_finalization first" instruction
    (integration.ts:1184) — it invokes the recovery itself.
- **R8 (observability).** Outstanding carried obligations and recovery refs are surfaced in
  `get_workspace_status` / `list_active_assignments`.
- **R9 (never-lose-work seams).** Nothing is removed before its content is durably anchored and
  re-verified; any failed proof (unreachable integration, malformed disposition, non-managed checkout)
  preserves the worktree and throws. Offline `ls-remote` is non-fatal (carry + proceed).

## Non-goals
- Pushing (D3); the internal Commander-only `cli.js` close-out crash-recovery parity; replacing
  `tombstoneTerminalAssignment`'s push-pending refusal (a follow-on the new release variant enables);
  interactive conflict resolution (M7).

## Remediation requirements (post-end-review)
- **R10 (never integrate rejected content).** When a close-out rebase resolution CHANGES the reviewed
  content, close-out MUST preserve-and-defer (return `rebase-recovery-repair-required` / a paused-conflict
  state), never integrate it onto main. The handler proceeds only on a clean-integrate `continue` result
  (`cleaned`/`integrated-local`); the `finalizeCloseOut` ready branch independently proves
  `cumulativeBinaryEffect` equality before integrating (close-out's authority is evidence-light).
- **R11 (preserved work survives row reuse).** A carried push obligation and a Case-B recovery snapshot
  MUST survive same-session workspace reuse (GUID = session id) and a subsequent close-out on the same
  GUID. Realized via an additive `preserved_work` table + per-lifecycle content-addressed recovery refs
  (`refs/ironclaude/recovery/<guid>-<oid>`, create-only); `reuseTerminalAssignment` + the reaper stay
  byte-untouched. `list_preserved_work` reads the table (unresolved only). Subsumes the Case-B crash-retry
  guard (per-lifecycle refs never overwrite a prior snapshot).
- **R12 (auto-heal, don't error).** An integrated push-pending row with worktree HEAD at the frozen
  commit (a `reconcileFinalization`-recoverable state) MUST auto-heal (`closeOutRelease` resets to the
  candidate) and complete, not hand an error back — with the dropped candidate/freeze consistency proofs
  restored.

## Acceptance
- Whole workspace-manager suite green (`--testTimeout=30000`) including new tests for R2–R7, R10–R12, and a
  regression proof for D5 (reconcile/commit/commit-and-push/push/repair unchanged, and reuse/reaper lanes
  byte-untouched). Negative cases: repair-required → main unmoved; reuse-then-list survives; double
  close-out → both snapshots survive; integrated-at-frozen → closed-out with carried obligation.
