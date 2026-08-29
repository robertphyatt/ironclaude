#!/usr/bin/env bash
# test-session-init-stable-dir-seed.sh — session-init seeds the stable hook dir
# when it has no hooks, NEVER overwrites a populated one, and surfaces a seed-copy
# failure visibly (project_hook_stable_dir_revert).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HOOK="${SCRIPT_DIR}/../session-init.sh"
SENTINEL="SENTINEL_STABLE_DIR_NO_OVERWRITE_MARKER"

pass=0; fail=0
check() { # desc, expected, actual
  if [ "$3" = "$2" ]; then echo "PASS: $1"; pass=$((pass+1))
  else echo "FAIL: $1"; echo "  expected: [$2]"; echo "  actual:   [$3]"; fail=$((fail+1)); fi
}

# Run session-init in an isolated HOME, from a non-git cwd; print its stdout
# (log_hook systemMessage JSON). stderr is dropped.
run_session_init() { # TMP_HOME -> stdout
  local th="$1"
  mkdir -p "${th}/.claude"
  ( cd "$th" && printf '{"session_id":"stable-dir-seed-test","source":"startup"}' \
      | HOME="$th" bash "$HOOK" 2>/dev/null )
}

# --- Case A: empty stable dir -> seeded ---
A_ROOT="$(mktemp -d)"; trap 'rm -rf "$A_ROOT"' EXIT
run_session_init "${A_ROOT}/home" >/dev/null
if [ -f "${A_ROOT}/home/.claude/ironclaude-hooks/professional-mode-guard.sh" ]; then
  check "empty stable dir is seeded (bootstrap)" "seeded" "seeded"
else
  check "empty stable dir is seeded (bootstrap)" "seeded" "absent"
fi

# --- Case B (falsifier): populated stable dir -> NOT overwritten ---
B_ROOT="$(mktemp -d)"; trap 'rm -rf "$A_ROOT" "$B_ROOT"' EXIT
B_STABLE="${B_ROOT}/home/.claude/ironclaude-hooks"
mkdir -p "$B_STABLE"
printf '#!/bin/bash\n# %s\n' "$SENTINEL" > "${B_STABLE}/professional-mode-guard.sh"
run_session_init "${B_ROOT}/home" >/dev/null
if grep -qF "$SENTINEL" "${B_STABLE}/professional-mode-guard.sh"; then
  check "populated stable dir is NOT overwritten (falsifier)" "preserved" "preserved"
else
  check "populated stable dir is NOT overwritten (falsifier)" "preserved" "overwritten"
fi

# --- Case C (falsifier for the C1 fix): a seed-copy failure is surfaced visibly ---
# Empty but UNWRITABLE stable dir: the `! ls` probe finds no *.sh -> seed branch ->
# `mkdir -p` succeeds (dir already exists) -> `cp` fails inside the `if` condition
# (set-e-exempt) -> failure branch must emit an ERROR systemMessage on stdout. The
# failure log uses decision "ERROR" (ungated); decision "Stable" would be silenced by
# the log_hook verbose gate, so reverting the C1 fix flips this case red.
# Skipped as root: permission bits do not bind root.
C_ROOT="$(mktemp -d)"
trap 'chmod -R u+rwx "$C_ROOT" 2>/dev/null || true; rm -rf "$A_ROOT" "$B_ROOT" "$C_ROOT"' EXIT
if [ "$(id -u)" -eq 0 ]; then
  echo "SKIP: Case C (running as root — permission bits do not bind)"
else
  C_STABLE="${C_ROOT}/home/.claude/ironclaude-hooks"
  mkdir -p "$C_STABLE"
  chmod 555 "$C_STABLE"
  C_OUT="$(run_session_init "${C_ROOT}/home" || true)"
  chmod 755 "$C_STABLE"
  if printf '%s' "$C_OUT" | grep -qF "failed to seed stable dir"; then
    check "seed-copy failure is surfaced visibly (C1 falsifier)" "reported" "reported"
  else
    check "seed-copy failure is surfaced visibly (C1 falsifier)" "reported" "silent"
  fi
fi

echo; echo "results: $pass pass, $fail fail"
[ "$fail" -eq 0 ] || exit 1
