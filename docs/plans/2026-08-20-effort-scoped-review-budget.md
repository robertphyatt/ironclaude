# Effort-Scoped Review Budget Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** Bind the one-blind-plan-review budget to the operator-initiated effort (not the auto `plan_lineage`), so a retreat→re-plan cannot mint a fresh blind review, while a changed plan proceeds via advisor-remediation and a genuine new effort still earns its one review.

**Requirements:** docs/plans/2026-08-20-effort-scoped-review-budget-requirements.md

**Architecture:** Add a session flag `inherit_review`, set on design-phase (re-)entry (target `brainstorming` OR `debugging`) from the FROM-stage (idle/execution_complete → 0 new effort; RETREAT_SOURCES → 1 inherit; brainstorming/debugging/unknown → unchanged), at BOTH set-sites: `executeWorkflowTransition` (MCP tools) and `skill-state-bridge.sh` (the dominant skill-invocation hook path). Gate the `plan_lineage` increment on it. Extend `advisor-remediated` to any canonical blind base so a post-retreat change re-binds at the new hash without a fresh blind review. Update the executing-plans skill text.

**Tech Stack:** TypeScript, better-sqlite3, vitest; bash (session-init.sh, skill-state-bridge.sh, hook tests); Markdown (skill).

**Execution invariants (author + reviewer check against these):** shell state does not persist between steps (literal absolute paths); Bash cwd is `commander/` (use `git -C <root>` / absolute paths); quote globs — zsh `nomatch` aborts an unquoted zero-match glob (also `rm -f <glob>` with no match); foreground `sleep` blocked; `docs/` gitignored (`git add -f`); an empty result must be distinguishable from a failed command; commander pytest runs as explicit-file foreground batches with `-m "not destructive"` (a single run is ~17 min and RAM-sensitive); the `professional-mode-guard` treats a `|` inside a quoted grep pattern as a shell pipe — use single-term greps.

---

## Task 1: Session schema — `inherit_review` column

**Files:**
- Modify: `worker/mcp-servers/state-manager/src/db.ts` (the `CREATE TABLE ... sessions` at :242-259 — after `plan_lineage` :253 — and the sessions `expectedColumns` migration array :161-166)
- Modify: `worker/mcp-servers/state-manager/src/types.ts` (the `Session` interface only — after `plan_lineage: number;` at :59; do NOT touch `TierUpReviewEntry` at :36-44)
- Modify: `worker/hooks/session-init.sh` (the ONE sessions CREATE — after `plan_lineage` at :64; do NOT touch the `tier_up_reviews` CREATE at :138-141)
- Test: `worker/mcp-servers/state-manager/src/__tests__/inherit-review-migration.test.ts` (new)

**Step 1: Write the migration test (RED).** Create `inherit-review-migration.test.ts`. Seed a `sessions` table with `terminal_session` AND `professional_mode TEXT NOT NULL DEFAULT 'undecided'` (REQUIRED — `migrateSchema` early-returns at db.ts:59-62 when `professional_mode` is absent, and `professional_mode` must be TEXT, not INTEGER, or the rename-migration path throws), then run `migrateSchema(db)` and assert `inherit_review` was added with default 0:

```typescript
import { describe, it, expect } from 'vitest';
import Database from 'better-sqlite3';
import { migrateSchema } from '../db.js';

describe('inherit_review migration', () => {
  it('adds inherit_review to a pre-existing sessions table with default 0', () => {
    const db = new Database(':memory:');
    db.exec(`CREATE TABLE sessions (
      terminal_session TEXT PRIMARY KEY,
      professional_mode TEXT NOT NULL DEFAULT 'undecided',
      workflow_stage TEXT NOT NULL DEFAULT 'idle',
      plan_lineage INTEGER NOT NULL DEFAULT 0
    );`);
    db.prepare(`INSERT INTO sessions (terminal_session) VALUES ('s1')`).run();

    migrateSchema(db);

    const cols = db.prepare(`PRAGMA table_info(sessions)`).all() as Array<{ name: string }>;
    expect(cols.some((c) => c.name === 'inherit_review')).toBe(true);
    const row = db.prepare(`SELECT inherit_review FROM sessions WHERE terminal_session = 's1'`).get() as { inherit_review: number };
    expect(row.inherit_review).toBe(0);
  });
});
```

Run:
```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/state-manager && npx vitest run src/__tests__/inherit-review-migration.test.ts
```
Expected: FAIL — `migrateSchema` does not yet add `inherit_review`, so the column assertion fails.

**Step 2: Add the column to the sessions CREATE (db.ts).** In the `CREATE TABLE IF NOT EXISTS sessions (...)` block, add immediately after `plan_lineage INTEGER NOT NULL DEFAULT 0,`:
```
      inherit_review INTEGER NOT NULL DEFAULT 0,
```

**Step 3: Add the migration entry (db.ts).** In the sessions `expectedColumns` array (`--- Migrate sessions ---`, :161-166), add:
```typescript
      { name: 'inherit_review', type: 'INTEGER NOT NULL', dflt: '0' },
```

**Step 4: Add to the `Session` type (types.ts).** In the `Session` interface (:48-65), add immediately after `plan_lineage: number;` (:59):
```typescript
  inherit_review: number;
```
Do NOT add it to `TierUpReviewEntry` (:36-44).

**Step 5: Add to session-init.sh bootstrap.** In the ONE `CREATE TABLE ... sessions` block, add immediately after the `plan_lineage INTEGER NOT NULL DEFAULT 0,` line (:64):
```
      inherit_review INTEGER NOT NULL DEFAULT 0,
```
Do NOT touch the `tier_up_reviews` CREATE (:138-141). No test: bash schema mirror verified by the identical column definition against db.ts.

**Step 6: Run the migration test — GREEN.**
```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/state-manager && npx vitest run src/__tests__/inherit-review-migration.test.ts
```
Expected: `Test Files 1 passed`.

**Step 7: Stage.**
```bash
git -C /Users/roberthyatt/Code/ironclaude add worker/mcp-servers/state-manager/src/db.ts worker/mcp-servers/state-manager/src/types.ts worker/hooks/session-init.sh worker/mcp-servers/state-manager/src/__tests__/inherit-review-migration.test.ts
```

---

## Task 2: Effort-scoped lineage increment (MCP-transition set-site)

**Files:**
- Modify: `worker/mcp-servers/state-manager/src/state-machine.ts` (`executeWorkflowTransition` + a new exported helper)
- Modify: `worker/mcp-servers/state-manager/src/tools/write-tools.ts` (`mark_design_ready` applyArtifacts + `consumeDesign`)
- Test: `worker/mcp-servers/state-manager/src/__tests__/effort-scoped-review-budget.test.ts` (new)
- Modify: `worker/mcp-servers/state-manager/src/__tests__/workflow-transition-idempotency.test.ts` (add the column to its hand-built schema)

**Depends on:** Task 1

**Step 1: Write the behavior tests (RED).** Create `effort-scoped-review-budget.test.ts`. Build the DB with `initDb(':memory:')` (real schema, includes `inherit_review`) — do NOT hand-build a partial schema. Cover: (a) retreat from `executing` to `brainstorming` then `mark_design_ready` keeps `plan_lineage` UNCHANGED; (b) entry to `brainstorming` from `execution_complete` then `mark_design_ready` INCREMENTS `plan_lineage`; (c) `mark_brainstorming` from `executing` sets `inherit_review=1` (next `mark_design_ready` does not increment); (d) the two-hop hole: `mark_debugging` from `executing` then `debugging → brainstorming` then `mark_design_ready` keeps `plan_lineage` UNCHANGED (inherit_review stays 1); (e) a genuine-new-effort detour `execution_complete → brainstorming → debugging → brainstorming` then `mark_design_ready` INCREMENTS (inherit_review stayed 0). Drive via the write-tools dispatch (read `workflow-transition-idempotency.test.ts` for the exact dispatch call shape) and read `plan_lineage`/`inherit_review` from the sessions row.

Run:
```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/state-manager && npx vitest run src/__tests__/effort-scoped-review-budget.test.ts
```
Expected: FAIL — current code increments `plan_lineage` on every brainstorming→design_ready and never sets `inherit_review`; cases (a), (c), (d) fail.

**Step 2: Add the helper + apply it on target brainstorming OR debugging (state-machine.ts).** Add a module-level exported helper near `RETREAT_SOURCES`:
```typescript
export function inheritReviewOnDesignReentry(from: WorkflowStage): Partial<Pick<Session, 'inherit_review'>> {
  if (from === 'idle' || from === 'execution_complete') return { inherit_review: 0 };
  if (RETREAT_SOURCES[from]) return { inherit_review: 1 };
  return {}; // brainstorming, debugging, or unknown -> preserve prior classification
}
```
In `executeWorkflowTransition`, after `const artifactFields = options.applyArtifacts?.(context) ?? {};`, add:
```typescript
    const designReentryFields = (target === 'brainstorming' || target === 'debugging')
      ? inheritReviewOnDesignReentry(from)
      : {};
    updateSession(db, sessionId, {
      ...options.updateFields,
      ...artifactFields,
      ...designReentryFields,
      workflow_stage: target,
    });
```

**Step 3: Gate the lineage increment (write-tools.ts).** In `mark_design_ready`'s `applyArtifacts`, replace `return { plan_lineage: session.plan_lineage + 1 };` with:
```typescript
          if (session.inherit_review === 1) {
            return { inherit_review: 0 };
          }
          return { plan_lineage: session.plan_lineage + 1 };
```
In `consumeDesign` (the `if (session.workflow_stage === 'brainstorming')` block calling `updateSession(... plan_lineage: session.plan_lineage + 1)`), gate identically: `inherit_review === 1` → update `{ workflow_stage: 'design_ready', inherit_review: 0 }`; else `{ workflow_stage: 'design_ready', plan_lineage: session.plan_lineage + 1 }`.

**Step 4: Fix the idempotency test's hand-built schema (C3).** In `workflow-transition-idempotency.test.ts` `createTestDb`, add `inherit_review INTEGER NOT NULL DEFAULT 0,` immediately after the `plan_lineage INTEGER NOT NULL DEFAULT 0,` line in its `sessions` CREATE — otherwise its brainstorming-entry cases throw `no such column: inherit_review` once Task 2 lands.

**Step 5: Run both tests — GREEN.**
```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/state-manager && npx vitest run src/__tests__/effort-scoped-review-budget.test.ts src/__tests__/workflow-transition-idempotency.test.ts
```
Expected: both files pass; all five new cases pass and the idempotency suite is green.

**Step 6: Stage.**
```bash
git -C /Users/roberthyatt/Code/ironclaude add worker/mcp-servers/state-manager/src/state-machine.ts worker/mcp-servers/state-manager/src/tools/write-tools.ts worker/mcp-servers/state-manager/src/__tests__/effort-scoped-review-budget.test.ts worker/mcp-servers/state-manager/src/__tests__/workflow-transition-idempotency.test.ts
```

---

## Task 3: skill-state-bridge.sh set-site (C2b — dominant live path)

**Files:**
- Modify: `worker/hooks/skill-state-bridge.sh`
- Test: `worker/hooks/tests/test-skill-state-bridge-inherit-review.sh` (new)

**Depends on:** Task 2

**Step 1: Write the hook test (RED).** Create `test-skill-state-bridge-inherit-review.sh`, mirroring an existing hook test's harness (e.g. `test-session-init-plan-lineage.sh`: it inits a temp DB with the real schema via session-init.sh and pipes JSON into a hook). Seed a session row, set `workflow_stage='executing'`, pipe a `Skill`/`brainstorming` invocation into `skill-state-bridge.sh`, and assert the sessions row now has `inherit_review=1`. Add a second case: `workflow_stage='execution_complete'` → invoke brainstorming → `inherit_review=0`. Run:
```bash
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-skill-state-bridge-inherit-review.sh
```
Expected: FAIL — the hook does not yet write `inherit_review`, so the `=1` assertion fails.

**Step 2: Append inherit_review to the hook UPDATES.** In `skill-state-bridge.sh`, after the `case "$SKILL_NAME" in ... esac` block (which sets `UPDATES` and `STAGE_CHANGE`) and before the idempotent-exit check, add:
```bash
# Effort-scoped review budget: on design-phase (re-)entry, classify the effort from the
# FROM-stage so a retreat / mid-effort re-entry inherits the effort's one blind review.
TARGET_FOR_BUDGET="${STAGE_CHANGE#workflow_stage=}"
if [ "$TARGET_FOR_BUDGET" = "brainstorming" ] || [ "$TARGET_FOR_BUDGET" = "debugging" ]; then
  case "$CURRENT_STAGE" in
    idle|execution_complete)
      UPDATES="${UPDATES}, inherit_review=0" ;;
    design_ready|design_marked_for_use|plan_ready|plan_marked_for_use|final_plan_prep|executing|reviewing|plan_interrupted)
      UPDATES="${UPDATES}, inherit_review=1" ;;
    # brainstorming|debugging|empty -> leave inherit_review unchanged
  esac
fi
```
(`CURRENT_STAGE` is already read at :31-35; the idempotent-exit at :74-79 correctly skips the UPDATE for a same-stage re-invocation, so no reclassification happens without a real transition.)

**Step 3: Run the hook test — GREEN.**
```bash
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-skill-state-bridge-inherit-review.sh
```
Expected: both cases pass (executing→brainstorming sets 1; execution_complete→brainstorming sets 0).

**Step 4: Stage.**
```bash
git -C /Users/roberthyatt/Code/ironclaude add worker/hooks/skill-state-bridge.sh worker/hooks/tests/test-skill-state-bridge-inherit-review.sh
```

---

## Task 4: Advisor-remediated on a passing base (decision C)

**Files:**
- Modify: `worker/mcp-servers/state-manager/src/db.ts` (`hasAdvisorRemediatedAtHash`)
- Modify: `worker/mcp-servers/state-manager/src/tools/write-tools.ts` (`submit_tier_up_review` guard + `start_execution` gate message)
- Test: `worker/mcp-servers/state-manager/src/tools/write-tools.tier-up.test.ts` (existing)

**Depends on:** Task 3

**Step 1: Write/adjust the tests (RED).** In `write-tools.tier-up.test.ts`: (a) add — a lineage with a passing SOLID canonical verdict at hash H1, plan changed to H2 → `submit_tier_up_review(advisor-remediated)` at H2 SUCCEEDS and `start_execution` at H2 passes; (b) add — same WITHOUT the advisor-remediated record → `start_execution` at H2 BLOCKS; (c) regression — a bare `advisor-remediated` with NO prior canonical blind verdict is still REFUSED; (d) regression — the HAS-ISSUES→advisor-remediated flow still passes. ALSO update the two existing `toContain('prior HAS-ISSUES')` assertions (:172, :402) to `toContain('prior canonical blind review')`.

Run:
```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/state-manager && npx vitest run src/tools/write-tools.tier-up.test.ts
```
Expected: FAIL — case (a) fails (advisor-remediated refused on a SOLID base; start_execution blocks a passing verdict at a different hash), and the updated :172/:402 assertions fail against the old message.

**Step 2: Accept a passing predecessor in `hasAdvisorRemediatedAtHash` (db.ts).** Change the inner EXISTS clause `AND failed_review.verdict = 'HAS-ISSUES'` to:
```sql
          AND failed_review.verdict IN ('SOLID', 'HAS-ISSUES', 'top-tier-self')
```

**Step 3: Allow advisor-remediated on any canonical base (`submit_tier_up_review`, write-tools.ts).** Replace the `else if (!hasEarlierTierUpVerdict(... 'HAS-ISSUES' ...))` guard with:
```typescript
      } else if (!getBlindTierUpReviewForLineage(db, resolvedId, session.plan_lineage)) {
        return err(
          `BLOCKED — advisor-remediated requires a prior canonical blind review in plan lineage ` +
          `${session.plan_lineage}.`,
        );
      }
```
(`getBlindTierUpReviewForLineage` is already imported — used at :776/:1471; leave `hasEarlierTierUpVerdict` import as-is, it is otherwise unused now but the build tolerates it.)

**Step 4: Accept advisor-remediated on a passing verdict at a different hash (`start_execution` gate, write-tools.ts).** In the `isPassingTierUpVerdict(canonicalReview.verdict)` branch, after the exact-hash-match `return null`, add the advisor-remediated check, and KEEP the substrings `different plan hash` and `Do not dispatch another plan review` in the BLOCKED message (existing tests at :250-251/:266-267 assert them):
```typescript
          if (isPassingTierUpVerdict(canonicalReview.verdict)) {
            if (canonicalReview.plan_hash === planHash) return null;
            if (hasAdvisorRemediatedAtHash(db, resolvedId, session.plan_lineage, planHash)) return null;
            return `BLOCKED — plan lineage ${session.plan_lineage} has a passing ` +
              `${canonicalReview.verdict} review for a different plan hash and no advisor-remediated ` +
              'record for the current plan. Run the non-blind fix advisor and record ' +
              'advisor-remediated for the current plan, or restore the exact reviewed plan. ' +
              'Do not dispatch another plan review.';
          }
```

**Step 5: Run the tier-up tests — GREEN.**
```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/state-manager && npx vitest run src/tools/write-tools.tier-up.test.ts
```
Expected: `Test Files 1 passed`; all cases pass, including the two regressions and the updated assertions.

**Step 6: Stage.**
```bash
git -C /Users/roberthyatt/Code/ironclaude add worker/mcp-servers/state-manager/src/db.ts worker/mcp-servers/state-manager/src/tools/write-tools.ts worker/mcp-servers/state-manager/src/tools/write-tools.tier-up.test.ts
```

---

## Task 5: executing-plans skill text

**Files:**
- Modify: `worker/skills/executing-plans/SKILL.md`

**Depends on:** Task 4

No tests required: skill documentation text; the behavior it describes is enforced and tested by Tasks 1–4.

**Step 1: Update the Step 1.5 hash-mismatch branch.** Replace the bullet "A passing canonical verdict whose hash does not match the current plan: fail closed. Restore the exact reviewed plan or make a verified retreat to brainstorming; do not dispatch another plan review." with:
```
- A passing canonical verdict whose hash does not match the current plan (an
  inherited review after a retreat that changed the plan): run the non-blind fix
  advisor to validate the change and record `advisor-remediated` at the current
  hash, then continue to Step 2. Restoring the exact reviewed plan also clears it.
  Do NOT dispatch another blind review.
```

**Step 2: Update Step 10's acceptance line.** Replace "`start_execution` accepts either **`SOLID`** at the current hash, or **`HAS-ISSUES` (earlier) + `advisor-remediated` (current hash)**. A bare `advisor-remediated` with no preceding `HAS-ISSUES` is rejected." with:
```
    - `start_execution` accepts a passing verdict (`SOLID`/`top-tier-self`) at the
      current hash, or any canonical blind verdict (passing OR `HAS-ISSUES`) paired
      with `advisor-remediated` at the current hash. A bare `advisor-remediated`
      with no preceding canonical blind review is rejected.
```

**Step 3: Update Step 10's REQUIRES-RETREAT line.** Replace "The new design produces a new plan lineage, which earns its own single review." with:
```
      A retreat INHERITS the current effort's already-consumed blind review — it does
      NOT earn a new one; the changed plan proceeds via `advisor-remediated` (above).
      Only a genuinely new operator-initiated effort (design entered from a terminal
      or idle state) earns its own single blind review.
```

**Step 4: Stage.**
```bash
git -C /Users/roberthyatt/Code/ironclaude add worker/skills/executing-plans/SKILL.md
```

---

## Task 6: Build + full regression + stage dist

**Files:**
- Modify: `worker/mcp-servers/state-manager/dist/index.js`

**Depends on:** Task 5

No tests required: rebuilds the generated bundle and runs existing suites.

**Step 1: Build.**
```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/state-manager && npm run build
```
Expected: tsc + esbuild bundle; no type errors.

**Step 2: state-manager suite.**
```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/state-manager && npm test
```
Expected: all test files pass (the prior files + the 2 new state-manager test files).

**Step 3: workspace-manager suite.**
```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager && npm test
```
Expected: `Test Files 8 passed (8)`.

**Step 4: Hook suites (includes the new skill-state-bridge inherit-review test).**
```bash
for t in /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-*.sh; do echo "== $t =="; bash "$t" || echo "SUITE FAILED: $t"; done
```
Expected: each 0 failed; no `SUITE FAILED`.

**Step 5: Build commander pytest batch lists.**
```bash
rm -f /tmp/esrb_tests.txt && ls /Users/roberthyatt/Code/ironclaude/commander/tests/test_*.py | sort > /tmp/esrb_tests.txt && total=$(wc -l < /tmp/esrb_tests.txt) && per=$(( (total + 3) / 4 )) && split -l "$per" /tmp/esrb_tests.txt /tmp/esrb_batch_ && wc -l /tmp/esrb_tests.txt /tmp/esrb_batch_aa /tmp/esrb_batch_ab /tmp/esrb_batch_ac /tmp/esrb_batch_ad
```
Expected: 4 batch files `_aa`.._ad`, line counts summing to the total.

**Step 6: Run commander pytest batches (repeat for _aa,_ab,_ac,_ad as SEPARATE Bash calls).**
```bash
test -s /tmp/esrb_batch_aa || { echo "FATAL: /tmp/esrb_batch_aa missing/empty"; exit 1; } && cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest -q -m "not destructive" $(tr '\n' ' ' < /tmp/esrb_batch_aa)
```
Expected: across the batches, all pass, 2 deselected, 0 failed. (Orchestrator batch-lifecycle tests need free RAM > 10% of total; failures there are the known environmental gap, not this change.)

**Step 7: Stage dist bundle.**
```bash
git -C /Users/roberthyatt/Code/ironclaude add worker/mcp-servers/state-manager/dist/index.js
```

**Step 8: Return shell cwd to repo root.**
```bash
cd /Users/roberthyatt/Code/ironclaude
```
Expected: cwd is `/Users/roberthyatt/Code/ironclaude`.
