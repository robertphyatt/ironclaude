---
name: systematic-debugging
description: Systematic root-cause investigation before fixes
---

# Systematic Debugging

## Purpose

Perform systematic root-cause investigation before proposing fixes. This skill prevents guess-and-check debugging by enforcing a structured investigative process.

## When to Use

- User reports a bug or unexpected behavior
- Tests are failing
- Application crashes or errors occur
- Performance issues need investigation

## Process

**Announce professional mode status:**
```
Using systematic-debugging skill. Professional mode is ACTIVE - architect mode enforced (no code changes).
```

### Phase 1: Reproduce and Document

**Step 1: Understand the symptom**

Ask clarifying questions ONE at a time:
1. What is the expected behavior?
2. What is the actual behavior?
3. When did this start happening?
4. What changed recently?
5. Can you reproduce it consistently?

**Step 2: Reproduce the issue**

Attempt to reproduce:
```bash
# Run the failing test, command, or scenario
<exact command from user>
```

Document:
- Exact error message
- Stack trace
- Exit code
- Environment details

### Phase 2: Root Cause Investigation

**Step 3: Trace through code**

Starting from error location:
1. Read the failing function/method
2. Identify where error originates
3. Check inputs to that function
4. Trace back to where those inputs come from
5. Continue until you find the source

**Do NOT guess.** Read actual code at each step.

**Step 4: Check related files**

Look for:
- Recent commits that touched this code
- Configuration changes
- Dependency updates
- Environment differences

```bash
# Check recent changes to file
git log -5 --oneline <file-path>

# Check what changed in last commit
git diff HEAD~1 <file-path>
```

**Step 5: Form hypothesis**

Based on investigation, state hypothesis:
```
Hypothesis: The error occurs because <specific reason>.

Evidence:
- [file.py:123] Variable X is undefined
- [config.yml:45] Setting Y was recently changed
- [test.log:67] Error message indicates Z

Expected: Fixing <specific issue> will resolve the error.
```

**Step 6: Test hypothesis**

Before making fixes, test the hypothesis:
- Add temporary logging to verify theory
- Create minimal reproduction case
- Test in isolation

```bash
# Add debug output
# Run test again
# Verify hypothesis is correct
```

### Phase 3: Plan Fix

**Step 7: Design minimal fix**

Based on confirmed root cause:
```
Root cause: <confirmed issue>

Minimal fix:
1. <specific change 1>
2. <specific change 2>

Files to modify:
- <file-path>:<line-numbers>

Why this fixes it:
<explanation>
```

**Step 8: Create or execute plan**

If fix is simple (1-2 files, < 20 lines):
```
This is a simple fix. Ready to implement?
[If yes: Use executing-plans or make changes directly if execution mode active]
```

If fix is complex:
```
This is a complex fix requiring multiple changes.

Recommend: Use writing-plans skill to create implementation plan.
[Use Skill tool to invoke writing-plans]
```

### Phase 4: Verify Fix

**Step 9: Test the fix**

After implementation:
```bash
# Run the originally failing test/command
<exact command that failed before>
```

Expected: Success

**Step 10: Check for regressions**

```bash
# Run full test suite
<test command>
```

Expected: All tests pass

**Step 11: Document the fix**

In commit message or documentation:
```
Root cause: <what was wrong>
Fix: <what was changed>
Verification: <how it was tested>
```

## Key Principles

- **Evidence before assertions**: Never claim without verification
- **Trace, don't guess**: Read actual code, don't assume
- **Root cause, not symptoms**: Fix the underlying issue, not workarounds
- **Test hypothesis**: Verify theory before implementing fix
- **Minimal fix**: Smallest change that addresses root cause
- **No "likely" or "probably"**: No guessing allowed - verify everything
- **Professional mode aware**: Investigate and plan fix, don't immediately change code
