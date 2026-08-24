---
name: executing-plans
description: Execute implementation plans wave-by-wave via MCP state management
---

# Executing Plans

## Purpose

Execute implementation plans wave-by-wave using MCP tools for state management. The MCP server validates the plan, computes dependency-based waves, tracks task progress, and enforces review gates. Claude calls MCP tools to advance through the plan.

## When to Use

- After completing a plan with writing-plans skill
- User wants to execute an existing implementation plan
- User invokes `/executing-plans docs/plans/<plan-file>.plan.json`

**Required argument:** Path to plan file (e.g., `docs/plans/2026-02-15-feature.plan.json` or `docs/plans/2026-02-15-feature.md`)

<HARD-GATE>
Do NOT skip review checkpoints between tasks. Do NOT modify files outside the
current task's allowed_files list. Do NOT proceed to the next wave until the
current wave passes review. The MCP server enforces this, but you should not
even attempt to circumvent it.

NEVER bypass the MCP state machine. If any MCP tool (mcp__plugin_ironclaude_state-manager__claim_task, mcp__plugin_ironclaude_state-manager__submit_task,
mcp__plugin_ironclaude_state-manager__get_next_tasks, etc.) returns an error:
- STOP immediately
- Report the error verbatim to the user
- Ask how to proceed using AskUserQuestion
- Do NOT work around it, re-implement the tracking manually, or dispatch
  subagents without MCP state calls
- Past problems with MCP do not authorize skipping it

If context feels incomplete after compaction: you will see a [ironclaude]
Session state: system message at the top of the resumed session. If you do
not see one, call mcp__plugin_ironclaude_state-manager__get_resume_state before taking any action.
</HARD-GATE>

## MANDATORY: Structured User Input

Whenever soliciting user input — choices, confirmations, or selections — ALWAYS use the `AskUserQuestion` tool. NEVER ask via prose. Follow the format in `../../rules/ask-user-question-format.md`: Re-ground context, Predict, Options.

## Mandatory Direct Transition Preflight

Before every direct workflow-transition MCP call:

1. Call `get_resume_state` and validate that its session identity is the
   provider-native root session for the active task. Missing or mismatched
   identity: fail closed; stop and report the mismatch.
2. Compare its current workflow stage to the requested target. On an
   equal-target result, skip the transition call and preserve all state.
3. Make one different-target call only and require returned `changed:true`. If the
   call errors or returns unexpected `changed:false`, stop and report it; do not
   retry without a fresh `get_resume_state` read. No blind retry.

The create_plan reload is exempt from target comparison: it is a domain operation
that must reload a revised plan even when its resulting workflow stage is
unchanged. Do not treat that exemption as permission to retry it blindly.

## Common Rationalizations (all wrong)

| Rationalization | Why it's wrong |
|----------------|---------------|
| "This file isn't in allowed_files but I need to touch it" | Update the plan first. Undocumented changes create drift. |
| "The review will obviously pass" | Reviews catch bugs you don't see. Never skip them. |
| "I'll fix this other thing while I'm here" | Scope creep. Stick to the current task. |
| "The next task is simple, let me just do both" | Each task has its own review. Batching skips reviews. |
| "Context might get long — let me checkpoint / find a safe stopping point" | Plan JSON + MCP task state + workflow_stage on disk ARE the checkpoint. Pauses are operator-initiated via `plan-interruption`. See `ironclaude:workflow-durability`. |

## Process

**Announce execution mode:**
```
Using executing-plans skill. Professional mode is ACTIVE.
Enabling execution mode for this session (code changes permitted during execution).
```

### Phase 0: Validate Plan Argument

**Step 0: Check for required plan path argument**

If no plan path is provided, display:
```
BLOCKED: Plan path required.

Usage: /executing-plans docs/plans/YYYY-MM-DD-feature.plan.json

The plan file must:
- Exist at the specified path
- Be a .plan.json file (or a .md file with a corresponding .plan.json)
- Contain valid plan JSON with tasks, dependencies, and allowed_files
```

Then STOP. Do not proceed without a valid plan path.

If plan path is provided:
1. Verify file exists
2. If the argument is a `.plan.json` file, use it directly
3. If the argument is a `.md` file, look for a corresponding `.plan.json` file (same basename)
4. Proceed to Phase 0.5

### Phase 0.5: Parse Execution Mode

Check for --mode argument in the args:
- `--mode=subagent-sequential` (default): Dispatch one subagent per task, wait for each
- `--mode=subagent-parallel`: Dispatch subagents for independent tasks in the wave together
- `--mode=inline`: Execute all tasks directly in main session, no subagents

Parse the mode:
```bash
MODE="subagent-sequential"  # default
if [[ "$ARGS" == *"--mode=subagent-parallel"* ]]; then
  MODE="subagent-parallel"
elif [[ "$ARGS" == *"--mode=inline"* ]]; then
  MODE="inline"
fi
```

Display:
```
Execution mode: $MODE
```

### Phase 1: Setup

**Step 1: Load plan JSON into MCP**

Read the plan JSON file:
- If the argument is a `.plan.json` file, read it directly
- If the argument is a `.md` file, look for a corresponding `.plan.json` file

Call the MCP `mcp__plugin_ironclaude_state-manager__create_plan` tool with the plan JSON:
```
Use MCP tool: mcp__plugin_ironclaude_state-manager__create_plan with the parsed JSON object
```

The MCP will:
- Validate schema, dependencies, and cycle-freedom
- Compute Wave 1
- Store the plan in the database

If validation fails, the MCP returns an error with specific issues. Fix the plan JSON and retry.

**Step 1.5: Tier-up plan review (policy-gated)**

Immediately after `create_plan`, before selecting or dispatching any reviewer, call
`mcp__plugin_ironclaude_state-manager__get_resume_state`. Inspect only its bounded
`review_summary` for the current plan lineage:

- No `canonical_blind_verdict`: continue to the policy branch below; this lineage
  has not consumed its one blind review.
- Matching `SOLID` or `top-tier-self`: skip reviewer dispatch and continue to Step 2.
- `HAS-ISSUES` without `current_hash_advisor_remediated`: resume the existing
  non-blind fix-advisor/remediation path below. Do not dispatch a replacement blind
  review. If its findings are unavailable after compaction, recover them with
  `ironclaude:remembering-conversations`; if they still cannot be recovered, fail
  closed rather than manufacturing a new review.
- `HAS-ISSUES` with `current_hash_advisor_remediated`: skip reviewer dispatch and
  continue to Step 2.
- A passing canonical verdict whose hash does not match the current plan (an
  inherited review after a retreat that changed the plan): run the non-blind fix
  advisor to validate the change and record `advisor-remediated` at the current
  hash, then continue to Step 2. Restoring the exact reviewed plan also clears it.
  Do not dispatch another plan review.

Read `tier_up_review_policy` from `~/.claude/ironclaude-hooks-config.json` (if the
`IRONCLAUDE_HOOKS_CONFIG_PATH` env var is set, read that path instead — this mirrors
the MCP server's resolution). Missing/unreadable/invalid ⇒ treat as `enforced`
(fail-secure).

- `off` → skip this step entirely; proceed to Step 2.
- `commander-choice` → use AskUserQuestion ("Run a tier-up plan review before
  execution? (default: yes)" / "Yes — run (Recommended)" | "No — skip"). If the user
  declines, proceed to Step 2. If yes, run the review below.
- `enforced` → always run the review below (no prompt). `start_execution` will
  refuse to advance without it.

**Run the review (commander-choice=yes or enforced):**

1. Call `mcp__plugin_ironclaude_state-manager__get_professional_mode` first and
   retain its trusted `client` field. **Decide the reviewer tier (LLM blast-radius
   judgment).** The reviewer defaults to the
   **SAME tier** as your current model — a fresh, blind reviewer catches author blind spots
   regardless of tier, which is where most review value is. Escalate to **one tier up** ONLY if
   you judge this plan high-blast-radius/complex enough that a same-tier blind review would miss
   defects a higher tier would catch — weigh, as *judgment* (not a checklist): critical-path or
   widely-shared code, schema/migration, concurrency, security surface, and diff size/spread.
   - **Autonomous PM loop:** make the call yourself and proceed.
   - **Operator interacting directly:** when you judge a tier-up warranted, do NOT silently
     escalate — SURFACE it via AskUserQuestion ("This plan looks high-blast-radius — run a
     one-tier-up plan review? / Yes, tier up (Recommended) | No, same-tier") and honor the choice.
   Resolve the reviewer model on the trusted client's ladder:
   - Claude Code: Haiku→`sonnet`, Sonnet→`opus`, Opus→`fable` unless Fable is
     unavailable→`opus`; Fable is the ceiling.
   - Codex: Luna→`gpt-5.6-terra`, Terra→`gpt-5.6-sol`; Sol is the ceiling and
     uses a fresh same-tier `gpt-5.6-sol` reviewer.

   **Same-tier** means the current model on that same client; never cross clients
   for reviewer tiering. To check Claude Fable availability, read the state flag at
   `IRONCLAUDE_FABLE_STATE_PATH` if set else `~/.ironclaude/state/fable_unavailable.json`; if it
   exists and `unavailable_until` > the current epoch time, Fable is unavailable. Fail-safe: on any
   read error treat Fable as available. (The MCP tier-up gate records `reviewer_model` opaquely and
   accepts a same-tier reviewer — a same-tier `SOLID` review satisfies `enforced` policy.)
2. Use the trusted `client` from step 1 to select the provider-native review
   branch. Do not infer the client from tool availability, environment variables,
   or command text.
3. Build the **plan-review provenance packet** before dispatch. Its authority chain,
   in this exact order, is:

   ```text
   operator directives
     → full scoped brainstorming
     → roadmap/design
     → derived requirements
     → human plan
     → machine plan
   ```

   Operator directives and settled brainstorming decisions outrank every derived
   document. Requirements, design, and plan files are evidence to audit, not
   presumed authority.

   Include every relevant brainstorming turn: operator directives, clarifications,
   corrections, constraints, approvals; assistant questions and interpretations
   the operator answered; alternatives and rejection rationale; preserved
   distinctions; scope reductions; non-goals; and unresolved ambiguity. You MUST
   NOT omit or compress this material to save tokens.

   If the active context is compacted or otherwise incomplete, invoke
   `ironclaude:remembering-conversations` and require complete relevant turns and
   decisions—not a token-saving synopsis. If packet completeness cannot be
   established, fail closed: do not dispatch the review, do not call
   `submit_tier_up_review`, and do not substitute requirements/design documents for
   missing operator or brainstorming context.

   Read `requirements_file` from the machine plan. It must identify the derived,
   operator-reviewed requirements artifact. If it is absent, STOP: return to
   writing-plans and add it to both the human plan and machine plan before review.
   Also read every governing roadmap named by the operator, design, requirements,
   or plan, and include its complete current contents in the roadmap/design portion
   of the packet.
4. Dispatch a fresh reviewer through the trusted client branch using the same
   authority order, same semantic-drift hunt, same materiality test, and same
   verdict rubric:

   - **Claude Code:** use a fresh `Agent`
     (`subagent_type="general-purpose"`, `model=<resolved model>`). Put the complete
     provenance packet inline and provide current artifact/source paths.
   - **Codex:** run a fresh report-only, read-only
     `codex exec --json --ephemeral --skip-git-repo-check -s read-only -m
     <resolved-codex-model> -`. Put the complete provenance packet and complete
     current artifact contents inline on stdin; path-only review is insufficient
     because the ephemeral reviewer may be hook-blocked from shell reads.

   For plan review, **blind** means blind to prior reviewer findings, verdicts,
   repair coaching, reviewer identities, diffs, fix rationale, and
   revision history. It is not blind to operator intent, rationale, alternatives,
   approvals, clarifications, or the brainstorming that produced the plan.
   Use this exact shared prompt contract:
   ```
   You are reviewing an implementation plan with fresh eyes. You have not seen
   prior reviews or repairs. You do have the complete operator and brainstorming
   context that defines what the plan is supposed to mean.

   Review authority, highest to lowest:
   1. Operator directives and clarifications:
      <OPERATOR_DIRECTIVES_COMPLETE>
   2. Full scoped brainstorming dialogue:
      <FULL_SCOPED_BRAINSTORMING_COMPLETE>
   3. Roadmap/design (derived): <DESIGN_MD_PATH>
   4. Derived requirements (operator-reviewed): <REQUIREMENTS_MD_PATH>
   5. Human plan: <PLAN_MD_PATH>
   6. Machine plan: <PLAN_JSON_PATH>

   Your verdict answers exactly two questions:
   - FIDELITY: do roadmap/design, derived requirements, human plan, and machine
     plan preserve the operator directives and full scoped brainstorming with no
     semantic drift?
   - EFFICACY: will executing the plan exactly as written succeed?

   Evaluate in this order:
   1. Operator directives → full scoped brainstorming: recover every settled
      distinction, constraint, alternative, rationale, approval, and non-goal.
   2. Full scoped brainstorming → roadmap/design → derived requirements → human
      plan → machine plan: challenge the derived frame before optimizing within
      it. Operator/brainstorming authority outranks every derived document.
   3. Semantic frame-drift hunt:
   - semantic merge: independent concepts represented as one;
   - semantic collapse: a coverage tuple/label reused as a scored or weighted value;
   - substitution: a proxy treated as the approved operator requirement;
   - lost independence: distinct axes, states, responsibilities, or decisions
     forced through one field, selector, weight, or task;
   - authority inversion: requirements/design prose treated as permission despite
     conflicting operator or brainstorming evidence.
   4. Technical executability:
   - Task ordering: depends_on is correct and cycle-free; foundations before
     dependents; tests after the code they cover.
   - allowed_files completeness: each task lists EVERY file its steps touch
     (an omission blocks the task mid-execution under the file guard).
   - Step granularity: steps are mechanical (exact paths, commands, code) —
     not hand-waving like "add validation".
   - TDD structure where the task involves executable code (RED→GREEN→stage),
     or an explicit "No tests required: [reason]".
   - JSON↔markdown consistency and schema validity.
   5. Latent-defect hunt — the findings this review exists for. Trace every
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
     repo's consistency checks and confirm every member is covered;
   - an `expected:` value that was predicted rather than measured — run the
     command yourself and compare it against what the plan claims;
   - a guard whose expected value the change itself moves (a count or grep over
     text the same step edits);
   - a guard defused by a later step of the same plan — evaluate every check
     against the state after ALL tasks land, not the state where it is introduced;
   - a factual claim (a count, a line number, a symbol) whose
     provenance is an agent summary rather than a file the author opened.

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
5. Record every completed review immediately with
   `mcp__plugin_ironclaude_state-manager__submit_tier_up_review` with
   `reviewer_model=<resolved model>` and exact verdict `SOLID` or `HAS-ISSUES`.
   The server binds it to sha256 of the loaded plan.
6. If verdict is `SOLID`, proceed to Step 2.
7. If verdict is `HAS-ISSUES`, execution is blocked. **Before any repair, dispatch a
   MANDATORY tier-up fix advisor.** Review verdicts are chains: when the model that wrote
   the plan also fixes it, it makes correlated mistakes and the next review fails again.
   The advisor exists to make this response correct the first time.
   - **Tier:** one above your current model on the trusted client's ladder (Claude:
     Opus→`fable`, Fable unavailable→`opus`, Fable ceiling→same-tier Fable; Codex:
     Terra→`gpt-5.6-sol`, Sol ceiling→same-tier Sol). Always a **subagent** — never swap
     the main-loop model, because prompt caches are model-scoped and a swap re-establishes
     the entire context before producing a single token.
   - **It is NOT blind.** Give it the complete operator provenance packet, all four
     artifacts, the reviewer's findings verbatim, and current source. The author already
     has the findings; the advisor's job is *how to respond*, not *whether the findings
     are real*.
   - **Output contract — one disposition per finding:** `CONFIRMED` (the specific change
     that resolves it), `REJECTED` (the evidence refuting it, so you do not "fix" a
     non-defect), or `REQUIRES-RETREAT` (the design premise that is actually broken).
     `REQUIRES-RETREAT` is required in the output space: a plan-level fixer cannot repair a
     broken design premise, and without it the advisor would send you back into the loop.
   Then proceed. Reviewer output is evidence, not authority — and the advisor's advice is
   held to exactly the same standard.
   Independently verify every finding against the four current
   artifacts and cited current source. Reject unsupported findings without changing
   operator requirements, design, or plan. Apply the reviewer's MATERIAL decision
   test yourself: only findings that survive it gate execution. Observations are
   non-blocking and never, alone, justify changing any artifact.
8. The first verified `HAS-ISSUES` activates the convergence rule.
   Finding-by-finding plan patching is forbidden. Before
   changing any artifact, perform one holistic invariant audit covering:
   - every active requirement → approved design decision;
   - every design decision → plan task and acceptance/test evidence;
   - task IDs, `depends_on`, `allowed_files`, ordered steps/commands, tests, and
     expected results;
   - semantic consistency between human and machine plans.
9. Handle the verified audit result without drift:
   - Requirements/design conflict or infeasibility: do not repair the plan. Run
     Mandatory Direct Transition Preflight for target `brainstorming`. Only after a
     different-target result, call MCP
     `mcp__plugin_ironclaude_state-manager__retreat` once with `to: "brainstorming"`,
     present the conflict to the operator, and preserve their requirements unless they
     explicitly change them.
   - Plan-only defects: regenerate one coherent human/machine plan candidate from
     the unchanged requirements and approved design. Do not apply a patch list.
     Re-stage and **RE-CALL `create_plan` with the revised plan JSON** so the MCP
     reloads `session.plan_json` and rebuilds `wave_tasks`. The regenerated plan
     carries no prior-review content forward — no findings, verdicts, fix rationale,
     drift audit, or round-by-round table. That content stays in workflow-private
     state (`tier_up_reviews`, `retreat` reasons); a plan that embeds it breaks the
     next blind review (MP-W02).
10. **A plan lineage gets exactly ONE blind review. Do not dispatch a second one.**
    `HAS-ISSUES` is a terminal verdict, not an invitation to iterate. Once the
    advisor-guided changes are applied, record the outcome and proceed:
    - Call `mcp__plugin_ironclaude_state-manager__submit_tier_up_review` with
      `reviewer_model=<the advisor's model>` and verdict `advisor-remediated`. It binds to
      the sha256 of the **revised** plan, which is a different hash than the `HAS-ISSUES`
      row — that pairing is what the gate checks.
    - `start_execution` accepts a passing verdict (`SOLID`/`top-tier-self`) at the
      current hash, or any canonical blind verdict (passing OR `HAS-ISSUES`) paired
      with `advisor-remediated` at the current hash. A bare `advisor-remediated`
      with no preceding canonical blind review is rejected.
    - **All findings `REJECTED`** (the reviewer was wrong): make no plan change, submit
      `advisor-remediated`, and continue. Forcing a fix for a non-defect is worse than the
      finding.
    - **Any surviving `REQUIRES-RETREAT`:** take the requirements/design-conflict branch in
      item 9 above — it already carries the Mandatory Direct Transition Preflight.
      A retreat INHERITS the current effort's already-consumed blind review — it does
      NOT earn a new one; the changed plan proceeds via `advisor-remediated` (above).
      Only a genuinely new operator-initiated effort (design entered from a terminal
      or idle state) earns its own single blind review.

    Verification does not disappear; it moves. Code review still runs at every task
    boundary, so a defect the advisor missed surfaces there rather than in a second plan
    review — later, cheaper, and against real code instead of a document.

**Top-tier note:** Claude Fable and Codex Sol have no higher model on their own
client ladder. They still run a fresh same-tier review when the client supports the
provider-native branch above. If a genuinely separate reviewer cannot be produced,
display the exact ceiling and unavailability. Under `enforced`, perform the complete
full-provenance self-audit and call `submit_tier_up_review` with the current model
and `verdict=top-tier-self`; under `commander-choice`/`off`, skip. Never use a
different client merely to manufacture a higher tier.

**Fail behavior under `enforced`:** if the reviewer model is genuinely unavailable
and no review can be produced, `start_execution` will block. This is intentional —
the human's lever is `tier_up_review_policy`. Report the block to the operator.

**Step 2: Start execution**

Run Mandatory Direct Transition Preflight for target `executing`. Only after a
different-target result, call the MCP
`mcp__plugin_ironclaude_state-manager__start_execution` tool once to transition
the workflow from plan_ready to executing.

Display:
```
Execution Plan: <plan-name>
Total Tasks: <N>

Professional mode: ACTIVE
Execution mode: ENABLED (managed by MCP)

Wave 1 tasks ready for execution.
```

### Phase 2: Execute Tasks

**Step 3: Get next wave of tasks**

Call the MCP `mcp__plugin_ironclaude_state-manager__get_next_tasks` tool. It returns one of:
- `{status: "next_wave", wave: N, tasks: [...]}` -- New wave of tasks ready
- `{status: "wave_in_progress", pending: [...]}` -- Current wave has incomplete tasks
- `{status: "complete"}` -- All tasks done, proceed to Phase 3

**Step 4: Execute tasks in the wave**

**For subagent-parallel mode:**
Dispatch all tasks in the current wave as parallel subagents (Task tool with run_in_background=true). As each completes, call `mcp__plugin_ironclaude_state-manager__submit_task` with task_id.

**For subagent-sequential mode:**
Dispatch one subagent per task (Task tool with subagent_type="general-purpose"), wait for completion, call `mcp__plugin_ironclaude_state-manager__submit_task` with task_id, then proceed to next task in the wave.

**For inline mode:**
Execute tasks directly in the main session, calling `mcp__plugin_ironclaude_state-manager__submit_task` with task_id after completing each.

### Subagent Prompt Construction Guide

When dispatching tasks via the Task tool, follow these rules to prevent context death spirals:

**Prompt template:**
- `description`: 3-5 word summary of what the subagent will do
- `prompt`: Include ONLY: the task description from the plan, the list of allowed_files, and the specific steps to execute. Do NOT include full plan context, history, or rationale — the subagent doesn't need it and it wastes context budget.
- `max_turns`: Set based on task complexity:
  - Simple file edits: 10-15 turns
  - Multi-file changes with builds: 20-30 turns
  - Never omit — unlimited turns enable death spirals

**Subagent model tier:**
- `model`: pick the LEAST capable tier that will reliably succeed —
  - `haiku`: mechanical or lookup work (locate a symbol, list files, apply a rote edit)
  - `sonnet`: routine implementation — **the default for plan-task execution**
  - `opus`: hard multi-step reasoning, unclear root cause, cross-cutting judgment
  - `fable`: only when a lower tier has genuinely failed this same task
- Apply that default according to who is driving. Run `echo "${IC_ROLE:-}"`:
  - **`worker` (autonomous PM loop):** make the call yourself, set `model=` on the dispatch, proceed.
  - **anything else (operator interacting directly):** recommend the tier, let the operator pick.
- This is INFORM-only: it sets the recommended default and nothing else. Tier choice alone never
  blocks a dispatch or lowers a review grade.

**Anti-patterns (never do these):**
- Dumping the full plan JSON or design doc into the subagent prompt
- Asking subagents to "figure out" what needs to be done (open-ended = spiral)
- Putting orchestration in subagents (code review, submit_task, state transitions)
- Dispatching subagents for tasks that require reading large portions of the codebase

**When to use inline instead:**
- Task requires understanding broad codebase context
- Task has ambiguous steps that may need clarification
- Previous subagent attempt hit context limits (circuit breaker tripped)

**Common execution steps (all modes):**

1. **Claim the task:**
   Call MCP `mcp__plugin_ironclaude_state-manager__claim_task` with task_id to transition the task from `pending → in_progress`.
   This MUST succeed before beginning any work. If it fails (task not found, wrong status), stop and report the error.

2. **Announce task:**
   ```
   Task N: <Task Name>
   ```

3. **Execute each step exactly as written in the plan:**
   - Follow commands precisely
   - Match expected output
   - If step fails, STOP and report
   - Don't proceed to next step until current step succeeds

4. **Verify completion before submitting:**
   Before calling `mcp__plugin_ironclaude_state-manager__submit_task`, verify the task is actually complete:
   - If the plan specifies test commands: **run them and show the output in the current response**
   - If the plan specifies expected outputs: **verify each one matches**
   - If the task modified files: **read the modified sections to confirm changes are present**

   Do NOT call `mcp__plugin_ironclaude_state-manager__submit_task` until verification evidence is visible in the current response. Claiming work is complete without fresh verification is dishonesty, not efficiency.

   If verification fails, fix the issue before submitting. If it cannot be fixed, report the failure per Step 6 (Handle failures).

5. **After completing the task, call the MCP `mcp__plugin_ironclaude_state-manager__submit_task` tool with task_id:**
   - This marks the task as submitted for review
   - The MCP sets review_pending=1

6. **Invoke code review explicitly:**
   ```
   [Use Skill tool: skill="ironclaude:code-review", args="--task-boundary"]
   ```
   - The code-review skill runs and displays its full report (files reviewed, findings, PASS/FAIL)
   - The user sees all review findings with file:line references
   - Code review is report-only: it records the verdict but does not repair task code.
   - **A/B:** after successful verdict response, run Mandatory Direct Transition Preflight for target `executing`. Only after a different-target result, call `mcp__plugin_ironclaude_state-manager__mark_executing` once and require `changed:true`; retain the existing pass/advance path.
   - **C/D/F:** require the successful verdict response and repair only its returned reopened `task_ids` in `executing`. Confirm `reopened_count` and `workflow_stage: "executing"`, run planned verification, resubmit those tasks, and invoke task-boundary code review again.
   - Never call `mark_executing` after C/D/F: the verdict transaction already returned the workflow to `executing`.
   - Task code re-review is required after repair; never dispatch another blind plan review for task repair.

7. **After task completes (MUST be the last output for each task):**
   ```
   Task N/M complete. Changes staged.
   ```

8. **Update progress:**
   ```
   Progress: N/M tasks complete
   ```

**Step 5: Advance to next wave**

After all tasks in the current wave pass review, call `mcp__plugin_ironclaude_state-manager__get_next_tasks` again.
- If more tasks: repeat Step 4
- If complete: proceed to Phase 3

**Step 6: Handle failures**

If any step fails:

1. **STOP execution immediately**
2. **Report failure:**
   ```
   Task N, Step X failed

   Command: <command>
   Expected: <expected output>
   Actual: <actual output>

   Execution paused.
   ```

3. **Use AskUserQuestion tool:**
   - question: "Task N, Step X failed. What would you like to do?"
   - header: "Step failed"
   - options: "Debug and fix" (investigate and fix the failing step) | "Skip this step" (mark skipped and continue — not recommended) | "Abort execution" (stop plan execution and return to brainstorming)

4. **Follow user direction**

**Handling retreat:**

If a task fails repeatedly or the approach is fundamentally wrong:
- Run Mandatory Direct Transition Preflight for target `brainstorming`. Only after a
  different-target result, call the MCP
  `mcp__plugin_ironclaude_state-manager__retreat` tool once with
  `to: "brainstorming"` and `reason: "explanation"`.
- This preserves progress history and transitions back to brainstorming
- The user can rethink the approach and create a new plan

**Interruption handling:**

If user appears to be changing topic or requesting different work during execution:
1. Recognize this as a potential interruption
2. Invoke the `plan-interruption` skill
3. Follow that skill's process to handle the state transition

### IronClaude self-update boundary

A self-update means IronClaude installs or updates its own Codex plugin while an
IronClaude PM loop is active. The main orchestrator owns this boundary; never
delegate restart, identity verification, workflow transitions, or recovery to a
subagent.

For an IronClaude self-update:

1. Finish all planned source tests and plugin validation.
2. Apply the plugin-creator cachebuster before the final build, then build the
   cachebusted source. A pre-cachebuster bundle cannot certify the installed
   runtime.
3. Reinstall through the confirmed local marketplace and preserve the current
   native task ID plus its task/goal state.
4. Fully quit and relaunch Codex. Compaction or reinstall alone is insufficient.
5. Reopen the same native task before submitting the self-update task. Require
   `get_resume_state.session_id` to equal the preserved native task ID.
6. Require a complete `run_diagnostics.runtime` containing the provider-active
   manifest, `plugin_root`, `plugin_version`, `manifest_sha256`,
   `bundle_sha256`, and `client`. Compare its startup hashes with the intended
   installed-cache hashes. Pass those same installed-cache values back as
   `expected_runtime` and require `Runtime activation match ... PASS`.
7. Before the next PM loop, perform one normal valid different-stage workflow
   transition and require `changed:true`.
8. Missing runtime fields, any fingerprint/identity mismatch, or a failed
   behavioral transition must fail closed. Create a new task only if same-task
   verification fails, using the native handoff context required by the roadmap.

`codex plugin list`, reinstall success, filesystem parity, and source/cache
hashes are installation evidence only; none proves that the current process
loaded the intended runtime. Do not add a workflow stage, do not add an MCP
tool, do not add a cache copier, and do not add transcript migration for this
boundary.

### Phase 3: Completion

**Step 7: Plan complete**

When `mcp__plugin_ironclaude_state-manager__get_next_tasks` returns `{status: "complete"}`:
1. The MCP automatically transitions workflow to execution_complete
2. **Re-stage the cumulative allowed_files.** A subagent's `git stash pop` (or any index
   churn) during execution can silently drop EARLIER tasks' staged content out of the git
   index, yielding a PARTIAL commit with NO error. Before suggesting a commit, re-stage the
   deduped **union** of every task's `allowed_files` from the plan JSON so the index holds
   the full intended set regardless of churn. This runs in the orchestrator, AFTER the
   subagents. Only these explicit paths are staged — a working change OUTSIDE allowed_files
   is never touched. Stage only paths that exist on disk (replace `<plan-json-path>` and
   `<repo-root>`):
   ```bash
   python3 -c "import json,sys; print('\n'.join(sorted({f for t in json.load(open(sys.argv[1]))['tasks'] for f in t.get('allowed_files', [])})))" <plan-json-path> \
     | while IFS= read -r f; do if [ -e "<repo-root>/$f" ]; then git -C <repo-root> add -- "$f"; fi; done
   ```
3. Suggest a commit message based on the plan's goal and changes:

Review the plan's Goal statement and the staged diff (`git diff --staged --stat`).
Draft a concise commit message (1-2 sentences) that:
- Summarizes the "why" not just the "what"
- Captures the most important changes
- Follows the repository's existing commit message style (check `git log --oneline -5`)

Present the suggestion to the user:
```
Suggested commit message:

  <your crafted message>

This covers: <brief list of key changes>
```

The user may use this message as-is, modify it, or write their own. This is a suggestion, not a requirement. Do not block on user response - proceed to the final summary immediately after presenting the suggestion.

**Step 7.5: Tier-up adversarial end review (LLM blast-radius judgment)**

Judge whether this change warrants a final adversarial review — now against the ACTUAL staged diff
(`git diff --staged`), which is more informative than the plan was. Use the same blast-radius criteria as
Step 1.5 (critical-path or widely-shared code, schema/migration, concurrency, security surface, diff size).

- **Routine / low-blast-radius change:** skip this step (the per-task code reviews already covered it);
  proceed to Step 8.
- **High-blast-radius change:**
  - **Autonomous PM loop:** run the review (below).
  - **Operator interacting directly:** do NOT silently run it — SURFACE it via AskUserQuestion
    ("This change looks high-blast-radius — run a tier-up adversarial review of the diff? / Yes
    (Recommended) | No"); if the operator declines, proceed to Step 8.

**Run the review (high-blast-radius + confirmed):** dispatch a fresh, blind, **one-tier-up** reviewer
(Agent tool, `subagent_type="general-purpose"`, `model=<one tier above your current model>` per the
Step 1.5 tier-up resolution incl. the Fable-availability fallback) over the staged diff, following the
`ironclaude:adversarial-review` approach: orientation → deep read → **verify every finding with grep/read
evidence (drop unverified)** → report severity-classified findings. The reviewer must evaluate the diff /
current code as-is — NO git-blame, NO prior-review or issue-tracker context, NO author rationale. Then:
independently verify each MATERIAL finding against the current source, and fix every verified MATERIAL
finding through the normal workflow (re-open the relevant task / new PM loop as needed) before treating the
effort complete. Observations are non-blocking. This end review is commander-orchestrated by judgment — it
is NOT a hard MCP state-machine gate. Proceed to Step 8 once no MATERIAL findings remain.

**Step 8: Final summary**

Display:
```
Execution Complete

Tasks completed: N/N
Professional mode: ACTIVE
Execution mode: DISABLED

All changes have been staged with 'git add'.
Review changes and commit when ready:

  git diff --staged
  git commit -m "<suggested_commit_message>"

Professional mode remains ACTIVE for next task.
```

## Key Principles

- **MCP manages state**: All plan state (progress, wave computation, review gates) is managed by the MCP server via typed tools. Claude calls `mcp__plugin_ironclaude_state-manager__create_plan`, `mcp__plugin_ironclaude_state-manager__get_next_tasks`, `mcp__plugin_ironclaude_state-manager__submit_task`, and `mcp__plugin_ironclaude_state-manager__retreat` to drive execution.
- **Wave-based execution**: Tasks are released in dependency-computed waves. All tasks in a wave can run in parallel (if using subagent-parallel mode) because their dependencies are already satisfied.
- **Explicit review gates**: After calling `mcp__plugin_ironclaude_state-manager__submit_task`, Claude explicitly invokes the code-review skill with `--task-boundary`. The review report is displayed to the user with full findings. The task-completion-validator hook validates and advances.
- **File-restricted**: Only files listed in the plan's allowed_files for the current wave tasks can be modified.
- **Precise execution**: Follow plan steps EXACTLY as written.
- **Stop on failure**: Don't proceed if step fails.
- **Stage not commit**: Use git add only - professional mode blocks commits.
- **Professional mode persists**: Mode stays active after execution completes.
