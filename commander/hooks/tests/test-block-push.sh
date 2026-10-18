#!/bin/bash
# block-push.sh blocks git push from the Brain for Claude (Bash/.command) and Codex
# (Bash/.command, exec_command/.cmd as string or argv array) shell payloads.
set -u
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"   # -> commander/
HOOK="$ROOT_DIR/hooks/block-push.sh"
PASS=0; FAIL=0
pass() { PASS=$((PASS + 1)); printf 'PASS: %s\n' "$1"; }
fail() { FAIL=$((FAIL + 1)); printf 'FAIL: %s%s\n' "$1" "${2:+ — $2}"; }
assert_eq() { if [ "$2" = "$3" ]; then pass "$1"; else fail "$1" "expected=$2 actual=$3"; fi; }
feed() {  # feed TOOL_NAME TOOL_INPUT_JSON -> exit code
  jq -cn --arg t "$1" --argjson i "$2" '{session_id:"bp-test",tool_name:$t,tool_input:$i}' \
    | bash "$HOOK" >/dev/null 2>&1
  printf '%s' "$?"
}
assert_eq "Claude Bash git push blocked"            "2" "$(feed Bash '{"command":"git push origin main"}')"
assert_eq "Bash leading-space git push blocked"     "2" "$(feed Bash '{"command":"  git push"}')"
assert_eq "Bash git -C path push blocked"           "2" "$(feed Bash '{"command":"git -C /tmp/r push origin main"}')"
assert_eq "Codex exec_command string push blocked"  "2" "$(feed exec_command '{"cmd":"git push origin main"}')"
assert_eq "Codex exec_command argv push blocked"    "2" "$(feed exec_command '{"cmd":["git","push","origin","main"]}')"
assert_eq "Bash git status allowed"                 "0" "$(feed Bash '{"command":"git status"}')"
assert_eq "exec_command git status allowed"         "0" "$(feed exec_command '{"cmd":"git status"}')"
assert_eq "git push-like word not blocked"          "0" "$(feed Bash '{"command":"git pushd-notes"}')"
assert_eq "non-shell tool ignored"                  "0" "$(feed Read '{"file_path":"/tmp/x"}')"
assert_eq "near-miss tool name ignored"             "0" "$(feed exec_commandx '{"cmd":"git push origin main"}')"
assert_eq "exec_command without cmd allowed"        "0" "$(feed exec_command '{}')"
printf '\n%d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
