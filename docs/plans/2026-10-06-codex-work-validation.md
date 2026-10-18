# Validation of Codex's 2026-10-04..06 Work: Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** Independently validate the three committed Codex loops in `55ba8b3..a3d400b` and the staged worker-handoff work. The validation runs checks, a Fable adversarial review and a single findings report. It makes no product changes.

**Requirements:** `docs/plans/2026-10-06-codex-work-validation-requirements.md`

**Design:** `docs/plans/2026-10-06-codex-work-validation-design.md`

**Loop:** light. The output is documents only; the reviewer confirms this label.

**Architecture:**
- Task 1 runs every check and records its exact result.
- Task 2 is the orchestrator-dispatched Fable review. It is blind to Codex's review verdicts.
- Task 3 verifies the findings and writes the report.

## Execution invariants

- **Shell use.**
  - Shell state does not persist between steps, so every path is absolute.
  - Git commands use `git -C /Users/roberthyatt/Code/ironclaude`.
  - zsh `nomatch` is in effect, so globs are quoted.
- **Staging.** `docs/` is gitignored, so docs are staged with `git add -f`.
- **Evidence.**
  - Each run writes its own log under `/private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/val-*`.
  - No evidence command uses `2>/dev/null`.
- **Expected results are not predictions.** This is a validation loop. Each check's expected result is to record its exit code and summary line exactly. A failing check becomes a finding; it is never fixed here. This is why no `expected:` value states a pass count.
- **Working tree.**
  - The working tree contains the staged handoff source, and both the Commander and workspace-manager checks run against it.
  - The workspace bundles in `dist/` have not been rebuilt. The TypeScript compile check uses `--noEmit` and writes no files.
- **Hard limits.** No restarts, deploys, configuration edits, live worker operations, commits or pushes.

---

## Task 1: Run and record every check (V1)

**Files:**
- Create: `docs/reviews/2026-10-06-codex-work-check-results.md`

**No tests required.** This task runs the existing checks and records their results.

**Step 1: hook suites.** Run each suite individually, so that one failure does not hide the suites after it:

```bash
cd /Users/roberthyatt/Code/ironclaude && for s in worker/hooks/test-*.sh worker/hooks/tests/test-*.sh commander/hooks/tests/test-*.sh; do bash "$s" > "/private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/val-hook-$(basename "$s").log" 2>&1; echo "$s exit=$?"; done
```

Expected: one `exit=` line per suite. Record every suite that exits non-zero, along with the tail of its log.

**Step 2: workspace-manager vitest, full suite**, with a Bash timeout of 600000 ms:

```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager && npx vitest run > /private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/val-vitest-full.log 2>&1; echo "exit=$?"
```

Expected: record the `Test Files`, `Tests` and `Errors` lines and the exit code. A lone `onTaskUpdate` RPC timeout from `integration-core.test.ts` is a known issue already in the backlog. Record it as such, separately from any test failures.

**Step 3: focused handoff and CLI vitest** (from the handoff plan):

```bash
npm --prefix /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager test -- src/__tests__/handoff.test.ts src/__tests__/cli.test.ts > /private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/val-vitest-handoff.log 2>&1; echo "exit=$?"
```

Expected: record the summary lines and the exit code.

**Step 4: TypeScript compile check, no output:**

```bash
/Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager/node_modules/.bin/tsc --noEmit --project /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager/tsconfig.json > /private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/val-tsc.log 2>&1; echo "exit=$?"
```

Expected: record the exit code and any errors.

**Step 5: the handoff loop's required pytest selection**, using the Commander venv and a Bash timeout of 600000 ms:

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest -q tests/test_worker_handoff_registry.py tests/test_worker_quota.py tests/test_worker_pm_handoff.py tests/test_worker_handoff.py tests/test_worker_handoff_process.py tests/test_worker_handoff_daemon.py tests/test_workspace_handoff_authority.py tests/test_provider_router.py tests/test_worker_registry.py tests/test_lifecycle_logging.py > /private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/val-pytest-handoff.log 2>&1; echo "exit=$?"
```

Expected: record the summary line and the exit code.

**Step 6: the full Commander suite**, with a Bash timeout of 600000 ms:

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest -q > /private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/val-pytest-full.log 2>&1; echo "exit=$?"
```

Expected: record the summary line and the exit code.

**Step 7: write the results document.** Write `docs/reviews/2026-10-06-codex-work-check-results.md` with:
- every command;
- every exit code and summary line;
- the name of each failing hook suite and the tail of its log;
- the known limitation (the RPC timeout) and the dist-not-rebuilt note;
- a statement that every suite ran against the working tree, which contains the staged handoff source.

**Step 8: stage it:**

```bash
git -C /Users/roberthyatt/Code/ironclaude add -f docs/reviews/2026-10-06-codex-work-check-results.md
```

Expected: no output, exit 0.

---

## Task 2: Fable adversarial review (V2)

**Files:**
- Create: `docs/reviews/2026-10-06-codex-work-fable-review.md`

**No tests required.** This task is a review.

**Step 1: dispatch the reviewer.** The orchestrator dispatches a Fable reviewer (`Agent`, `model=fable`). If `model=fable` is unavailable, it dispatches `model=opus` with the identical prompt, and the review header records the substitution. The reviewer's constraints:
- It writes only `docs/reviews/2026-10-06-codex-work-fable-review.md`. It runs no tests and no mutating git or shell commands.
- It is blind to Codex's review verdicts and reasoning. It must not read `docs/reviews/2026-10-04-*` or any `docs/plans/2026-10-06-codex-work-validation*` file. It may read the Codex loops' requirements, design and plan documents listed below.
- The orchestrator's prompt passes only the scope, the commands, the requirement files and the checklist. It does not quote the operator's or Codex's characterisation of the handoff work.

The review covers:
1. **The committed diff** `git -C /Users/roberthyatt/Code/ironclaude diff 55ba8b3..a3d400b -- . ':!docs/reviews'`, checked against:
   - `docs/plans/2026-10-04-codex-hook-corrections-requirements.md` (R1–R10);
   - `docs/plans/2026-10-04-codex-hook-boundary-safeguards.md`;
   - `docs/plans/2026-10-05-gbtw-evaluation-warnings-requirements.md`.

   The two `docs/plans/2026-10-04-replan-progress-experiment-stage-*` documents in this range belong to L3 and are out of scope; the reviewer does not review them.
2. **The staged diff** `git -C /Users/roberthyatt/Code/ironclaude diff --staged -- . ':!docs/reviews' ':!docs/plans/2026-10-06-codex-work-validation*'`, checked against:
   - `docs/plans/2026-10-05-worker-provider-handoff-requirements.md` (R1–R9);
   - both handoff designs.

The checklist:
- process-stop safety (R4);
- ownership compare-and-swap and rebinding, and fail-closed behavior (R5);
- quota-evidence false triggers and switching back and forth (R7);
- PM progress and review attribution, and design-registration transfer (R3);
- preserving the worktree (R2);
- identical behavior on both clients;
- bundle/source agreement for the committed bundles, and the staged source with no rebuilt bundles as a deploy prerequisite;
- tests that cannot fail.

The reviewer must verify every material finding from source. It writes the review to the target file with:
- a verdict per loop;
- findings rated Critical, Important or Minor, each with `file:line`, a scenario and evidence;
- observations.

**Step 2: stage it:**

```bash
git -C /Users/roberthyatt/Code/ironclaude add -f docs/reviews/2026-10-06-codex-work-fable-review.md
```

Expected: no output, exit 0.

---

## Task 3: Findings report (V3, V4)

**Files:**
- Create: `docs/reviews/2026-10-06-codex-work-validation-report.md`
- Stage these files, which this task does not edit:
  - `docs/plans/2026-10-06-codex-work-validation-requirements.md`
  - `docs/plans/2026-10-06-codex-work-validation-design.md`
  - `docs/plans/2026-10-06-codex-work-validation.md`
  - `docs/plans/2026-10-06-codex-work-validation.plan.json`

  They are listed in `allowed_files` because `/commit` scopes to the union.

**No tests required.** This task writes the report.

**Step 1: verify the findings.** Check every Critical or Important finding from Task 2 against the current source, or with a scratch reproduction. Mark each one verified or unverified. An unverified finding becomes an observation.

**Step 2: write the report.** It contains:
- **Verdicts.** One per loop:
  - hook corrections;
  - boundary safeguards;
  - GBTW warnings;
  - worker handoff.

  Each is CLEAN or HAS-FINDINGS.
- **Check summary**, taken from Task 1.
- **Verified findings.**
- **Recommendations.** For each loop, one of:
  - keep or commit;
  - fix in a named follow-up loop;
  - backlog.
- **Deploy prerequisites:**
  - rebuild the workspace bundles before any Commander restart that would load the staged handoff source;
  - refresh the Claude plugin cache, which is behind `59b5ca7` and `a3d400b`.

**Step 3: stage the report and the loop documents:**

```bash
git -C /Users/roberthyatt/Code/ironclaude add -f docs/reviews/2026-10-06-codex-work-validation-report.md docs/plans/2026-10-06-codex-work-validation-requirements.md docs/plans/2026-10-06-codex-work-validation-design.md docs/plans/2026-10-06-codex-work-validation.md docs/plans/2026-10-06-codex-work-validation.plan.json
```

Expected: no output, exit 0.
