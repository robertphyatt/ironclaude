# Reaper Integration-Target Fix (#1) Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** Make `reapAmbiguousOrphans` judge an orphan branch's merged/unmerged status against the repo's canonical default branch (`refs/remotes/origin/HEAD`) instead of the primary checkout's currently-checked-out branch.

**Requirements:** docs/plans/2026-09-25-reaper-defects-fix-requirements.md

**Scope note:** This loop is **#1 only**. Defect #4 (dead-owner worktrees) was withdrawn: adversarial review showed the reconcile-and-mark-killed approach would violate the "seam owns all completion" invariant and force-kill operator-held finalization-recovery rows, and that the real defect is a *silently-failing terminal `abandon`* — a separate observability fix, planned in its own loop.

**Architecture:** A one-line target swap in `reapAmbiguousOrphans` (`workspace-service.ts:990`) to the existing `canonicalDefaultBranchRef` (git.ts:209, from `refs/remotes/origin/HEAD`, fallback `main`, never throws) — the same target `mergeOrphanThenReap` (workspace-service.ts:~1357) already uses. `canonicalDefaultBranchRef` is ALREADY imported (`workspace-service.ts:25`) — do NOT touch the import block.

**Tech Stack:** TypeScript (workspace-manager) + vitest.

**Execution invariants:** Bash cwd is `commander/`; use `git -C /Users/roberthyatt/Code/ironclaude` + absolute paths. `docs/` gitignored (`-f`). The vitest may print a benign `onTaskUpdate` RPC-timeout line — judge by "0 failed".

---

## Task 1: #1 — reaper judges against the canonical default branch (TS)

**Files:**
- Modify: `worker/mcp-servers/workspace-manager/src/workspace-service.ts:990` (line 990 ONLY — the import at :25 already has `canonicalDefaultBranchRef`)
- Test: `worker/mcp-servers/workspace-manager/src/__tests__/workspace-service.test.ts`

**Step 1 (RED): add two falsifiable tests + fix the one existing test that the swap flips.**
- **New PRESERVE test:** temp repo, `refs/remotes/origin/HEAD → refs/remotes/origin/main` set, primary checked out on a FEATURE branch that diverged from main; an orphan (`ironclaude/<guid>`) whose tip is reachable from the feature branch but NOT `origin/main`, clean, tip older than TTL, no assignments row, unprotected. Assert it is **PRESERVED** (guid in `preservedUnmerged`/`preservedDetail`, not `reaped`).
- **New REAP test:** same shape, but the orphan tip is a commit that IS an ancestor of `origin/main` but NOT of the feature branch (e.g. a main commit the feature branch predates). Assert it is **REAPED** (guid in `reaped`). This covers AC#1's reap direction and is falsifiable against the old target.
- **Fix existing test `workspace-service.test.ts:2141-2165`** ("classifies a squash-merge into a non-main primary branch using the reaper target, not a hardcoded refs/heads/main"): it inits `--initial-branch=trunk` with NO `origin/HEAD`, so post-swap `canonicalDefaultBranchRef` falls back to `refs/heads/main` (absent) and the test fails. Add `git update-ref refs/remotes/origin/trunk <trunk-sha>` + `git symbolic-ref refs/remotes/origin/HEAD refs/remotes/origin/trunk` to its setup (mirroring the pattern at ~`:2877`), so `canonicalDefaultBranchRef` returns `refs/heads/trunk` and the existing `evidence`/`category` assertions hold and now exercise the canonical target.
```
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager && npx vitest run src/__tests__/workspace-service.test.ts
```
Expected: RED — the two NEW tests fail against the current `integrationTargetRef(primaryBranch(...))` target (PRESERVE: orphan wrongly reaped; REAP: orphan wrongly preserved). The edited `:2141` test now sets origin/HEAD but still passes pre-swap (pre-swap target = trunk via primaryBranch, evidence=refs/heads/trunk — unchanged assertion), so it is not itself a RED signal — it is a compatibility fix so it survives Step 2.

**Step 2 (GREEN): swap the target at `workspace-service.ts:990` ONLY.** Change
`const target = integrationTargetRef(primaryBranch(repository.primaryCheckoutPath));`
to
`const target = canonicalDefaultBranchRef(repository.primaryCheckoutPath);`
Do NOT modify the import block — `canonicalDefaultBranchRef` is already imported at `:25`. `canonicalDefaultBranchRef` returns `refs/heads/<name>` (same shape as the replaced expression), so `isAncestor(tip, target)` (`:1045/:1057`), `rev-list --count target..tip` (`:1046`), and `classifyPreservedOrphan` (`:1449`) are unchanged. (Behavior note: for a repo with no `origin/HEAD` and no `main`, the reaper now preserves-all rather than judging against the primary's branch — fail-safe, not "strictly more robust".)

**Step 3 (GREEN verify): rerun the test.**
```
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager && npx vitest run src/__tests__/workspace-service.test.ts
```
Expected: the two new tests pass; the edited `:2141` test passes; all other `reapAmbiguousOrphans` / classify tests pass; 0 failed.

**Step 4 (falsifiable): confirm the old expression is gone.**
```
rg -n "integrationTargetRef\(primaryBranch" /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager/src/workspace-service.ts ; echo "rg-exit=$?"
```
Expected: no match lines, then `rg-exit=1`.

**Step 5: stage.**
```
git -C /Users/roberthyatt/Code/ironclaude add worker/mcp-servers/workspace-manager/src/workspace-service.ts worker/mcp-servers/workspace-manager/src/__tests__/workspace-service.test.ts
```
Expected: staged.

---

## Task 2: dist rebuild + full-suite verification

**Files:**
- `worker/mcp-servers/workspace-manager/dist/cli.js`
- `worker/mcp-servers/workspace-manager/dist/index.js`
- `worker/mcp-servers/workspace-manager/dist/hook-intent.js`

**Depends on:** Task 1. **No tests required: build + full-suite verification.**

**Step 1: rebuild the workspace-manager bundle.**
```
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager && npm run build
```
Expected: tsc + bundle succeed, no error.

**Step 2: full workspace-manager vitest.**
```
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager && npx vitest run
```
Expected: `0 failed`.

**Step 3: full commander pytest** (no commander code changed, run for safety).
```
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest -q
```
Expected: `0 failed`.

**Step 4: stage the rebuilt dist.**
```
git -C /Users/roberthyatt/Code/ironclaude add worker/mcp-servers/workspace-manager/dist/cli.js worker/mcp-servers/workspace-manager/dist/index.js worker/mcp-servers/workspace-manager/dist/hook-intent.js
```
Expected: staged.
