# Background Job Detection — Suppress Block Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** Prevent workers monitoring long-running pipelines from getting blocked in re-check loops by approving stops when `run_in_background=true` is found in recent transcript turns.

**Architecture:** Insert a bg-detection preamble inside the `elif IN_PROGRESS_OR_PENDING_COUNT != 0` branch (line 290) of `get-back-to-work-claude.sh`. If `run_in_background: true` appears in the last 3 assistant turns, emit approve JSON and exit 0. Fail-open: transcript missing or jq failure falls through to existing block behavior. After committing, copy the updated hook to `~/.claude/ironclaude-hooks/` for the running worker.

**Tech Stack:** bash, jq, sqlite3 (existing hook stack)

---

## Task 1: Add bg-detection preamble to get-back-to-work hook

**Files:**
- Modify: `worker/hooks/get-back-to-work-claude.sh:290-299`

No tests required: the hook has no standalone test harness. The detection pattern is validated by Task 2 (running `test-bg-detection.sh`).

**Step 1: Edit `worker/hooks/get-back-to-work-claude.sh` — insert detection block**

Use the Edit tool with this exact old_string and new_string:

old_string:
```
    elif [ "${IN_PROGRESS_OR_PENDING_COUNT:-0}" != "0" ]; then
        # Tasks still in progress or pending -- block
        increment_block_counter
        block_stop "GET-BACK-TO-WORK" "STOP — TASKS STILL IN PROGRESS

You have ${IN_PROGRESS_OR_PENDING_COUNT} task(s) still pending or in-progress. You are not done yet.

Continue working on your current task. Follow the plan steps exactly as written.

Do NOT stop. Do NOT ask the user what to do. Keep executing the plan."
```

new_string:
```
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

You have ${IN_PROGRESS_OR_PENDING_COUNT} task(s) still pending or in-progress. You are not done yet.

Continue working on your current task. Follow the plan steps exactly as written.

Do NOT stop. Do NOT ask the user what to do. Keep executing the plan."
```

**Step 2: Verify the edit is syntactically valid**

Run:
```bash
bash -n worker/hooks/get-back-to-work-claude.sh
```

Expected: no output (exit 0 = no syntax errors)

**Step 3: Stage changes**

Run:
```bash
git add worker/hooks/get-back-to-work-claude.sh
```

Expected: Changes staged

---

## Task 2: Add monitoring-pattern test case to test-bg-detection.sh

**Files:**
- Modify: `worker/hooks/test-bg-detection.sh`

**Step 1: Add Fixture 6 — monitoring pattern**

Use the Edit tool with this exact old_string and new_string:

old_string:
```
{"type":"assistant","requestId":"req-004","message":{"content":[{"type":"text","text":"Turn 4 - final"}]}}
EOF

# ─── TESTS ───
```

new_string:
```
{"type":"assistant","requestId":"req-004","message":{"content":[{"type":"text","text":"Turn 4 - final"}]}}
EOF

# Fixture 6: monitoring pattern — bg launched, followed by monitoring turns
cat > "$TMPDIR_TEST/monitoring_pattern.jsonl" << 'EOF'
{"type":"assistant","requestId":"req-001","message":{"content":[{"type":"tool_use","id":"tu-001","name":"Bash","input":{"command":"./pipeline.sh","run_in_background":true}}]}}
{"type":"assistant","requestId":"req-002","message":{"content":[{"type":"tool_use","id":"tu-002","name":"Monitor","input":{"command":"./pipeline.sh"}}]}}
{"type":"assistant","requestId":"req-003","message":{"content":[{"type":"text","text":"Pipeline still running, checking again next turn"}]}}
EOF

# ─── TESTS ───
```

**Step 2: Add new assertion under Core Detection**

Use the Edit tool with this exact old_string and new_string:

old_string:
```
assert_eq "stale bg job (>3 turns ago): not detected" "false" "$(detect_bg_job "$TMPDIR_TEST/stale_bg.jsonl")"
```

new_string:
```
assert_eq "stale bg job (>3 turns ago): not detected" "false" "$(detect_bg_job "$TMPDIR_TEST/stale_bg.jsonl")"
assert_eq "monitoring pattern (bg within 3-turn window): detected" "true" "$(detect_bg_job "$TMPDIR_TEST/monitoring_pattern.jsonl")"
```

**Step 3: Run the test suite — verify all pass**

Run:
```bash
bash worker/hooks/test-bg-detection.sh
```

Expected:
```
=== Core Detection ===
PASS: bg job in last turn: detected
PASS: bg job 2 turns back (within window): detected
PASS: no bg job: not detected
PASS: run_in_background=false (explicit): not detected
PASS: stale bg job (>3 turns ago): not detected
PASS: monitoring pattern (bg within 3-turn window): detected
=== Fail-Open Edge Cases ===
PASS: missing transcript: not detected
PASS: empty transcript: not detected

Results: 8 passed, 0 failed
```

Exit code: 0

**Step 4: Stage changes**

Run:
```bash
git add worker/hooks/test-bg-detection.sh
```

Expected: Changes staged

---

## Task 3: Commit repo changes and deploy installed hook

**Files:** (no Edit/Write — Bash only)

**Step 1: Stage plan files**

Run:
```bash
git add docs/plans/2026-05-24-bg-detection-suppress-block-design.md docs/plans/2026-05-24-bg-detection-suppress-block.md docs/plans/2026-05-24-bg-detection-suppress-block.plan.json
```

Expected: Changes staged

**Step 2: Commit**

Run:
```bash
git commit -m "$(cat <<'EOF'
feat(hooks): suppress get-back-to-work block when worker has active background shell

Workers monitoring long-running pipelines (run_in_background=true) have
pending tasks and previously hit the tasks-in-progress branch on every
heartbeat stop, burning tokens in re-check loops.

Now checks the last 3 assistant turns for run_in_background=true before
blocking. If found, approves the stop with message "[GET-BACK-TO-WORK]:
Passed - background job active, worker monitoring pipeline". Fail-open:
missing transcript or jq failure falls through to existing block behavior.

Adds monitoring-pattern fixture and assertion to test-bg-detection.sh
(8 tests, all passing).
EOF
)"
```

Expected: Commit succeeds, shows 1-line summary

**Step 3: Copy updated hook to installed path**

Run:
```bash
cp worker/hooks/get-back-to-work-claude.sh ~/.claude/ironclaude-hooks/get-back-to-work-claude.sh
```

Expected: No output (exit 0)

**Step 4: Verify installed copy matches repo**

Run:
```bash
diff worker/hooks/get-back-to-work-claude.sh ~/.claude/ironclaude-hooks/get-back-to-work-claude.sh
```

Expected: No output (files are identical)
