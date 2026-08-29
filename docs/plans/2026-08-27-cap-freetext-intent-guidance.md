# QW-CAP — Free-Text Git-Verb Intent Guidance — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** On the free-text git-verb path, steer the agent to the rendered `/<verb>` form and make the
missing-intent refusal self-explaining — via the two surfaces the free-text path actually reads (tool
description + refusal string), with the intent-mint gate byte-unchanged.

**Requirements:** docs/plans/2026-08-27-cap-freetext-intent-guidance-requirements.md

**Design:** docs/plans/2026-08-27-cap-freetext-intent-guidance-design.md

**Architecture:** Enrich the shared MCP tool DESCRIPTION for commit/commit_and_push/push (the dominant
surface — always resident at the tool-selection decision point) and APPEND a remedy to the missing-intent
refusal STRING (the deterministic backstop). Guidance/UX only; `state-activator.sh` (the mint gate) is
NOT edited (excluded from allowed_files — HC1). No free-text promotion; no confirmation-click; no banner.

**Tech Stack:** TypeScript, vitest.

**Execution invariants:** absolute paths; `docs/` gitignored (`git add -f`); vitest `--testTimeout=30000`;
each task's full-suite run is its falsifier; distinctive `-t` names; baseline 297 green; Bash cwd persists
— `cd` to the workspace-manager dir once for `npx tsc`; `git -C /Users/roberthyatt/Code/ironclaude` for
staging; plan/doc filenames avoid "push"/"commit" (execution_complete guard — HC4).

---

## Task 1: enrich the tool description + append the refusal remedy

**Files:**
- Modify: `worker/mcp-servers/workspace-manager/src/index.ts:122-137` (shared tool-description template)
- Modify: `worker/mcp-servers/workspace-manager/src/git-authority.ts:556, :612` (refusal string, both sites)
- Test: `worker/mcp-servers/workspace-manager/src/__tests__/tool-dispatch.test.ts` (tool description assertion)
- Test: `worker/mcp-servers/workspace-manager/src/__tests__/git-authority.test.ts` (refusal-message assertion)

`state-activator.sh` is deliberately ABSENT from this list — the file guard enforces HC1 (mint gate
untouched).

**Step 1 — RED (two assertions):**
- In `tool-dispatch.test.ts` add `it('the commit_and_push tool description tells the agent free-text does not carry intent and to render the form', …)`: `const def = publicToolDefinitions.find((t) => t.name === 'commit_and_push'); expect(def?.description).toContain('free-text prose does not carry it'); expect(def?.description).toContain('reply with the /commit-and-push form');`. (`publicToolDefinitions` is already imported at :10.)
- In `git-authority.test.ts` add `it('the missing-intent refusal names the remedy (form to invoke) while preserving the matching-human-intent prefix', …)`: seed a missing-intent verify (mirror an existing `.toThrow('matching human intent')` test in the file), and assert the thrown message `.toContain('matching human intent')` AND `.toContain('does not mint intent')`. (Ground the exact seed by reading a current missing-intent test in git-authority.test.ts.)

Run:
```bash
npm --prefix /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager test -- --testTimeout=30000 -t "does not carry intent"
```
(and `-t "names the remedy"`) Expected: FAIL — the current description is the thin "Consume exact human intent…" and the refusal has no remedy clause.

**Step 2 — GREEN (two edits):**
- `index.ts:124` — replace the description template. New value (`v = name.replaceAll('_', '-')`):
  ```ts
  description: `Consume exact human intent and perform direct ${name.replaceAll('_', '-')} authority. Intent exists ONLY when the operator typed /${name.replaceAll('_', '-')} as their literal prompt this turn; free-text prose does not carry it. On a prose request, reply with the /${name.replaceAll('_', '-')} form for the operator to type — do NOT call this tool (it refuses without intent). After the verb completes, carry forward any remaining instruction from the prose.`,
  ```
- `git-authority.ts:556 and :612` — APPEND to both refusal strings (identical edit):
  ```ts
  throw new Error('Direct Git operation requires a matching human intent — the operator must invoke the rendered git form (/commit, /commit-and-push, or /push) as their literal prompt; free-text prose does not mint intent');
  ```

**Step 3 — run suite (the 18 existing substring assertions must survive):**
```bash
npm --prefix /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager test -- --testTimeout=30000
```
Expected: all pass (baseline 297 + 2 new; the 15 git-authority.test.ts + 3 workspace-service.test.ts
`matching human intent` substring assertions stay green because the prefix is preserved).

**Step 4 — typecheck + verify mint gate untouched + stage:**
```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager && npx tsc --noEmit
git -C /Users/roberthyatt/Code/ironclaude status --porcelain worker/hooks/state-activator.sh
git -C /Users/roberthyatt/Code/ironclaude add worker/mcp-servers/workspace-manager/src/index.ts worker/mcp-servers/workspace-manager/src/git-authority.ts worker/mcp-servers/workspace-manager/src/__tests__/tool-dispatch.test.ts worker/mcp-servers/workspace-manager/src/__tests__/git-authority.test.ts
```
Expected: tsc exit 0; the `state-activator.sh` status line is EMPTY (unchanged — HC1); the four files staged.
