# Orphan-Consent-Cleanup Final-Review Fixes Design

> **Created:** 2026-09-18
> **Status:** Design Complete
> **Type:** Second corrective loop — fixes the 2 Important findings + 2 cheap observations from the final tier-up (Fable) adversarial END review of the full combined staged diff (consent-cleanup feature + first corrective loop).

## Summary

The consent-gated orphan-cleanup effort (7-task feature, all Grade A) and its first
corrective loop (4 Important consent-safety fixes) passed every gate. A final
tier-up Fable adversarial review of the FULL combined staged diff (20 files) found
**no Critical**, verified the whole guard chain correct, and reported **2 Important
findings** plus non-blocking observations. Both Important findings were
independently verified against the current staged code before this loop. This loop
fixes exactly those two, plus two cheap observations. No new features; no version
bump; no DB migration. Parent designs live at
`commander/docs/plans/2026-09-16-orphan-consent-cleanup-design.md` and
`commander/docs/plans/2026-09-16-orphan-consent-endreview-fixes-design.md`.

> **Docs location note:** this corrective loop's docs live at the repo-root
> `docs/plans/` (this session's cwd is the repo root, so the professional-mode-guard
> docs carve-out and the MCP path resolution both key off repo-root). The parent
> effort's docs are under `commander/docs/plans/`; the location differs only because
> the earlier loops ran with cwd=commander/. `design_file`/`requirements_file` in the
> plan JSON are therefore repo-root-relative (`docs/plans/...`), and `allowed_files`
> are repo-root-relative (`commander/...`, `worker/...`).

The I1 bounding approach was investigated by a dedicated Fable subagent (at the
operator's request) which produced the recommended design below (Approach C).

## Findings and Fixes

### I1 — Unbounded per-sweep patch-id scan degrades the daemon [operational, material on this box]

`patchIdAggregateTellMerged` (worker/mcp-servers/workspace-manager/src/git.ts:599-613)
runs `git log --max-parents=1 <mergeBase>..<targetRef>` with NO cap and then 2
`spawnSync` per commit (`patchId`, git.ts:554-557 = `git diff` + `git patch-id`).
`contentMergedInto` (git.ts:669-674) runs three tells `cherry → patchIdAggregate →
reverseApply`; `classifyPreservedOrphan` (workspace-service.ts:1322-1340) calls it
TWICE (local target, then `refs/remotes/origin/<target>`) for every **non-dirty,
non-ancestor** orphan, on every daemon maintenance sweep (main.py step 7, hourly,
synchronous).

For a **genuinely-unmerged** orphan (the operator's actual ~9), `cherry` and
`reverseApply` both correctly fail, so `contentMergedInto` ALWAYS falls through to
the unbounded patch-id scan — `18 + 4M` spawns per orphan (M = commits on target
since merge-base). This classification path is NEW in this diff (released v1.1.11
used strict `git merge-base --is-ancestor`). On an active `main` with M≈100, 9
orphans × 2 refs ≈ 3.8k synchronous git spawns per tick, hitting the 60s
`subprocess.run` timeout — which itself makes the reap fail for that repo and the
heartbeat under-report to 0 (symptom eliminated by this fix).

**Fix (Fable Approach C — bounded, detection-preserving):**
1. **Reorder** `contentMergedInto` tells to `cherry → reverseApply →
   patchIdAggregate` (cheapest/most-likely first; order only affects merged-case
   latency — all three run in the unmerged steady state anyway). Optionally compute
   `merge-base` once in `contentMergedInto` and pass it to the tells (saves a spawn;
   a failed merge-base short-circuits to false).
2. **Rewrite `patchIdAggregateTellMerged`** to a fixed spawn count independent of
   history depth:
   - `aggregateId = patchId(mergeBase, tip)`; `null` → return false (unchanged).
   - Compute the branch's touched paths `P`:
     `git diff --name-only --no-renames -z <mergeBase> <tip>` (NUL-split;
     `--no-renames` so a rename contributes both old and new path).
   - One walk restricted to those paths:
     `git --literal-pathspecs log --no-merges --format=commit %H -p
     <mergeBase>..<targetRef> -- <P...>` (`--literal-pathspecs` is MANDATORY so a
     filename like `weird[1].txt` is not treated as a glob; `--no-merges` ≡
     `--max-parents=1`; root commits now diff against the empty tree instead of
     throwing). Use `maxBuffer` (e.g. 64 MiB); overflow → `error` → `tryTell` →
     false (fail-safe).
   - Pipe that into ONE `git patch-id --stable` (reads the multi-commit `log -p`
     stream, splits on `commit <sha>` lines, emits one `<patch-id> <sha>` per
     commit).
   - Return true iff any emitted patch-id equals `aggregateId`.
   - Add `--no-ext-diff` to BOTH the `patchId` `git diff` (git.ts:554) and the walk,
     so both sides generate diffs identically (patch-ids must compare like-for-like;
     `git log -p` never runs external diff drivers, `git diff` does by default).
   - Result: 4 spawns, fixed. Semantics are a **strict superset** of today: a
     full-diff match implies the path-restricted match; the only deliberate widening
     is a target commit that landed the branch's exact hunks on `P` AND touched
     other files (e.g. a release mega-squash) — a true "content reached target"
     claim, fail-safe in the required direction, never firing for genuinely-unmerged
     content. (A zero-semantic-change two-stage `--name-only` exact-set variant with
     the same spawn bound is the fallback if the plan reviewer insists.)
3. **Gate the origin double-scan** in `classifyPreservedOrphan` (workspace-service.ts
   :1331-1338): run the origin pass only when `originRef` resolves AND
   `!isAncestor(primary, originRef, target)`. `merged-on-origin` is definitionally
   only possible when origin is AHEAD of local target; when origin is at/behind,
   skipping yields `genuinely-unmerged` instead of a mislabel (fail-safe) and halves
   steady-state cost in the common post-push state.

**Residual genuinely-unmerged cost:** ~11-12 spawns/orphan/sweep, independent of M.
**Detection given up:** none relative to the current loop; two fail-safe narrowings
to document (walk output > maxBuffer, or a path set exceeding ARG_MAX → that tell
reports not-merged).

**Rejected:** (A) reverse-apply-only — loses genuinely squash-merged orphans as they
age past a later same-file edit (the original nag bug returns; the operator's repos
re-squash CHANGELOG/README on every release). (B) `--max-count=N` — a squash commit
ages without bound while an orphan lingers, so any age cap has an old-squash blind
spot, and it still pays `2·min(N,M)` spawns/orphan/sweep. (memoize) — needs to
persist the target-tip classified against (schema change, out of scope) and
recomputes whenever `main` advances (frequently).

### I2 — reap `--force` bound to the sweep-refreshed category, not operator consent [consent-safety]

`resolveOneOrphan` reap path (workspace-service.ts:1176-1188) sets `force = true`
when the worktree is currently dirty AND `row.category === 'dirty'`. But
`row.category` is refreshed by the daemon sweep (`upsertOrphanSurface`), not pinned
to what the operator was shown. The first corrective loop closed tip-revalidation
(refused-changed on tip change) and re-surfacing on category change (main.py keys
`{id:(tip,category)}`), but NOT category revalidation at resolution time. Sequence:
surfaced `genuinely-unmerged` (post A, clean) → uncommitted work appears in the
assignment-less orphan worktree (tip unchanged) → sweep re-classifies `dirty`,
re-posts (post B) → operator replies `reap <id>` to the STALE post A → tip check
passes (tip unchanged), `row.category` is now `dirty` → `--force` discards
uncommitted work the post-A consent never covered.

**Fix:** revalidate the operator-seen category at resolution time, symmetric with
tip revalidation.
- Add optional `category?: PreservedOrphan['category']` to `OrphanResolutionRequest`
  (workspace-service.ts:167-182).
- Thread it through `cli.ts requiredResolutions` (:122-159) mirroring the existing
  `integration_target → integrationTarget` handling: read `record.category`,
  validate it is one of the known categories when present, output `category`.
- `WorkspaceClient.resolve_orphan` already passes `resolutions` verbatim (generic
  passthrough, proven at test_workspace_client.py) — no change.
- `resolveOneOrphan` reap path: the `--force` branch fires ONLY when the operator's
  consent explicitly covered a dirty worktree — i.e. `resolution.category ===
  'dirty'` AND the worktree is currently dirty AND `row.category === 'dirty'`. If the
  operator's consented category is absent or not `dirty`, refuse (`refused-changed`)
  rather than force-discard. Non-force paths (clean worktree) are unaffected and do
  not require `category`.
- Document in the orchestrator MCP `resolve_orphan` docstrings (:4066/:7428
  resolutions descriptions) and the Brain rule that each resolution SHOULD carry the
  surfaced `category` so a consented reap can only `--force` when consent covered a
  dirty worktree.

### obs #1 — CAS failure mislabeled `refused-changed` [Boy-Scout, cheap]

`mergeOrphanThenReap` (workspace-service.ts:1283) returns `refused-changed` when the
`update-ref` CAS fails because the TARGET moved concurrently. README/Brain-rule
define `refused-changed` as "branch changed since surfacing — re-surface it", but
here the orphan is unchanged and a plain retry would succeed (merge-tree recomputes
against the advanced target). **Fix:** return a distinct `target-moved` outcome
(`outcome` is a plain string — no type change) and add it to the outcome contract in
orchestrator_mcp.py docstrings (:4076/:7435), workflow.md, README.md, with guidance
"target advanced concurrently — retry the same resolution."

### obs #4 — surface header count disagrees with heartbeat count [Boy-Scout, cheap]

`format_orphaned_orphans` (notifications.py:218) headers `len(details)` which
INCLUDES squash-merged, while the heartbeat count (main.py:1940-1942) EXCLUDES
squash-merged — two disagreeing "needs review" numbers in the same Slack channel.
**Fix:** the surface header counts the need-review set (excluding squash-merged),
matching the heartbeat. All entries stay LISTED (squash-merged included, so still
reapable by id) — only the header number/wording changes, no behavior change.

## Out of Scope (recorded, not fixed)

- obs #2 (orphan_surface rows never deleted when a guid vanishes externally →
  `refused-changed` forever) and obs #3/#5 — deferred; scope held.
- The 60s-timeout heartbeat under-report is a *symptom* of I1 and is eliminated once
  the scan is bounded; no separate fix.
- The classifier stays HEAD-relative for the `dirty`/ancestry path (existing reaper
  behavior); only cost and the origin gate change.

## Testing Strategy

TDD per fix. Every guard names the broken state it catches.
- **I1 bound proof (git-content-merged.test.ts):** a genuinely-unmerged branch,
  target advanced K commits (half touching the branch's files); run at K=5 and K=60
  and assert the `spawnSync` call count for `contentMergedInto` is EQUAL and ≤ ~12
  (restoring the per-commit loop makes the delta 2×55; a `--max-count` reintroduction
  breaks equality). Use a pass-through `spawnSync` counter via `vi.mock`.
- **I1 detection preserved:** squash-then-later-overlapping-edit → still `true` (only
  the patch-id tell can fire; proves it was not dropped). Plain squash → `true`.
  Genuinely-unmerged with hot-path target edits → `false` (fail-safe). Partial
  landing (only one of two files) → `false` (bounds the widening). `weird[1].txt`
  literal-pathspec case → `true`. Empty-diff tip → `false`, no walk. Keep the
  existing no-object-writes/refs/status assertion.
- **I1 origin gate (workspace-service.test.ts):** the currently-UNTESTED
  `merged-on-origin` positive (origin ahead of local) → that category; origin
  at/behind → gate skips (assert spawn count == no-origin-ref run + 1) → still
  `genuinely-unmerged`.
- **I2 (workspace-service.test.ts / cli.test.ts):** surfaced `genuinely-unmerged`,
  worktree goes dirty, `reap` with `category:'genuinely-unmerged'` → `refused-changed`,
  NO force, worktree+branch present. `reap` with `category:'dirty'` on a genuinely
  dirty consented row → reaped with force. `reap` with no `category` on a dirty
  worktree → `refused-changed`. cli.test.ts: `category:'dirty'` maps to the service
  call; invalid category rejected.
- **obs #1:** merge-then-reap where the target advances between `rev-parse` and CAS →
  `target-moved` (not `refused-changed`).
- **obs #4 (test_notifications.py):** details containing a squash-merged entry → the
  header number equals the non-squash-merged count and matches what the heartbeat
  would report; squash-merged entry still appears as a bullet.

Suites: git-content-merged.test.ts, workspace-service.test.ts, cli.test.ts,
tool-dispatch.test.ts (TS); test_orchestrator_mcp.py, test_notifications.py,
test_worktree_reaper.py, test_workspace_client.py (Python). Full commander +
workspace-manager suites green.

## Implementation Notes

- Files (all touched by the parent effort), repo-root-relative:
  worker/mcp-servers/workspace-manager/src/{git.ts, workspace-service.ts, cli.ts} +
  their `__tests__`; commander/src/ironclaude/{orchestrator_mcp.py, notifications.py,
  main.py} + tests; commander/src/brain/rules/workflow.md; README.md.
  (`commander/src/ironclaude/workspace_client.py` needs no change.)
- Ground all git-command behavior against live git during execution (patch-id stream
  parsing, `--literal-pathspecs`, `--no-ext-diff`, empty-tree root diff) — do not
  assume.
- No new DB migration; `orphan_surface` schema and `muted_tip` semantics unchanged.
- Commit/push, version decision, and Commander restart remain operator-gated.
