# Scope Fidelity and Proportionality Implementation Plan (v1.1.16 L1)

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** Add a shared `scope-fidelity` rule, loaded by both clients, and hook it into the brainstorming, writing-plans, executing-plans and code-review skills and the review checklist, so that work stays proportionate to the request and review findings cannot inflate it.

**Requirements:** `docs/plans/2026-10-02-scope-fidelity-requirements.md`

**Design:** `docs/plans/2026-10-02-scope-fidelity-design.md` (Approach A)

**Architecture:**
- One new rule file, `worker/rules/scope-fidelity.md`.
- Small additive edits to four skills and to `worker/rules/review-checklist.md`, each pointing at the rule.
- No banner, instruction-file, template, hook, MCP or Brain changes.

**Tech Stack:** Markdown skill and rule text, verified by the existing Commander pytest suite.

**Loop classification:** full loop. The change touches no risk area, but it spans 6 files and about 150 lines, so it exceeds the light-loop size bound.

## Execution invariants

- **Shell state does not persist between steps.** Use literal absolute paths.
- **Bash cwd is `commander/`.** Use `git -C /Users/roberthyatt/Code/ironclaude` for staging.
- **zsh `nomatch`.** No unquoted globs are used.
- **`docs/` is gitignored.** Plan artifacts need `git add -f`; this plan stages no docs.
- **Measured baselines.**
  - At HEAD `dcca0d1`, `../../rules/scope-fidelity.md` occurs 0 times in every skill file, and `worker/rules/scope-fidelity.md` does not exist.
  - The full Commander suite passed (3457) at `dcca0d1`.
- **Falsifiability of the checks.**
  - The `grep -c` presence checks print `0` against current HEAD, so they fail if the edit is missing.
  - `test_rules_references_resolve.py` fails if any skill references `../../rules/scope-fidelity.md` while the file is absent.

No tests required, for every task: the work is skill and rule Markdown with no executable code. The existing suites guard rule-reference resolution, checklist headings, plan-authoring invariants and transition preflight text.

---

## Task 1: Create the shared rule `worker/rules/scope-fidelity.md`

**Files:**
- Create: `worker/rules/scope-fidelity.md`

**Step 1: Write the file with exactly this content**

```markdown
# Scope Fidelity

Applies to every client (Claude and Codex) and every workflow stage. Loaded by
brainstorming, writing-plans, executing-plans, and code-review.

The workflow banner stands unchanged: every change goes through brainstorm → design →
plan → execute, with no stage skipped. This rule governs how large each stage is. Rigor
means completing every stage, not enlarging the work.

## 1. Deliver what was asked

- Build the thing the operator asked for, at the size they asked for it.
- Side quests unless the operator named them: instrumentation, evaluation or test
  harnesses, review/listening/survey pages, probe banks, receipts, hash binding,
  byte-equality proofs, independent rebuilds, and any new verification tooling.
- Verification means running the planned check once and reporting its output. If you
  believe new verification tooling is needed, ask the operator; do not build it.
- An unrequested artifact is a defect even when it is well made.

## 2. Light loop vs full loop

Classify every change in brainstorming and state the result in one line in the design doc
(`Loop: light — <why no risk area; ~N files/~M lines>` or `Loop: full — <risk area or size>`).

A change is **full** if it touches any of:
- database writes or schema/migrations
- auth, permissions, or guard/hook enforcement code
- concurrency (locks, async coordination, shared mutable state)
- git or worktree operations
- a public API or MCP tool schema

Otherwise it is **light**. A light change that grows beyond ~3 files or ~150 lines is
promoted to full. If the classification is uncertain, it is full.

A light loop still runs every stage, sized down:
- one short requirements + design note (a single `-design.md` with a `## Requirements`
  section is acceptable as the requirements artifact);
- a one-task plan (same JSON schema);
- a same-tier blind plan review — no tier-up escalation;
- the normal task-boundary code review.

If a light change outgrows its bounds mid-execution, stop and retreat to brainstorming to
reclassify; do not silently expand it.

The light label is checked, not trusted. Record it with its reason. The blind plan
reviewer verifies it against the risk list and size bound, and a wrong label is a
MATERIAL finding that forces the full loop. Task code review fails a light task whose
diff touches a risk area or exceeds the size bound (`review-checklist.md` → "Light-loop
bound").

## 3. Delegated authority

Delegation is in effect only when:
- the operator explicitly delegated in this session ("don't ask", "use your judgment",
  "proceed autonomously", or equivalent), or
- the session is a Commander-spawned worker (its Brain answers on the operator's behalf).

Under delegation, replace routine approval questions (design-section sign-offs, "ready
for the plan?", the `commander-choice` tier-up prompt, execution-mode choice) with a
recorded line — `Predicted answer: <answer> (delegated)` — in the design doc or plan, and
proceed.

Always ask, even under delegation: push, delete, destructive or irreversible actions,
outward-facing actions (external systems, publishing), and anything needing new authority.

## 4. Operator prohibitions

When the operator forbids a pattern ("stop making listening surveys"), append one bullet to
`docs/operator-prohibitions.md` in the target repository — date, the forbidden pattern,
and the operator's words quoted. Create the file if absent. Read it at the start of
brainstorming and writing-plans and honor every entry. `docs/` is writable in every
workflow stage.

## 5. Experiments

Research questions ("does CLAP rank these scenes well?", "try weighted-median") are
answered with throwaway scratch scripts whose results are reported in chat, and become
product code only when the operator says "keep it". Until the experiment stage ships,
run an experiment as a light loop whose task states "No tests required: experiment".

## 6. Review bound

Reviewers label each finding `blocks requirement` or `hardening`. Only blockers can fail a
task. Reviewers may not demand evidence artifacts the plan did not declare. See
`review-checklist.md` → "Review scope bound".
```

**Step 2: Verify the file exists and carries its six sections**

Run:
```bash
grep -c -e "^## 1. Deliver what was asked" -e "^## 2. Light loop vs full loop" -e "^## 3. Delegated authority" -e "^## 4. Operator prohibitions" -e "^## 5. Experiments" -e "^## 6. Review bound" /Users/roberthyatt/Code/ironclaude/worker/rules/scope-fidelity.md
```

Expected: `6`. Against current HEAD the file is absent, and grep exits 2.

**Step 3: Stage**

Run:
```bash
git -C /Users/roberthyatt/Code/ironclaude add worker/rules/scope-fidelity.md
```

Expected: no output, exit 0.

---

## Task 2: Brainstorming skill applies scope fidelity

Depends on Task 1.

**Files:**
- Modify: `worker/skills/brainstorming/SKILL.md`. Three edits: the Step 0.2 insertion after Step 0.1 (before `### Step 0.5`), Step 5, and Step 9.

**Step 1: Insert Step 0.2**

Replace this text:
```
### Step 0.5: Search Episodic Memory (REQUIRED)

Before proceeding with any brainstorming:
```
with this text:
```
### Step 0.2: Apply scope fidelity (REQUIRED)

Read `../../rules/scope-fidelity.md` and, if it exists, the target repository's
`docs/operator-prohibitions.md`. Then:
- Classify the change as light or full per the rule, and state it in one line:
  `Loop: light — <why no risk area; ~N files/~M lines>` or `Loop: full — <risk area or size>`.
  Record the same line in the design doc.
- For a light change, the design doc may be one short note with a `## Requirements` section.
- Honor every operator prohibition; never design a forbidden pattern back in.
- Design only what was asked. Instrumentation, harnesses, review pages, receipts, and new
  verification tooling are out of scope unless the operator named them.
- Note whether delegated authority is in effect (rule §3). It changes Steps 5 and 9.

### Step 0.5: Search Episodic Memory (REQUIRED)

Before proceeding with any brainstorming:
```

**Step 2: Add the delegation clause to Step 5**

Replace this text:
```
Wait for confirmation before continuing to next section.
```
with this text:
```
Wait for confirmation before continuing to next section.

Under delegated authority (`../../rules/scope-fidelity.md` §3), present each section and
continue without asking; when Step 7 writes the design doc, put
`Predicted answer: yes (delegated)` under each section heading.
```

**Step 3: Add the delegation clause to Step 9**

Replace this text:
```
If user declines: stop here. The design doc is saved; they can invoke /writing-plans later.
```
with this text:
```
If user declines: stop here. The design doc is saved; they can invoke /writing-plans later.

Under delegated authority (`../../rules/scope-fidelity.md` §3), skip the question, record
`Predicted answer: Yes, invoke writing-plans (delegated)` in the design doc, and invoke the
Skill tool immediately as above.
```

**Step 4: Verify the references**

Run:
```bash
grep -c -F "../../rules/scope-fidelity.md" /Users/roberthyatt/Code/ironclaude/worker/skills/brainstorming/SKILL.md
```

Expected: `3`. Against current HEAD this prints `0`.

**Step 5: Run the guarding tests**

Run:
```bash
PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_rules_references_resolve.py tests/test_workflow_transition_preflight.py -q
```

Expected: 0 failed, exit 0.

**Step 6: Stage**

Run:
```bash
git -C /Users/roberthyatt/Code/ironclaude add worker/skills/brainstorming/SKILL.md
```

Expected: no output, exit 0.

---

## Task 3: Writing-plans and executing-plans apply scope fidelity

Depends on Task 1.

**Files:**
- Modify: `worker/skills/writing-plans/SKILL.md`. Two edits: after the "Prove every verification can fail." bullet, and after the "No tests required" paragraph.
- Modify: `worker/skills/executing-plans/SKILL.md`. Four edits: the `commander-choice` bullet, the reviewer-tier decision, the latent-defect hunt list, and the materiality test in the blind plan-review prompt.

**Step 1: writing-plans, clarify the verification bullet**

Replace this text:
```
  nothing — prefer a non-default value and an equality that fails on any deviation.
```
with this text:
```
  nothing — prefer a non-default value and an equality that fails on any deviation.
  This applies to the checks the task already needs. It never authorizes new
  verification tooling, receipts, extra suites, or independent rebuilds
  (`../../rules/scope-fidelity.md` §1).
```

**Step 2: writing-plans, add the light-loop plan shape**

Replace this text:
```
If a task genuinely does not need tests (pure config, documentation, version bump, hook script with no test framework), document why: "No tests required: [reason]" in the task description.
```
with this text:
```
If a task genuinely does not need tests (pure config, documentation, version bump, hook script with no test framework), document why: "No tests required: [reason]" in the task description.

**Light-loop plans** (classified in brainstorming per `../../rules/scope-fidelity.md` §2): one
task, same JSON schema, steps limited to the requested change and the checks it already
needs. A light design note with a `## Requirements` section may serve as `requirements_file`.
Read the target repository's `docs/operator-prohibitions.md`, if present, and plan nothing it forbids.
```

**Step 3: executing-plans, delegation in `commander-choice`**

Replace this text:
```
  declines, proceed to Step 2. If yes, run the review below.
```
with this text:
```
  declines, proceed to Step 2. If yes, run the review below. Under delegated
  authority (`../../rules/scope-fidelity.md` §3), do not ask: record
  `Predicted answer: Yes — run (delegated)` in the plan's Markdown file (never
  `.plan.json`; its hash is bound at `create_plan`) and run the review. For a plan
  labeled `Loop: light`, do not ask either: run the review below at the same tier.
```

**Step 4: executing-plans, the light loop uses a same-tier reviewer**

Replace this text:
```
   widely-shared code, schema/migration, concurrency, security surface, and diff size/spread.
```
with this text:
```
   widely-shared code, schema/migration, concurrency, security surface, and diff size/spread.
   A light-loop plan (`../../rules/scope-fidelity.md` §2) always uses the same tier — skip
   this judgment and do not surface a tier-up question.
```

**Step 4a: executing-plans, the reviewer checks the light label (hunt list)**

Replace this text:
```
   - a factual claim (a count, a line number, a symbol) whose
     provenance is an agent summary rather than a file the author opened.
```
with this text:
```
   - a factual claim (a count, a line number, a symbol) whose
     provenance is an agent summary rather than a file the author opened;
   - a `Loop: light` label the change does not qualify for — it touches DB
     writes/schema, auth/permission/guard code, concurrency, git/worktree
     operations, or a public API/MCP schema, or it exceeds ~3 files / ~150 lines.
```

**Step 4b: executing-plans, the materiality test gains case (e)**

Replace this text:
```
   (d) leave a required verification unable to detect the failure it exists
       to catch.
```
with this text:
```
   (d) leave a required verification unable to detect the failure it exists
       to catch; or
   (e) run a plan labeled `Loop: light` that does not qualify for the light
       loop.
```

**Step 5: Verify the references and the reviewer additions**

Run:
```bash
grep -c -F "../../rules/scope-fidelity.md" /Users/roberthyatt/Code/ironclaude/worker/skills/writing-plans/SKILL.md /Users/roberthyatt/Code/ironclaude/worker/skills/executing-plans/SKILL.md
```

Expected:
```
/Users/roberthyatt/Code/ironclaude/worker/skills/writing-plans/SKILL.md:2
/Users/roberthyatt/Code/ironclaude/worker/skills/executing-plans/SKILL.md:2
```

Against current HEAD both print `0`.

Then run:
```bash
grep -c -e "Loop: light. label the change does not qualify for" -e "(e) run a plan labeled .Loop: light." /Users/roberthyatt/Code/ironclaude/worker/skills/executing-plans/SKILL.md
```

Expected: `2`. Against current HEAD this prints `0`.

**Step 6: Run the guarding tests**

Run:
```bash
PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_rules_references_resolve.py tests/test_plan_authoring_invariants.py tests/test_workflow_transition_preflight.py tests/test_executing_plans_skill.py -q
```

Expected: 0 failed, exit 0.

**Step 7: Stage**

Run:
```bash
git -C /Users/roberthyatt/Code/ironclaude add worker/skills/writing-plans/SKILL.md worker/skills/executing-plans/SKILL.md
```

Expected: no output, exit 0.

---

## Task 4: Code review and checklist use requirement-bound grading; full suite

Depends on Tasks 2 and 3. It is the last task, so it runs the full suite.

**Files:**
- Modify: `worker/skills/code-review/SKILL.md`. Two edits: Step 7 categorization and the note under the Step 7.5 grade table.
- Modify: `worker/rules/review-checklist.md`. Two edits: a new "Review scope bound" section before `## CRITICAL CHECKS`, and two new checks, "Pinned mutable count" and "Light-loop bound", after "Widened-guard scope".

**Step 1: code-review, label findings in Step 7**

Replace this text:
```
- **Minor**: Style issues, documentation gaps
```
with this text:
```
- **Minor**: Style issues, documentation gaps

Also label every finding. `blocks requirement` is any of: a Critical finding above
(security, broken functionality, testing theatre — regardless of whether the plan
named it); a checklist Critical check (SQL & Data Safety, Race Conditions &
Concurrency, LLM Output Trust Boundary, Command Injection, Pinned mutable count,
Light-loop bound); planned behavior that is wrong or missing; or a planned check that
cannot fail. `hardening` is everything else: extra negative cases, defensive checks,
evidence the plan did not declare. See `../../rules/scope-fidelity.md` §6 and the
checklist's "Review scope bound".
```

**Step 2: code-review, bound the grade to blockers**

Replace this text:
```
| F | Stage 1 FAIL **or** any critical issue (security vulnerability, broken functionality, testing theatre) |
```
with this text:
```
| F | Stage 1 FAIL **or** any critical issue (security vulnerability, broken functionality, testing theatre) |

Grade from `blocks requirement` findings only; the F row above is unchanged by this
rule because every Critical finding is a blocker. `hardening` findings are listed under
"Hardening (backlog)" in the report, never lower the grade below B, and never reopen a
task.
```

**Step 3: checklist, add the Review scope bound section**

Replace this text:
```
## CRITICAL CHECKS (block on failure)
```
with this text:
```
## Review scope bound

Every check under CRITICAL CHECKS blocks when its condition holds in the diff, whether
or not the plan mentioned it: a diff-introduced injection, race, trust-boundary gap,
pinned mutable count, or light-loop breach is `blocks requirement` by definition. The
requirement bound narrows only the three fidelity checks — Falsifiability, Provenance,
Widened-guard scope — which block only for the requirement's own checks and admitted set.
Findings outside both sets are `hardening`: report them under "Hardening (backlog)";
they do not fail the task. Never demand evidence artifacts (receipts, extra suites,
independent rebuilds, new tooling) that the plan did not declare.

---

## CRITICAL CHECKS (block on failure)
```

**Step 4: checklist, add the Pinned mutable count and Light-loop bound checks**

Replace this text:
```
DO NOT flag: widenings whose negative cases exist elsewhere in the same suite (say where), pure refactors that preserve the admitted set.
```
with this text:
```
DO NOT flag: widenings whose negative cases exist elsewhere in the same suite (say where), pure refactors that preserve the admitted set.

### Pinned mutable count

- [ ] **Acceptance guard pins a count the work is expected to change** — A hardcoded total (tracks, files, rows, windows) in a runner or acceptance gate, where adding the operator's next input changes that total. Each new input then forces a "rebind" loop. Derive the count from the input, or assert a property instead. This is a blocker (D6).

DO NOT flag: counts fixed by specification (a protocol field width, a schema version), counts asserted by a test about a fixed fixture.

### Light-loop bound

- [ ] **Light task outgrew its label** — The plan says `Loop: light`, but the diff touches DB writes/schema, auth/permission/guard code, concurrency, git/worktree operations, or a public API/MCP schema, or it exceeds ~3 files / ~150 lines. This is a blocker: the work must retreat and run as a full loop.

DO NOT flag: full-loop plans, light tasks within the bound.
```

**Step 5: Verify the references and the new headings**

Run:
```bash
grep -c -e "../../rules/scope-fidelity.md" -e "^## Review scope bound" -e "^### Pinned mutable count" -e "^### Light-loop bound" -e "^Every check under CRITICAL CHECKS blocks" /Users/roberthyatt/Code/ironclaude/worker/skills/code-review/SKILL.md /Users/roberthyatt/Code/ironclaude/worker/rules/review-checklist.md
```

Expected:
```
/Users/roberthyatt/Code/ironclaude/worker/skills/code-review/SKILL.md:1
/Users/roberthyatt/Code/ironclaude/worker/rules/review-checklist.md:4
```

The fourth checklist match confirms that the scope bound keeps every CRITICAL check blocking.

Against current HEAD both print `0`.

**Step 6: Run the full Commander suite**

Run:
```bash
PYTHONUNBUFFERED=1 .venv/bin/python -m pytest -q
```

Expected: 0 failed, exit 0. `tests/test_review_checklist.py` still passes, because the transitional checklist headings remain a subset of the canonical ones.

**Step 7: Stage**

Run:
```bash
git -C /Users/roberthyatt/Code/ironclaude add worker/skills/code-review/SKILL.md worker/rules/review-checklist.md
```

Expected: no output, exit 0.
