---
name: code-review
description: Review completed work for quality, bugs, and standards
---

# Code Review

## Purpose

Review completed work against the original plan, checking for code quality, bugs, security issues, standards compliance, and testing theatre. This skill is automatically invoked between tasks during plan execution.

## When to Use

- Automatically invoked by executing-plans skill after each task
- User manually requests code review with `/code-review`
- Before creating a pull request
- After completing a significant feature

## MANDATORY: Structured User Input

Whenever soliciting user input — choices, confirmations, or selections — ALWAYS use the `AskUserQuestion` tool. NEVER ask via prose. Follow the format in `.claude/rules/ask-user-question-format.md`: Re-ground context, Predict, Options.

## Process

**Announce professional mode status:**
```
Using code-review skill. Professional mode is ACTIVE - architect mode enforced (no code changes).
```

### Phase 1: Understand Context

**Step 1: Identify what changed**

```bash
git diff --staged --name-only
```

If no staged changes:
```bash
git diff HEAD~1 --name-only
```

**Step 2: Read the plan (if available)**

Look for plan in docs/plans/:
Use the Glob tool with pattern `docs/plans/*.md` to find plan documents (exclude *-design.md files).

If plan exists:
- Read the plan to understand intended behavior
- Note which task was just completed
- Understand success criteria

### Phase 2: Stage 1 — Spec Compliance Review

**This stage MUST complete before Stage 2 begins.**

**Step 3: Compare implementation against plan/design**

For each task in the plan:
- Was it implemented? Check every requirement.
- Were any requirements silently dropped or partially implemented?
- Does the implementation match the design architecture?
- Were any scope items added that weren't in the plan?

**Default posture:** The implementer may have finished suspiciously quickly. Their self-report may be incomplete or inaccurate. Verify EVERY requirement against actual code — do not trust summaries or claims of completion.

For each requirement in the plan:
- Find the exact line(s) in the diff that implement it
- If you cannot point to specific code, the requirement is NOT implemented
- Read the changed files directly — do not rely on the implementer's description

**Step 4: Report spec compliance**

Display:
```
Stage 1: Spec Compliance
Plan: [plan name]

Implemented:
- [requirement] → file.py:123-145
- [requirement] → file.py:67, file.py:89

Missing:
- [requirement] — no corresponding code found in diff

Unplanned: [list of additions not in plan]

Stage 1 result: [PASS | FAIL]
```

If Stage 1 FAILS:
1. **Record grade F immediately:** Call `mcp__plugin_ironclaude_state-manager__record_review_verdict` with `grade: "F"` and `task_boundary: true` (if invoked with `--task-boundary`) or `task_boundary: false` (if standalone).
2. **Do NOT proceed to Stage 2.** The F grade blocks task advancement via GBTW.
3. Report the failure with specific missing requirements and recommend fixing before re-running code review.

### Phase 3: Stage 2 — Code Quality Review

**Only proceed here after Stage 1 passes.**

**Step 4.5: Load review checklist**

Read `.claude/rules/review-checklist.md` (if it exists). Apply each check procedure against the diff, following the specific detection steps and respecting the "DO NOT flag" suppressions. If the file doesn't exist, fall back to the generic checks below.

**Step 5: Check for common issues**

For each changed file, check:

**Security issues:**
- SQL injection vulnerabilities
- XSS vulnerabilities
- Command injection
- Hardcoded secrets/credentials
- Insecure authentication/authorization
- Missing input validation

**Code quality:**
- Functions doing too much (SRP violation)
- Deep nesting (>3 levels)
- Magic numbers without explanation
- Commented-out code
- Console.log/print statements left in
- Dead code

**Standards compliance:**
- Follows project naming conventions
- Proper error handling
- Appropriate logging
- Documentation for complex logic

**Architecture:**
- Matches plan architecture
- Doesn't introduce unnecessary coupling
- No premature optimization
- YAGNI - no unnecessary features

**TDD compliance:**
For tasks involving executable code, check `git diff --staged` for test file changes alongside implementation changes. If implementation files were modified but no corresponding test files exist:
- Check the plan's task description for "No tests required: [reason]"
- If documented: accept (the plan author made a deliberate decision)
- If NOT documented: flag as Important issue ("Implementation changed without corresponding test changes — was TDD followed?")

**Documentation staleness:**
List all `.md` files in the repo (excluding `docs/plans/`). For each, check if code changes in the diff affect features or components described in that doc. If the `.md` was NOT updated in the diff but the code it describes WAS changed, flag as INFORMATIONAL: `"Documentation may be stale: [file] describes [feature/component] but code changed in this branch."` This check is informational only — never critical, never AUTO-FIX.

### Phase 4: Testing Review

**Step 6: Invoke testing-theatre-detection**

Automatically invoke the testing-theatre-detection skill:

```
Invoking testing-theatre-detection skill to check test quality.
[Use Skill tool to invoke testing-theatre-detection]
```

Wait for results. Note any issues found.

**Step 6.5: Verify testing-theatre-detection was invoked**

Before assigning a final grade, check the `testing_theatre_checked` flag:

```
Use MCP tool: mcp__plugin_ironclaude_state-manager__get_testing_theatre_status
(no parameters required)
```

If the tool returns `testing_theatre_checked: 0`, or returns an error (treat as 0):
- **Cap the grade at C regardless of other findings.**
- Display: "Testing-theatre-detection was not invoked. Grade capped at C. Run testing-theatre-detection before finalizing the review."
- Do NOT record the verdict until testing-theatre-detection has been run.

If the tool returns `testing_theatre_checked: 1`: Proceed normally with grade assignment.

### Phase 5: Report Findings

**Step 7: Categorize issues**

Organize findings into:
- **Critical**: Security vulnerabilities, broken functionality, testing theatre
- **Important**: Code quality issues, standards violations, architecture concerns
- **Minor**: Style issues, documentation gaps

**Step 7.1: Fix-First Pass (task-boundary reviews only)**

If invoked with `--task-boundary` AND workflow_stage is `executing` or `reviewing`:

1. Classify each finding as AUTO-FIX or ASK:
   - **AUTO-FIX** (mechanical, unambiguous, low risk): missing imports, obvious typos, formatting violations, unused imports/variables in diff, console.log/print left in, missing semicolons
   - **ASK** (requires judgment): security vulnerabilities, architecture concerns, logic errors, missing error handling, anything where two developers might disagree on the fix

2. Apply AUTO-FIX items directly:
   - For each: make the edit, output: `[AUTO-FIXED] [file:line] Problem → what was done`

3. ASK items follow the current process (report, recommend, AskUserQuestion if important+).

4. Grade reflects the post-AUTO-FIX state. Successfully auto-fixed items don't count against the grade.

If NOT invoked with `--task-boundary` (standalone review): skip this step. Standalone reviews are report-only.

**Step 7.5: Assign letter grade**

Based on findings, assign an overall grade using this rubric:

| Grade | Criteria |
|-------|----------|
| A | Stage 1 PASS + Stage 2 no issues |
| B | Stage 1 PASS + Stage 2 minor issues only |
| C | Stage 1 PASS + one or more important issues (no criticals) |
| D | Stage 1 PASS + multiple important issues or borderline critical |
| F | Stage 1 FAIL **or** any critical issue (security vulnerability, broken functionality, testing theatre) |

**Step 8: Generate report**

Display:
```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Code Review Results
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Files reviewed: N
Stage 1 (Spec Compliance): [PASS | FAIL]
Stage 2 (Code Quality): [PASS | ISSUES FOUND | CRITICAL ISSUES]
Overall Grade: [A/B/C/D/F] — [one-sentence rationale]

[If issues found:]

Critical Issues (must fix):
- [file.py:123] SQL injection vulnerability in query construction
- [test.py:45] Test has no assertions - testing theatre

Important Issues (should fix):
- [api.py:67] Function exceeds 50 lines - consider splitting
- [utils.py:12] No error handling for file operations

Minor Issues (nice to fix):
- [component.js:34] Missing JSDoc comment
- [styles.css:89] Inconsistent indentation

[If no issues:]

✅ Code review passed

All checks passed:
✓ No security vulnerabilities
✓ Code quality acceptable
✓ Standards compliant
✓ Architecture matches plan
✓ No testing theatre

Ready to proceed.
```

**Step 8.5: Record review verdict (--task-boundary only)**

If invoked with `--task-boundary` argument, call the `mcp__plugin_ironclaude_state-manager__record_review_verdict` MCP tool:

```
Use MCP tool: mcp__plugin_ironclaude_state-manager__record_review_verdict with:
  grade: [the letter grade from Step 7.5]
  task_boundary: true
```

The tool auto-determines submitted task_ids and wave_number from the DB.

If the MCP call fails (tool unavailable, or error), display a warning and continue — do NOT block skill completion:
```
⚠️ Warning: mcp__plugin_ironclaude_state-manager__record_review_verdict failed — GBTW will block on next stop.
Grade is captured in the narrative above.
```

If invoked WITHOUT `--task-boundary` (standalone review): skip this step entirely.

**Step 9: Recommend action**

Based on findings:

- **Critical issues found:**
  ```
  ❌ Critical issues must be fixed before proceeding.

  Recommend: Fix issues now.
  ```

- **Important issues found:**

  Use AskUserQuestion tool:
  - question: "⚠️ Important issues found. How would you like to proceed?"
  - header: "Important issues"
  - options: "Fix issues now (Recommended)" (address all important issues before proceeding) | "Create TODO comments and proceed" (add TODOs and continue to next task) | "Proceed anyway" (ignore issues and continue — not recommended)

- **Only minor issues or no issues:**
  ```
  ✅ Ready to proceed to next task.
  ```

## Key Principles

- **Always check plan**: Verify implementation matches intended design
- **Security first**: Critical issues block progress
- **Auto-invoke testing-theatre-detection**: Always check test quality
- **Categorize findings**: Clear distinction between critical, important, minor
- **Be specific**: Include file paths and line numbers for all issues
- **Recommend action**: Clear next steps based on findings
- **Professional mode aware**: Report issues but don't fix them directly (unless executing-plans is active)
