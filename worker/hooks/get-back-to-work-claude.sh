#!/bin/bash
# get-back-to-work-claude.sh - client-aware Stop-hook wrapper (Approach C').
#
# The enforcement logic lives (byte-identical) in get-back-to-work-impl.sh. This wrapper
# is the fixed path both Claude Code and Codex invoke for the Stop event:
#   - Non-codex (claude/default): exec the impl -> byte-identical stdout+exit (no capture).
#   - Codex: capture the impl's Claude {decision,reason} output and translate it to
#     Codex's native Stop-output shape.
# Client is detected from the plugin-root path (grounded: codex plugin root is under
# /.codex/, claude's under /.claude/ — see docs/plans/2026-07-21-codex-stop-hook-fix-findings.md).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IMPL="$SCRIPT_DIR/get-back-to-work-impl.sh"

# Preserve helper-exposure for tests that source this file with GBTW_TEST_MODE=1
# (delegate to impl, whose own shim exposes the _gbtw_* helpers and returns).
if [ "${GBTW_TEST_MODE:-0}" = "1" ]; then
  source "$IMPL"
  return 0 2>/dev/null || exit 0
fi

# Is THIS Stop-hook invocation running under Codex?
_is_codex() {
  [ "${GBTW_FORCE_CODEX:-0}" = "1" ] && return 0
  case "${PLUGIN_ROOT:-${CLAUDE_PLUGIN_ROOT:-}}" in
    */.codex/*|*/.codex) return 0 ;;
  esac
  return 1
}

# Emit a bounded wrapper-failure response. An initial Stop requests one native
# verification continuation. A continued Stop terminates visibly rather than
# trusting a failed implementation to update its own throttle.
_codex_stop_wrapper_failure() {
  if [ "${1:-false}" = "true" ]; then
    jq -cn '{
      continue: false,
      stopReason: "[GET-BACK-TO-WORK]: Stop enforcement verification failed after continuation.",
      systemMessage: "[GET-BACK-TO-WORK]: Stop enforcement verification failed after continuation."
    }'
  else
    jq -cn '{
      decision: "block",
      reason: "[GET-BACK-TO-WORK]: Stop enforcement verification failed; continue once to retry."
    }'
  fi
}

# Translate one Claude Stop result (stdin) to native Codex Stop JSON.
# Arguments: stop_hook_active, implementation exit status.
_translate_stop_to_codex() {
  local stop_active="${1:-false}"
  local impl_rc="${2:-1}"
  local raw normalized
  raw="$(cat)"

  if [ "$impl_rc" -ne 0 ]; then
    _codex_stop_wrapper_failure "$stop_active"
    return 0
  fi

  normalized="$(printf '%s' "$raw" | jq -cse '
    if length != 1
    then error("expected exactly one Stop result")
    else .[0] |
      if type != "object" or
         (.decision | type) != "string" or
         (.decision != "approve" and .decision != "block")
      then error("invalid Stop result")
      elif .decision == "approve"
      then {}
      else
        ((.reason // .systemMessage // "") |
          if type == "string" and length > 0
          then .
          else "[GET-BACK-TO-WORK]: Stop blocked without a reason."
          end) as $reason |
        {decision: "block", reason: $reason}
      end
    end
  ' 2>/dev/null)" || {
    _codex_stop_wrapper_failure "$stop_active"
    return 0
  }
  printf '%s' "$normalized"
}

# Run the Codex branch with a testable implementation boundary. Codex consumes
# Stop JSON only on exit zero, so implementation failures are represented in
# the bounded fail-closed JSON contract above.
_run_codex_stop() {
  local input="${1:-}"
  local impl_path="${2:-$IMPL}"
  local stop_active out impl_rc

  stop_active="$(printf '%s' "$input" | jq -r '
    if type == "object" and .stop_hook_active == true
    then "true"
    else "false"
    end
  ' 2>/dev/null)" || stop_active="false"

  out="$(printf '%s' "$input" | bash "$impl_path")"
  impl_rc=$?
  printf '%s' "$out" | _translate_stop_to_codex "$stop_active" "$impl_rc"
  return 0
}

# Wrapper-only test shim: expose _is_codex/_translate_stop_to_codex without running the body.
if [ "${GBTW_WRAPPER_TEST_MODE:-0}" = "1" ]; then
  return 0 2>/dev/null || exit 0
fi

INPUT="$(cat)"
if _is_codex; then
  _run_codex_stop "$INPUT" "$IMPL"
  exit 0
else
  # Byte-identical pass-through: exec makes the impl's stdout+exit the wrapper's.
  # (impl reads stdin via $(cat), so a re-fed stdin without a trailing newline parses identically.)
  printf '%s' "$INPUT" | exec bash "$IMPL"
fi
