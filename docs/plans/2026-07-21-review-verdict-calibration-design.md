# Plan-Review Verdict Calibration + Working-Tree Completion Design

> **Created:** 2026-07-21
> **Status:** Design Complete
> **Ships as:** v1.0.25
> **Supersedes as first fix:** `docs/plans/2026-07-21-tier-up-circuit-breaker-design.md` (circuit breaker remains valid as a later backstop; this design removes the *cause* the breaker was written to *bound*)

## Summary

IronClaude's tier-up plan review does not converge. A prior Codex session made 15 `submit_tier_up_review` calls including a run of **8 consecutive HAS-ISSUES**, never reaching SOLID. This session reproduced it: 5 consecutive HAS-ISSUES on a 3-task plan, with rounds 4 and 5 explicitly writing *"the plan is largely SOLID"* / *"well-crafted… correct conventions… correct allowed_files"* and scoring HAS-ISSUES anyway. Round 3's findings were genuine regressions introduced by round 2's fixes — the loop was net-negative for two rounds.

The cause is verdict miscalibration in the reviewer prompt, not plan quality. The prompt never defines `SOLID`, offers a `Minor` severity tier with no stated effect on the verdict, and includes an open-ended *"hidden risks, ambiguities, or edge cases"* bullet that licenses unbounded nitpicking. A materiality standard already exists in the skill — but it lives in the *orchestrator's* instructions at line 239, where the reviewer never sees it, while `start_execution` gates on the *verdict*. The standard is written down and structurally unable to take effect.

This design closes that gap with a prompt-only rewrite, and simultaneously completes an incomplete staged commit that would otherwise ship a crashing plugin.

## Architecture

Three changes, one release:

1. **Complete the staged set.** The working tree holds sound, fully-green work (2003/2003 pytest, 134/134 vitest, 44/44 hook tests) whose staged subset does not compile. Three ship-together dependencies sit outside the index.
2. **Recalibrate the reviewer prompt.** Replace the fenced prompt block in `worker/skills/executing-plans/SKILL.md` so the reviewer receives an explicit MATERIAL decision test, a mechanical verdict rubric, a sharpened latent-defect hunt, and a non-blocking `Observations` bucket for nitpicks.
3. **Ship it.** Bump the four version files that must move together, update CHANGELOG, run full gates, stage. Commit/push remain operator-gated.

Deliberately **prompt-only** for change 2: no new verdict values, no TypeScript, no `dist/` rebuild. `TIER_UP_VERDICTS = ['SOLID','HAS-ISSUES','top-tier-self']` is untouched. Nitpicks are demoted inside the report body rather than given a new verdict.

### Why not the circuit breaker first

A consecutive-HAS-ISSUES circuit breaker (designed separately, artifacts retained) *bounds* the loop. This design *removes* it. The breaker remains worthwhile as a backstop for plans that genuinely cannot converge, but shipping it first would leave the generator of false HAS-ISSUES verdicts in place and simply cap the wasted rounds at 2.

### Why the prior fix didn't take

A prior "Wave 0" effort improved the orchestration around the prompt — requirements artifact in the review packet, requirements→design→plan ordering, *"reviewer output is evidence, not authority"*, holistic invariant audit on first HAS-ISSUES, regenerate-not-patch. Those changes are real and are KEPT. They reached the Codex install (byte-identical to the working tree, cachebusted `1.0.24+codex.20260721013248`) but never reached Claude, because the marketplace updater keys off `marketplace.json`'s version and no version bump was ever made. Critically: **the 8-consecutive-HAS-ISSUES Codex run happened with those orchestration fixes live** — direct evidence they were necessary but not sufficient, and that the residual defect is calibration.

## Components

### 1. Working-tree completion

`git add` these seven paths. Each is a compile-time or startup dependency of already-staged code, or a test pairing with an already-staged skill change:

| Path | Why it must ship together |
|---|---|
| `worker/mcp-servers/state-manager/src/session-identity.ts` | Imported by staged `index.ts`, `tool-dispatch.ts`, `read-tools.ts`, `runtime-fingerprint.ts` — build fails without it |
| `worker/mcp-servers/state-manager/src/__tests__/session-identity.test.ts` | Coverage for the above |
| `worker/mcp-servers/state-manager/src/__tests__/tool-dispatch.test.ts` | Coverage for staged `tool-dispatch.ts` |
| `worker/mcp-servers/state-manager/src/db.ts` | `getLatestTierUpReview()` imported at line 39 / called at line 769 of staged `write-tools.ts` — `tsc` fails without it |
| `worker/mcp-servers/state-manager/src/tools/write-tools.tier-up.test.ts` | Covers the new verdict validation and latest-HAS-ISSUES gating |
| `worker/.mcp.json` | Adds `IRONCLAUDE_CLIENT=claude`. New `index.ts` calls `parseIronClaudeClient(process.env.IRONCLAUDE_CLIENT)` **at module load** and throws when unset. The Codex manifest equivalent is already staged; without this the MCP server dies at startup for every Claude Code user |
| `commander/tests/test_writing_plans_skill.py` | Pairs with staged `writing-plans/SKILL.md` changes |

### 2. Reviewer prompt replacement

Replace the fenced block at `worker/skills/executing-plans/SKILL.md` **lines 176–207** (inclusive of both fences) with the text in Implementation Notes below. Structure of the change:

- **Framing** — states up front that the verdict answers exactly two questions: FIDELITY (does the plan implement approved requirements and design without drift) and EFFICACY (will executing it exactly as written succeed).
- **Evaluation order preserved** — items 1/2/3 keep `requirements → design` before `design → plan` before `technical executability`, because `test_executing_plans_skill.py` asserts that ordering.
- **New item 4: latent-defect hunt.** Instructs the reviewer to trace code blocks as compiler-then-runtime, to open current source and verify every asserted identifier, and names five failure archetypes to hunt.
- **MATERIAL decision test.** A finding blocks only if executing the plan as written would (a) violate a requirement or design decision, (b) ship wrong behavior nothing in the plan would catch, (c) make a step/command/test fail or be unrunnable, or (d) leave a required verification unable to detect the failure it exists to catch. Unverified suspicions are explicitly not material.
- **Observations bucket.** Everything else — style, redundancy, count/wording mismatches with no behavioral effect, tests weaker than ideal but still failing on real regressions, hypotheticals with no concrete failure path. Capped at 5, one line each, **zero effect on the verdict**.
- **Mechanical rubric.** `SOLID` = zero material findings, stated as *"no material defect found", not "nothing could be improved"*. `HAS-ISSUES` = one or more. Plus the anti-pathology clause: *"a verdict that contradicts your own findings is an invalid review."*
- **`Minor` tier removed.** No test pins the `Critical / Important / Minor` grouping; material findings group `Critical / Important` only.

### 3. Orchestrator coherence edit

One sentence appended to step 6 (currently lines 213–216), after *"Reject unsupported findings…"*:

> Apply the reviewer's MATERIAL decision test yourself: only findings that survive it gate execution. Observations are non-blocking and never, alone, justify changing any artifact.

This makes step 9's pre-existing *"Repeat only while current evidence identifies a material defect"* reference a standard the reviewer has now actually seen.

### 4. Version + CHANGELOG

Bump to `1.0.25` in all four files `test_version_consistency.py` requires to agree: `commander/pyproject.toml`, `worker/.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json`, `worker/.codex-plugin/plugin.json` (the last as `1.0.25+codex.<fresh-lowercase-stamp>`; the test strips one `+codex.` suffix matching `[a-z0-9-]+`). CHANGELOG follows repo convention: rename `## [Unreleased]` to `## 1.0.25: <title>`, add a fresh `## [Unreleased]` with `_Nothing yet._` above it.

## Data Flow

A review round after this change:

```
executing-plans Step 1.5 dispatches blind reviewer
  → reviewer reads requirements + design + plan.md + plan.json
  → evaluates items 1-3 (fidelity, then executability)
  → item 4: traces code as compiler/runtime, verifies identifiers against live source
  → for each candidate finding, applies MATERIAL test (a/b/c/d)
      material   → Critical/Important findings list, with verified evidence
      not material → Observations (max 5, non-blocking)
  → verdict = (material count == 0) ? SOLID : HAS-ISSUES
  → commander records verdict via submit_tier_up_review
      SOLID       → Step 2, start_execution
      HAS-ISSUES  → commander independently verifies each material finding,
                    rejects unsupported ones, then holistic invariant audit,
                    then retreat (requirements conflict) or regenerate (plan defect)
```

The behavioral delta: a plan whose only defects are cosmetic now returns SOLID on round 1 instead of cycling indefinitely. A plan with a real latent defect still returns HAS-ISSUES — and the archetype list plus source-verification mandate make it *more* likely to be caught than before.

## Error Handling

- **Reviewer returns a verdict contradicting its own findings** (the round-4/5 pathology). Outlawed explicitly in the prompt. The commander independently verifies findings under step 6 regardless, so a miscalibrated verdict is recoverable rather than fatal.
- **Reviewer reports zero findings but says HAS-ISSUES.** Step 6's verification finds nothing to substantiate; unsupported findings are rejected without changing any artifact.
- **Prompt edit breaks a test assertion.** `test_executing_plans_skill.py` and `test_workflow_transition_preflight.py` both constrain this file. Every required phrase, the three-way ordering, and the ±500-char preflight windows are preserved because the edit stays inside the fenced block plus one sentence in step 6. Verified before staging by running both suites.
- **Incomplete commit.** Mitigated by the working-tree completion step. Verification is a clean full-suite run *after* staging, not before.
- **Version files drift.** `test_version_consistency.py` fails the build if any of the four disagree.

## Testing Strategy

No new test files. This change is a prompt rewrite plus staging plus version metadata; correctness is established by existing suites plus one behavioral validation.

**Regression gates (must all pass before staging):**

| Gate | Command | Baseline |
|---|---|---|
| Skill contract | `pytest commander/tests/test_executing_plans_skill.py commander/tests/test_workflow_transition_preflight.py -v` | must stay green |
| Version consistency | `pytest commander/tests/test_version_consistency.py -v` | must go green at 1.0.25 |
| Commander full | `make -C commander test` | 2003/2003 |
| state-manager full | `cd worker/mcp-servers/state-manager && npx vitest run` | 134/134 |
| Hooks | `worker/hooks/tests/test-workflow-transition-idempotency.sh` | 44/44 |
| Build integrity | `cd worker/mcp-servers/state-manager && npx tsc --noEmit` | must compile with the completed staged set |

**Behavioral validation (operator-run, after ship):** the next real plan review is the test. A plan whose findings are all cosmetic should return SOLID on round 1. Record the round count for the first two post-ship plans; if either exceeds two rounds with zero material findings, the calibration needs another pass.

**No tests required for:** the CHANGELOG entry and version-string edits (metadata; covered by `test_version_consistency.py`).

## Implementation Notes

### Replacement prompt text

Replaces `worker/skills/executing-plans/SKILL.md` lines 176–207 inclusive. Three-space indentation is load-bearing — the block sits inside numbered list item 3.

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

### Validation of the rewrite against known time bombs

Each real latent defect caught during this session, traced against the new text:

| Time bomb | Caught by | Material under |
|---|---|---|
| `applyArtifacts` block-body refactor silently dropped the `Partial<Session>` return; return type is `\| void` so `tsc` does not error and no test asserts it | Archetype 1 verbatim, plus the compiler/runtime tracing directive | (b) |
| Test fixture invented SQL columns `design_path`/`created_at`; production is `design_file`/`registered_at` | Archetype 2 plus mandatory source-verification of DB columns | (c) |
| Version bump covered 2 of the 4 files `test_version_consistency.py` requires | Archetype 5 (change-together sets) plus "find the repo's consistency checks" | (c) |

Clause (c) is deliberately unconditional rather than "and undetected" — a defect that merely crashes mid-execution is still material; the review should not wave it through on the grounds that the failure would eventually surface.

Correspondingly demoted to Observations: test-count mismatches between documents, hardcoded example timestamps, "this test would also pass in RED state", "temporary `console.log` is fragile".

### Scope boundaries

Not in scope: the consecutive-verdict circuit breaker (separate design, artifacts retained at `docs/plans/2026-07-21-tier-up-circuit-breaker*`), Codex episodic-memory ingestion (roadmap Wave 8), the unimplemented `codex-review-lifecycle-convergence` design, and the `requirements_file` field missing from `PlanJson`/`types.ts` (documented, queued as roadmap Wave 11; runtime tolerates the unknown field).

### Ship sequence after execution completes

Operator-gated. Commit and push are not part of plan execution.

1. `/plugin` → update the ironclaude marketplace and plugin (the GitHub `robertphyatt/ironclaude` marketplace updater keys off `marketplace.json`'s version — without the bump, pushing ships nothing to Claude).
2. `make deploy-hooks` — **after** the install, so `PLUGIN_CACHE_VERSION` resolves to 1.0.25 and the updated `mcp-state-logger.sh` / `skill-state-bridge.sh` reach both the stable dir and the new cache.
3. Restart Claude Code so the MCP server and skills reload.
4. Verify shipped: the new cache's `skills/executing-plans/SKILL.md` contains `MATERIAL` and `Observations (non-blocking)`, and no longer contains `Revise / Proceed / Abort`.
5. Codex side: re-cachebust, `codex plugin add ironclaude@ironclaude` from the local marketplace, fully quit/relaunch, reopen the same native task, verify via `run_diagnostics(expected_runtime)`.
