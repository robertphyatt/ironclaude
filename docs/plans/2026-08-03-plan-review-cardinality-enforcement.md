# Plan-Review Cardinality Enforcement Implementation Plan

> **For Codex:** REQUIRED SUB-SKILL: Use `ironclaude:executing-plans` to implement this plan task-by-task.

**Goal:** Make the existing v1.1.2 rule—one blind plan review per plan lineage—a durable, restart-safe state-manager invariant.

**Requirements:** `docs/plans/2026-08-03-plan-review-cardinality-enforcement-requirements.md`

**Architecture:** Persist an integer plan-lineage generation on sessions and review rows. Advance it only when a design actually enters `design_ready`; reject any second blind verdict for the same session and generation before review/audit mutation, with a SQLite trigger as race-safe backstop. Bind execution gating and advisor remediation to the current generation.

**Tech Stack:** TypeScript, better-sqlite3, Vitest, Bash/sqlite3 hook bootstrap, esbuild.

**Execution mode:** Sequential Terra subagents. Tasks share state-manager seams and must run in dependency order; no orchestration or review invocation is delegated.

## Execution Invariants

- Every shell step is independent. Use literal paths and do not rely on exported state from an earlier step.
- Codex Bash cwd may be `commander/`; use absolute paths or `git -C /Users/roberthyatt/Code/ironclaude`.
- Quote globs under zsh. Do not suppress evidence-command stderr or truncate absence checks.
- `docs/` is ignored; stage plan/findings artifacts with `git add -f`.
- Preserve all pre-existing staged and unstaged changes. Stage only each task's allowed files.
- Test expectations use the measured baseline: state-manager `161 passed`; focused tier-up plus transition tests `47 passed`; transition shell harness `44 pass, 0 fail`.
- No commit, push, plugin reinstall, Commander restart, provider switch, Slack write, directive, or worker operation is authorized by this plan.

---

## Task 1: Persist plan lineage and advance it at design approval

**Files:**

- Modify: `worker/mcp-servers/state-manager/src/types.ts`
- Modify: `worker/mcp-servers/state-manager/src/db.ts`
- Modify: `worker/mcp-servers/state-manager/src/tools/write-tools.ts`
- Modify: `worker/mcp-servers/state-manager/src/__tests__/workflow-transition-idempotency.test.ts`
- Create: `worker/mcp-servers/state-manager/src/__tests__/plan-review-lineage-migration.test.ts`
- Modify: `worker/hooks/session-init.sh`
- Create: `worker/hooks/tests/test-session-init-plan-lineage.sh`

**Step 1: Write RED transition, migration, and bootstrap tests**

Extend the test session schemas with `plan_lineage INTEGER NOT NULL DEFAULT 0`, then add behavioral assertions that:

- a changed `mark_design_ready` transition increments `plan_lineage` once;
- same-target `mark_design_ready` preserves the full snapshot, including lineage;
- `consume_design` increments only when it changes `brainstorming` to `design_ready`;
- an injected audit failure during `consume_design` leaves the design row, session stage, lineage, and audit log unchanged;
- `migrateSchema` adds `plan_lineage` to an old `sessions` table and `tier_up_reviews` table without deleting duplicate historical rows;
- a fresh `session-init.sh` bootstrap creates both columns with default `0`.

The migration test must create a temporary file database with two historical blind rows before calling `migrateSchema`, then assert both rows remain. The hook test must use a temporary `HOME`, pipe a non-default session ID into the real hook, and inspect the resulting schema with `PRAGMA table_info`.

Run:

```bash
npm --prefix /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/state-manager test -- --run src/__tests__/workflow-transition-idempotency.test.ts src/__tests__/plan-review-lineage-migration.test.ts
```

Expected RED: non-zero exit; new lineage/migration assertions fail at the missing `plan_lineage` seam. Existing transition assertions remain green.

Run:

```bash
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-session-init-plan-lineage.sh
```

Expected RED: non-zero exit because the real bootstrap schema lacks `plan_lineage`.

**Step 2: Add additive schema and type fields**

Add to both interfaces:

```ts
export interface TierUpReviewEntry {
  // existing fields...
  plan_lineage: number;
}

export interface Session {
  // existing fields...
  plan_lineage: number;
}
```

Add `plan_lineage INTEGER NOT NULL DEFAULT 0` to both table definitions in `db.ts` and `session-init.sh`. Add both columns to `migrateSchema`'s idempotent missing-column migration; do not rewrite existing row values.

**Step 3: Advance lineage only on real design-ready transitions**

In `mark_design_ready`, return an artifact field from the existing atomic transition:

```ts
applyArtifacts: ({ session }) => {
  if (file) {
    dbRegisterDesign(db, file, resolvedId);
    dbConsumeDesign(db, file);
  }
  return { plan_lineage: session.plan_lineage + 1 };
},
```

Wrap the full `consume_design` operation—design consumption, optional stage and lineage update, and audit insertion—in one database transaction. When and only when `session.workflow_stage === 'brainstorming'`, include `plan_lineage: session.plan_lineage + 1` in the same session update that changes the stage. Preserve the existing later-stage consumption behavior inside the transaction. Do not increment on `create_plan`, plan reload, retreat, reset, or same-target calls.

**Step 4: Run GREEN verification**

Run both RED commands again.

Expected GREEN: both Vitest files pass; hook harness reports all assertions passing. The migration test proves duplicate historical rows remain and both fresh-schema defaults equal `0`.

Run:

```bash
git -C /Users/roberthyatt/Code/ironclaude diff --check -- worker/mcp-servers/state-manager/src/types.ts worker/mcp-servers/state-manager/src/db.ts worker/mcp-servers/state-manager/src/tools/write-tools.ts worker/mcp-servers/state-manager/src/__tests__/workflow-transition-idempotency.test.ts worker/mcp-servers/state-manager/src/__tests__/plan-review-lineage-migration.test.ts worker/hooks/session-init.sh worker/hooks/tests/test-session-init-plan-lineage.sh
```

Expected: exit 0, no output.

**Step 5: Stage only Task 1 files**

```bash
git -C /Users/roberthyatt/Code/ironclaude add -- worker/mcp-servers/state-manager/src/types.ts worker/mcp-servers/state-manager/src/db.ts worker/mcp-servers/state-manager/src/tools/write-tools.ts worker/mcp-servers/state-manager/src/__tests__/workflow-transition-idempotency.test.ts worker/mcp-servers/state-manager/src/__tests__/plan-review-lineage-migration.test.ts worker/hooks/session-init.sh worker/hooks/tests/test-session-init-plan-lineage.sh
```

Expected: Task 1 files staged; unrelated index entries unchanged.

---

## Task 2: Enforce one blind review and bind the execution gate to lineage

**Depends on:** Task 1

**Files:**

- Modify: `worker/mcp-servers/state-manager/src/db.ts`
- Modify: `worker/mcp-servers/state-manager/src/tools/read-tools.ts`
- Modify: `worker/mcp-servers/state-manager/src/tools/write-tools.ts`
- Modify: `worker/mcp-servers/state-manager/src/tools/write-tools.tier-up.test.ts`
- Modify: `worker/mcp-servers/state-manager/src/__tests__/plan-review-lineage-migration.test.ts`
- Modify: `worker/mcp-servers/state-manager/src/__tests__/tool-dispatch.test.ts`
- Modify: `worker/skills/executing-plans/SKILL.md`
- Modify: `commander/tests/test_executing_plans_skill.py`

**Step 1: Replace the legacy re-review test with RED cardinality tests**

Update the tier-up fixture schemas and seeded sessions to include `plan_lineage`. Replace `commander-choice + latest review HAS-ISSUES requires a current-plan pass`, which currently performs a prohibited second `SOLID` review, with tests that prove:

1. first `SOLID`, `HAS-ISSUES`, or `top-tier-self` insert succeeds;
2. a second blind verdict at the same hash is rejected with unchanged review and audit counts;
3. `HAS-ISSUES`, changed plan JSON, then `SOLID` is rejected in the same lineage;
4. formatting-only JSON change cannot bypass the cap;
5. `HAS-ISSUES` plus same-lineage `advisor-remediated` still advances at same or changed hash;
6. old-lineage `HAS-ISSUES` cannot authorize current-lineage `advisor-remediated`;
7. after a newly approved design advances the generation, one blind review is accepted;
8. `start_execution` block text after `HAS-ISSUES` contains `advisor-remediated` and contains neither `fresh blind review`, `fresh SOLID review`, nor `re-review`;
9. migrated duplicate rows use the earliest blind row by `id ASC` as canonical, whether `HAS-ISSUES` or a passing verdict came first;
10. a passing canonical verdict with a changed current plan hash fails closed without mutating state for both `SOLID` and `top-tier-self`;
11. a fresh file database installs the trigger, rejects a second blind insert, and still rejects it after close/reopen;
12. `get_resume_state` reports a bounded current-lineage review summary and tool dispatch exposes it;
13. executing-plans branches on that summary before dispatching any reviewer and never recommends another blind review for a consumed lineage.

Every rejection test must snapshot both `tier_up_reviews` and `audit_log` before the call and compare them afterward.

Run:

```bash
npm --prefix /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/state-manager test -- --run src/tools/write-tools.tier-up.test.ts src/__tests__/plan-review-lineage-migration.test.ts
```

Expected RED: new duplicate/cross-lineage/message assertions fail; existing first-review and advisor-remediation paths remain green.

Run:

```bash
npm --prefix /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/state-manager test -- --run src/__tests__/tool-dispatch.test.ts
```

Expected RED: resume-state review-summary assertions fail at the missing read seam.

Run:

```bash
python -m pytest /Users/roberthyatt/Code/ironclaude/commander/tests/test_executing_plans_skill.py -q
```

Expected RED: executing-plans still permits reviewer dispatch before inspecting persisted review state.

**Step 2: Add lineage-aware database helpers and trigger**

Change `insertTierUpReview`, `getTierUpReviewByHash`, `getLatestTierUpReview`, and `hasEarlierTierUpVerdict` to accept/filter `planLineage`. Add a helper that returns the canonical blind row for a lineage; canonical always means the earliest row by `id ASC`, including migrated lineage `0` rows:

```ts
export function getBlindTierUpReviewForLineage(
  db: Database.Database,
  sessionId: string,
  planLineage: number,
): TierUpReviewEntry | undefined {
  return db.prepare(`
    SELECT * FROM tier_up_reviews
    WHERE terminal_session = ? AND plan_lineage = ?
      AND verdict IN ('SOLID', 'HAS-ISSUES', 'top-tier-self')
    ORDER BY id ASC LIMIT 1
  `).get(sessionId, planLineage) as TierUpReviewEntry | undefined;
}
```

Install an idempotent `BEFORE INSERT` trigger in both existing-db migration and fresh schema initialization. It must raise `plan lineage already has a blind review` when `NEW.verdict` is blind and such a row already exists for the same `(terminal_session, plan_lineage)`. Do not delete or renumber historical rows; SQLite trigger creation must succeed with historical duplicates already present. Add separate tests for migrated and freshly initialized file databases.

**Step 3: Reject second blind submissions before mutation**

Define the blind set separately from the full verdict set:

```ts
const BLIND_TIER_UP_VERDICTS = ['SOLID', 'HAS-ISSUES', 'top-tier-self'] as const;
```

Inside `submit_tier_up_review`, after session/stage validation and before insertion:

```ts
if (isBlindTierUpVerdict(verdict)) {
  const existing = getBlindTierUpReviewForLineage(
    db, resolvedId, session.plan_lineage,
  );
  if (existing) {
    return err(
      `BLOCKED — plan lineage ${session.plan_lineage} already consumed its one blind review ` +
      `(${existing.verdict}). Do not dispatch another plan review. ` +
      `Use the fix advisor and advisor-remediated path after HAS-ISSUES, or retreat ` +
      `to brainstorming when a verified design premise is invalid.`,
    );
  }
}
```

Pass `session.plan_lineage` into the insert and include it in the success object and audit context. Catch only the trigger's exact constraint message and return the same blocked result; rethrow unrelated database failures.

Reject `advisor-remediated` at submission when no earlier same-lineage `HAS-ISSUES` exists. This makes the documented bare/cross-lineage rejection happen before mutation.

**Step 4: Expose bounded resume evidence before reviewer dispatch**

Extend `get_resume_state` with a bounded current-lineage review summary containing:

- current lineage number;
- canonical first blind verdict and plan hash, if present;
- whether that hash matches the current plan;
- whether a current-hash `advisor-remediated` row exists.

Expose the summary through the existing read-tool dispatch path. Do not expose review transcripts or add a new tool.

Update executing-plans so its post-`create_plan`, pre-reviewer branch always reads this summary:

- no canonical blind verdict: use the existing one-review path;
- matching `SOLID` or `top-tier-self`: skip reviewer dispatch;
- `HAS-ISSUES` without current remediation: resume the existing non-blind fix-advisor/remediation path;
- `HAS-ISSUES` with current remediation: skip reviewer dispatch;
- passing verdict with changed plan hash: fail closed and require restoration of the reviewed plan or a verified retreat to brainstorming.

If prior `HAS-ISSUES` findings cannot be recovered from current context or conversation history, fail closed. Do not dispatch a replacement blind review.

**Step 5: Make execution gating current-lineage only**

Pass `session.plan_lineage` into every review lookup. Preserve policy-off behavior. Under `enforced` or a current-lineage `HAS-ISSUES`:

- no blind row yet: request the lineage's one blind review;
- `HAS-ISSUES`: require holistic repair plus `advisor-remediated`, never another review;
- current-hash `advisor-remediated`: accept only with an earlier same-lineage `HAS-ISSUES`;
- `SOLID` or `top-tier-self` at the current hash: accept;
- `SOLID` or `top-tier-self` at a different hash: fail closed for restoration or verified retreat, never another review;
- older-lineage rows: ignore completely.

Remove all stale `fresh SOLID review`, `fresh blind review`, hash-change re-review, and `re-review is required` instructions from production error strings.

**Step 6: Run GREEN and race-backstop verification**

Run all three Task 2 RED commands again.

Expected GREEN: all first-review, duplicate, changed-hash, formatting, remediation, new-lineage, and message tests pass.

The migration test must also insert through raw SQL after migration: first new-generation blind row succeeds; second is rejected by the trigger; close/reopen the file database and prove a further second insert remains rejected. A separate fresh-file initialization test must inspect `sqlite_master`, accept one blind row, reject the second, then close/reopen and reject it again. Migrated duplicate tests must prove the earliest row controls in both verdict orderings.

Run:

```bash
git -C /Users/roberthyatt/Code/ironclaude diff --check -- worker/mcp-servers/state-manager/src/db.ts worker/mcp-servers/state-manager/src/tools/read-tools.ts worker/mcp-servers/state-manager/src/tools/write-tools.ts worker/mcp-servers/state-manager/src/tools/write-tools.tier-up.test.ts worker/mcp-servers/state-manager/src/__tests__/plan-review-lineage-migration.test.ts worker/mcp-servers/state-manager/src/__tests__/tool-dispatch.test.ts worker/skills/executing-plans/SKILL.md commander/tests/test_executing_plans_skill.py
```

Expected: exit 0, no output.

**Step 7: Stage only Task 2 files**

```bash
git -C /Users/roberthyatt/Code/ironclaude add -- worker/mcp-servers/state-manager/src/db.ts worker/mcp-servers/state-manager/src/tools/read-tools.ts worker/mcp-servers/state-manager/src/tools/write-tools.ts worker/mcp-servers/state-manager/src/tools/write-tools.tier-up.test.ts worker/mcp-servers/state-manager/src/__tests__/plan-review-lineage-migration.test.ts worker/mcp-servers/state-manager/src/__tests__/tool-dispatch.test.ts worker/skills/executing-plans/SKILL.md commander/tests/test_executing_plans_skill.py
```

Expected: Task 2 changes staged; unrelated index entries unchanged.

---

## Task 3: Build the runtime bundle and run bounded regression verification

**Depends on:** Task 2

**Files:**

- Modify: `worker/mcp-servers/state-manager/dist/index.js`
- Create: `docs/plans/2026-08-03-plan-review-cardinality-enforcement-findings.md`

**Step 1: Build the actual state-manager entry bundle**

Run:

```bash
npm --prefix /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/state-manager run build
```

Expected: TypeScript and esbuild exit 0; `dist/index.js` is rebuilt.

**Step 2: Verify focused and full state-manager behavior**

Run:

```bash
npm --prefix /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/state-manager test -- --run src/tools/write-tools.tier-up.test.ts src/__tests__/workflow-transition-idempotency.test.ts src/__tests__/plan-review-lineage-migration.test.ts
```

Expected: all focused tests pass; count is the measured baseline 47 plus every newly added focused test.

Run:

```bash
npm --prefix /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/state-manager test -- --run src/__tests__/tool-dispatch.test.ts
```

Expected: resume-state review-summary and dispatch tests pass.

Run:

```bash
python -m pytest /Users/roberthyatt/Code/ironclaude/commander/tests/test_executing_plans_skill.py -q
```

Expected: all executing-plans skill contract tests pass.

Run:

```bash
npm --prefix /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/state-manager test -- --run
```

Expected: all state-manager tests pass; count is the measured baseline 161 plus every newly added test, with no skipped or failed file.

Run:

```bash
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-session-init-plan-lineage.sh
```

Expected: all new hook bootstrap assertions pass.

Run:

```bash
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-workflow-transition-idempotency.sh
```

Expected: `Results: 44 pass, 0 fail`.

**Step 3: Prove bundle/source parity and absence of stale guidance**

Run independent uncapped searches:

```bash
rg -n "plan lineage already has a blind review|plan_lineage" /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/state-manager/src/db.ts /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/state-manager/src/tools/write-tools.ts
```

Expected: source schema and enforcement markers appear.

```bash
rg -n "plan_lineage|canonical_blind|advisor_remediated" /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/state-manager/src/tools/read-tools.ts
```

Expected: bounded resume-state evidence markers appear in source.

```bash
rg -n "plan_lineage" /Users/roberthyatt/Code/ironclaude/worker/hooks/session-init.sh
```

Expected: hook bootstrap marker appears.

```bash
rg -n "plan lineage already has a blind review|plan_lineage" /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/state-manager/dist/index.js
```

Expected: bundled runtime contains schema and enforcement markers.

```bash
rg -n "fresh SOLID review|fresh blind review|re-review is required" /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/state-manager/src/tools/write-tools.ts /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/state-manager/dist/index.js
```

Expected: exit 1 with no matches and no stderr.

```bash
rg -n "fresh SOLID review|fresh blind review|re-review is required|dispatch a second" /Users/roberthyatt/Code/ironclaude/worker/skills/executing-plans/SKILL.md
```

Expected: exit 1 with no matches and no stderr. Independent absence checks prevent a clean source file from masking a stale bundle or skill.

**Step 4: Write bounded findings**

Create the findings document with:

- root cause and exact invariant;
- migration and historical-row preservation result;
- RED and GREEN commands with actual counts;
- trigger reopen/race-backstop evidence;
- source/bundle marker evidence;
- scoped diff summary;
- explicit statements that Commander/Slack/providers/code-review frequency were unchanged and no commit or push occurred.

Do not include conversation transcripts, credentials, provider auth output, or unrelated repository findings.

**Step 5: Final scoped checks and staging**

Run:

```bash
git -C /Users/roberthyatt/Code/ironclaude diff --check -- worker/mcp-servers/state-manager/src/types.ts worker/mcp-servers/state-manager/src/db.ts worker/mcp-servers/state-manager/src/tools/read-tools.ts worker/mcp-servers/state-manager/src/tools/write-tools.ts worker/mcp-servers/state-manager/src/tools/write-tools.tier-up.test.ts worker/mcp-servers/state-manager/src/__tests__/workflow-transition-idempotency.test.ts worker/mcp-servers/state-manager/src/__tests__/plan-review-lineage-migration.test.ts worker/mcp-servers/state-manager/src/__tests__/tool-dispatch.test.ts worker/hooks/session-init.sh worker/hooks/tests/test-session-init-plan-lineage.sh worker/skills/executing-plans/SKILL.md commander/tests/test_executing_plans_skill.py worker/mcp-servers/state-manager/dist/index.js
```

Expected: exit 0, no output.

Stage only the Task 3 bundle:

```bash
git -C /Users/roberthyatt/Code/ironclaude add -- worker/mcp-servers/state-manager/dist/index.js
```

Expected: bundle staged; unrelated index entries unchanged.

Stage the ignored findings artifact separately:

```bash
git -C /Users/roberthyatt/Code/ironclaude add -f -- docs/plans/2026-08-03-plan-review-cardinality-enforcement-findings.md
```

Expected: findings staged; unrelated index entries unchanged. Do not commit or push.
