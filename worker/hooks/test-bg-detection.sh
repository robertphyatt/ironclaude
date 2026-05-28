#!/bin/bash
# test-bg-detection.sh — Unit tests for run_in_background detection logic
#
# Tests the detect_bg_job() helper, which replicates the gate logic from
# get-back-to-work-claude.sh. Run: bash worker/hooks/test-bg-detection.sh

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

# Replicates the detection gate from get-back-to-work-claude.sh.
# Returns "true" if run_in_background=true found in last 3 assistant turns; "false" otherwise.
detect_bg_job() {
  local transcript_path="$1"
  local _bg_job_active="false"

  local _recent_req_ids
  _recent_req_ids=$(
    tail -n 500 "$transcript_path" 2>/dev/null | \
    jq -r 'select(.type == "assistant") | .requestId // empty' 2>/dev/null | \
    tail -3
  )

  if [ -n "$_recent_req_ids" ]; then
    while IFS= read -r _req_id; do
      [ -z "$_req_id" ] && continue
      if tail -n 500 "$transcript_path" 2>/dev/null | grep -F "\"$_req_id\"" | \
         jq -r '.message.content[]? | select(.type == "tool_use") | .input.run_in_background // false' \
         2>/dev/null | grep -q "^true$" 2>/dev/null; then
        _bg_job_active="true"
        break
      fi
    done <<< "$_recent_req_ids"
  fi

  echo "$_bg_job_active"
}

# ─── TEST FIXTURES ───
TMPDIR_TEST=$(mktemp -d)
trap 'rm -rf "$TMPDIR_TEST"' EXIT

# Fixture 1: run_in_background=true in most recent turn
cat > "$TMPDIR_TEST/bg_last_turn.jsonl" << 'EOF'
{"type":"assistant","requestId":"req-001","message":{"content":[{"type":"text","text":"Starting pipeline"}]}}
{"type":"assistant","requestId":"req-002","message":{"content":[{"type":"tool_use","id":"tu-001","name":"Bash","input":{"command":"./extract.sh","run_in_background":true}}]}}
EOF

# Fixture 2: run_in_background=true two turns back (still within 3-turn window)
cat > "$TMPDIR_TEST/bg_second_turn.jsonl" << 'EOF'
{"type":"assistant","requestId":"req-001","message":{"content":[{"type":"tool_use","id":"tu-001","name":"Bash","input":{"command":"./pipeline.sh","run_in_background":true}}]}}
{"type":"assistant","requestId":"req-002","message":{"content":[{"type":"text","text":"Pipeline started, extracting pages..."}]}}
EOF

# Fixture 3: no run_in_background present
cat > "$TMPDIR_TEST/no_bg.jsonl" << 'EOF'
{"type":"assistant","requestId":"req-001","message":{"content":[{"type":"text","text":"Running command"}]}}
{"type":"assistant","requestId":"req-002","message":{"content":[{"type":"tool_use","id":"tu-001","name":"Bash","input":{"command":"echo hello"}}]}}
EOF

# Fixture 4: run_in_background=false (explicit false)
cat > "$TMPDIR_TEST/bg_false.jsonl" << 'EOF'
{"type":"assistant","requestId":"req-001","message":{"content":[{"type":"tool_use","id":"tu-001","name":"Bash","input":{"command":"./script.sh","run_in_background":false}}]}}
EOF

# Fixture 5: stale bg job — launched >3 turns ago (req-001), 3 later turns with no bg
cat > "$TMPDIR_TEST/stale_bg.jsonl" << 'EOF'
{"type":"assistant","requestId":"req-001","message":{"content":[{"type":"tool_use","id":"tu-001","name":"Bash","input":{"command":"./pipeline.sh","run_in_background":true}}]}}
{"type":"assistant","requestId":"req-002","message":{"content":[{"type":"text","text":"Turn 2"}]}}
{"type":"assistant","requestId":"req-003","message":{"content":[{"type":"text","text":"Turn 3"}]}}
{"type":"assistant","requestId":"req-004","message":{"content":[{"type":"text","text":"Turn 4 - final"}]}}
EOF

# Fixture 6: monitoring pattern — bg launched, followed by monitoring turns
cat > "$TMPDIR_TEST/monitoring_pattern.jsonl" << 'EOF'
{"type":"assistant","requestId":"req-001","message":{"content":[{"type":"tool_use","id":"tu-001","name":"Bash","input":{"command":"./pipeline.sh","run_in_background":true}}]}}
{"type":"assistant","requestId":"req-002","message":{"content":[{"type":"tool_use","id":"tu-002","name":"Monitor","input":{"command":"./pipeline.sh"}}]}}
{"type":"assistant","requestId":"req-003","message":{"content":[{"type":"text","text":"Pipeline still running, checking again next turn"}]}}
EOF

# ─── TESTS ───

echo "=== Core Detection ==="
assert_eq "bg job in last turn: detected" "true" "$(detect_bg_job "$TMPDIR_TEST/bg_last_turn.jsonl")"
assert_eq "bg job 2 turns back (within window): detected" "true" "$(detect_bg_job "$TMPDIR_TEST/bg_second_turn.jsonl")"
assert_eq "no bg job: not detected" "false" "$(detect_bg_job "$TMPDIR_TEST/no_bg.jsonl")"
assert_eq "run_in_background=false (explicit): not detected" "false" "$(detect_bg_job "$TMPDIR_TEST/bg_false.jsonl")"
assert_eq "stale bg job (>3 turns ago): not detected" "false" "$(detect_bg_job "$TMPDIR_TEST/stale_bg.jsonl")"
assert_eq "monitoring pattern (bg within 3-turn window): detected" "true" "$(detect_bg_job "$TMPDIR_TEST/monitoring_pattern.jsonl")"

echo "=== Fail-Open Edge Cases ==="
assert_eq "missing transcript: not detected" "false" "$(detect_bg_job "/nonexistent/path.jsonl")"
assert_eq "empty transcript: not detected" "false" "$(detect_bg_job /dev/null)"

echo ""
echo "Results: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ] && exit 0 || exit 1
