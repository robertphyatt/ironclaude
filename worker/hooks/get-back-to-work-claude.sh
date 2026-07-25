#!/bin/bash
# get-back-to-work-claude.sh - client-aware Stop-hook wrapper (Approach C').
#
# The enforcement logic lives (byte-identical) in get-back-to-work-impl.sh. This wrapper
# is the fixed path both Claude Code and Codex invoke for the Stop event:
#   - Non-codex (claude/default): exec the impl -> byte-identical stdout+exit (no capture).
#   - Codex: capture the impl's Claude {decision,reason} output and translate it to
#     Codex's Stop-output shape (Codex rejects decision/reason; it accepts systemMessage).
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

# Translate one Claude Stop-JSON object (stdin) -> Codex Stop-output shape (stdout).
# Allow (approve / no explicit block) -> {}  (silent; Codex has no Stop-continuation).
# Block -> {"systemMessage": <reason/text>}  (surfaces the GBTW message; Codex cannot
# hard-block on Stop, so this is best-effort — a documented limitation).
_translate_stop_to_codex() {
  jq -c 'if (.decision // "approve") == "block"
         then ((.systemMessage // .reason // "") as $m | if $m == "" then {} else {systemMessage: $m} end)
         else {} end' 2>/dev/null || printf '{}'
}

# Wrapper-only test shim: expose _is_codex/_translate_stop_to_codex without running the body.
if [ "${GBTW_WRAPPER_TEST_MODE:-0}" = "1" ]; then
  return 0 2>/dev/null || exit 0
fi

INPUT="$(cat)"
if _is_codex; then
  OUT="$(printf '%s' "$INPUT" | bash "$IMPL")"; RC=$?
  if [ -z "$OUT" ]; then printf '{}'; else printf '%s' "$OUT" | _translate_stop_to_codex; fi
  exit $RC
else
  # Byte-identical pass-through: exec makes the impl's stdout+exit the wrapper's.
  # (impl reads stdin via $(cat), so a re-fed stdin without a trailing newline parses identically.)
  printf '%s' "$INPUT" | exec bash "$IMPL"
fi
