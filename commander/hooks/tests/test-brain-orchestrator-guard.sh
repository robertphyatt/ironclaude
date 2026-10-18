#!/bin/bash
# brain-orchestrator-guard.sh on Claude (Edit/Write/Bash .command) and Codex
# (apply_patch, Bash .command, exec_command .cmd) Brain payloads.
set -u
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
REPO_ROOT="$(cd "$ROOT_DIR/.." && pwd)"
TEST_ROOT=$(mktemp -d)
trap 'rm -rf "$TEST_ROOT"' EXIT
mkdir -p "$TEST_ROOT/with" "$TEST_ROOT/without"
cp "$ROOT_DIR/hooks/brain-orchestrator-guard.sh" "$REPO_ROOT/worker/hooks/bash-readonly-guard.sh" "$TEST_ROOT/with/"
cp "$ROOT_DIR/hooks/brain-orchestrator-guard.sh" "$TEST_ROOT/without/"
PASS=0; FAIL=0
pass() { PASS=$((PASS + 1)); printf 'PASS: %s\n' "$1"; }
fail() { FAIL=$((FAIL + 1)); printf 'FAIL: %s%s\n' "$1" "${2:+ — $2}"; }
assert_eq() { if [ "$2" = "$3" ]; then pass "$1"; else fail "$1" "expected=$2 actual=$3"; fi; }
feed() {  # feed DIR ROLE CLIENT TOOL TOOL_INPUT_JSON -> exit code
  jq -cn --arg t "$4" --argjson i "$5" '{session_id:"bog",tool_name:$t,tool_input:$i}' \
    | IC_ROLE="$2" IRONCLAUDE_CLIENT="$3" bash "$TEST_ROOT/$1/brain-orchestrator-guard.sh" >/dev/null 2>&1
  printf '%s' "$?"
}
# Claude Brain (behaviour unchanged)
assert_eq "claude Edit blocked"            "2" "$(feed with brain "" Edit '{"file_path":"/tmp/x"}')"
assert_eq "claude Bash git status allowed" "0" "$(feed with brain "" Bash '{"command":"git status"}')"
assert_eq "claude Bash git push blocked"   "2" "$(feed with brain "" Bash '{"command":"git push"}')"
assert_eq "claude Bash cat blocked"        "2" "$(feed with brain "" Bash '{"command":"cat /etc/hosts"}')"
# Codex Brain
assert_eq "codex apply_patch blocked"          "2" "$(feed with brain codex apply_patch '{"command":"*** Begin Patch"}')"
assert_eq "codex exec git status allowed"      "0" "$(feed with brain codex exec_command '{"cmd":"git status"}')"
assert_eq "codex exec git push blocked"        "2" "$(feed with brain codex exec_command '{"cmd":"git push origin main"}')"
assert_eq "codex exec argv git push blocked"   "2" "$(feed with brain codex exec_command '{"cmd":["git","push"]}')"
assert_eq "codex exec rg allowed"              "0" "$(feed with brain codex exec_command '{"cmd":"rg -n parity /tmp"}')"
assert_eq "codex Bash cat allowed"             "0" "$(feed with brain codex Bash '{"command":"cat /etc/hosts"}')"
assert_eq "codex exec rm blocked"              "2" "$(feed with brain codex exec_command '{"cmd":"rm -rf /tmp/x"}')"
assert_eq "codex exec find -delete blocked"    "2" "$(feed with brain codex exec_command '{"cmd":"find /tmp -delete"}')"
assert_eq "codex exec piped cat blocked"       "2" "$(feed with brain codex exec_command '{"cmd":"cat a | sh"}')"
assert_eq "codex cat without predicate file blocked" "2" "$(feed without brain codex exec_command '{"cmd":"cat /etc/hosts"}')"
assert_eq "codex exec without cmd blocked (fail closed)" "2" "$(feed with brain codex exec_command '{}')"
assert_eq "codex near-miss tool name passes"   "0" "$(feed with brain codex apply_patchx '{"command":"*** Begin Patch"}')"
# git --output writes a file (design 2026-10-18 v120-security-fixes 1b); --output-indicator-* does not
assert_eq "claude Bash git diff --output= blocked"      "2" "$(feed with brain "" Bash '{"command":"git diff --output=/tmp/x"}')"
assert_eq "codex exec git diff --output= blocked"       "2" "$(feed with brain codex exec_command '{"cmd":"git diff --output=/tmp/x"}')"
assert_eq "claude Bash git log --output PATH blocked"   "2" "$(feed with brain "" Bash '{"command":"git log --output /tmp/x"}')"
assert_eq "claude Bash git diff quoted --output= blocked" "2" "$(feed with brain "" Bash '{"command":"git diff \"--output=/tmp/x\""}')"
assert_eq "claude Bash git diff backslash --output= blocked" "2" "$(feed with brain "" Bash '{"command":"git diff --\\output=/tmp/x"}')"
# Shell expansion rewrites an approved command (design 2026-10-18 v120-security-fixes-remediation)
assert_eq "claude Bash git diff brace --output blocked" "2" "$(feed with brain "" Bash '{"command":"git diff {--output=/tmp/x,}"}')"
assert_eq "codex exec git diff brace --output blocked"  "2" "$(feed with brain codex exec_command '{"cmd":"git diff {--output=/tmp/x,}"}')"
assert_eq "codex exec git -C brace blocked"             "2" "$(feed with brain codex exec_command '{"cmd":"git -C {.,diff,--output=/tmp/x} status"}')"
assert_eq "claude Bash git diff glob blocked"           "2" "$(feed with brain "" Bash '{"command":"git diff *"}')"
assert_eq "claude Bash git diff ? blocked"              "2" "$(feed with brain "" Bash '{"command":"git diff ?"}')"
assert_eq "claude Bash git log brace range blocked"     "2" "$(feed with brain "" Bash '{"command":"git log {1..2}"}')"
assert_eq "codex exec find brace -delete blocked"       "2" "$(feed with brain codex exec_command '{"cmd":"find /tmp {-delete,}"}')"
assert_eq "claude Bash git log HEAD@{1} allowed"        "0" "$(feed with brain "" Bash '{"command":"git log HEAD@{1}"}')"
assert_eq "claude Bash git diff allowed"                "0" "$(feed with brain "" Bash '{"command":"git diff"}')"
assert_eq "codex exec git diff allowed"                 "0" "$(feed with brain codex exec_command '{"cmd":"git diff"}')"
assert_eq "claude Bash git diff --output-indicator-new allowed" "0" "$(feed with brain "" Bash '{"command":"git diff --output-indicator-new=+"}')"
# Non-Brain sessions pass through
assert_eq "worker apply_patch passes"          "0" "$(feed with worker codex apply_patch '{"command":"*** Begin Patch"}')"
printf '\n%d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
