---
name: plan-interruption
description: Handle graceful interruption of plan execution
---

# Plan Interruption

## Purpose

Handle situations where plan execution is interrupted, ensuring clean state transitions between execution mode and architect mode.

## When to Use

**Claude should invoke this skill when detecting interruption during plan execution:**

1. User asks about unrelated topic during execution
2. User requests work on different files/features
3. User says "stop", "hold on", "wait", "actually", or similar
4. After context compaction when execution state is unclear
5. User explicitly invokes `/plan-interruption`

**Signs of interruption:**
- Question about different feature/file than current task
- Request to "look at" or "check" something unrelated
- Explicit stop/pause language
- Topic change mid-task

## Process

**Announce interruption detection:**
```
⚠️ Plan Interruption Detected

I notice we may be interrupting the current plan execution:
  Plan: <current-plan-name>
  Progress: Task N of M
```

### Step 1: Confirm Interruption

Ask user using AskUserQuestion tool:

```
Is this interruption intentional?

A) Yes, stop the plan - Set plan_interrupted, revert to ARCHITECT MODE
B) No, continue the plan - Resume from Task N
C) Pause for now - Set plan_interrupted, I'll re-acquire later with /executing-plans
```

### Step 2: Handle User Choice

**If A (Stop the plan) or C (Pause for now):**

Both options set plan_interrupted. The skill-state-bridge hook set workflow_stage to 'plan_interrupted'. Plan state (plan_json, wave_tasks) is preserved so the plan can be resumed later with /executing-plans.

1. Announce state change:
   ```
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   Plan Execution [Stopped/Paused]
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

   Plan: <plan-name>
   [Stopped/Paused] at: Task N of M

   Plan state preserved (workflow set to plan_interrupted).
   Mode: ARCHITECT (code changes blocked)

   To resume this plan later:
     /executing-plans docs/plans/<plan-file>.md

   Ready for your next request.
   ```

3. Proceed with user's new request in architect mode.

**If B (Continue the plan):**

1. Announce continuation:
   ```
   Continuing plan execution from Task N.
   ```

2. Resume execution from current task.

## Key Principles

- **Always ask**: Never assume what user wants
- **State preserved**: Any interruption (stop or pause) sets workflow_stage to plan_interrupted. Plan state is preserved for possible resume.
- **Explicit re-acquisition**: Resuming requires `/executing-plans` invocation
- **Self-documenting**: Clear messages about current state

## Integration with executing-plans

The `executing-plans` skill should reference this skill in its failure handling:

> If user appears to be changing topic during execution, invoke the `plan-interruption` skill.
