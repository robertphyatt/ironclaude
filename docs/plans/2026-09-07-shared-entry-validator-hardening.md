# Shared-Entry Validator Hardening Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** Make workspace-manager `git.ts` `isSafeSharedEntry` reject entries with a newline, carriage return, or leading/trailing whitespace, and add a bounding test, so `addSharedResourceEntries` cannot persist a smuggled multi-line or padded config entry.

**Requirements:** `docs/plans/2026-09-07-shared-entry-validator-hardening-design.md`

**Architecture:** Two pure predicate additions to the existing `isSafeSharedEntry` (the single validator gating both the config write and the read-time relink). No change to callers or write format. TDD in `git.test.ts`; rebuild the bundle.

**Tech Stack:** TypeScript (workspace-manager) + vitest.

## Execution invariants (reviewer checks commands against these)

- Bash cwd is not stable; each command is self-contained. TS build/test: `cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager && ...` (test=`npm test`; build=`npm run build` → `dist/{index,cli,hook-intent}.js`). Stage with `git -C /Users/roberthyatt/Code/ironclaude add …`; `dist/` is gitignored but the three bundles are tracked (stage by exact path with `-f`).
- `isSafeSharedEntry` is module-private in `git.ts`; it is exercised through the exported `addSharedResourceEntries` (write gate) and `linkSharedResources` (read-time gate), and compiled into the three `dist` bundles — so the fix needs a rebuild to reach runtime.
- Verified current source: `isSafeSharedEntry` at `git.ts:236-245`; the reject test "rejects unsafe entries and never writes them" at `git.test.ts:181-188` with `bad = ['../escape', '/abs', 'a*', 'trailing/', '!neg', '#comment']`; the clean-accept test "appends valid entries and reports them as added" at `git.test.ts:157-163` (the over-rejection guard).

---

## Task 1: Harden `isSafeSharedEntry` (reject control chars + surrounding whitespace)

**Files:**
- Modify: `worker/mcp-servers/workspace-manager/src/git.ts`
- Test: `worker/mcp-servers/workspace-manager/src/__tests__/git.test.ts`
- Modify: `worker/mcp-servers/workspace-manager/dist/index.js`
- Modify: `worker/mcp-servers/workspace-manager/dist/cli.js`
- Modify: `worker/mcp-servers/workspace-manager/dist/hook-intent.js`

**Step 1 (RED):** In `git.test.ts`, extend the reject case (`git.test.ts:181-188`) so `bad` includes control-char and surrounding-whitespace members: change the `bad` array to `['../escape', '/abs', 'a*', 'trailing/', '!neg', '#comment', 'ok\nescape', 'a\rb', ' models', 'models ']`. The existing assertions (`result.rejected` equals `bad`; `result.added` equals `[]`; `readSharedResourceConfig(id)` equals `[]`) then bound the fix. The existing "appends valid entries" test (`git.test.ts:157`) with clean entries `['data/models', 'caches/vision']` is the positive control that the new clauses do not over-reject.

**Step 2: Run RED.**
```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager && npm test -- git.test
```
Expected: FAIL — the current validator admits `'ok\nescape'`, `'a\rb'`, `' models'`, `'models '`, so they land in `added` (not `rejected`) and `'ok\nescape'` writes two physical lines, so `readSharedResourceConfig` is not `[]` and `result.rejected` does not equal `bad`.

**Step 3 (GREEN):** In `git.ts` `isSafeSharedEntry` (before the final `return true;` at `git.ts:244`), add two clauses:
```typescript
  if (entry !== entry.trim()) return false;
  if (/[\x00-\x1f]/.test(entry)) return false;
```
The first rejects leading/trailing ASCII whitespace; the second rejects any ASCII control character (covers interior `\n`, `\r`, tab). Both are pure; no other change.

**Step 4: Run GREEN.**
```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager && npm test -- git.test
```
Expected: passed.

**Step 5: Build bundle.**
```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager && npm run build
```
Expected: tsc clean + 3 dist bundles written; no errors.

**Step 6: Full TS suite.**
```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager && npm test
```
Expected: 0 failed (a `[vitest-worker] Timeout calling "onTaskUpdate"` reporter line may appear under load and is not a test failure — only a nonzero failed count is).

**Step 7: Stage.**
```bash
git -C /Users/roberthyatt/Code/ironclaude add worker/mcp-servers/workspace-manager/src/git.ts worker/mcp-servers/workspace-manager/src/__tests__/git.test.ts
git -C /Users/roberthyatt/Code/ironclaude add -f worker/mcp-servers/workspace-manager/dist/index.js worker/mcp-servers/workspace-manager/dist/cli.js worker/mcp-servers/workspace-manager/dist/hook-intent.js
```
Expected: staged.

---

## Notes

- Lands as staged changes on top of the (staged, uncommitted) worktree-shared-resources self-serve change; both land together for the operator's single commit. No push.
- Project-agnostic; no roleplaying-agents/pf2e specifics.
