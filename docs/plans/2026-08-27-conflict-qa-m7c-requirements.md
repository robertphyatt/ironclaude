# M7c Interactive Conflict Resolution — Requirements

> **Created:** 2026-08-27
> **Status:** Operator-approved (derived from the M7c brainstorming, 2026-08-27)
> **Design:** docs/plans/2026-08-27-conflict-qa-m7c-design.md
> **Derived from:** the M7a design (authority), the M7c brainstorming dialogue, and two
> explicit operator approvals on 2026-08-27 (D1 reuse-no-split; D2 final-keystroke authority).

This is the derived, operator-reviewed contract. It does not replace the operator directives
or the full brainstorming that produced it; where they conflict, the operator statements and
settled brainstorming decisions win.

## Operator-settled decisions (authority)

- **D1 — Reuse the proven keystroke-intent primitive; no new confirmation mechanism, no
  Claude/Codex split.** Operator directive: "use what already works — I really don't want a
  big split." A Fable adjudication verified the reuse is non-forgeable and superior to the
  alternative.
- **D2 — Authority of record is a single `/confirm-resolution` keystroke over the complete
  resolved diff, amending M7a C4's per-hunk-confirmation locus.** Per-hunk prose Q&A remains,
  for correctness only. Explicitly approved by the operator on 2026-08-27.

## Functional requirements

- **R1 — New verb `confirm-resolution`.** A first-class direct-git operation added to the mint
  gate and the authority/evidence layers, distinct from `reconcile`. Its human intent is
  minted only by the trusted UserPromptSubmit hook on the exact `/confirm-resolution` (and the
  `/ironclaude:` and Codex `$ironclaude:` / command-link) forms — never by free text, never by
  any MCP tool. Being mintable requires two prerequisites: (a) the `human_intents.operation`
  SQL CHECK allowlist must include `confirm-resolution` (a schema migration), else the insert
  fails; (b) a `worker/skills/confirm-resolution/SKILL.md` must exist so the slash command is a
  typeable, mintable surface on both clients (an unregistered command never reaches the hook).
- **R2 — Apply-resolution tool.** An MCP tool that takes one ambiguous hunk plus the operator's
  choice (keep-mine → the reviewed side / take-target → the target side / prose-merged content),
  stages the resolved path in the paused rebase (respecting the rebase side orientation:
  `--theirs` is the reviewed work, `--ours` is the target), returns the resulting candidate for
  show-back, and — only when the rebase, which may span several commits, continues to TRUE
  completion — records the resolved candidate commit OID (candidate ref + a server-side record
  keyed to the assignment). An interim mid-rebase state is never registered as the candidate.
- **R3 — Land tool.** An MCP tool that consumes the `confirm-resolution` intent, additionally
  requires the server-registered candidate OID to equal live worktree HEAD, and lands the
  candidate through the existing `finalizeAttestedCandidate` (isRepair) channel. It replaces
  the `cumulativeBinaryEffect` equality gate **for this operation only** with the operator's
  keystroke over the shown diff. It advances LOCAL main; it never pushes.
- **R4 — Verbs wired.** Interactive resolution is available from `reconcile` and `close-out`
  (both replay frozen work onto a drifted target). The pure `/push` lane is untouched (it
  never rebases).
- **R5 — Skill orchestration.** The `reconcile` and `close-out` skills orchestrate the
  `AskUserQuestion` loop between detect (M7b) and apply, and instruct the operator to type
  `/confirm-resolution` over the cumulative diff at the end.

## Security / safety invariants (must hold)

- **S1 — Non-forgeable landing.** The agent cannot manufacture a resolution landing: it cannot
  mint the intent (mint is hook-only — `issueDirectGitHumanIntent` imported solely by
  `hook-intent.ts`), cannot forge the literal-prompt match, and cannot swap content after the
  keystroke (intent evidence binds `headOid`, re-observed live at consume; the land tool also
  requires registered-OID === live HEAD; `finalizeAttestedCandidate` re-proves
  `worktreeHead === candidate`).
- **S2 — Free-text-never-minted invariant untouched.** `state-activator.sh` stays an exact
  slash-command match; adding one operation entry widens nothing about what free text can do.
- **S3 — Distinct-verb provenance guard (M6-C1 class), reconcile unchanged.** The genuinely-new
  protection is candidate *provenance*, not a reconcile equality gate (reconcile's ready repair
  lane lands any descendant with no equality check, by shipped design). It must hold that: a
  `reconcile` intent cannot be consumed by `land_resolved_conflict`, and a `confirm-resolution`
  authority cannot drive `finalizeReconcile` (cross-verb non-interchange); a parked-but-
  unregistered HEAD refuses at the land tool (registered candidate ≠ HEAD); and the existing
  reconcile repair suite passes byte-unchanged (the new verb does not touch the old lanes).
- **S4 — C4b headless fallback.** No keystroke → no intent → land refuses → preserve-and-defer
  floor. The detect step also refuses to enter the interactive loop in a non-interactive
  context. A non-interactive caller can never land a resolution (negative test required).
  Identical on both clients; zero dedicated fallback code.
- **S5 — Never-lose-work / abort.** The frozen reviewed commit is preserved until a
  fully-confirmed resolution lands; abort at any point restores it byte-identical via
  `recoverRebaseInProgress('abort')`; the target is never corrupted mid-resolution.
- **S6 — All-or-abort.** No partial landing of a subset of resolved hunks.
- **S7 — Never push.** No `git push` is reachable from any resolution path.
- **S8 — Never-manual.** The operator is never returned a raw conflict / "resolve and re-run"
  message on a class-2 conflict once M7c is live.

## Out of scope (scope=hold)

- No new PostToolUse-on-`AskUserQuestion` confirmation primitive; no per-client confirmation
  code (D1).
- No AI that guesses a resolution without the operator's prose and the final keystroke.
- No async/Slack medium; no Commander-orchestrated async variant.
- No change to plain `/reconcile` / `/close-out` behavior on cleanly-replayable or
  altered-but-unconfirmed content.
