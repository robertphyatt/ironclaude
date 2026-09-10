#!/usr/bin/env bash
# Tests for ic_skill_ref / ic_is_codex in hook-logger.sh.
# RED until hook-logger.sh defines these functions.

source "$(dirname "${BASH_SOURCE[0]}")/../hook-logger.sh"

pass_count=0
fail_count=0

check() {
  local desc="$1" got="$2" want="$3"
  if [ "$got" = "$want" ]; then
    echo "PASS: $desc"
    pass_count=$((pass_count + 1))
  else
    echo "FAIL: $desc (got=[$got] want=[$want])"
    fail_count=$((fail_count + 1))
  fi
}

check_rc() {
  local desc="$1" rc="$2" want_zero="$3"
  if [ "$want_zero" = "yes" ]; then
    if [ "$rc" -eq 0 ]; then
      echo "PASS: $desc"
      pass_count=$((pass_count + 1))
    else
      echo "FAIL: $desc (rc=$rc, wanted 0)"
      fail_count=$((fail_count + 1))
    fi
  else
    if [ "$rc" -ne 0 ]; then
      echo "PASS: $desc"
      pass_count=$((pass_count + 1))
    else
      echo "FAIL: $desc (rc=$rc, wanted nonzero)"
      fail_count=$((fail_count + 1))
    fi
  fi
}

# ic_skill_ref: codex, with args
got="$(IC_HOOK_CLIENT=codex ic_skill_ref "ironclaude:code-review" "--task-boundary")"
check "ic_skill_ref codex with args" "$got" '$ironclaude:code-review --task-boundary'

# ic_skill_ref: claude, with args
got="$(IC_HOOK_CLIENT=claude ic_skill_ref "ironclaude:code-review" "--task-boundary")"
check "ic_skill_ref claude with args" "$got" 'Skill(skill="ironclaude:code-review", args="--task-boundary")'

# ic_skill_ref: codex, no args
got="$(IC_HOOK_CLIENT=codex ic_skill_ref "ironclaude:plan-interruption")"
check "ic_skill_ref codex no args" "$got" '$ironclaude:plan-interruption'

# ic_skill_ref: claude, no args
got="$(IC_HOOK_CLIENT=claude ic_skill_ref "ironclaude:plan-interruption")"
check "ic_skill_ref claude no args" "$got" 'Skill(skill="ironclaude:plan-interruption", args="")'

# ic_is_codex: IC_HOOK_CLIENT=codex -> 0
if IC_HOOK_CLIENT=codex ic_is_codex; then rc=0; else rc=$?; fi
check_rc "ic_is_codex IC_HOOK_CLIENT=codex returns 0" "$rc" "yes"

# ic_is_codex: IC_HOOK_CLIENT=claude -> nonzero
if IC_HOOK_CLIENT=claude ic_is_codex; then rc=0; else rc=$?; fi
check_rc "ic_is_codex IC_HOOK_CLIENT=claude returns nonzero" "$rc" "no"

# ic_is_codex: unset IC_HOOK_CLIENT, PLUGIN_ROOT under /.codex/ -> 0
if env -u IC_HOOK_CLIENT PLUGIN_ROOT=/x/.codex/y bash -c 'source "'"$(dirname "${BASH_SOURCE[0]}")/../hook-logger.sh"'"; ic_is_codex'; then rc=0; else rc=$?; fi
check_rc "ic_is_codex unset client, PLUGIN_ROOT=/x/.codex/y returns 0" "$rc" "yes"

# ic_is_codex: unset IC_HOOK_CLIENT, PLUGIN_ROOT under /.claude/ -> nonzero
if env -u IC_HOOK_CLIENT PLUGIN_ROOT=/x/.claude/y bash -c 'source "'"$(dirname "${BASH_SOURCE[0]}")/../hook-logger.sh"'"; ic_is_codex'; then rc=0; else rc=$?; fi
check_rc "ic_is_codex unset client, PLUGIN_ROOT=/x/.claude/y returns nonzero" "$rc" "no"

echo ""
echo "Results: $pass_count passed, $fail_count failed"

if [ "$fail_count" -ne 0 ]; then
  exit 1
fi
exit 0
