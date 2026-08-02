# Code Review Checklist

Specific check procedures for the code-review skill. Each check includes what to look for, how to verify, and when to suppress.

---

## CRITICAL CHECKS (block on failure)

### SQL & Data Safety

- [ ] **Raw SQL with string interpolation** — Search diff for SQL strings built with `${}`, `f""`, `.format()`, or `+` concatenation containing variables. Verify parameterized queries or ORM methods are used instead.
- [ ] **Database migrations** — Check for irreversible schema changes (column drops, type narrowing). Verify rollback strategy exists.
- [ ] **Bulk operations without limits** — UPDATE/DELETE without WHERE clause or with unbounded scope.

DO NOT flag: ORM queries with hardcoded values, parameterized queries, read-only queries.

### Race Conditions & Concurrency

- [ ] **Read-then-write without atomicity** — Pattern: read a value, make a decision, write back. Check for atomic operations, database-level WHERE guards, or locking.
- [ ] **Status transitions without WHERE guard** — `UPDATE SET status='new' WHERE id=X` should include `AND status='expected_old'` to prevent double-processing.
- [ ] **Shared mutable state in async paths** — Variables modified from multiple async/concurrent code paths without synchronization.

DO NOT flag: Single-threaded scripts, purely functional code, immutable data structures.

### LLM Output Trust Boundary

- [ ] **LLM response in SQL/shell/file path** — Any LLM-generated text used to construct database queries, shell commands, or file system paths must be validated/sanitized before use.
- [ ] **LLM response rendered in UI** — Check for HTML escaping/sanitization when displaying LLM output in web contexts.
- [ ] **LLM response as structured data** — When parsing LLM output as JSON/YAML/etc for programmatic use, verify schema validation before acting on the parsed data.

DO NOT flag: LLM output displayed as plain text to the requesting user, LLM output logged for debugging.

### Command Injection

- [ ] **External data in shell commands** — User input, environment variables, or file contents interpolated into `exec()`, `spawn()`, `system()`, or backtick commands. Check for argument arrays vs string interpolation.
- [ ] **Template strings in subprocess calls** — Shell commands built with template literals containing variables.

DO NOT flag: Hardcoded commands with no external input, argument arrays (safe by construction).

### Falsifiability at the end state

- [ ] **Guard that cannot fail** — For every added assertion, guard, or `expected:` value, name the deletion that would make it fail. Re-evaluate that expression against the state where **every change in this branch has landed**, not the state at the line where it appears. If it still holds, the check verifies nothing.
- [ ] **Guard defused by a sibling change** — A check whose subject is relocated or rewritten by another change in the same diff: a path moved under a redirected root, a count the diff itself alters, a marker whose text another step edits.

DO NOT flag: checks weaker than ideal that still fail on a real regression, deliberate assertions of a current limitation where the diff or plan says it is removed later, documentation, comments, logging.

### Provenance for factual claims

- [ ] **Unsupported count, line number, or symbol** — List every numeric or positional claim in the diff and its commit/plan text; for each, find the command output or file read that establishes it.
- [ ] **Agent summary as evidence** — A claim whose only support is a summary — an agent report, a prior document, a recollection — is unverified provenance. A count derived from a line range rather than traced through the code is unverified.

DO NOT flag: claims the diff itself demonstrates (a test asserting the value), approximations explicitly marked as such, values whose supporting command appears in the same change.

### Widened-guard scope

- [ ] **Loosened allowlist, matcher, or validation without negative cases** — Identify any pattern or predicate the diff makes more permissive. Compare the scope the requirement states against the scope the code actually admits — read the regex or condition, do not trust its description.
- [ ] **Widening tested only with positive cases** — Passing examples prove nothing about what the guard still refuses. Require negative cases bounding the newly-admitted set.

DO NOT flag: widenings whose negative cases exist elsewhere in the same suite (say where), pure refactors that preserve the admitted set.

---

## INFORMATIONAL CHECKS (report, don't block)

### Conditional Side Effects

- [ ] **Side effects in conditional branches** — DB writes, API calls, file operations, or message sends inside if/else or switch branches. Verify all conditional paths are tested, not just the happy path.
- [ ] **Silent exception swallowing** — Catch blocks that don't log, re-throw, or otherwise surface the error. Flag if the caught error could mask a real failure.

### Enum & Value Completeness

- [ ] **New enum/status/type value added** — When the diff introduces a new constant value: use Grep to find ALL files that reference sibling values from the same enum/type. Read those files and check if the new value is handled in every switch/case/match/map.
- This check REQUIRES reading code OUTSIDE the diff. It is the one category where within-diff review is insufficient.

### Dead Code & Consistency

- [ ] **Unreferenced functions in diff** — Functions or methods added in the diff that are never called anywhere.
- [ ] **Unused imports** — Imports added but not referenced in the file.
- [ ] **Commented-out code blocks** — Blocks of commented code (not explanatory comments). Should be deleted, not commented.

### Documentation Staleness

- [ ] **Code changed but docs not updated** — List all `.md` files in the repo (excluding `docs/plans/`). For each, check if code changes in the diff affect features or components described in that doc. Flag if the `.md` was NOT updated but the code it describes WAS changed.
- This check is informational only. Never critical, never AUTO-FIX.
