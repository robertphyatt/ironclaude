---
name: writing-plans
description: Create detailed step-by-step implementation plans
---

# Writing Plans

## Purpose

Create comprehensive implementation plans with bite-sized tasks (2-5 minutes each), exact file paths, complete code snippets, and expected output. Plans assume the implementer has zero context and needs mechanical instructions.

## When to Use

- After completing a design with brainstorming skill
- When user requests an implementation plan
- Before beginning implementation of any non-trivial feature

**Required argument:** Path to design doc (e.g., `docs/plans/2026-01-29-feature-design.md`)

<HARD-GATE>
Do NOT execute any plan tasks, write implementation code, or make non-docs changes
during plan writing. Your job is to create a complete, mechanical plan document —
not to start implementing it. The executing-plans skill handles implementation.
</HARD-GATE>

## Common Rationalizations (all wrong)

| Rationalization | Why it's wrong |
|----------------|---------------|
| "I'll just implement this one thing while planning" | Implementation belongs in executing-plans. Stay in your lane. |
| "The plan is obvious, I don't need to write it down" | Plans catch missing steps. "Obvious" plans miss edge cases. |
| "Let me test if this approach works first" | That's prototyping, not planning. Design validates approach. |
| "This task is too small for a full plan" | Small tasks get the same plan structure. No exceptions. |

## Process

**Announce professional mode status:**
```
Using writing-plans skill. Professional mode is ACTIVE - architect mode enforced (no code changes).
```

### Before You Begin: Episodic Memory Search

Episodic memory search is REQUIRED before plan writing (see Step 0.5). Relevant when:
- Design relates to past work in this project
- Similar features were implemented before
- User references "last time", "like we did before"
- Context feels incomplete (after compaction)

To search: dispatch the `ironclaude:search-conversations` agent with relevant query.

### Phase 0: Validate Design Doc Argument

**Step 0: Check for required design doc path argument**

If no design doc path is provided, display:
```
BLOCKED: Design doc path required.

Usage: /writing-plans docs/plans/YYYY-MM-DD-feature-design.md

The design file must:
- Exist at the specified path
- End with -design.md
```

Then STOP. Do not proceed without a valid design doc path.

If design doc path is provided:
1. Verify file exists
2. Verify it ends with `-design.md`
3. Read the design doc for context
4. Proceed to Phase 1

### Step 0.5: Search Episodic Memory (REQUIRED)

Before creating the implementation plan:

1. Dispatch ironclaude:search-conversations agent with query based on design topic
2. Look for:
   - Similar implementations done before
   - Patterns that worked or failed
   - Integration gotchas
3. Incorporate relevant learnings into plan

This is REQUIRED, not optional. Do not skip.

### Phase 1: Read Design Document

**Step 1: Locate design document**

Check `docs/plans/` for the most recent design:

Use the Glob tool with pattern `docs/plans/*-design.md` to find design documents.

Read the design document completely to understand:
- Architecture
- Components to build
- Data flow
- Testing strategy

### Phase 2: Break Down Into Tasks

**Step 2: Identify major components**

List the major components that need to be built:
- What files need to be created?
- What files need to be modified?
- What tests need to be written?
- What integrations are required?

**Step 3: Order tasks by dependencies**

Arrange tasks so:
- Foundation components come first
- Dependent components come after dependencies
- Tests come after the code they test
- Integration comes after individual components

### Phase 3: Write Bite-Sized Steps

**Step 4: Break each task into 2-5 minute steps**

For each task, create steps that are ONE action each:

Example breakdown:
```
Task: Add user authentication

Step 1: Write test for authentication function (2 min)
Step 2: Run test to verify it fails (1 min)
Step 3: Implement minimal authentication function (3 min)
Step 4: Run test to verify it passes (1 min)
Step 5: Stage changes (1 min)
```

**Each step must include:**
- Exact command to run (if applicable)
- Expected output
- Exact file path
- Complete code snippet (not "add validation" - show the actual code)

**TDD requirement:** When a task involves creating or modifying executable code (not config files, documentation, or version bumps), the steps MUST follow this structure:

1. Write or modify the test (RED)
2. Run the test — verify it fails with expected failure
3. Write the implementation (GREEN)
4. Run the test — verify it passes
5. Stage changes

If a task genuinely does not need tests (pure config, documentation, version bump, hook script with no test framework), document why: "No tests required: [reason]" in the task description.

### Phase 4: Write Plan Document

**Step 5: Create plan document**

Save to `docs/plans/YYYY-MM-DD-<feature-name>.md`:

```markdown
# <Feature Name> Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** [One sentence describing what this builds]

**Architecture:** [2-3 sentences about approach from design]

**Tech Stack:** [Key technologies/libraries]

---

## Task 1: [Component Name]

**Files:** (MUST be explicit paths — never use glob patterns like `**/*.py`)
- Create: `exact/path/to/file.py`
- Modify: `exact/path/to/existing.py:123-145`
- Test: `tests/exact/path/to/test.py`

**Step 1: [Action]**

[If writing code, include complete snippet]
[If running command, include exact command and expected output]

Example:
```python
def authenticate(username, password):
    # TODO: implement
    pass
```

Run:
```bash
pytest tests/auth/test_authentication.py -v
```

Expected: FAIL with "function not defined"

**Step 2: [Next action]**

[Continue with exact steps...]

**Step 5: Stage changes**

Run:
```bash
git add src/auth/authentication.py tests/auth/test_authentication.py
```

Expected: Changes staged (professional mode blocks commit)

---

## Task 2: [Next Component]

[Repeat structure...]
```

**Step 5.5: Create machine-readable plan JSON**

After creating the markdown plan, also create `docs/plans/YYYY-MM-DD-<feature-name>.plan.json`:

The JSON must follow this exact schema:
```json
{
  "name": "Feature Name",
  "goal": "One sentence describing what this builds",
  "design_file": "docs/plans/YYYY-MM-DD-<feature-name>-design.md",
  "tasks": [
    {
      "id": 1,
      "name": "Task Name",
      "description": "What this task does",
      "allowed_files": ["exact/path/to/file.py", "exact/path/to/other.py"],
      "depends_on": [],
      "steps": [
        {
          "description": "Step description",
          "command": "optional shell command",
          "expected": "optional expected output"
        }
      ]
    }
  ]
}
```

Rules:
- `id` must be positive integers, unique across all tasks
- `depends_on` references other task IDs (must exist, no circular deps)
- `allowed_files` must be exact paths (no globs) — these are the only files the MCP will permit editing during that task
- The JSON is the source of truth for the MCP server; the markdown is for human review
- The MCP validates schema, dependency integrity, and cycle-freedom before accepting

Stage the JSON file alongside the markdown:
```bash
git add docs/plans/YYYY-MM-DD-<feature-name>.plan.json
```

**Step 5.6: Signal plan files written**

Call MCP `mcp__plugin_ironclaude_state-manager__mark_plan_ready` to transition the session to `plan_ready`. The statusline will show orange "plan_ready" until executing-plans is invoked.

If `mcp__plugin_ironclaude_state-manager__mark_plan_ready` returns an error (wrong stage), display the error to the user. Do NOT proceed to Phase 5 until it succeeds.

Note: Do NOT call `mcp__plugin_ironclaude_state-manager__create_plan` here. That is called by executing-plans.

### Phase 5: Offer Execution Options

**Step 6: Stage plan document**

Run:
```bash
git add docs/plans/YYYY-MM-DD-<feature-name>.md
```

**Step 7: Present execution options with recommendation**

Before presenting options, analyze the plan to determine the recommended execution strategy:

**Recommend inline (option 3) when:**
- Total tasks ≤ 2
- Any task has complex or ambiguous steps requiring judgment
- Tasks require reading large portions of the codebase

**Recommend subagent-parallel (option 2) when:**
- Wave 1 has 3+ independent tasks
- Tasks are self-contained file edits with clear, mechanical steps
- No task requires broad codebase context

**Recommend subagent-sequential (option 1) when:**
- Moderate task count (3-5) with mixed complexity
- Tasks have subtle inter-dependencies beyond what depends_on captures
- Default choice when neither inline nor parallel is clearly better

Display with recommendation:
```
Plan complete and saved to:
- docs/plans/YYYY-MM-DD-<feature-name>.md (human-readable)
- docs/plans/YYYY-MM-DD-<feature-name>.plan.json (machine-readable)

Professional mode is ACTIVE - plan staged for your review.

The MCP server releases tasks in dependency-computed waves:
- Wave 1: All tasks with no dependencies
- Wave 2: Tasks whose dependencies are all in Wave 1 (released after Wave 1 review passes)
- And so on until all tasks complete

Recommended: Option N ([mode name])
Rationale: [one sentence explaining why based on plan characteristics]

Three execution options:

1. Subagent tasks, sequential
   - MCP releases one wave at a time
   - Subagent per task, sequential within each wave
   - Code review after each task

2. Subagent tasks, parallel
   - MCP releases one wave at a time
   - Independent tasks within a wave run in parallel
   - Code review as each completes

3. No subagents, main session
   - Execute all tasks in this session
   - Maximum control, no subagent drift possible

Which approach? (1/2/3)
```

(Move the "(Recommended)" label to whichever option is recommended for this specific plan.)

Wait for user choice.

**If option 1 chosen (subagent tasks, sequential):**

Invoke executing-plans skill:
[Use Skill tool: skill="ironclaude:executing-plans", args="<plan-path> --mode=subagent-sequential"]

**If option 2 chosen (subagent tasks, parallel):**

Invoke executing-plans skill:
[Use Skill tool: skill="ironclaude:executing-plans", args="<plan-path> --mode=subagent-parallel"]

**If option 3 chosen (no subagents, main session):**

Invoke executing-plans skill:
[Use Skill tool: skill="ironclaude:executing-plans", args="<plan-path> --mode=inline"]

## Key Principles

- **Bite-sized tasks**: Every step is 2-5 minutes maximum
- **Exact paths**: No ambiguity about what file to touch. NEVER use glob patterns (`**/*.py`, `src/*`). Every file must be listed by its full relative path.
- **Complete code**: Show the actual code, not "add validation"
- **Exact commands**: Full command with all flags and expected output
- **TDD cycle**: Write test → run to fail → implement → run to pass → stage
- **Professional mode aware**: All steps use "git add" to stage, never commit
- **Explicit skill invocation**: Use Skill tool for executing-plans
