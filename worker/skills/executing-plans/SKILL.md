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

Whenever soliciting user input — choices, confirmations, or selections — ALWAYS use the `AskUserQuestion` tool. NEVER ask via prose. Follow the format in `.claude/rules/ask-user-question-format.md`: Re-ground context, Predict, Options.

## Common Rationalizations (all wrong)

| Rationalization | Why it's wrong |
|----------------|---------------|
| "This file isn't in allowed_files but I need to touch it" | Update the plan first. Undocumented changes create drift. |
| "The review will obviously pass" | Reviews catch bugs you don't see. Never skip them. |
| "I'll fix this other thing while I'm here" | Scope creep. Stick to the current task. |
| "The next task is simple, let me just do both" | Each task has its own review. Batching skips reviews. |

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

**Step 2: Start execution**

Call the MCP `mcp__plugin_ironclaude_state-manager__start_execution` tool to transition the workflow from plan_ready to executing.

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
   - If critical issues are found, fix them before proceeding
   - The task-completion-validator hook validates that completed work matches the task description. The code-review skill calls `mcp__plugin_ironclaude_state-manager__record_review_verdict` to record the grade, and GBTW advances tasks on passing grades.
   - Once review passes, the MCP clears review_pending
   - After code-review returns, call MCP `mcp__plugin_ironclaude_state-manager__mark_executing` to transition back from `reviewing` to `executing`

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
- Call the MCP `mcp__plugin_ironclaude_state-manager__retreat` tool with `to: "brainstorming"` and `reason: "explanation"`
- This preserves progress history and transitions back to brainstorming
- The user can rethink the approach and create a new plan

**Interruption handling:**

If user appears to be changing topic or requesting different work during execution:
1. Recognize this as a potential interruption
2. Invoke the `plan-interruption` skill
3. Follow that skill's process to handle the state transition

### Phase 3: Completion

**Step 7: Plan complete**

When `mcp__plugin_ironclaude_state-manager__get_next_tasks` returns `{status: "complete"}`:
1. The MCP automatically transitions workflow to execution_complete
2. Suggest a commit message based on the plan's goal and changes:

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
