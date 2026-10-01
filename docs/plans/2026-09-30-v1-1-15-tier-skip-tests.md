# v1.1.15 Tier-Skip Test Coverage Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** Add two vitest cases that prove the Codex broker skips an empty installed tier and applies the R4 built-in fallback.

**Requirements:** `docs/plans/2026-09-30-v1-1-15-tier-skip-tests-requirements.md` (T1–T3)

**Design:** `docs/plans/2026-09-30-v1-1-15-tier-skip-tests-design.md`

**Architecture:** This is a tests-only change in `advisor-review.test.ts`, and the production code is unchanged. Each new case overrides the harness `readFile` with a specific installed-model cache and asserts which reviewer model is chosen.

**Tech Stack:** TypeScript, vitest.

## Execution invariants

- Use absolute paths. Shell state does not persist between steps.
- Run vitest as `cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/state-manager && npx vitest run src/tools/advisor-review.test.ts`.
- `docs/` is gitignored, so stage the plan artifacts with `git add -f`.
- `advisor-review.ts` must end the task byte-identical to `HEAD`. It is edited only for the temporary mutation check in Step 3. No commit.

---

## Task 1: Tier-skip and fallback test cases (T1–T3)

**Files:**
- Test: `worker/mcp-servers/state-manager/src/tools/advisor-review.test.ts`, inserted directly before the line `  it('prefers the newest version at a tier', async () => {` (currently line 656)
- Temporary mutation, then revert: `worker/mcp-servers/state-manager/src/tools/advisor-review.ts:235-239`

**Step 1: Add the test cases.** Insert this block directly before `  it('prefers the newest version at a tier', async () => {`:

```ts
  it.each([
    ['gpt-6-luna', ['gpt-6-sol', 'gpt-6-astra'], 'gpt-6-sol'],
    ['gpt-6-sol', ['gpt-6-luna'], 'gpt-6-astra'],
  ])('skips empty installed tiers for %s (installed %j) -> %s', async (requester, installed, reviewer) => {
    const h = harness();
    h.deps.readFile = vi.fn(async (target: string) => {
      if (target === MODELS_CACHE_PATH) return JSON.stringify({ models: installed.map((slug) => ({ slug })) });
      throw enoent();
    });
    h.deps.trustedRequesterModel = requester;
    const result = await runCodexAdvisorReview(
      { packet: 'packet', requester_model: requester }, identity, session, runtime, h.deps,
    );
    expect(payload(result)).toEqual(expect.objectContaining({ success: true, reviewer_model: reviewer }));
    expect(h.spawn.mock.calls[1][1]).toContain(reviewer);
  });
```

What the two rows check:
- **Row 1 (T1):** a luna requester with no terra installed must step up to sol.
- **Row 2 (T2):** a sol requester whose only installed model is lower must use the built-in fallback `gpt-6-astra`, as R4 requires.

**Step 2: Run the tests and confirm they pass.** RED is not possible here: the behavior already exists, so falsifiability is proven in Step 3 instead.

```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/state-manager && npx vitest run src/tools/advisor-review.test.ts
```

Expected: 67 passed, 0 failed. That is the current 65 plus 2.

**Step 3: Prove T1 can fail, using a temporary mutation that you then revert.**

(a) In `advisor-review.ts`, replace these four lines:

```ts
  for (let index = start; index <= ceiling; index += 1) {
    const newest = newestAtTier(installed, TIER_ORDER[index]);
    if (newest) return newest;
  }
```

with:

```ts
  return newestAtTier(installed, TIER_ORDER[start]) ?? FALLBACK_CODEX_MODELS[start];
```

(b) Run the tests:

```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/state-manager && npx vitest run src/tools/advisor-review.test.ts
```

Expected: exactly 1 failure, `skips empty installed tiers for gpt-6-luna …`. The mutant returns `gpt-5.6-terra` instead of `gpt-6-sol`. Every other test passes. Record the failing name in the report.

(c) Revert by restoring the original four-line loop, then confirm the file matches `HEAD`:

```bash
git -C /Users/roberthyatt/Code/ironclaude diff --exit-code -- worker/mcp-servers/state-manager/src/tools/advisor-review.ts
```

Expected: no output, exit 0.

**Step 4: Re-verify after the revert.**

```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/state-manager && npx vitest run src/tools/advisor-review.test.ts
```

Expected: 67 passed, 0 failed.

```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/state-manager && npx tsc --noEmit
```

Expected: exit 0, no output.

**Step 5: Stage.**

```bash
git -C /Users/roberthyatt/Code/ironclaude add worker/mcp-servers/state-manager/src/tools/advisor-review.test.ts
```

```bash
git -C /Users/roberthyatt/Code/ironclaude add -f docs/plans/2026-09-30-v1-1-15-tier-skip-tests-requirements.md docs/plans/2026-09-30-v1-1-15-tier-skip-tests-design.md docs/plans/2026-09-30-v1-1-15-tier-skip-tests.md docs/plans/2026-09-30-v1-1-15-tier-skip-tests.plan.json
```

Expected: exit 0 for both.
