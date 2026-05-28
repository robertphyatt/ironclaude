# Background Job Detection — Suppress Block in Tasks-In-Progress Branch Design

> **Created:** 2026-05-24
> **Status:** Design Complete

## Summary

Workers monitoring long-running pipelines (launched with `run_in_background=true`) have pending/in-progress tasks and hit the `elif IN_PROGRESS_OR_PENDING_COUNT != 0` branch on every heartbeat stop. The branch currently blocks unconditionally, trapping these workers in re-check loops that burn tokens on empty pipeline status polls.

The fix inserts a background-job detection preamble inside the `elif` branch. If `run_in_background: true` appears in any of the last 3 assistant turns, the hook approves the stop instead of blocking — the worker is legitimately idle between heartbeat checks. Fail-open: if the transcript is missing or any jq step fails, the code falls through to the existing block behavior unchanged.

## Architecture

Single change point: the `elif` branch at line 290 of `get-back-to-work-claude.sh` gains a bg-detection preamble. All other branches are untouched.

**Logic flow (new path):**
```
elif IN_PROGRESS_OR_PENDING_COUNT != 0:
  if TRANSCRIPT_PATH exists and is readable:
    collect last 3 assistant requestIds from tail -n 500
    for each requestId:
      grep lines containing that requestId
      jq: extract run_in_background from tool_use entries
      if run_in_background == true: emit approve JSON, exit 0
  # fall-through: transcript missing, jq failed, or no bg job found
  increment_block_counter
  block_stop "TASKS STILL IN PROGRESS"
```

**Fail-open guarantee:** entire detection block is guarded by `if [ -n "$TRANSCRIPT_PATH" ] && [ -f "$TRANSCRIPT_PATH" ]`. Any jq failure or empty result falls through silently to existing block behavior.

## Components

### `worker/hooks/get-back-to-work-claude.sh` (lines 290–299)

Insert before the existing `# Tasks still in progress or pending -- block` comment:

```bash
elif [ "${IN_PROGRESS_OR_PENDING_COUNT:-0}" != "0" ]; then
    # Suppress block if worker has an active background shell (monitoring pipeline)
    if [ -n "$TRANSCRIPT_PATH" ] && [ -f "$TRANSCRIPT_PATH" ]; then
        _ic_bg_active="false"
        _ic_recent_req_ids=$(
            tail -n 500 "$TRANSCRIPT_PATH" 2>/dev/null | \
            jq -r 'select(.type == "assistant") | .requestId // empty' 2>/dev/null | \
            tail -3
        )
        if [ -n "$_ic_recent_req_ids" ]; then
            while IFS= read -r _ic_req_id; do
                [ -z "$_ic_req_id" ] && continue
                if tail -n 500 "$TRANSCRIPT_PATH" 2>/dev/null | grep -F "\"$_ic_req_id\"" | \
                   jq -r '.message.content[]? | select(.type == "tool_use") | .input.run_in_background // false' \
                   2>/dev/null | grep -q "^true$" 2>/dev/null; then
                    _ic_bg_active="true"
                    break
                fi
            done <<< "$_ic_recent_req_ids"
        fi
        if [ "$_ic_bg_active" = "true" ]; then
            echo '{"decision": "approve", "reason": "Background job active", "systemMessage": "[GET-BACK-TO-WORK]: Passed - background job active, worker monitoring pipeline"}'
            exit 0
        fi
    fi
    # Tasks still in progress or pending -- block
    increment_block_counter
    block_stop "GET-BACK-TO-WORK" "STOP — TASKS STILL IN PROGRESS
...existing message...
```

Variable prefix `_ic_` avoids collisions with existing script-level variables. The `while ... done <<< "$_ic_recent_req_ids"` pattern uses a here-string (not a pipe), so the `_ic_bg_active="true"` assignment propagates to the outer shell — no subshell boundary.

### `worker/hooks/test-bg-detection.sh`

Add Fixture 6 (monitoring pattern) and one new assertion under "Core Detection":

**Fixture:**
```bash
# Fixture 6: monitoring pattern — bg launched, followed by monitoring turns
cat > "$TMPDIR_TEST/monitoring_pattern.jsonl" << 'EOF'
{"type":"assistant","requestId":"req-001","message":{"content":[{"type":"tool_use","id":"tu-001","name":"Bash","input":{"command":"./pipeline.sh","run_in_background":true}}]}}
{"type":"assistant","requestId":"req-002","message":{"content":[{"type":"tool_use","id":"tu-002","name":"Monitor","input":{"command":"./pipeline.sh"}}]}}
{"type":"assistant","requestId":"req-003","message":{"content":[{"type":"text","text":"Pipeline still running, checking again next turn"}]}}
EOF
```

**Assertion:**
```bash
assert_eq "monitoring pattern (bg job within 3-turn window): detected" "true" "$(detect_bg_job "$TMPDIR_TEST/monitoring_pattern.jsonl")"
```

### Installed copy

After committing, copy updated hook to `~/.claude/ironclaude-hooks/get-back-to-work-claude.sh` so the running worker gets the fix immediately.

## Data Flow

1. Worker finishes a heartbeat check and issues a natural stop
2. Hook fires; `IN_PROGRESS_OR_PENDING_COUNT` is non-zero (tasks still pending)
3. Detection preamble runs: reads last 500 lines of transcript, extracts last 3 assistant requestIds
4. For each requestId, checks if any `tool_use` entry in that turn has `run_in_background: true`
5. If found → approve (worker is legitimately monitoring); if not found → fall through to block

## Error Handling

| Condition | Behavior |
|---|---|
| `TRANSCRIPT_PATH` empty or file missing | Skip detection, fall through to `block_stop` |
| `jq` fails on malformed JSONL | `2>/dev/null` suppresses stderr; grep finds nothing; fall through to `block_stop` |
| `_ic_recent_req_ids` is empty (no assistant turns) | Inner guard is false; while loop never runs; `_ic_bg_active` stays "false"; fall through |
| `run_in_background: false` explicitly set | jq outputs "false"; `grep -q "^true$"` fails; fall through to `block_stop` |
| Stale bg job (>3 turns ago) | `tail -3` on requestIds excludes it; detection returns false; fall through to `block_stop` |

## Testing Strategy

- Existing 7 test cases in `test-bg-detection.sh` continue to pass (no changes to existing fixtures or assertions)
- New Fixture 6 ("monitoring pattern") covers the exact real-world trigger: bg job launched, followed by Monitor tool call and text turns, still within the 3-turn window
- Run: `bash worker/hooks/test-bg-detection.sh`

## Implementation Notes

- `TRANSCRIPT_PATH` is set at line 122 of the hook (before the `elif` block at line 290) — no new wiring needed
- The `_ic_` variable prefix is consistent with the naming convention already in the script
- The detection logic is intentionally inlined (not extracted to a function) — single use site, hold scope
