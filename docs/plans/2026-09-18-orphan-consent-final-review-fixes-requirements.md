# Orphan-Consent-Cleanup Final-Review Fixes — Requirements

> **Created:** 2026-09-18
> **Status:** Operator-approved (final end-review findings verified; I1 approach delegated to and decided by a Fable investigation)

Fix the 2 Important findings + 2 cheap observations from the final tier-up (Fable)
adversarial END review of the full combined staged diff. Design:
`docs/plans/2026-09-18-orphan-consent-final-review-fixes-design.md`. Scope held:
exactly these fixes, in files the parent effort already touched; no new features; no
version bump; no DB migration.

## R1 — I1: bound the per-sweep squash-detection scan, preserve detection
`contentMergedInto`'s `patchIdAggregateTellMerged` must not scan target history
per-commit. Replace the per-commit `patchId` loop with a single pathspec-restricted
`git log -p` (over the branch's touched paths) piped into one `git patch-id
--stable`, compared to the branch's aggregate patch-id. Reorder tells to
`cherry → reverseApply → patchIdAggregate`. Add `--no-ext-diff` to both diff sources.
Gate the origin double-scan in `classifyPreservedOrphan` on
`!isAncestor(originRef, target)`. Result: fixed spawn count independent of target
depth; detection a strict superset of today's; fail-safe preserved (never
over-claims merged). No `--max-count` cap (old-squash blind spot).

## R2 — I2: revalidate the operator-seen category before a `--force` reap
The reap `--force` path must fire only when the operator's consent explicitly
covered a dirty worktree. Thread an optional `category` through the resolution
(cli.ts `requiredResolutions`, `OrphanResolutionRequest`, MCP tool, Brain rule) and,
in `resolveOneOrphan`, force-remove only when `resolution.category === 'dirty'` AND
the worktree is currently dirty AND `row.category === 'dirty'`; otherwise
`refused-changed`. Absent/ non-dirty consented category ⇒ never force. Non-force
(clean-worktree) reaps are unaffected and need no `category`.

## R3 — obs #1: distinct `target-moved` outcome for a concurrent CAS failure
`mergeOrphanThenReap`'s `update-ref` CAS failure (target advanced concurrently) must
return a distinct `target-moved` outcome, not `refused-changed`, with guidance that a
plain retry will succeed. Add it to the outcome contract (orchestrator_mcp.py
docstrings, workflow.md, README.md) and tests.

## R4 — obs #4: surface header count consistent with heartbeat
`format_orphaned_orphans`'s header must count the same need-review set the heartbeat
does (exclude squash-merged). All entries stay listed (squash-merged included, still
reapable) — only the header number/wording changes.

## R5 — Safety + verification
TDD; every guard names the broken state it catches — including an I1 bound-proof that
asserts EQUAL `spawnSync` counts at target depth K=5 vs K=60, and the
currently-untested `merged-on-origin` positive case. No new DB migration. No mutation
outside the resolve/merge/classification paths' existing safety gates. Full commander
+ workspace-manager suites green. Commit/push/version/restart remain operator-gated.
