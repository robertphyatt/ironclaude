#!/bin/bash
# brain-hooks-canary.sh writes <gate root>/<gate session>/hooks-live. The gate root is
# IRONCLAUDE_BRAIN_GATE_ROOT (default $HOME/.ironclaude/state/brain-gate) and must be a real 0700
# directory owned by this user (design 2026-10-17 v120-completeness R3); otherwise nothing is written.
set -u
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
HOOK="$ROOT_DIR/hooks/brain-hooks-canary.sh"
SESSION="canary-test-$$-$(date +%s)"
TMP_DIR=$(mktemp -d /tmp/ironclaude-canary-test.XXXXXX)
trap 'rm -rf "$TMP_DIR"' EXIT
ROOT="$TMP_DIR/gate-root"
mkdir -m 700 "$ROOT"
PASS=0; FAIL=0
pass() { PASS=$((PASS + 1)); printf 'PASS: %s\n' "$1"; }
fail() { FAIL=$((FAIL + 1)); printf 'FAIL: %s%s\n' "$1" "${2:+ — $2}"; }
printf '{}' | IRONCLAUDE_BRAIN_GATE_ROOT="$ROOT" IRONCLAUDE_BRAIN_GATE_SESSION="$SESSION" bash "$HOOK"; RC=$?
[ "$RC" = "0" ] && pass "canary exits 0" || fail "canary exits 0" "rc=$RC"
[ -f "$ROOT/$SESSION/hooks-live" ] && pass "marker written under IRONCLAUDE_BRAIN_GATE_ROOT" || fail "marker written under IRONCLAUDE_BRAIN_GATE_ROOT"
mkdir -p "$TMP_DIR/home/.ironclaude/state"
mkdir -m 700 "$TMP_DIR/home/.ironclaude/state/brain-gate"
printf '{}' | env -u IRONCLAUDE_BRAIN_GATE_ROOT HOME="$TMP_DIR/home" IRONCLAUDE_BRAIN_GATE_SESSION="$SESSION" bash "$HOOK"
[ -f "$TMP_DIR/home/.ironclaude/state/brain-gate/$SESSION/hooks-live" ] && pass "default root is \$HOME/.ironclaude/state/brain-gate" || fail "default root is \$HOME/.ironclaude/state/brain-gate"
printf '{}' | IRONCLAUDE_BRAIN_GATE_ROOT="$ROOT" IRONCLAUDE_BRAIN_GATE_SESSION="../escape" bash "$HOOK"; RC=$?
[ "$RC" = "0" ] && pass "invalid session exits 0" || fail "invalid session exits 0" "rc=$RC"
[ ! -e "$ROOT/../escape" ] && pass "invalid session writes nothing" || fail "invalid session writes nothing"
# refused NAME ROOT: the canary exits 0 and writes nothing under ROOT.
refused() {
  printf '{}' | IRONCLAUDE_BRAIN_GATE_ROOT="$2" IRONCLAUDE_BRAIN_GATE_SESSION="$SESSION" bash "$HOOK"; RC=$?
  if [ "$RC" = "0" ] && [ ! -e "$2/$SESSION" ]; then pass "$1"; else fail "$1" "rc=$RC"; fi
}
mkdir -m 700 "$TMP_DIR/real"
ln -s "$TMP_DIR/real" "$TMP_DIR/link"
mkdir "$TMP_DIR/group"; chmod 770 "$TMP_DIR/group"
mkdir "$TMP_DIR/other"; chmod 701 "$TMP_DIR/other"
refused "symlinked root writes nothing" "$TMP_DIR/link"
refused "group-accessible root writes nothing" "$TMP_DIR/group"
refused "other-accessible root writes nothing" "$TMP_DIR/other"
refused "missing root writes nothing" "$TMP_DIR/missing"
refused "foreign-owned root writes nothing" "$(cd /tmp && pwd -P)"
printf '\n%d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
