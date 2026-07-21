# Plan-Review Verdict Calibration + Working-Tree Completion — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** Ship v1.0.25 — recalibrate the tier-up reviewer prompt so `SOLID` is reachable and nitpicks stop blocking execution, complete an incomplete staged commit that would otherwise crash the MCP server for Claude users, and land the result on **both** the Codex and Claude installs.

**Architecture:** Prompt-only rewrite of the reviewer template in `worker/skills/executing-plans/SKILL.md` (adds a MATERIAL decision test, a mechanical verdict rubric, a sharpened latent-defect hunt, and a non-blocking Observations bucket) plus `git add` of seven ship-together files plus a four-file version bump. No new verdict values, no TypeScript change, no `dist/` rebuild.

**Tech Stack:** Markdown skill contracts, Python pytest, TypeScript/vitest (verification only), git.

**Design:** `docs/plans/2026-07-21-review-verdict-calibration-design.md`

**`estimated_memory_gb` is required.** `plan.json` sets it to `2`. The Zod schema (`state-machine.ts:421`) marks it optional so `create_plan` accepts a plan without it silently, but `professional-mode-guard.sh:167-183` hard-blocks the `Skill(ironclaude:executing-plans)` invocation when it is null — the failure surfaces only at the skill call this plan's header mandates. Adding it later changes `hashPlan(session.plan_json)` and invalidates any recorded tier-up review, forcing an extra round.

**Baselines established by investigation (verify, do not re-derive):**
- Commander suite: 2003/2003 passing. state-manager vitest: 134/134. Hook suite: 44/44.
- `dist/index.js` is already in sync with the full working tree and byte-identical to the live Codex install.
- Valid verdicts are `['SOLID','HAS-ISSUES','top-tier-self']` in `write-tools.ts` — unchanged by this plan.
- No test pins the `Critical / Important / Minor` grouping, so dropping the `Minor` tier is safe.

---

## Task 1: Complete the staged set (fix the broken-commit hazard)

**Files (staged, not edited):**
- `worker/mcp-servers/state-manager/src/session-identity.ts`
- `worker/mcp-servers/state-manager/src/__tests__/session-identity.test.ts`
- `worker/mcp-servers/state-manager/src/__tests__/tool-dispatch.test.ts`
- `worker/mcp-servers/state-manager/src/db.ts`
- `worker/mcp-servers/state-manager/src/tools/write-tools.tier-up.test.ts`
- `worker/.mcp.json`
- `commander/tests/test_writing_plans_skill.py`

**Depends on:** nothing.

**No tests required for the staging action itself** — it changes no file content. Correctness is proven by the `tsc --noEmit` and vitest gates in Steps 3–4, which are the checks that would have caught the hazard.

**Why this matters:** the currently-staged snapshot does not compile and would ship a plugin that dies at startup. `src/session-identity.ts` is untracked but imported by four staged files. `src/db.ts` is unstaged but its `getLatestTierUpReview()` is imported by staged `write-tools.ts`. `worker/.mcp.json` is unstaged but supplies `IRONCLAUDE_CLIENT=claude`, which the new `index.ts` requires at module load — without it the MCP server throws on startup for every Claude Code user.

**Step 1: Confirm the hazard exists before fixing it**

Run:
```bash
git status --short worker/mcp-servers/state-manager/src/session-identity.ts worker/mcp-servers/state-manager/src/db.ts worker/.mcp.json
```

Expected: `session-identity.ts` shows `??` (untracked); `db.ts` and `worker/.mcp.json` show a space in column 1 with `M` in column 2 (modified, unstaged). If all three already show `M` or `A` in column 1, the hazard is already fixed — note it and continue.

**Step 2: Stage the seven files**

Run:
```bash
git add \
  worker/mcp-servers/state-manager/src/session-identity.ts \
  worker/mcp-servers/state-manager/src/__tests__/session-identity.test.ts \
  worker/mcp-servers/state-manager/src/__tests__/tool-dispatch.test.ts \
  worker/mcp-servers/state-manager/src/db.ts \
  worker/mcp-servers/state-manager/src/tools/write-tools.tier-up.test.ts \
  worker/.mcp.json \
  commander/tests/test_writing_plans_skill.py
```

Expected: no output (success).

**Step 3: Verify the staged tree now compiles**

Run:
```bash
cd worker/mcp-servers/state-manager && npx tsc --noEmit
```

Expected: no output, exit 0. Any error naming `session-identity` or `getLatestTierUpReview` means Step 2 did not take — re-check the paths.

**Step 4: Verify state-manager tests still pass**

Run:
```bash
cd worker/mcp-servers/state-manager && npx vitest run
```

Expected: `134 passed` (12 files), 0 failed.

**Step 5: Confirm staging is complete**

Run:
```bash
git status --short | grep -E '^\?\?|^ M' | grep -E 'state-manager/src|worker/\.mcp\.json' || echo "CLEAN: no unstaged state-manager sources"
```

Expected: `CLEAN: no unstaged state-manager sources`.

---

## Task 2: Recalibrate the reviewer prompt

**Files:**
- Modify: `worker/skills/executing-plans/SKILL.md` (fenced reviewer-prompt block, and one sentence appended to orchestration step 6)

**Depends on:** nothing.

**No new tests required:** this is a skill-contract document. Two existing suites already constrain it — `test_executing_plans_skill.py` (required phrases and the requirements→design→plan ordering) and `test_workflow_transition_preflight.py` (preflight phrases within ±500 chars of each transition call). Both are run as gates in Steps 4–5.

**Step 1: Read the current block to confirm the anchor text**

Read `worker/skills/executing-plans/SKILL.md` lines 170–220. Confirm the fenced reviewer prompt begins with `You are reviewing an implementation plan with fresh eyes.` and ends with `plan or propose fixes. Do not edit any files.`, and that orchestration step 6 contains `Reject unsupported findings without changing` / `operator requirements, design, or plan.`

If the text differs from what this plan assumes, STOP and report — do not guess at the replacement boundaries.

**Step 2: Replace the prompt body**

Replace the text **between** the fences (leave both ``` fences in place). The old body runs from `You are reviewing an implementation plan with fresh eyes.` through `plan or propose fixes. Do not edit any files.`

New body — three-space indentation is load-bearing, the block sits inside numbered list item 3:

```
   You are reviewing an implementation plan with fresh eyes. You have NOT seen
   this plan before and know nothing about how it was written.

   Read these files:
   - Requirements (operator-approved): <REQUIREMENTS_MD_PATH>
   - Design (approved): <DESIGN_MD_PATH>
   - Plan (human): <PLAN_MD_PATH>
   - Plan (machine): <PLAN_JSON_PATH>

   Your verdict answers exactly two questions:
   - FIDELITY: does the plan implement the operator's approved requirements
     and design with no drift?
   - EFFICACY: will executing the plan exactly as written succeed?

   Evaluate in this order:
   1. Requirements → design fidelity: every operator requirement is preserved;
      nothing is weakened, reinterpreted, silently deferred, or invented.
   2. Design → plan fidelity: every approved design component has a task and
      acceptance/test coverage; nothing is silently dropped.
   3. Technical executability:
   - Task ordering: depends_on is correct and cycle-free; foundations before
     dependents; tests after the code they cover.
   - allowed_files completeness: each task lists EVERY file its steps touch
     (an omission blocks the task mid-execution under the file guard).
   - Step granularity: steps are mechanical (exact paths, commands, code) —
     not hand-waving like "add validation".
   - TDD structure where the task involves executable code (RED→GREEN→stage),
     or an explicit "No tests required: [reason]".
   - JSON↔markdown consistency and schema validity.
   4. Latent-defect hunt — the findings this review exists for. Trace every
      code block in the plan as if you were the compiler and then the runtime:
      follow return values, types, and control flow. Open the current source
      files and verify every identifier the plan asserts — function names and
      signatures, DB columns, schema fields, config keys, file paths,
      commands. Hunt these archetypes specifically:
   - a refactor that silently drops or changes a return value or side effect
     in a way no type checker or existing test will flag;
   - symbols, columns, APIs, fixtures, or files that do not exist as written
     in the current source;
   - a file a step modifies that is missing from that task's allowed_files;
   - a test or verification step that cannot fail when the behavior it
     guards is broken;
   - a partial update to a set that must change together (version
     declarations, generated artifacts, human/machine plan pairs) — find the
     repo's consistency checks and confirm every member is covered.

   Classify every candidate finding with this decision test. A finding is
   MATERIAL only if executing the plan exactly as written would:
   (a) violate an operator requirement or approved design decision; or
   (b) ship wrong behavior that no step, test, or check in the plan would
       catch; or
   (c) make a step, command, or test fail or be unrunnable as written; or
   (d) leave a required verification unable to detect the failure it exists
       to catch.
   A MATERIAL finding must cite the artifact and the source evidence you
   personally verified. A suspicion you did not verify is not MATERIAL.
   Everything else is an OBSERVATION: style, redundancy, count or wording
   mismatches with no behavioral effect, tests that are weaker than ideal
   but still fail on real regressions, hypothetical edge cases with no
   concrete failure path. Report at most 5 observations, one line each;
   they are non-blocking and have zero effect on the verdict.

   Verdict rubric — apply it mechanically:
   - SOLID: zero MATERIAL findings. SOLID means "no material defect found",
     not "nothing could be improved". If everything you found is an
     observation, the verdict IS SOLID.
   - HAS-ISSUES: one or more MATERIAL findings.
   A verdict that contradicts your own findings is an invalid review.

   Output the verdict line (SOLID or HAS-ISSUES), then MATERIAL findings
   grouped Critical / Important, each citing the specific task/step and the
   verified evidence, then "Observations (non-blocking)". IDENTIFY PROBLEMS
   ONLY — do not rewrite the plan or propose fixes. Do not edit any files.
```

**Step 3: Append the coherence sentence to orchestration step 6**

Find this text in step 6 (outside the fenced block):

```
   artifacts and cited current source. Reject unsupported findings without changing
   operator requirements, design, or plan.
```

Append immediately after `operator requirements, design, or plan.` (same paragraph, new sentence):

```
 Apply the reviewer's MATERIAL decision test yourself: only findings that survive it gate execution. Observations are non-blocking and never, alone, justify changing any artifact.
```

This makes step 9's pre-existing `Repeat only while current evidence identifies a material defect` refer to a standard the reviewer has now actually been given.

**Step 4: Run the skill-contract gates**

Run:
```bash
cd commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_executing_plans_skill.py tests/test_workflow_transition_preflight.py -v
```

Expected: all pass. These assert the four `<*_MD_PATH>` placeholders, the phrase `operator-approved requirements`, the blindness phrases, the `requirements → design` < `design → plan` < `technical executability` ordering, `reviewer output is evidence, not authority`, `holistic invariant audit`, and that `Revise / Proceed / Abort` and `After 3 rounds` remain absent — plus the ±500-char preflight windows around each transition call.

If any assertion fails, the edit moved text it should not have. Re-read the failing assertion and correct the edit — do not weaken the test.

**Step 5: Verify the intended content actually landed**

Run — `|| true` on each, because `grep -c` exits 1 on a zero count, which is the *success* condition for the third grep and must not read as a failed step:

```bash
grep -c 'MATERIAL' worker/skills/executing-plans/SKILL.md || true
grep -c 'Observations (non-blocking)' worker/skills/executing-plans/SKILL.md || true
grep -c 'Hidden risks, ambiguities' worker/skills/executing-plans/SKILL.md || true
```

Expected, three numbers in order: `MATERIAL` ≥ 5, `Observations (non-blocking)` = 1, `Hidden risks, ambiguities` = **0** (the nitpick license is gone).

**Step 6: Stage changes**

Run:
```bash
git add worker/skills/executing-plans/SKILL.md
```

Expected: no output.

---

## Task 3: Version bump (four files) + CHANGELOG

**Files:**
- Modify: `commander/pyproject.toml`
- Modify: `worker/.claude-plugin/plugin.json`
- Modify: `.claude-plugin/marketplace.json`
- Modify: `worker/.codex-plugin/plugin.json`
- Modify: `CHANGELOG.md`
- Modify (conditional, Step 1 only): `Makefile` — pre-authorized in `allowed_files` so the conditional branch cannot stall mid-task

**Depends on:** nothing.

**No tests required:** version strings and release notes are metadata. `test_version_consistency.py` is the gate and is run in Step 7.

**Step 1: Confirm the Makefile is not a fifth version site**

Past releases (v1.0.6, v1.0.12) treated the Makefile as a version site with a hardcoded `.../ironclaude/<VERSION>/hooks` path. Investigation indicates it now derives the version at runtime. Verify rather than assume:

Run:
```bash
grep -nE 'ironclaude/ironclaude/[0-9]+\.[0-9]+\.[0-9]+' Makefile || echo "OK: Makefile has no hardcoded version"
```

Expected: `OK: Makefile has no hardcoded version`. If a hardcoded version IS found, add `Makefile` to this task's edits, bump it too, and note the deviation — `test_version_consistency.py` also checks the Makefile when such a path exists.

**Step 2: Bump `commander/pyproject.toml`**

Change line 4 from `version = "1.0.24"` to `version = "1.0.25"`.

**Step 3: Bump `worker/.claude-plugin/plugin.json`**

Change line 3 from `"version": "1.0.24",` to `"version": "1.0.25",`. Preserve all other fields and formatting.

**Step 4: Bump `.claude-plugin/marketplace.json`**

Change the ironclaude entry (`plugins[0]`, approximately line 10) from `"version": "1.0.24",` to `"version": "1.0.25",`. If multiple plugin entries exist, change ONLY the ironclaude one.

**Step 5: Generate a fresh cachebuster**

Run:
```bash
date -u +%Y%m%d%H%M%S
```

Record the output — this is the cachebuster. It must match `[a-z0-9-]+` (a bare digit string does). Generating it at execution time avoids shipping a stale hardcoded example.

**Step 6: Bump `worker/.codex-plugin/plugin.json`**

Change line 3 from `"version": "1.0.24+codex.20260721013248",` to `"version": "1.0.25+codex.<CACHEBUSTER>",` substituting the value from Step 5. `test_version_consistency.py` strips exactly one `+codex.<[a-z0-9-]+>` suffix to recover the release version.

**Step 7: Verify all version sources agree**

Run:
```bash
cd commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_version_consistency.py -v
```

Expected: PASS. A failure lists the mismatching sources — fix whichever file was missed. This is the gate that catches a partial bump.

**Step 8: Update CHANGELOG.md**

The file currently opens (after the header blockquote) with:

```markdown
## [Unreleased]

_Nothing yet._

## 1.0.24: workflow durability, Codex compatibility, and Commander hardening
```

Replace that region with — keeping a fresh empty `[Unreleased]` at top, per repo convention:

```markdown
## [Unreleased]

_Nothing yet._

## 1.0.25: plan-review verdict calibration

### Fixed

- **Plan-review verdict calibration.** The tier-up plan review could not converge: a prior Codex session recorded 8 consecutive `HAS-ISSUES` verdicts without ever reaching `SOLID`, and reviewers routinely described a plan as "largely SOLID" while scoring it `HAS-ISSUES`. The reviewer prompt never defined `SOLID`, offered a `Minor` severity tier with no stated effect on the verdict, and included an open-ended "hidden risks, ambiguities, or edge cases" bullet that licensed unbounded nitpicking. A materiality standard existed but lived in the orchestrator's instructions where the reviewer never saw it, while `start_execution` gates on the verdict. The reviewer prompt now carries an explicit MATERIAL decision test, a mechanical verdict rubric (`SOLID` = zero material findings; "no material defect found", not "nothing could be improved"), a sharpened latent-defect hunt naming five failure archetypes, and a capped non-blocking `Observations` section where non-material findings land without affecting the verdict. Prompt-only change: verdict values, MCP schema, and `dist/` are untouched.

### Changed

- Completed the state-manager working set so the committed tree compiles and starts: `src/session-identity.ts` (imported by four already-committed modules), `src/db.ts` (`getLatestTierUpReview()`), and `worker/.mcp.json` (`IRONCLAUDE_CLIENT=claude`, required at MCP module load) now ship together with the code that depends on them.

## 1.0.24: workflow durability, Codex compatibility, and Commander hardening
```

Preserve the existing 1.0.24 section and everything below it exactly. If actual line numbers differ, work by section content.

**Step 9: Stage changes**

Run:
```bash
git add commander/pyproject.toml worker/.claude-plugin/plugin.json .claude-plugin/marketplace.json worker/.codex-plugin/plugin.json CHANGELOG.md
```

Expected: no output.

---

## Task 4: Full regression gates, final staging, dual-client ship runbook

**Files:**
- Modify (only if a gate reveals a needed correction): `docs/plans/2026-07-21-review-verdict-calibration.md`
- Stage: `docs/plans/2026-07-21-review-verdict-calibration-design.md`, `docs/plans/2026-07-21-review-verdict-calibration.plan.json`
- Modify (conditional, Step 4 only, if the bundle is stale): `worker/mcp-servers/state-manager/dist/index.js`

All four are listed in this task's `allowed_files` — staged-only and conditional paths included, so neither the `git add -f` in Step 6 nor a stale-bundle rebuild in Step 4 can stall mid-task.

**Depends on:** Tasks 1, 2, 3.

**No tests required:** this task runs existing suites and stages. It authors no new behavior.

**Step 1: Commander full suite**

Run:
```bash
make -C commander test
```

Expected: `2003 passed`, 0 failed. Runtime ~10 minutes. This plan adds no test cases to any suite — any deviation from 2003 is a regression to investigate, not an expected increase.

**Step 2: state-manager full suite**

Run:
```bash
cd worker/mcp-servers/state-manager && npx vitest run
```

Expected: `134 passed`, 0 failed.

**Step 3: Hook suite**

Run:
```bash
bash worker/hooks/tests/test-workflow-transition-idempotency.sh
```

Expected: `44 pass, 0 fail`.

**Step 4: Verify `dist/index.js` was built from the current src**

This plan edits no `src/*.ts`. But a diff-based check cannot establish that: the staged release diff legitimately contains six pre-existing non-test `src/*.ts` files (`index.ts`, `state-machine.ts`, `tool-dispatch.ts`, `read-tools.ts`, `write-tools.ts`, `runtime-fingerprint.ts`), and Task 1 stages two more. Any `git diff --staged | grep src` proxy would therefore always report "src changed" and could never take its own success branch.

Test the property that actually matters — does the bundle contain symbols from the newest staged sources. Both identifiers below were verified by reading the source, not inferred from a summary: `captureRuntimeFingerprintFromPaths` is the real export at `runtime-fingerprint.ts:44`, and `getLatestTierUpReview` is imported at `write-tools.ts:39`. The bundle is non-minified esbuild (`package.json`'s bundle script passes no `--minify`), so identifiers survive into `dist/index.js`.

```bash
grep -c 'captureRuntimeFingerprintFromPaths' worker/mcp-servers/state-manager/dist/index.js || true
grep -c 'getLatestTierUpReview' worker/mcp-servers/state-manager/dist/index.js || true
```

Expected: both counts ≥ 1, proving `dist/index.js` was built from the current tree.

**Step 4b (CONDITIONAL — skip entirely if both counts were ≥ 1):** the bundle is stale. Rebuild, stage, and **re-verify** — do not proceed on an unverified rebuild:

```bash
cd worker/mcp-servers/state-manager && npm run build && cd -
git add worker/mcp-servers/state-manager/dist/index.js
grep -c 'captureRuntimeFingerprintFromPaths' worker/mcp-servers/state-manager/dist/index.js
grep -c 'getLatestTierUpReview' worker/mcp-servers/state-manager/dist/index.js
```

Expected: both re-verified counts ≥ 1. `dist/index.js` is the only tracked dist file and is what the plugin actually executes.

**Step 5: Review the complete staged set**

Run:
```bash
git status --short
git diff --staged --stat
```

Expected: every intended file staged; no unstaged modifications to `worker/mcp-servers/state-manager/src/`, `worker/skills/`, or the version files. `docs/` files require `git add -f` (the directory is gitignored) and follow existing repo precedent.

**Step 6: Stage plan and design docs**

Run:
```bash
git add -f docs/plans/2026-07-21-review-verdict-calibration-design.md docs/plans/2026-07-21-review-verdict-calibration.md docs/plans/2026-07-21-review-verdict-calibration.plan.json
```

Expected: no output.

**Step 7: Present the dual-client ship runbook to the operator**

Execution ends here. Commit and push are operator-gated. Present this sequence:

```
SHIP RUNBOOK — v1.0.25 (both clients)

Why both: the two installs currently disagree. Codex runs the working tree
(local-path marketplace). Claude's cache still serves the old prompt with
"Revise / Proceed / Abort". Same repo, two running realities — that is why a
fix can look shipped and not be. One push fixes both, because the Codex
marketplace points at this repo and Claude's points at GitHub.

  1. [operator] git commit    (no Co-Authored-By trailer)
  2. [operator] git push origin main
  3. [Claude]   /plugin  → update ironclaude marketplace + plugin
                The updater keys off marketplace.json's version.
                WITHOUT the version bump, pushing ships nothing.
  4. [Claude]   make deploy-hooks
                MUST run AFTER step 3 so PLUGIN_CACHE_VERSION resolves to
                1.0.25 and the updated hooks reach the new cache.
  5. [Claude]   Fully quit and relaunch Claude Code (reloads MCP + skills)
  6. [Codex]    Refresh cachebuster, then:
                codex plugin add ironclaude@ironclaude
  7. [Codex]    Fully quit and relaunch Codex, reopen the same native task,
                verify with run_diagnostics(expected_runtime)

VERIFICATION GATE — evidence, not assertion. Both must pass:

  # Claude cache
  grep -c 'MATERIAL' \
    ~/.claude/plugins/cache/ironclaude/ironclaude/1.0.25/skills/executing-plans/SKILL.md
  grep -c 'Revise / Proceed / Abort' \
    ~/.claude/plugins/cache/ironclaude/ironclaude/1.0.25/skills/executing-plans/SKILL.md

  # Codex cache (path carries the +codex.<cachebuster> suffix)
  grep -c 'MATERIAL' \
    ~/.codex/plugins/cache/ironclaude/ironclaude/1.0.25+codex.*/skills/executing-plans/SKILL.md
  grep -c 'Revise / Proceed / Abort' \
    ~/.codex/plugins/cache/ironclaude/ironclaude/1.0.25+codex.*/skills/executing-plans/SKILL.md

  PASS: MATERIAL >= 1 AND "Revise / Proceed / Abort" == 0, in BOTH caches.
  Anything else means the ship did not take, regardless of install output.
```

---

## Implementation Notes

- **Wave structure — run Wave 1 SERIALLY, not in parallel.** Tasks 1, 2, and 3 touch disjoint *file* sets and carry no `depends_on` edges, so the MCP releases all three as Wave 1. But all three end with `git add`, and concurrent `git add` invocations contend on `.git/index.lock` — a collision produces `Unable to create '.git/index.lock': File exists` and halts the task. Use `--mode=inline` or `--mode=subagent-sequential`. Do **not** use `--mode=subagent-parallel` for this plan. Task 4 is Wave 2.
- **Pin the Python interpreter.** All pytest gates use `commander/.venv/bin/python`, matching `commander/Makefile:4` (`PYTHON := $(VENV)/bin/python`). The commander project isolates its environment precisely because the global environment has a known `anyio` conflict; a bare `python` can resolve to that global interpreter and fail a gate for reasons unrelated to the edits it gates.
- **What this plan does NOT do.** No commit, no push, no install. Professional mode stages only; the operator ships.
- **Circuit breaker deferred.** `docs/plans/2026-07-21-tier-up-circuit-breaker*` remains valid as a later backstop for plans that genuinely cannot converge. This plan removes the *cause* of false `HAS-ISSUES`; the breaker would only *bound* it. Revisit after observing post-ship review behavior.
- **Codex episodic memory is a separate loop.** Verified this session: `episodic-memory/src/sync-cli.ts:61` hardcodes `~/.claude/projects` as the sole source, nothing reads `~/.codex/sessions/`, and the known Codex thread id appears nowhere in the archive. The schemas differ enough to need a translation adapter, not a config change. Roadmap Wave 8, zero implementation today.
- **Post-ship behavioral check.** The next two real plan reviews are the acceptance test for this change. A plan whose findings are all cosmetic should return `SOLID` on round 1. If either of the next two plans exceeds two rounds with zero material findings, the calibration needs another pass.
