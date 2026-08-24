# Reaper "release failed" garbage — root-cause diagnosis (investigation loop) Design

> **Created:** 2026-08-21
> **Status:** Design Complete
> **Scope mode:** selective (investigation-only; no code fix until the cause is proven)

## Summary

On every commander startup the worktree reaper logs ~47 WARNINGs of the form
`Worktree reaper: release failed for workspace=<guid> worker=<id>: ... Managed worktree
Git identity does not match durable assignment; reconciliation must preserve it`. These are
the old game-repo pipeline workers (`pf2e`/`gm-core`/`d1466`/`d1475`…) — the same leak that
once left the game repo at 17 worktrees / 75GB — whose physical worktrees were removed in a
manual cleanup but whose durable assignment rows remain nonterminal.

**Systematic-debugging conclusion (evidence-backed, one step short of live confirmation):**
the exact error is thrown only by `validateManagedIdentity` (workspace-service.ts:179), and in
**current** repo code the reaper's owned release path canNOT reach it for a *gone* worktree —
`cleanupWorkspace` gates the guard on `if (present)` (:754, introduced by `7388ad7`, verified
via `git log -S`), and `abandonWorkspace`/`rescueAbandon` never call it and tolerate absence.
So the running commander is almost certainly executing a **pre-`7388ad7` workspace-manager
build** whose `cleanupWorkspace` validated identity *unconditionally* and threw on any
already-removed worktree. Loop 3 (`7388ad7`, committed 2026-08-19; deployed locally only on
2026-08-21) already fixes this; the long-running commander never picked it up. This is the
same "committed ≠ deployed ≠ loaded" gap seen elsewhere this session, applied to the commander
as a separate process.

Two live facts are still unconfirmed and decide the remediation:
- **H1 (stale deploy):** commander runs pre-`7388ad7`; the 47 worktrees are truly **gone**.
  Remediation is operational — point the commander at the current build and restart it; the
  now-gone-tolerant reaper reaps the 47 rows on its next sweep. No code change.
- **H2b (residual / foreign-branch):** commander already runs current code, but the worktrees
  are **present on a foreign branch** — in which case the guard firing is *correct*
  preserve-not-destroy behavior, and the "fix" is different (quieter surfacing and/or an
  operator-driven recovery path), decided in a follow-up loop.

## Architecture

A read-only investigation whose **execute stage** (which unblocks Bash/sqlite) gathers the
evidence that brainstorming/debugging stages cannot, and writes a findings note. It changes no
product code and touches no live worktree, branch, or DB row.

**The worktree presence/branch state is the PRIMARY, time-robust discriminator** — not the
bundle version. Two facts make it decisive: (i) worktree deletion is monotonic (gone now ⟹ gone
at the warning-time startup, since a reaper throw removes nothing and an external deletion is
ruled out by comparing the worktrees-dir mtime against the commander's start time); and (ii) on
the reaper's owned release path, only PRE-`7388ad7` code throws `validateManagedIdentity`'s
message on a *gone* worktree — post-`7388ad7` skips it via `if (present)` (workspace-service.ts
:754) and `abandon`/`rescueAbandon` tolerate absence. So `identity-error-logged` +
`worktree-gone-now` ⟹ pre-`7388ad7` ran ⟹ **H1**, regardless of which bundle is on disk now;
`worktree-present-on-a-foreign-branch` ⟹ the guard fired correctly under EITHER code version ⟹
**H2b** (follow-up). Per-guid, so a MIXED result is allowed.

Two disciplines guard the inference:
- **Exact message.** Only `validateManagedIdentity`'s exact string
  (`Managed worktree Git identity does not match durable assignment; reconciliation must preserve
  it`) enters the gone⟹H1 inference — post-`7388ad7` code throws OTHER messages on gone worktrees
  through the same reaper wrapper (tombstone/durable-proof/eligibility guards). Grep the commander
  log for `release failed`, record each warned guid and its exact exception text, and only
  `:179`-text warnings count.
- **Conjunction.** "present" is the CODE's test: `existsSync(worktree_path)` AND the path is in
  `git worktree list --porcelain`; "foreign branch" = that conjunction holds AND the porcelain
  `branch` ≠ `refs/heads/ironclaude/<guid>` (detached/null counts as foreign). Test both, not
  `ls` alone.

Evidence steps: (1) PRIMARY — for every leaked row, classify gone vs present-on-foreign-branch
by the conjunction above; (2) the leaked-row DB dump (`lifecycle_status`/`recovery_ref`/
`worktree_path`); (3) CORROBORATION — identify the bundle the commander resolves by
version-match (`_select_discovered_root`: the claude/codex cache root whose `plugin.json`
base_version == the commander version), grep it for the post-`7388ad7` marker
`reapLeakedAssignment`, and capture temporal signals (bundle `cli.js` mtime vs the commander's
process start time; whether `:179` warnings recur on the maintenance cadence after the deploy).
Corroboration identifies what runs now/next-sweep for the remediation recommendation; it cannot
alone prove what ran at warning time.

The findings note states which hypothesis holds (or a mixed per-guid split), with the command
output as evidence, and the recommended remediation (H1 → update the claude-cache
workspace-manager to post-`7388ad7` and restart the commander, after which the gone-tolerant
reaper reaps the rows, anchoring any branch tip first; H2b → a scoped follow-up loop). No
remediation is performed in this loop.

## Components

- `docs/plans/2026-08-21-reaper-owned-gone-findings.md` — the sole deliverable (a findings
  note). Created in the execute stage; `git add -f` (docs/ is gitignored).
- No product-code files. No tests (this is a read-only diagnosis; the "expected" outputs are
  measured live, not asserted).

## Data Flow

commander startup → reaper sweep → `_release_leaked_assignment` (owned branch) → workspace-
manager CLI `cleanup`/`abandon` → (running build) `validateManagedIdentity` throws on the
already-removed worktree → WARNING logged, retried next boot. The investigation reads: the
commander's CLI path + its `if (present)` marker; the workspace-manager DB rows; on-disk
worktree presence — and writes the findings note.

## Error Handling

- All commands are read-only: `grep`, `sqlite3 … SELECT`, `ls`, `git worktree list`. No writes,
  no `git worktree prune`, no DB mutation, no worktree/branch/ref changes.
- If the workspace-manager DB path cannot be resolved, the note records the blocker and the
  exact resolution attempted, rather than guessing.
- Absence must be provable: list row values and paths, never counts; do not `2>/dev/null` an
  evidence command; distinguish an empty result from a failed command.
- The game repo is a **different** repository than ironclaude; the investigation only reads it.

## Testing Strategy

No unit tests — the deliverable is a findings note, and every `expected:` is measured live in
the execute stage (paste actual output). Verification is that the note answers all three
evidence questions with command output and states H1 vs H2b decisively (or records exactly why
a question could not be answered).

## Implementation Notes

- **No code fix in this loop.** Remediation is chosen from the findings: H1 → the commander
  runs the current build and is restarted (operational; the reaper then clears the 47 rows via
  the gone-tolerant path — never-lose-work still anchors any branch tip first); H2b → a scoped
  follow-up loop.
- **Open question for the follow-up (if H2b or a residual gap):** the pre-Loop-3 stance
  (episodic memory, 08-07…08-16) was that owned-but-gone assignments are *intentionally* not
  auto-reaped (operator-driven recovery). Loop 3's reap path changed the calculus by anchoring
  work before tombstoning. Whether the reaper's owned branch should route worktree-absent rows
  through the same anchor-then-reap path is a design decision for that loop, not this one.
- Human commits, no push. Local only.
