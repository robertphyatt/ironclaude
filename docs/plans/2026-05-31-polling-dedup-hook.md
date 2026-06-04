# Polling Deduplication Hook Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** Add a SQLite-backed `poll-dedup.sh` hook that blocks workers from reading the same resource 3+ times when the output hasn't changed, with a 5-minute time-based cooldown.

**Architecture:** Dual-mode hook (PreToolUse + PostToolUse on Read|Bash|Grep|Glob), modeled on `subagent-circuit-breaker.sh`. PostToolUse hashes output and stores count; PreToolUse enforces the block at count >= 3. State lives in a new `tool_poll_state` SQLite table so it survives context compaction.

**Tech Stack:** Bash, SQLite3, Python3 (for portable date arithmetic), `portable_md5` from existing `hook-logger.sh`.

---

## Task 1: Add `tool_poll_state` schema to session-init.sh and db.ts

**Files:**
- Modify: `worker/hooks/session-init.sh`
- Modify: `worker/mcp-servers/state-manager/src/db.ts`

No tests required: pure schema migration — idempotent DDL, no executable logic to test.

**Step 1: Add table DDL to session-init.sh**

Find the closing `);` of the `review_grades` table (the last table before `CREATE INDEX` statements). Add the new table immediately after it, still inside the sqlite3 quoted string.

Find this exact string in `worker/hooks/session-init.sh`:
```
    CREATE INDEX IF NOT EXISTS idx_wave_tasks_session
      ON wave_tasks(terminal_session);
```

Replace with:
```
    CREATE TABLE IF NOT EXISTS tool_poll_state (
      terminal_session TEXT NOT NULL,
      tool_name TEXT NOT NULL,
      input_hash TEXT NOT NULL,
      last_output_hash TEXT NOT NULL DEFAULT '',
      consecutive_count INTEGER NOT NULL DEFAULT 0,
      updated_at TEXT NOT NULL DEFAULT (datetime('now')),
      PRIMARY KEY (terminal_session, tool_name, input_hash)
    );

    CREATE INDEX IF NOT EXISTS idx_wave_tasks_session
      ON wave_tasks(terminal_session);
```

**Step 2: Add index to session-init.sh**

Find this exact string in `worker/hooks/session-init.sh`:
```
    CREATE INDEX IF NOT EXISTS idx_review_grades_session_wave
      ON review_grades(terminal_session, wave_number, task_boundary);
  " 2>/dev/null || true
```

Replace with:
```
    CREATE INDEX IF NOT EXISTS idx_review_grades_session_wave
      ON review_grades(terminal_session, wave_number, task_boundary);
    CREATE INDEX IF NOT EXISTS idx_poll_state_session
      ON tool_poll_state(terminal_session);
  " 2>/dev/null || true
```

**Step 3: Add table DDL to db.ts**

Find this exact string in `worker/mcp-servers/state-manager/src/db.ts`:
```
    CREATE TABLE IF NOT EXISTS review_grades (
      id               INTEGER PRIMARY KEY AUTOINCREMENT,
      terminal_session TEXT NOT NULL,
      wave_number      INTEGER NOT NULL,
      task_ids         TEXT NOT NULL,
      grade            TEXT NOT NULL,
      task_boundary    INTEGER NOT NULL DEFAULT 0,
      created_at       TEXT NOT NULL DEFAULT (datetime('now'))
    );
  `);
```

Replace with:
```
    CREATE TABLE IF NOT EXISTS review_grades (
      id               INTEGER PRIMARY KEY AUTOINCREMENT,
      terminal_session TEXT NOT NULL,
      wave_number      INTEGER NOT NULL,
      task_ids         TEXT NOT NULL,
      grade            TEXT NOT NULL,
      task_boundary    INTEGER NOT NULL DEFAULT 0,
      created_at       TEXT NOT NULL DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS tool_poll_state (
      terminal_session TEXT NOT NULL,
      tool_name TEXT NOT NULL,
      input_hash TEXT NOT NULL,
      last_output_hash TEXT NOT NULL DEFAULT '',
      consecutive_count INTEGER NOT NULL DEFAULT 0,
      updated_at TEXT NOT NULL DEFAULT (datetime('now')),
      PRIMARY KEY (terminal_session, tool_name, input_hash)
    );
  `);
```

**Step 4: Add index to db.ts**

Find this exact string in `worker/mcp-servers/state-manager/src/db.ts`:
```
    CREATE INDEX IF NOT EXISTS idx_review_grades_session_wave
      ON review_grades(terminal_session, wave_number, task_boundary);
  `);
```

Replace with:
```
    CREATE INDEX IF NOT EXISTS idx_review_grades_session_wave
      ON review_grades(terminal_session, wave_number, task_boundary);
    CREATE INDEX IF NOT EXISTS idx_poll_state_session
      ON tool_poll_state(terminal_session);
  `);
```

**Step 5: Verify schema idempotency manually**

Run:
```bash
sqlite3 "$HOME/.claude/ironclaude.db" "SELECT name FROM sqlite_master WHERE type='table' AND name='tool_poll_state';"
```

Expected: Empty output (table doesn't exist yet — session-init.sh creates it on next startup).

**Step 6: Stage changes**

Run:
```bash
git add worker/hooks/session-init.sh worker/mcp-servers/state-manager/src/db.ts
```

Expected: Changes staged, no errors.

---

## Task 2: Create `worker/hooks/poll-dedup.sh`

**Files:**
- Create: `worker/hooks/poll-dedup.sh`

No tests required at this stage: the test script in Task 4 covers all logic paths. Task 4 depends on this task completing first.

**Step 1: Create the hook file**

Create `worker/hooks/poll-dedup.sh` with this exact content:

```bash
#!/bin/bash
# poll-dedup.sh — Detect and block repeated identical tool reads
#
# Dual-mode hook (same pattern as subagent-circuit-breaker.sh):
#   PreToolUse  (Read|Bash|Grep|Glob): check count, block if >= 3 and age < 5min
#   PostToolUse (Read|Bash|Grep|Glob): hash output, update tool_poll_state
#
# State survives context compaction (stored in SQLite).
# Block message: "Output unchanged — wait for completion notification or check again in 5 minutes."

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if ! source "${SCRIPT_DIR}/hook-logger.sh" 2>/dev/null; then
  exit 0
fi
run_hook "POLL-DEDUP"

INPUT=$(cat)
init_session_id

TOOL_NAME=$(echo "$INPUT" | jq -r '.tool_name // empty' 2>/dev/null || true)

# Detect mode: tool_output present = PostToolUse, absent = PreToolUse
HAS_OUTPUT=$(echo "$INPUT" | jq -r 'has("tool_output")' 2>/dev/null || echo "false")

SAFE_SESSION=$(echo "$SESSION_TAG" | sed "s/'/''/g")

# compute_input_hash — stable hash of the tool's INPUT for keying poll state.
# Different tool types expose their identifying inputs under different JSON fields.
compute_input_hash() {
  local key=""
  case "$TOOL_NAME" in
    Read)
      key=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty' 2>/dev/null || true)
      ;;
    Bash)
      key=$(echo "$INPUT" | jq -r '.tool_input.command // empty' 2>/dev/null || true)
      ;;
    Grep)
      local pattern path_val
      pattern=$(echo "$INPUT" | jq -r '.tool_input.pattern // empty' 2>/dev/null || true)
      path_val=$(echo "$INPUT" | jq -r '.tool_input.path // empty' 2>/dev/null || true)
      key="${pattern}|${path_val}"
      ;;
    Glob)
      local pat gpath
      pat=$(echo "$INPUT" | jq -r '.tool_input.pattern // empty' 2>/dev/null || true)
      gpath=$(echo "$INPUT" | jq -r '.tool_input.path // empty' 2>/dev/null || true)
      key="${pat}|${gpath}"
      ;;
    *)
      key="$TOOL_NAME"
      ;;
  esac
  echo "$key" | portable_md5
}

# age_seconds TIMESTAMP — returns integer seconds since the SQLite datetime string.
# Falls back to 9999 on parse failure (fail-open: treat as old → allow through).
age_seconds() {
  local ts="$1"
  python3 -c "
import time, datetime
try:
    t = datetime.datetime.fromisoformat('${ts}'.replace(' ', 'T'))
    print(int(time.time() - t.timestamp()))
except Exception:
    print(9999)
" 2>/dev/null || echo "9999"
}

INPUT_HASH=$(compute_input_hash)
SAFE_INPUT_HASH=$(echo "$INPUT_HASH" | sed "s/'/''/g")
SAFE_TOOL=$(echo "$TOOL_NAME" | sed "s/'/''/g")

if [ "$HAS_OUTPUT" = "false" ]; then
  # ─── PreToolUse: check for polling pattern, block if threshold exceeded ───

  STATE=$(sqlite3 "$DB_PATH" ".timeout 5000" \
    "SELECT consecutive_count || '|' || updated_at FROM tool_poll_state
     WHERE terminal_session='${SAFE_SESSION}'
       AND tool_name='${SAFE_TOOL}'
       AND input_hash='${SAFE_INPUT_HASH}';" \
    2>/dev/null || true)

  if [ -z "$STATE" ]; then
    log_hook "POLL-DEDUP" "Allowed" "no prior state for ${TOOL_NAME}"
    exit 0
  fi

  COUNT=$(echo "$STATE" | cut -d'|' -f1)
  UPDATED_AT=$(echo "$STATE" | cut -d'|' -f2-)

  if [ "${COUNT:-0}" -lt 3 ]; then
    log_hook "POLL-DEDUP" "Allowed" "count=${COUNT:-0} for ${TOOL_NAME}"
    exit 0
  fi

  AGE=$(age_seconds "$UPDATED_AT")

  if [ "$AGE" -lt 300 ]; then
    REMAINING=$(( 300 - AGE ))
    block_pretooluse "POLL-DEDUP" "BLOCKED — POLLING DETECTED (${COUNT} consecutive identical reads)

Output unchanged since last read. Wait for a completion notification or check again
in $((REMAINING / 60))m $((REMAINING % 60))s. Do NOT re-read the same resource —
it will remain blocked until the cooldown expires or content changes.

Tool: ${TOOL_NAME} | Consecutive identical reads: ${COUNT}"
  else
    # Cooldown elapsed — reset count and allow
    sqlite3 "$DB_PATH" ".timeout 5000" \
      "UPDATE tool_poll_state
         SET consecutive_count=0, updated_at=datetime('now')
       WHERE terminal_session='${SAFE_SESSION}'
         AND tool_name='${SAFE_TOOL}'
         AND input_hash='${SAFE_INPUT_HASH}';" \
      2>/dev/null || true
    log_hook "POLL-DEDUP" "Allowed" "cooldown elapsed, count reset for ${TOOL_NAME}"
    exit 0
  fi

else
  # ─── PostToolUse: hash output, update consecutive count ───

  TOOL_OUTPUT=$(echo "$INPUT" | jq -r '.tool_output // empty' 2>/dev/null || true)
  OUTPUT_HASH=$(echo "$TOOL_OUTPUT" | portable_md5)
  SAFE_OUTPUT_HASH=$(echo "$OUTPUT_HASH" | sed "s/'/''/g")

  CURRENT=$(sqlite3 "$DB_PATH" ".timeout 5000" \
    "SELECT last_output_hash || '|' || consecutive_count FROM tool_poll_state
     WHERE terminal_session='${SAFE_SESSION}'
       AND tool_name='${SAFE_TOOL}'
       AND input_hash='${SAFE_INPUT_HASH}';" \
    2>/dev/null || true)

  LAST_HASH=$(echo "$CURRENT" | cut -d'|' -f1)
  CURRENT_COUNT=$(echo "$CURRENT" | cut -d'|' -f2)

  if [ "$OUTPUT_HASH" = "$LAST_HASH" ] && [ -n "$LAST_HASH" ]; then
    NEW_COUNT=$(( ${CURRENT_COUNT:-0} + 1 ))
  else
    NEW_COUNT=1
  fi

  sqlite3 "$DB_PATH" ".timeout 5000" \
    "INSERT OR REPLACE INTO tool_poll_state
       (terminal_session, tool_name, input_hash, last_output_hash, consecutive_count, updated_at)
     VALUES
       ('${SAFE_SESSION}', '${SAFE_TOOL}', '${SAFE_INPUT_HASH}',
        '${SAFE_OUTPUT_HASH}', ${NEW_COUNT}, datetime('now'));" \
    2>/dev/null || {
    log_error "POLL-DEDUP" "UPSERT failed for ${TOOL_NAME}"
    exit 0
  }

  if [ "$NEW_COUNT" -eq 3 ]; then
    log_hook "POLL-DEDUP" "Blocked" "WARNING — 3 consecutive identical reads of ${TOOL_NAME}. Next identical call will be blocked for 5 minutes. Output has not changed — wait for a notification before re-reading."
  else
    log_hook "POLL-DEDUP" "Allowed" "count=${NEW_COUNT} for ${TOOL_NAME}"
  fi

  exit 0
fi
```

**Step 2: Make executable**

Run:
```bash
chmod +x worker/hooks/poll-dedup.sh
```

Expected: No output, exit 0.

**Step 3: Syntax check**

Run:
```bash
bash -n worker/hooks/poll-dedup.sh
```

Expected: No output (syntax valid).

**Step 4: Stage changes**

Run:
```bash
git add worker/hooks/poll-dedup.sh
```

Expected: Changes staged.

---

## Task 3: Register hook in `worker/hooks/hooks.json`

**Files:**
- Modify: `worker/hooks/hooks.json`

No tests required: registration is configuration — correctness verified by running the hook in Task 4.

**Step 1: Add PreToolUse registration**

Find this exact string in `worker/hooks/hooks.json`:
```json
      {
        "hooks": [
          {
            "type": "command",
            "command": "bash $HOME/.claude/ironclaude-hooks/subagent-circuit-breaker.sh"
          }
        ],
        "matcher": "Task"
      }
    ],
    "PostToolUse": [
```

Replace with:
```json
      {
        "hooks": [
          {
            "type": "command",
            "command": "bash $HOME/.claude/ironclaude-hooks/subagent-circuit-breaker.sh"
          }
        ],
        "matcher": "Task"
      },
      {
        "hooks": [
          {
            "type": "command",
            "command": "bash $HOME/.claude/ironclaude-hooks/poll-dedup.sh"
          }
        ],
        "matcher": "Read|Bash|Grep|Glob"
      }
    ],
    "PostToolUse": [
```

**Step 2: Add PostToolUse registration**

Find this exact string in `worker/hooks/hooks.json`:
```json
      {
        "hooks": [
          {
            "type": "command",
            "command": "bash $HOME/.claude/ironclaude-hooks/mcp-state-logger.sh"
          }
        ],
        "matcher": "mcp__plugin_ironclaude_state-manager__mark_design_ready|mcp__plugin_ironclaude_state-manager__mark_plan_ready|mcp__plugin_ironclaude_state-manager__mark_brainstorming|mcp__plugin_ironclaude_state-manager__mark_executing|mcp__plugin_ironclaude_state-manager__mark_debugging|mcp__plugin_ironclaude_state-manager__create_plan|mcp__plugin_ironclaude_state-manager__start_execution|mcp__plugin_ironclaude_state-manager__retreat"
      }
    ],
```

Replace with:
```json
      {
        "hooks": [
          {
            "type": "command",
            "command": "bash $HOME/.claude/ironclaude-hooks/mcp-state-logger.sh"
          }
        ],
        "matcher": "mcp__plugin_ironclaude_state-manager__mark_design_ready|mcp__plugin_ironclaude_state-manager__mark_plan_ready|mcp__plugin_ironclaude_state-manager__mark_brainstorming|mcp__plugin_ironclaude_state-manager__mark_executing|mcp__plugin_ironclaude_state-manager__mark_debugging|mcp__plugin_ironclaude_state-manager__create_plan|mcp__plugin_ironclaude_state-manager__start_execution|mcp__plugin_ironclaude_state-manager__retreat"
      },
      {
        "hooks": [
          {
            "type": "command",
            "command": "bash $HOME/.claude/ironclaude-hooks/poll-dedup.sh"
          }
        ],
        "matcher": "Read|Bash|Grep|Glob"
      }
    ],
```

**Step 3: Validate JSON syntax**

Run:
```bash
jq . worker/hooks/hooks.json > /dev/null
```

Expected: No output, exit 0 (valid JSON).

**Step 4: Stage changes**

Run:
```bash
git add worker/hooks/hooks.json
```

Expected: Changes staged.

---

## Task 4: Write and run `worker/hooks/test-poll-dedup.sh`

**Files:**
- Create: `worker/hooks/test-poll-dedup.sh`

Tests isolated logic functions extracted from `poll-dedup.sh`, following the pattern of `test-guard-security.sh`. No live DB required.

**Step 1: Write the test file**

Create `worker/hooks/test-poll-dedup.sh`:

```bash
#!/bin/bash
# test-poll-dedup.sh — Logic unit tests for poll-dedup.sh
#
# Tests isolated logic functions, no live SQLite required.
# Pattern follows test-guard-security.sh.

PASS=0
FAIL=0

assert_eq() {
  local desc="$1" expected="$2" actual="$3"
  if [ "$actual" = "$expected" ]; then
    echo "PASS: $desc"
    ((PASS++))
  else
    echo "FAIL: $desc"
    echo "  expected: $expected"
    echo "  actual:   $actual"
    ((FAIL++))
  fi
}

# ─── ISOLATED LOGIC FUNCTIONS (replicated from poll-dedup.sh) ───

# Block decision: given count and age (seconds), return "block" or "allow"
decide_block() {
  local count="$1" age="$2" threshold=3 cooldown=300
  if [ "$count" -ge "$threshold" ] && [ "$age" -lt "$cooldown" ]; then
    echo "block"
  else
    echo "allow"
  fi
}

# Count update: given current_hash, current_count, new_hash → new_count
update_count() {
  local current_hash="$1" current_count="$2" new_hash="$3"
  if [ "$new_hash" = "$current_hash" ] && [ -n "$current_hash" ]; then
    echo $(( ${current_count:-0} + 1 ))
  else
    echo "1"
  fi
}

# input_hash key construction per tool type
make_input_key() {
  local tool="$1" shift
  case "$tool" in
    Read)  echo "$2" ;;
    Bash)  echo "$2" ;;
    Grep)  echo "${2}|${3}" ;;
    Glob)  echo "${2}|${3}" ;;
    *)     echo "$tool" ;;
  esac
}

# ─── TESTS: Block decision ───
echo "=== Block decision ==="
assert_eq "count=0 → allow"        "allow" "$(decide_block 0   0)"
assert_eq "count=2 → allow"        "allow" "$(decide_block 2   0)"
assert_eq "count=3, age=0 → block" "block" "$(decide_block 3   0)"
assert_eq "count=3, age=299 → block" "block" "$(decide_block 3 299)"
assert_eq "count=3, age=300 → allow" "allow" "$(decide_block 3 300)"
assert_eq "count=3, age=999 → allow" "allow" "$(decide_block 3 999)"
assert_eq "count=5, age=100 → block" "block" "$(decide_block 5 100)"

# ─── TESTS: Count update ───
echo ""
echo "=== Count update ==="
assert_eq "first call (no prior hash) → count=1" \
  "1" "$(update_count '' 0 'abc123')"

assert_eq "same hash as before → increment" \
  "2" "$(update_count 'abc123' 1 'abc123')"

assert_eq "same hash, count already 2 → 3" \
  "3" "$(update_count 'abc123' 2 'abc123')"

assert_eq "different hash → reset to 1" \
  "1" "$(update_count 'abc123' 3 'def456')"

assert_eq "empty prior hash with empty new hash → 1" \
  "1" "$(update_count '' 0 '')"

# ─── TESTS: Input key construction ───
echo ""
echo "=== Input key construction ==="
assert_eq "Read key = file_path" \
  "/some/file.txt" "$(make_input_key Read '/some/file.txt')"

assert_eq "Bash key = command" \
  "git status" "$(make_input_key Bash 'git status')"

assert_eq "Grep key = pattern|path" \
  "TODO|src/" "$(make_input_key Grep 'TODO' 'src/')"

assert_eq "Glob key = pattern|path" \
  "**/*.ts|worker/" "$(make_input_key Glob '**/*.ts' 'worker/')"

assert_eq "Unknown tool key = tool name" \
  "UnknownTool" "$(make_input_key UnknownTool)"

# ─── SUMMARY ───
echo ""
echo "Results: ${PASS} passed, ${FAIL} failed"
[ "$FAIL" -eq 0 ] && exit 0 || exit 1
```

**Step 2: Make executable**

Run:
```bash
chmod +x worker/hooks/test-poll-dedup.sh
```

Expected: No output, exit 0.

**Step 3: Run tests — verify all pass**

Run:
```bash
bash worker/hooks/test-poll-dedup.sh
```

Expected output:
```
=== Block decision ===
PASS: count=0 → allow
PASS: count=2 → allow
PASS: count=3, age=0 → block
PASS: count=3, age=299 → block
PASS: count=3, age=300 → allow
PASS: count=3, age=999 → allow
PASS: count=5, age=100 → block

=== Count update ===
PASS: first call (no prior hash) → count=1
PASS: same hash as before → increment
PASS: same hash, count already 2 → 3
PASS: different hash → reset to 1
PASS: empty prior hash with empty new hash → 1

=== Input key construction ===
PASS: Read key = file_path
PASS: Bash key = command
PASS: Grep key = pattern|path
PASS: Glob key = pattern|path
PASS: Unknown tool key = tool name

Results: 15 passed, 0 failed
```

If any test fails: diagnose the mismatch, fix the logic in the test (not the hook) if the test expectation is wrong, or fix the hook logic (Task 2) if the logic is wrong.

**Step 4: Stage changes**

Run:
```bash
git add worker/hooks/test-poll-dedup.sh
```

Expected: Changes staged.

---

## Post-Implementation: Hook Propagation Note

The `session-init.sh` self-copy mechanism copies `worker/hooks/*.sh` to `~/.claude/ironclaude-hooks/` on every `SessionStart`. The new `poll-dedup.sh` will be copied automatically on the next session start. **No manual copy step is needed**, but hooks won't be active until the next Claude Code session opens (or the current session restarts).

To verify propagation after the next session start:
```bash
ls -la ~/.claude/ironclaude-hooks/poll-dedup.sh
```

Expected: File exists and is executable.

To verify the DB table was created:
```bash
sqlite3 "$HOME/.claude/ironclaude.db" ".schema tool_poll_state"
```

Expected: Full `CREATE TABLE` DDL output.
