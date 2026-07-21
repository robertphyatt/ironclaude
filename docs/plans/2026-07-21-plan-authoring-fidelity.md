# Plan-Authoring Fidelity Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use `ironclaude:executing-plans` to implement this plan task-by-task.

**Goal:** Ship v1.0.26 — restore the v1.1 design's plan-parity wording to MP-W10's "semantically identical" requirement, add live-source grounding and a review-history prohibition to the plan-authoring skills, and land it on both clients.

**Requirements:** `docs/plans/2026-07-19-multi-provider-v1-1-requirements.md` (MP-W02, MP-W10, MP-R07)

**Architecture:** Three instruction-and-test changes plus a design amendment. No new MCP tool, no schema change, no renderer, no `dist` rebuild. Enforcement teeth already exist: the v1.0.25 reviewer prompt's archetype 2 (source-verified symbol checking) and the skill-contract test suites.

**Tech Stack:** Markdown skill contracts, Python pytest (phrase-assertion pattern), TypeScript/vitest (regression only), git.

**Design:** `docs/plans/2026-07-21-plan-authoring-fidelity-design.md`

**Live-source grounding (verified before authoring, per the contract this plan implements):**
- Skill-contract tests read `REPO_ROOT / "worker" / "skills" / <name> / "SKILL.md"` via a `_skill_text()`/`_read()` helper; assertions are lowercased phrase checks with positive and forbidden-token forms (`test_writing_plans_skill.py:13-45`, `test_executing_plans_skill.py:10-50`).
- writing-plans Phase 1 Step 1 ends at `SKILL.md:119`; Step 5.6 parity audit exists.
- executing-plans step 8 plan-only-defect paragraph is at `SKILL.md:277-280`.
- Design edit sites: lines 106, 107, 108, 303, 417 (grounded verbatim).
- All four version files currently `1.0.25`; codex carries `+codex.20260721191336`.

---

## Task 1: Amend the v1.1 design to MP-W10 semantic-parity wording

**Files:**
- Modify: `docs/plans/2026-07-19-multi-provider-v1-1-design.md`

**Depends on:** nothing.

**No tests required:** design document (prose). The correctness check is that the amended wording matches MP-W10's "semantically identical" requirement, verified by Task 6's review of the diff and by the absence of any remaining byte-parity/renderer language.

**Step 1: Restate line 106** — replace the canonical-PlanJson / duplicated-mirror sentence with the semantic-parity contract:

Old (line 106):
```
- PlanJson is the single canonical plan representation. Normative behavior is stored once in direct top-level, task, and step fields, including complete step instructions, commands, expected results, tests, rejection cases, and acceptance conditions. The schema rejects duplicated semantic mirrors such as a second nested normative-contract object; repair code never patches two JSON copies.
```
New:
```
- The human and machine plans express the same normative behavior and must remain semantically identical (MP-W10): the same requirement/design coverage, task IDs, dependencies, allowed files, ordered steps and commands, tests, and expected results. Neither representation may introduce, omit, or supersede behavior present in the other.
```

**Step 2: Restate line 107** — replace the deterministic-rendering / byte-parity sentence:

Old (line 107):
```
- Human Markdown is a deterministic rendering of that one normalized PlanJson. Planning and revision edit the machine object once, regenerate the complete human file, and validate byte parity; they never hand-edit both representations. Neither artifact may introduce, omit, or supersede behavior from the canonical object.
```
New:
```
- Planning and revision keep both representations in lockstep and run the writing-plans parity audit (requirement/design coverage, task IDs, dependencies, allowed files, ordered steps and commands, tests and expected results) before mark_plan_ready. A revision that changes behavior updates both representations coherently rather than patching one.
```

**Step 3: Restate line 108** — replace the strict-schema / byte-parity clause while RETAINING the live-source grounding clause (that clause is a real MP-W10 obligation this release implements):

Old (line 108):
```
- Before blind review, strict schema validation rejects unknown mirror containers and incomplete step contracts, deterministic parity compares actual human bytes with a fresh render, and live-source grounding fails unresolved files, symbols, or incompatible signatures.
```
New:
```
- Before blind review, the semantic parity audit confirms the human and machine plans agree, and live-source grounding fails plans that assert unresolved files, symbols, columns, keys, signatures, or commands. Plan facts are verified against current source, not inferred from design prose or an earlier plan.
```

**Step 4: Restate line 303** (Testing Strategy) — replace the canonical-object / regenerate-once bullet:

Old (line 303):
```
- PlanJson rejects duplicated normative mirrors; a revision mutates one canonical machine object and regenerates human Markdown exactly once
```
New:
```
- Human and machine plans stay semantically identical; a revision updates both coherently and the parity audit rejects omitted, added, or contradictory task behavior, dependencies, files, tests, and acceptance conditions
```

**Step 5: Restate line 417** (acceptance criterion 19) — replace the byte-parity clause:

Old (line 417):
```
19. Plans are grounded in inspected live code, tests, schemas, and call sites; PlanJson stores each normative contract once without mirrored copies; and automated byte-parity checks prove the human plan is the deterministic rendering of that canonical machine object.
```
New:
```
19. Plans are grounded in inspected live code, tests, schemas, and call sites; the human and machine plans remain semantically identical (MP-W10); and the writing-plans parity audit rejects any divergence in requirement coverage, task IDs, dependencies, files, steps, tests, or acceptance conditions.
```

**Step 6: Verify no byte-parity/renderer language remains**

Run:
```bash
grep -n 'byte parity' docs/plans/2026-07-19-multi-provider-v1-1-design.md || echo "OK: no byte-parity language remains"
```
Expected: `OK: no byte-parity language remains`

Run:
```bash
grep -n 'deterministic rendering' docs/plans/2026-07-19-multi-provider-v1-1-design.md || echo "OK: no renderer language remains"
```
Expected: `OK: no renderer language remains`

**Step 7: Confirm live-source grounding language survived**

Run:
```bash
grep -c 'live-source grounding' docs/plans/2026-07-19-multi-provider-v1-1-design.md
```
Expected: `1` or higher (the MP-W10 grounding obligation must remain).

**Step 8: Stage changes**

`docs/` is gitignored (`.gitignore:24`) and this design doc is untracked, so `-f` is required or `git add` errors non-zero:
```bash
git add -f docs/plans/2026-07-19-multi-provider-v1-1-design.md
```
Expected: no output.

---

## Task 2: Add live-source grounding + review-history prohibition to writing-plans

**Files:**
- Modify: `worker/skills/writing-plans/SKILL.md`

**Depends on:** nothing.

**No tests required in this task:** the skill-contract tests that pin this content are authored in Task 4 (they must exist as a separate reviewable unit and depend on both skill edits). This task's own verification is the grep checks in Steps 3–4.

**Step 1: Add a grounding subsection after Phase 1 Step 1**

Insert immediately after the `requirements_file` paragraph that ends at `SKILL.md:119` (before `### Phase 2`):

```markdown
### Step 1.6: Ground every plan fact in live source (REQUIRED)

Before writing any task, inspect the current implementation, tests, schemas, and
call sites that constrain it. Every file path, function name, signature, DB column,
config key, and command the plan asserts MUST be verified against current source —
read it, do not infer it from design prose, an earlier plan, or a summary. A
symbol you did not open does not exist for planning purposes. Speculative
replacement snippets are not implementation authority; any indispensable code
fragment must be derived from and checked against the current source contract.

This is verified rather than inferred: if a plan names `foo()` at `bar.py:42`, open
`bar.py:42` and confirm `foo` is there with the signature the plan assumes.
```

**Step 2: Add a review-history prohibition to the plan-document instructions**

Insert a new bullet in the Key Principles list at the end of the skill (after the existing `**Explicit skill invocation**` principle):

```markdown
- **No review history in plan artifacts**: A plan (human or machine) MUST NOT contain prior-review findings, verdicts, fix rationale, reviewer-drift audits, or round-by-round obligation tables. Because the human plan is a mandatory blind-reviewer input, any such content reaches the reviewer and breaks blind review (MP-W02, MP-R07). This content already has durable homes — `tier_up_reviews` rows, `retreat` reasons, and workflow-private session state. Never record it in the plan.
```

**Step 3: Verify grounding language landed**

Run:
```bash
grep -c 'Ground every plan fact in live source' worker/skills/writing-plans/SKILL.md
```
Expected: `1`.

Run:
```bash
grep -c 'verified rather than inferred' worker/skills/writing-plans/SKILL.md
```
Expected: `1` or higher.

**Step 4: Verify prohibition language landed**

Run:
```bash
grep -c 'No review history in plan artifacts' worker/skills/writing-plans/SKILL.md
```
Expected: `1`.

**Step 5: Stage changes**

Run:
```bash
git add worker/skills/writing-plans/SKILL.md
```
Expected: no output.

---

## Task 3: Mirror the review-history prohibition into executing-plans regeneration

**Files:**
- Modify: `worker/skills/executing-plans/SKILL.md`

**Depends on:** nothing.

**No tests required in this task:** pinned by Task 4.

**Step 1: Extend the step-8 plan-only-defect paragraph**

The paragraph at `SKILL.md:277-280` currently reads:
```
   - Plan-only defects: regenerate one coherent human/machine plan candidate from
     the unchanged requirements and approved design. Do not apply a patch list.
     Re-stage and **RE-CALL `create_plan` with the revised plan JSON** so the MCP
     reloads `session.plan_json` and rebuilds `wave_tasks`.
```

Append one sentence to that bullet (after `rebuilds \`wave_tasks\`.`):
```
 The regenerated plan carries no prior-review content forward — no findings, verdicts, fix rationale, drift audit, or round-by-round table. That content stays in workflow-private state (`tier_up_reviews`, `retreat` reasons); a plan that embeds it breaks the next blind review (MP-W02).
```

**Step 2: Verify the mirror landed**

Run:
```bash
grep -c 'carries no prior-review content forward' worker/skills/executing-plans/SKILL.md
```
Expected: `1`.

**Step 3: Confirm no unrelated content shifted**

Run:
```bash
grep -c 'RE-CALL' worker/skills/executing-plans/SKILL.md
```
Expected: `1` (the existing create_plan instruction is intact).

**Step 4: Stage changes**

Run:
```bash
git add worker/skills/executing-plans/SKILL.md
```
Expected: no output.

---

## Task 4: Add skill-contract tests pinning grounding, prohibition, and regeneration mirror

**Files:**
- Modify: `commander/tests/test_writing_plans_skill.py`
- Modify: `commander/tests/test_executing_plans_skill.py`

**Depends on:** Tasks 2, 3 (the tests assert content those tasks add; RED before, GREEN after).

**Step 1: RED — add tests to test_writing_plans_skill.py**

Append to `commander/tests/test_writing_plans_skill.py` (uses the existing `_skill_text()` helper and lowercased phrase-assertion pattern):

```python
def test_writing_plans_requires_live_source_grounding():
    text = _skill_text().lower()
    assert "ground every plan fact in live source" in text
    assert "verified rather than inferred" in text


def test_writing_plans_forbids_review_history_in_plan_artifacts():
    text = _skill_text().lower()
    assert "no review history in plan artifacts" in text
    # The prohibition must name the blind-review rationale it protects
    assert "blind-reviewer input" in text or "blind review" in text


def test_writing_plans_does_not_instruct_recording_review_rounds():
    # Negative (design Testing Strategy item 3): the skill must not instruct
    # authors to build the review-history antipatterns that contaminated a prior
    # plan. These exact foundation-plan table headers are NOT used by the
    # prohibition bullet (which names "reviewer-drift audits" and "round-by-round
    # obligation tables"), so asserting their absence is non-tautological and
    # catches reintroduction of the actual contamination vocabulary.
    text = _skill_text().lower()
    assert "regression obligations retained" not in text, \
        "writing-plans must not carry a foundation-style review-obligations table"
    assert "reviewer-driven correction" not in text, \
        "writing-plans must not carry a foundation-style reviewer-drift audit column"
```

**Step 2: RED — add a test to test_executing_plans_skill.py**

Append to `commander/tests/test_executing_plans_skill.py` (uses the existing `_read()` helper):

```python
def test_executing_plans_regeneration_carries_no_review_history():
    text = _read().lower()
    assert "carries no prior-review content forward" in text
```

**Step 3: Confirm the new tests are GREEN (regression-pin pattern)**

Because Tasks 2 and 3 already added the asserted content and this task depends on them, these four tests are GREEN on first run, not RED. This is intentional: the skill edits are the implementation, the tests are regression pins. Run to confirm GREEN:

```bash
commander/.venv/bin/python -m pytest commander/tests/test_writing_plans_skill.py commander/tests/test_executing_plans_skill.py -q
```
Expected: all pass (existing + 4 new).

To confirm the tests actually bite (not vacuous), the reviewer in Task 6 verifies each new assertion maps to a specific line the skill edits added. No separate RED step is manufactured.

**Step 4: Stage changes**

Run:
```bash
git add commander/tests/test_writing_plans_skill.py commander/tests/test_executing_plans_skill.py
```
Expected: no output.

---

## Task 5: Version bump (four files) + CHANGELOG

**Files:**
- Modify: `commander/pyproject.toml`
- Modify: `worker/.claude-plugin/plugin.json`
- Modify: `.claude-plugin/marketplace.json`
- Modify: `worker/.codex-plugin/plugin.json`
- Modify: `CHANGELOG.md`
- Modify (conditional, Step 1 only): `Makefile`

**Depends on:** nothing (metadata; sequences after skill edits but shares no files).

**No tests required:** version strings and release notes are metadata; `test_version_consistency.py` is the gate (Task 6).

**Step 1: Confirm the Makefile is not a fifth version site**

Run:
```bash
grep -nE 'ironclaude/ironclaude/[0-9]+\.[0-9]+\.[0-9]+' Makefile || echo "OK: Makefile has no hardcoded version"
```
Expected: `OK: Makefile has no hardcoded version`. If a version IS found, bump it too.

**Step 2:** Bump `commander/pyproject.toml` line 4 from `version = "1.0.25"` to `version = "1.0.26"`.

**Step 3:** Bump `worker/.claude-plugin/plugin.json` line 3 to `"version": "1.0.26",`.

**Step 4:** Bump `.claude-plugin/marketplace.json` plugins[0] (line 10) to `"version": "1.0.26",`.

**Step 5: Generate a fresh cachebuster**

Run:
```bash
date -u +%Y%m%d%H%M%S
```
Record the 14-digit value (matches `[a-z0-9-]+`, strictly greater than the prior `20260721191336`).

**Step 6:** Bump `worker/.codex-plugin/plugin.json` line 3 to `"version": "1.0.26+codex.<CACHEBUSTER>",` using the Step 5 value.

**Step 7: Verify all version sources agree**

Run:
```bash
cd commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_version_consistency.py -v
```
Expected: PASS.

**Step 8: Update CHANGELOG.md**

The file opens (after the header blockquote) with `## [Unreleased]` / `_Nothing yet._` / `## 1.0.25: plan-review verdict calibration`. Rename `[Unreleased]` to the new version and add a fresh empty `[Unreleased]` above:

```markdown
## [Unreleased]

_Nothing yet._

## 1.0.26: plan-authoring fidelity

### Fixed

- **Plan-authoring fidelity.** A v1.1.0 blind review surfaced two fidelity defects no plan revision could repair: the human plan embedded six rounds of prior-review history (breaking blind review — MP-W02/MP-R07, confirmed empirically when a fresh reviewer reported receiving those rounds through the plan), and the plan pair failed a canonical-PlanJson/byte-parity contract. Investigation showed the operator requirement MP-W10 asks only for "semantically identical" human and machine plans; the v1.1 design had unilaterally escalated that to byte parity with a deterministic renderer, contradicting the already-shipped anti-flailing design's explicit "instruction-and-test contract, not a new renderer" decision. This release restores the v1.1 design's parity wording to the requirement, adds a live-source grounding step to `writing-plans` (every asserted file, symbol, signature, column, key, and command is verified against current source before it is written — the missing half of MP-W10 that produced two fabricated-symbol defects during v1.0.25 authoring), and prohibits plan artifacts from containing review history in both `writing-plans` and the `executing-plans` regeneration path. Instruction-and-test only: no renderer, no MCP tool, no schema change, no `dist` rebuild.

## 1.0.25: plan-review verdict calibration
```

Preserve the 1.0.25 section and everything below it exactly.

**Step 9: Stage changes**

Run:
```bash
git add commander/pyproject.toml worker/.claude-plugin/plugin.json .claude-plugin/marketplace.json worker/.codex-plugin/plugin.json CHANGELOG.md
```
Expected: no output.

---

## Task 6: Full regression gates, final staging, dual-client ship runbook

**Files:**
- Modify (only if a gate reveals a needed correction): `docs/plans/2026-07-21-plan-authoring-fidelity.md`
- Stage: `docs/plans/2026-07-21-plan-authoring-fidelity-design.md`, `docs/plans/2026-07-21-plan-authoring-fidelity.plan.json`

**Depends on:** Tasks 1, 2, 3, 4, 5.

**No tests required:** runs existing suites and stages; authors no new behavior.

**Step 1: Skill-contract + version gates**

Run:
```bash
cd commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_writing_plans_skill.py tests/test_executing_plans_skill.py tests/test_version_consistency.py tests/test_workflow_transition_preflight.py -v
```
Expected: all pass — new grounding/prohibition/regeneration assertions plus the pre-existing skill-contract and preflight guards.

**Step 2: Commander full suite**

Run:
```bash
make -C commander test
```
Expected: exactly `2007 passed` (2003 baseline + 4 new tests from Task 4: 3 in test_writing_plans_skill.py, 1 in test_executing_plans_skill.py), 0 failed.

**Step 3: state-manager full suite**

Run:
```bash
cd worker/mcp-servers/state-manager && npx vitest run
```
Expected: `134 passed`, 0 failed. (No `dist`/`src` changes this release, so this is pure regression.)

**Step 4: Hook suite**

Run:
```bash
bash worker/hooks/tests/test-workflow-transition-idempotency.sh
```
Expected: `44 pass, 0 fail`.

**Step 5: Confirm no dist rebuild needed**

This release changes no `src/*.ts`. Confirm:
```bash
git diff --staged --name-only | grep 'state-manager/dist' || echo "OK: dist untouched — no rebuild"
```
Expected: `OK: dist untouched — no rebuild`.

**Step 6: Review the complete staged set**

Run:
```bash
git status --short
```
Expected: only the intended files staged; nothing unstaged in `worker/skills/`, `commander/tests/`, the version files, or the amended design.

**Step 7: Stage plan and design docs (docs/ is gitignored — requires -f)**

Run:
```bash
git add -f docs/plans/2026-07-21-plan-authoring-fidelity-design.md docs/plans/2026-07-21-plan-authoring-fidelity.md docs/plans/2026-07-21-plan-authoring-fidelity.plan.json
```
Expected: no output.

**Step 8: Present the dual-client ship runbook to the operator**

```
SHIP RUNBOOK — v1.0.26 (both clients)

  1. [operator] git commit    (no Co-Authored-By trailer)
  2. [operator] git push origin main    (GitHub mirror; local installs already read the repo)
  3. [Codex]    codex plugin remove ironclaude@ironclaude
                codex plugin add ironclaude@ironclaude
  4. [Claude]   claude plugin uninstall ironclaude
                claude plugin install ironclaude@ironclaude
  5. [Claude]   make deploy-hooks    (after step 4, so PLUGIN_CACHE_VERSION resolves to 1.0.26)
  6. [both]     Fully restart each client to reload skills + MCP

VERIFICATION GATE — both caches must pass:

  # Claude
  grep -c 'Ground every plan fact in live source' \
    ~/.claude/plugins/cache/ironclaude/ironclaude/1.0.26/skills/writing-plans/SKILL.md
  # Codex
  grep -c 'Ground every plan fact in live source' \
    ~/.codex/plugins/cache/ironclaude/ironclaude/1.0.26+codex.*/skills/writing-plans/SKILL.md

  PASS: count >= 1 in BOTH.
```

Execution ends here. Commit, push, and install are operator-gated.

---

## Implementation Notes

- **Wave structure.** Tasks 1, 2, 3, 5 are independent (Wave 1). Task 4 depends on 2,3 (Wave 2). Task 6 depends on all (Wave 3). Run subagent-sequential or inline — every task ends in `git add`, so avoid parallel `git add` index contention.
- **Follow-up obligation (out of scope, recorded so it is not lost):** `docs/plans/2026-07-19-multi-provider-v1-1-foundation.md:45-69` still contains the six-round review-history table. Stripping it belongs to the foundation loop's own revision, performed under the rule this release establishes. The foundation plan cannot pass a clean blind review until those sections are removed.
- **Behavioral acceptance.** The real test is the next foundation review: under this contract it should surface neither an invented-symbol finding nor review-history contamination. Escalation trigger: if the next authored plan still produces an invented-symbol MATERIAL finding, that is the evidence to build the automated validator (rejected in the design on feasibility) rather than assume the instruction worked.
- **No commit/push/install in this plan.** Professional mode stages only; the operator ships.
