# Polling Deduplication Hook Design

> **Created:** 2026-05-31
> **Status:** Design Complete

## Summary

Workers polling the same tool repeatedly burn tokens in a death spiral: read → identical output → forget → read again. The PF2e pipeline demonstrated this at scale (100K+ tokens on unchanged polling). This design adds a native, zero-dependency hook that detects consecutive identical reads and blocks further polling until the output changes or a 5-minute cooldown elapses.

Implementation is a single new hook (`poll-dedup.sh`) following the dual-mode pattern of `subagent-circuit-breaker.sh`. State lives in SQLite (survives context compaction — the exact failure mode that causes spirals). No new external dependencies.

## Architecture

```
poll-dedup.sh  (dual-mode: detects mode via has("tool_output"))
├── PreToolUse  (Read|Bash|Grep|Glob)
│   ├── Extract tool_name + compute input_hash from tool inputs
│   ├── Query tool_poll_state → (last_output_hash, consecutive_count, updated_at)
│   ├── count >= 3 AND age < 5min → block_pretooluse with cooldown message
│   └── count >= 3 AND age >= 5min → allow through + reset count in DB
└── PostToolUse  (Read|Bash|Grep|Glob)
    ├── Compute output_hash = portable_md5(tool_output)
    ├── Compare to stored last_output_hash for (session, tool_name, input_hash)
    ├── hash == last → consecutive_count++
    ├── hash != last → consecutive_count = 1, last_output_hash = new hash
    ├── UPSERT tool_poll_state
    └── count == 3 → emit systemMessage warning (PreToolUse blocks the next call)

New DB table: tool_poll_state
  PRIMARY KEY (terminal_session, tool_name, input_hash)
  + last_output_hash TEXT, consecutive_count INTEGER, updated_at TEXT

Schema migration: session-init.sh CREATE TABLE IF NOT EXISTS (idempotent)
hooks.json: 2 new registrations (PreToolUse + PostToolUse on Read|Bash|Grep|Glob)
```

## Components

### `worker/hooks/poll-dedup.sh` (new file)

Structure follows `subagent-circuit-breaker.sh` exactly:

```bash
source hook-logger.sh
run_hook "POLL-DEDUP"
INPUT=$(cat)
init_session_id
HAS_OUTPUT=$(echo "$INPUT" | jq -r 'has("tool_output")')
```

**PreToolUse branch** (HAS_OUTPUT == false):

1. Extract `tool_name` from `$INPUT`
2. Extract input fields by tool type:
   - `Read` → `tool_input.file_path`
   - `Bash` → `tool_input.command`
   - `Grep` → `tool_input.pattern` + `tool_input.path`
   - `Glob` → `tool_input.pattern` + `tool_input.path`
3. Compute `INPUT_HASH=$(echo "$INPUT_KEY" | portable_md5)`
4. Query: `SELECT consecutive_count, last_output_hash, updated_at FROM tool_poll_state WHERE terminal_session=? AND tool_name=? AND input_hash=?`
5. If `consecutive_count >= 3`:
   - Compute age: `$(( $(date +%s) - $(date -d "$UPDATED_AT" +%s 2>/dev/null || ...) ))`
   - Age < 300s → `block_pretooluse` with message:
     ```
     BLOCKED — POLLING DETECTED (3+ identical reads)
     Output unchanged since last read. Wait for completion notification or
     check again in 5 minutes. Consecutive identical reads: N
     ```
   - Age >= 300s → reset count to 0 in DB, allow through
6. Exit 0 (allow) for count < 3

**PostToolUse branch** (HAS_OUTPUT == true):

1. Extract `tool_name` and recompute `INPUT_HASH` (same logic as PreToolUse)
2. Extract `tool_output`, compute `OUTPUT_HASH=$(echo "$TOOL_OUTPUT" | portable_md5)`
3. Query current state: `SELECT last_output_hash, consecutive_count FROM tool_poll_state WHERE ...`
4. Compare hashes:
   - Same → `NEW_COUNT=$((CURRENT_COUNT + 1))`
   - Different → `NEW_COUNT=1`, `LAST_HASH=$OUTPUT_HASH`
5. UPSERT: `INSERT OR REPLACE INTO tool_poll_state ...`
6. If `NEW_COUNT == 3`: emit systemMessage warning:
   ```
   ⚠️ [POLL-DEDUP]: WARNING — 3 consecutive identical reads of [tool_name]
   Next identical call will be blocked for 5 minutes. The output has not changed.
   Wait for a notification or completion signal before reading again.
   ```

**Date arithmetic (macOS/Linux compatible):**

macOS `date -d` is not available; use `date -j -f` or Python fallback:

```bash
age_seconds() {
  local ts="$1"
  if python3 -c "import time,datetime; print(int(time.time() - datetime.datetime.fromisoformat('$ts').timestamp()))" 2>/dev/null; then
    return
  fi
  echo 9999  # fallback: treat as old → allow through
}
```

### `worker/hooks/session-init.sh` (schema addition)

Add to the `CREATE TABLE IF NOT EXISTS` block (idempotent):

```sql
CREATE TABLE IF NOT EXISTS tool_poll_state (
  terminal_session TEXT NOT NULL,
  tool_name TEXT NOT NULL,
  input_hash TEXT NOT NULL,
  last_output_hash TEXT NOT NULL DEFAULT '',
  consecutive_count INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL DEFAULT (datetime('now')),
  PRIMARY KEY (terminal_session, tool_name, input_hash)
);
```

### `worker/hooks/hooks.json` (2 new registrations)

```json
{
  "hooks": [
    {
      "matcher": "Read|Bash|Grep|Glob",
      "hooks": [{"type": "command", "command": "bash $HOME/.claude/ironclaude-hooks/poll-dedup.sh"}]
    }
  ]
}
```

Register this entry under **both** `PreToolUse` and `PostToolUse`. Single script handles both via `has("tool_output")`.

## Data Flow

```
Worker calls Read(file_path)
  → PreToolUse fires poll-dedup.sh
      → input_hash = md5(file_path)
      → query tool_poll_state → count=0 → allow
  → Read executes → tool_output returned to context
  → PostToolUse fires poll-dedup.sh
      → output_hash = md5(tool_output)
      → count was 0, hash differs from '' → count=1, store hash
      → UPSERT tool_poll_state

[Worker reads same file again, output unchanged]
  → PreToolUse: count=1 → allow
  → PostToolUse: hash matches → count=2 → UPSERT

[Worker reads same file again, output unchanged]
  → PreToolUse: count=2 → allow
  → PostToolUse: hash matches → count=3 → UPSERT + emit ⚠️ warning

[Worker reads same file again, still unchanged]
  → PreToolUse: count=3, age < 5min → BLOCKED
  → Worker sees: "Output unchanged — check again in 5 minutes"

[Worker makes an edit → file changes → reads again]
  → PreToolUse: count=3, but allow (edit made content change)
  → PostToolUse: hash differs from stored → count=1, new hash stored
  → Block cleared naturally

[5 minutes pass without edit → Worker tries again]
  → PreToolUse: count=3, age >= 300s → reset count=0 → allow through
```

## Error Handling

- **DB not initialized:** `db_read` returns empty; hook defaults to `count=0` (allow through). Fail-open: don't block reads on DB error.
- **UPSERT fails:** log_error + exit 0 (don't block). Tracking state is not worth breaking reads.
- **Session not found in DB:** Same as above — fail-open.
- **Date arithmetic failure:** `age_seconds` fallback returns 9999 → treated as old → allow through. Cooldown side fails open.
- **Glob/Grep with no path:** `INPUT_KEY` constructed from pattern alone; still produces stable hash.
- **Empty tool_output:** Hash of empty string is stable; counts correctly. A process that repeatedly returns empty output IS a polling spiral.
- **`set -euo pipefail` active:** All sqlite3 calls use `|| true` on reads; writes use `|| log_error + exit 0` (not hard_fail).

## Testing Strategy

The existing `test-guard-security.sh` tests the guard hook; `poll-dedup.sh` gets analogous tests in `test-poll-dedup.sh`:

1. **First read:** count=0, PreToolUse allows, PostToolUse stores hash, count=1
2. **Second identical read:** count=2 after PostToolUse, no block yet
3. **Third identical read:** count=3, ⚠️ warning emitted, no block yet
4. **Fourth identical read:** PreToolUse blocks with cooldown message
5. **Content changes:** PostToolUse resets count to 1, next PreToolUse allows
6. **5-minute cooldown:** Manipulate `updated_at` to past timestamp → PreToolUse allows, resets count
7. **Different files same worker:** Each (tool_name, input_hash) tracked independently; file A polling doesn't affect file B reads
8. **DB error simulation:** Remove DB write permission → hook exits 0, read succeeds

Manual verification: set `consecutive_count=3` in SQLite for current session, trigger a Read, confirm block message appears.

## Implementation Notes

- The `MAINTENANCE` comment in `session-init.sh` says "Keep in sync with db.ts initDb()." The `tool_poll_state` table must also be added to `worker/mcp-servers/state-manager/src/db.ts`.
- `INSERT OR REPLACE` is used instead of `INSERT ... ON CONFLICT UPDATE` for SQLite 3.24- compatibility (macOS ships SQLite 3.31+; Windows Git Bash may be older).
- The `5-minute cooldown` matches the "check again in 5 minutes" language in the block message. If this threshold needs tuning, it should be configurable via `ironclaude-hooks-config.json` (`poll_dedup_cooldown_seconds`, defaulting to 300).
- `Glob` typically returns file paths, not content. Its output hash will stabilize once the directory stops changing — legitimate reason to track it.
- Do NOT register on `Edit|Write|NotebookEdit` — those are write tools; tracking their output would generate false positives from legitimate repeated writes.

## Selective Cherry-Picks (not in scope for this plan)

The following improvements were evaluated and deferred:

**A. Content-type routing for Read (medium value, low complexity)**
PreToolUse checks file size/type before Read completes. Block Read on files > 100KB with "use Grep or offset+limit." Block binary files with file info. This IS achievable via PreToolUse — stat the file, detect MIME type, block if oversized.

**B. Command pattern detection for Bash (medium value, medium complexity)**
PreToolUse detects `git diff` (without `--stat`) and blocks with suggestion to use `git diff --stat` first. Similarly: `cat large_file.log` → "use tail -n 100 instead." Pattern-matched against known expensive commands.

**C. Compaction checkpoint file (lower value — review_pending recovery already implemented)**
The April 11 design implemented `REVIEW_PENDING` recovery in `session-init.sh`. A broader checkpoint would auto-write `_checkpoint.md` with plan state + next steps on every resume. Deferred: unclear trigger (context size not exposed to hooks), and the existing DB-based recovery covers the primary pain.

**Note on tool output truncation:** Hooks cannot intercept or modify tool output content after a tool executes — they can only inject `systemMessage` alongside it. True output truncation requires either (A) blocking the read pre-emptively (content-type routing, cherry-pick A above) or (B) a command substitution wrapper (cherry-pick B). Post-hoc truncation via PostToolUse hooks is not achievable in the current hook architecture.
