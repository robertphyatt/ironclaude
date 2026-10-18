#!/bin/bash
# Worker hooks stand down in the Commander's Codex Brain (IC_ROLE=brain +
# IRONCLAUDE_CLIENT=codex); Brain hooks run through codex-brain-hooks.sh.
set -u
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"   # -> worker/hooks
TEST_ROOT=$(mktemp -d)
trap 'rm -rf "$TEST_ROOT"' EXIT
export HOME="$TEST_ROOT/home"
mkdir -p "$HOME/.claude" "$TEST_ROOT/brainhooks"
printf '{"verbose_hook_logs":false}\n' > "$HOME/.claude/ironclaude-hooks-config.json"
PASS=0; FAIL=0
pass() { PASS=$((PASS + 1)); printf 'PASS: %s\n' "$1"; }
fail() { FAIL=$((FAIL + 1)); printf 'FAIL: %s%s\n' "$1" "${2:+ — $2}"; }
assert_eq() { if [ "$2" = "$3" ]; then pass "$1"; else fail "$1" "expected=$2 actual=$3"; fi; }
PAYLOAD='{"session_id":"sd-test","tool_name":"exec_command","tool_input":{"cmd":"rg -n x /tmp"}}'

# Probe Brain hook: records env + stdin, exits 3.
cat > "$TEST_ROOT/brainhooks/probe-hook.sh" <<EOF
#!/bin/bash
printf 'IC_BRAIN_HOOK=%s\n' "\${IC_BRAIN_HOOK:-}" > "$TEST_ROOT/probe.out"
cat >> "$TEST_ROOT/probe.out"
exit 3
EOF
dispatch() {  # dispatch ROLE CLIENT ARG -> exit code
  rm -f "$TEST_ROOT/probe.out"
  printf '%s' "$PAYLOAD" | IC_ROLE="$1" IRONCLAUDE_CLIENT="$2" IC_BRAIN_HOOK_DIR="$TEST_ROOT/brainhooks" \
    bash "$DIR/codex-brain-hooks.sh" "$3" >/dev/null 2>&1
  printf '%s' "$?"
}
assert_eq "dispatcher runs target for Codex Brain" "3" "$(dispatch brain codex probe-hook.sh)"
if grep -qx 'IC_BRAIN_HOOK=1' "$TEST_ROOT/probe.out" && grep -q '"sd-test"' "$TEST_ROOT/probe.out"; then pass "target sees IC_BRAIN_HOOK=1 and payload"; else fail "target env/payload"; fi
assert_eq "dispatcher no-op for Claude Brain" "0" "$(dispatch brain claude probe-hook.sh)"
if [ -e "$TEST_ROOT/probe.out" ]; then fail "Claude Brain must not run target"; else pass "Claude Brain did not run target"; fi
assert_eq "dispatcher no-op for Codex worker" "0" "$(dispatch worker codex probe-hook.sh)"
if [ -e "$TEST_ROOT/probe.out" ]; then fail "Codex worker must not run target"; else pass "Codex worker did not run target"; fi
assert_eq "dispatcher blocks missing target" "2" "$(dispatch brain codex absent-hook.sh)"
assert_eq "dispatcher blocks path traversal" "2" "$(dispatch brain codex ../probe-hook.sh)"

# run_hook stand-down
cat > "$TEST_ROOT/uses-run-hook.sh" <<EOF
#!/bin/bash
source "$DIR/hook-logger.sh"
run_hook "uses-run-hook"
exit 7
EOF
rh() { IC_ROLE="$1" IRONCLAUDE_CLIENT="$2" IC_BRAIN_HOOK="$3" bash "$TEST_ROOT/uses-run-hook.sh" </dev/null >/dev/null 2>&1; printf '%s' "$?"; }
assert_eq "run_hook stands down in Codex Brain" "0" "$(rh brain codex "")"
assert_eq "run_hook runs Brain hooks in Codex Brain" "7" "$(rh brain codex 1)"
assert_eq "run_hook runs in Codex worker" "7" "$(rh worker codex "")"
assert_eq "run_hook runs in Claude Brain" "7" "$(rh brain claude "")"

# Real worker guard: blocks in a Codex worker session (no DB here), stands down in the Codex Brain.
pmg() { printf '%s' "$PAYLOAD" | IC_ROLE="$1" IRONCLAUDE_CLIENT=codex bash "$DIR/professional-mode-guard.sh" >/dev/null 2>&1; printf '%s' "$?"; }
assert_eq "professional-mode-guard stands down in Codex Brain" "0" "$(pmg brain)"
if [ "$(pmg worker)" != "0" ]; then pass "professional-mode-guard still enforces for Codex worker"; else fail "professional-mode-guard enforces for Codex worker" "exit 0"; fi

# Stop hook (does not use run_hook)
OUT=$(printf '{"session_id":"sd-test"}' | IC_ROLE=brain IRONCLAUDE_CLIENT=codex bash "$DIR/get-back-to-work-claude.sh"); RC=$?
assert_eq "GBTW stands down in Codex Brain (rc)" "0" "$RC"
assert_eq "GBTW stands down in Codex Brain (no output)" "" "$OUT"

printf '\n%d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
