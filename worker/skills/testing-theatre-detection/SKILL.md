---
name: testing-theatre-detection
description: Detect and eliminate testing theatre - tests that can't prevent regressions
---

# Test Quality Auditor

## Purpose

Enforce zero tolerance for testing theatre across all test types.

## MANDATORY: Structured User Input

Whenever soliciting user input — choices, confirmations, or selections — ALWAYS use the `AskUserQuestion` tool. NEVER ask via prose. Follow the format in `.claude/rules/ask-user-question-format.md`: Re-ground context, Predict, Options.

## When to Use

1. Manual: User runs /testing-theatre-detection
2. Automatic: Invoked by code-review skill

## Process

**Announce professional mode status:**
```
Using testing-theatre-detection skill. Professional mode is ACTIVE - architect mode enforced (no code changes).
```

## Determining Scope

**If invoked with exact file path:**
Skip scope determination. Analyze the provided file directly.
Example: `/testing-theatre-detection src/auth/login.test.js`

**If invoked from plan execution (subagent with task context):**
Identify tests related to the specific task/feature:
1. Check task description for mentioned files/components
2. Use Grep to find test files importing those components
3. Use git diff to find modified test files in task branch
4. Analyze all identified test files

**If manual invocation (no file path provided):**
Use AskUserQuestion tool:
- question: "What scope should I analyze?"
- header: "Analysis scope"
- options: "Current changes" (test files in git diff) | "Entire test suite" (all test files in the project) | "Specific path" (I'll specify a path) | "Task-specific" (tests for a particular feature or task)

**If auto-invoked (from code-review):**
Automatically use current changes (git diff --name-only)

## Finding Test Files

Run appropriate command based on scope:
- **Single file:** Use the exact file path provided (skip discovery)
- **Task-specific:**
  - Parse task description for component/file names
  - Use Grep to find test files: `import.*{ComponentName}` or `import.*'path/to/file'`
  - Use git diff if on task branch
- **Current changes:** `git diff --name-only | grep -E '\.(test|spec)\.(js|ts|tsx|java|py)$'`
- **Entire suite:** Use Glob tool with patterns: `**/*.test.js`, `**/*Test.java`, `**/test_*.py`
- **Specific path:** Use Glob tool with user-provided path

## Auto-Detecting Test Framework

For each test file, determine framework:

**Jest Detection:**
- File extension: `.test.js`, `.spec.js`, `.test.ts`, `.spec.ts`, `.test.tsx`, `.spec.tsx`
- Import patterns: `import { test, expect } from`, `import { describe, it } from`
- Framework: `jest`

**JUnit Detection:**
- File pattern: `*Test.java`, `*Tests.java`
- Import patterns: `import org.junit`, `@Test`, `@Disabled`
- Framework: `junit`

**pytest Detection:**
- File pattern: `test_*.py`, `*_test.py`
- Import patterns: `import pytest`, `@pytest.mark`
- Framework: `pytest`

**React Testing Library Detection:**
- File extension: `.test.tsx`, `.spec.tsx`
- Import patterns: `import { render } from '@testing-library/react'`
- Framework: `jest` + `react-testing-library`

If framework cannot be determined:
- Use AskUserQuestion tool:
  - question: "What test framework is {file} using?"
  - header: "Test framework"
  - options: "Jest" (JavaScript/TypeScript) | "JUnit" (Java) | "pytest" (Python) | "React Testing Library" (Jest + RTL)
- Fall back to generic pattern matching

## Static Analysis Phase

### Check 1: Pending/Skipped Tests

**Jest patterns to detect:**
- `it.skip(`, `test.skip(`, `xit(`, `xtest(`, `describe.skip(`
- Search pattern: Use Grep tool with: `\.(skip|xit|xtest)\(`

**JUnit patterns to detect:**
- `@Disabled`, `@Ignore` annotations
- Search pattern: Use Grep tool with: `@(Disabled|Ignore)`

**pytest patterns to detect:**
- `@pytest.mark.skip`, `@pytest.mark.xfail`
- Search pattern: Use Grep tool with: `@pytest\.mark\.(skip|xfail)`

**For each match found:**
1. Extract line number
2. Extract test name from surrounding context
3. Add to issues list:
   ```
   Issue: Skipped Test (Critical)
   Line {number}: {test name}
   Problem: Test is disabled - known broken behavior being ignored
   Risk: Production bug hiding behind disabled test
   Fix: Remove skip/disable annotation and fix the failing test
   ```

### Check 2: Missing/Weak Assertions

**Detection approach:**
For each test function, count assertion statements.

**Jest assertions to count:**
- `expect(` statements
- `assert(` statements
- Search pattern: Use Grep tool with: `expect\(|assert\(`

**JUnit assertions to count:**
- `assert`, `assertEquals`, `assertTrue`, `assertFalse`, etc.
- Search pattern: Use Grep tool with: `assert[A-Z]\w+\(`

**pytest assertions to count:**
- `assert ` statements
- Search pattern: Use Grep tool with: `^\s+assert\s`

**Tautological assertion patterns:**
- Jest: `expect(x).toBe(x)`, `expect(true).toBe(true)`
- JUnit: `assertTrue(true)`, `assertEquals(x, x)`
- Python: `assert True`, `assert x == x`

**For each test with zero assertions:**
1. Extract test name and line number
2. Add to issues list with code example:
   ```javascript
   Issue: No Assertions (Critical)
   Line {number}: test "{name}"

   Problem: Test has no expect() calls - always passes regardless of implementation
   Risk: Cannot detect regressions in {component} behavior

   Fix:
   test('{name}', () => {
     // Arrange
     const result = functionUnderTest(input);

     // Assert
     expect(result).toBe(expectedValue);
     expect(result.property).toEqual(expectedProperty);
   });
   ```

### Check 3: Over-Mocking

**Detection approach:**
Count mock statements vs real code invocations in each test.

**Mock patterns to count:**

**Jest:**
- `jest.mock(`, `jest.spyOn(`, `mockImplementation`, `mockReturnValue`
- Search pattern: `jest\.(mock|spyOn)|mock(Implementation|ReturnValue)`

**JUnit:**
- `@Mock`, `Mockito.mock(`, `when(`, `verify(`
- Search pattern: `@Mock|Mockito\.(mock|when|verify)`

**pytest:**
- `@patch`, `Mock()`, `MagicMock()`
- Search pattern: `@patch|Mock\(\)|MagicMock\(\)`

**Calculate ratio:**
- Mock lines / total test lines
- If ratio > 0.8 (80%), flag as over-mocking

**For each over-mocked test:**
1. Add to issues list:
   ```
   Issue: Over-Mocking (Critical)
   Line {number}: test "{name}"

   Problem: {percentage}% of test is mocking - not testing real behavior
   Risk: Tests pass but production code may be broken

   Fix: Reduce mocking. Test real integrations when possible:
   - Mock external dependencies (APIs, databases) at boundaries
   - Use real implementations for internal logic
   - Integration tests should test actual integration
   ```

### Check 4: Snapshot-Only Tests

**Detection approach:**
Find tests that ONLY use snapshot assertions without behavioral assertions.

**Snapshot patterns:**
- Jest: `toMatchSnapshot()`, `toMatchInlineSnapshot()`
- Search pattern: `toMatchSnapshot|toMatchInlineSnapshot`

**Check logic:**
1. Count snapshot assertions in test
2. Count non-snapshot assertions (expect, assert, etc.)
3. If snapshots > 0 AND non-snapshot === 0, flag as snapshot-only

**For each snapshot-only test:**
1. Add to issues list:
   ```
   Issue: Snapshot Only (Critical)
   Line {number}: test "{name}"

   Problem: Only uses toMatchSnapshot() with no behavior validation
   Risk: Doesn't verify {component} actually works

   Fix: Add assertions for interactive behavior:
   - Test click handlers are called with correct arguments
   - Test state changes correctly
   - Test props are applied correctly
   - Test accessibility attributes
   - Then use snapshots as supplementary check
   ```

### Check 5: Error Swallowing

**Detection approach:**
Find try/catch blocks or conditional logic that can prevent test failures.

**Patterns to detect:**

**Try/catch with no rethrow:**
- Jest/JS: `try { ... } catch (e) { }` or `catch (e) { console.log }`
- Java: `catch (Exception e) { }` with no throw/fail
- Python: `except: pass` or `except Exception:`

**Conditional assertions:**
- `if (condition) { expect(...) }` - assertion might not run

**Search patterns:**
- Try/catch: Use Grep with: `catch\s*\([^)]+\)\s*\{\s*\}`
- Conditional assertions: Use Read tool to parse test structure

**For each error swallowing pattern:**
1. Add to issues list:
   ```
   Issue: Error Swallowing (Critical)
   Line {number}: test "{name}"

   Problem: Try/catch or conditional logic can prevent test failure
   Risk: Test passes even when code throws errors

   Fix: Either:
   - Remove try/catch and let test fail on error
   - If testing error handling, assert the error: expect(() => fn()).toThrow()
   - Remove conditional logic around assertions
   ```

## Dynamic Analysis Phase

### Running Tests

**Find test command:**

1. **Check package.json** (for JS/TS projects):
   - Read file with Read tool
   - Parse JSON and look for `scripts.test` or `scripts.test-ci`
   - Command: `npm test` or `yarn test`

2. **Check Makefile** (for any project):
   - Use Grep to find: `^test:|^test-.*:`
   - Command: `make test`

3. **Check build.gradle** (for Java projects):
   - Use Grep to find: `task test`
   - Command: `./gradlew test`

4. **Direct framework invocation** (fallback):
   - Jest: `npx jest --coverage`
   - JUnit: `./gradlew test` or `mvn test`
   - pytest: `pytest --cov`

5. **If ambiguous, use AskUserQuestion tool:**
   - question: "I found multiple test commands. Which should I use?"
   - header: "Test command"
   - options: one option per discovered command (label = command string, description = where it was found)

**Execute test command:**
Run with Bash tool:
- Capture stdout, stderr, and exit code
- Set timeout to 10 minutes (tests can be slow)
- Continue even if exit code != 0

Example:
```bash
npm test 2>&1
```

### Parsing Test Output

**Check 1: Exit Code**
- If exit code != 0: Tests failed
- Add to issues:
  ```
  ❌ Test Failures (Critical)

  Problem: {count} tests failed
  Risk: Cannot assess test quality when tests don't pass

  Fix: Address test failures first:
  {list of failed tests from output}
  ```

**Check 2: Warning Messages**

Scan output for warning patterns:
- `WARN:`, `WARNING:`, `Warning:`
- `deprecated`, `deprecation`
- `(node:`) with warning

For each warning:
```
⚠️ Test Warning (Critical)

Problem: Test output contains warning
Warning: {warning text}
Risk: Warnings indicate unreliable test behavior

Fix: Address the warning - update deprecated APIs, fix configuration
```

**Check 3: Error Messages (even if tests pass)**

Scan output for error patterns:
- `Error:`, `ERROR:`
- `Exception:`
- `failed to`

For each error in passing tests:
```
❌ Hidden Error (Critical)

Problem: Test output contains errors but tests still pass
Error: {error text}
Risk: Test is swallowing errors

Fix: Update test to fail on errors or fix the underlying issue
```

**Check 4: Flaky Indicators**

Scan for flaky patterns:
- `timeout`, `timed out`
- `ETIMEDOUT`, `ECONNREFUSED`
- `UnhandledPromiseRejection`
- `race condition`

For each flaky indicator:
```
🔀 Flaky Test Indicator (Critical)

Problem: Test output suggests flaky/unreliable behavior
Indicator: {text}
Risk: Test may pass/fail randomly

Fix:
- Add proper async/await handling
- Increase timeouts if necessary
- Fix race conditions
- Mock unstable dependencies
```

### Coverage Analysis

**Find coverage reports:**

**Jest (JSON format):**
- Default location: `coverage/coverage-final.json`
- Read with Read tool
- Parse JSON to get coverage metrics

**JUnit (JaCoCo XML format):**
- Default location: `build/reports/jacoco/test/jacocoTestReport.xml`
- Read with Read tool
- Parse XML for coverage percentages

**pytest (JSON format via pytest-cov):**
- Default location: `.coverage` or `coverage.json`
- Read with Read tool

**Coverage metrics to extract:**
- Statements covered / total
- Branches covered / total
- Functions covered / total
- Lines covered / total

**Correlation with static analysis:**
1. Compare coverage % to assertion count from static analysis
2. Flag: High coverage (>80%) with low assertion count (<10)

```
⚠️ High Coverage, Low Assertions (Critical)

Problem: {coverage}% code coverage but only {count} assertions
Risk: Executing code without verifying behavior - false confidence

Fix: Add meaningful assertions that verify:
- Return values
- State changes
- Side effects
- Error conditions
```

## Generating Report

**Report structure:**

```
Testing Theatre Audit Report
=============================

Scope: {scope description} ({count} test files analyzed)
Status: {✅ CLEAN or ❌ {count} issues found (MUST FIX)}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

📁 {file_path} ({issue_count} issues)

  {for each issue in file:}
  ❌ {Issue Type} (Critical)
  Line {number}: {test name}

  Problem: {description}
  Risk: {impact}

  Fix: {guidance with code example if applicable}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Summary: {total_issues} critical issues across {file_count} files
All issues must be fixed before production readiness.
```

**Grouping logic:**
1. Group issues by file
2. Within each file, sort by line number
3. Show file path relative to project root
4. Include issue count per file

**Issue formatting:**
- All issues labeled "Critical"
- Include line number for navigation
- Include test name for context
- Provide problem description, risk, and fix guidance
- Add code examples for concrete fixes (no assertions, skipped tests)
- Add bullet points for conceptual fixes (over-mocking, architecture)
