#!/bin/bash
# test-episodic-memory-sync-singleton.sh — runs the real episodic-memory-sync.sh
# against a stub sync-cli.js and asserts only one sync runs at a time.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOOK="$SCRIPT_DIR/../episodic-memory-sync.sh"

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

TMP="$(mktemp -d)"
export HOME="$TMP/home"
mkdir -p "$HOME/.claude"
export CLAUDE_PLUGIN_ROOT="$TMP/plugin"
STUB_DIR="$CLAUDE_PLUGIN_ROOT/mcp-servers/episodic-memory/dist"
mkdir -p "$STUB_DIR"
export IRONCLAUDE_SYNC_PROC_PATTERN="$STUB_DIR/sync-cli.js"
export STUB_MARKER="$TMP/marker"
: > "$STUB_MARKER"

# Stub mirrors the real --background detach (dist/sync-cli.js:1285-1298).
cat > "$STUB_DIR/sync-cli.js" <<'EOF'
const { spawn } = require("child_process");
const fs = require("fs");
if (process.argv.slice(2).includes("--background")) {
  const child = spawn(process.execPath, [process.argv[1]], { detached: true, stdio: "ignore" });
  child.unref();
  process.exit(0);
}
fs.appendFileSync(process.env.STUB_MARKER, `${process.pid}\n`);
setTimeout(() => process.exit(0), 120000);
EOF

cleanup() {
  pkill -f "$IRONCLAUDE_SYNC_PROC_PATTERN"
  rm -rf "$TMP"
}
trap cleanup EXIT

running_count() { pgrep -f "$IRONCLAUDE_SYNC_PROC_PATTERN" | wc -l | tr -d ' '; }
marker_lines() { wc -l < "$STUB_MARKER" | tr -d ' '; }
# Bounded polls (max 15 s) instead of fixed sleeps: node startup is slow under load.
wait_marker_at_least() { local i=0; while [ "$(marker_lines)" -lt "$1" ] && [ "$i" -lt 150 ]; do sleep 0.1; i=$((i+1)); done; }
wait_none_running() { local i=0; while [ "$(running_count)" -ne 0 ] && [ "$i" -lt 150 ]; do sleep 0.1; i=$((i+1)); done; }

# Case 1: five back-to-back session starts → one sync.
exits=""
for i in 1 2 3 4 5; do
  bash "$HOOK" --background < /dev/null > /dev/null
  exits="$exits$?"
done
wait_marker_at_least 1
sleep 2  # settle: lets any duplicate syncs also reach the marker
assert_eq "all five hook runs exit 0" "00000" "$exits"
assert_eq "exactly one sync process running" "1" "$(running_count)"
assert_eq "exactly one sync started" "1" "$(marker_lines)"

# Case 2: a dead sync is replaced on the next session start.
pkill -f "$IRONCLAUDE_SYNC_PROC_PATTERN"
wait_none_running
assert_eq "no sync running after kill" "0" "$(running_count)"
bash "$HOOK" --background < /dev/null > /dev/null
wait_marker_at_least 2
assert_eq "new sync started after previous died" "2" "$(marker_lines)"
assert_eq "exactly one sync process running again" "1" "$(running_count)"

echo ""
echo "Results: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
