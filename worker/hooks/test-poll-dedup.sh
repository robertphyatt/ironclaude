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

# Input key construction per tool type
make_input_key() {
  local tool="$1"
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
assert_eq "count=0 → allow"          "allow" "$(decide_block 0   0)"
assert_eq "count=2 → allow"          "allow" "$(decide_block 2   0)"
assert_eq "count=3, age=0 → block"   "block" "$(decide_block 3   0)"
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

assert_eq "empty prior hash with new hash → 1" \
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
