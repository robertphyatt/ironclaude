#!/bin/bash
# config-guard.sh — shared, tested decision logic for key-scoped enforcement of
# ~/.claude/ironclaude-hooks-config.json. Sourced by professional-mode-guard.sh (the
# enforcer) AND by test-config-guard.sh (the tests) so the enforced logic and the tested
# logic are the SAME code (no drift).
#
# PROVABILITY BOUNDARY: the key-scoping is a provable control for any tool call that NAMES
# the config file via a structured file tool (Write/Edit/MultiEdit), matched
# case-insensitively — an agent cannot change a guardrail key through such a call. It is
# NOT provable for (a) the Bash channel (config_bash_write_vector is best-effort — an
# opaque interpreter command 'python3 -c open(cfg,"w")…' or a split filename evades it),
# nor (b) filesystem aliasing (a symlink/hardlink to the config created via the Bash
# residual, then written through the alias path). Both are v1.0.19's acknowledged
# limitation ("a determined agent with arbitrary Bash in executing needs OS-level
# sandboxing").
#
# Benign (operator-configurable) top-level keys: validation_backend, ollama,
# timeout_seconds. Everything else — the guardrail keys (tier_up_review_policy,
# debug_allow_config_writes) AND any unrecognized key — is protected (allowlist =>
# fail-closed for unknowns).

# config_write_verdict <current_json_str> <proposed_json_str> -> "allow"|"block"
config_write_verdict() {
  local current="$1" proposed="$2"
  printf '%s' "$proposed" | jq -e 'type == "object"' >/dev/null 2>&1 || { echo "block"; return; }
  local prop_proj cur_proj
  prop_proj=$(printf '%s' "$proposed" | jq -cS 'del(.validation_backend, .ollama, .timeout_seconds)' 2>/dev/null) || { echo "block"; return; }
  if printf '%s' "$current" | jq -e 'type == "object"' >/dev/null 2>&1; then
    cur_proj=$(printf '%s' "$current" | jq -cS 'del(.validation_backend, .ollama, .timeout_seconds)' 2>/dev/null) || cur_proj='{}'
  else
    cur_proj='{}'
  fi
  if [ "$prop_proj" = "$cur_proj" ]; then echo "allow"; else echo "block"; fi
}

# config_tool_write_verdict <pretooluse_event_json> [config_path] -> "allow"|"block"
config_tool_write_verdict() {
  local event="$1"
  local config_path="${2:-$HOME/.claude/ironclaude-hooks-config.json}"
  local proposed current
  proposed=$(printf '%s' "$event" | jq -r '.tool_input.content // empty' 2>/dev/null || true)
  current=$(cat "$config_path" 2>/dev/null || echo "")
  config_write_verdict "$current" "$proposed"
}

# is_config_path <file_path> -> "yes"|"no"  (CASE-INSENSITIVE: default APFS case-folds, so
# IRONCLAUDE-HOOKS-CONFIG.JSON names the same file — it must still match.)
is_config_path() {
  local fp="$1" canon lc_canon lc_fp lc_target
  canon=$(realpath -m "$fp" 2>/dev/null || echo "$fp")
  lc_canon=$(printf '%s' "$canon" | tr '[:upper:]' '[:lower:]')
  lc_fp=$(printf '%s' "$fp" | tr '[:upper:]' '[:lower:]')
  lc_target=$(printf '%s' "$HOME/.claude/ironclaude-hooks-config.json" | tr '[:upper:]' '[:lower:]')
  if [[ "$lc_canon" == "$lc_target" || "$lc_fp" == *"ironclaude-hooks-config.json" ]]; then
    echo "yes"
  else
    echo "no"
  fi
}

# config_bash_write_vector <command_string> -> "block"|"allow"  (BEST-EFFORT; see boundary.
# Case-insensitive filename match loosened to the distinctive stem so escaped/normalized
# variants are still caught; the write-vector list now includes rm/unlink.)
config_bash_write_vector() {
  local cmd="$1"
  if printf '%s' "$cmd" | grep -qi "ironclaude-hooks-config" \
     && printf '%s' "$cmd" | grep -qE '(>>?|\btee\b|\bsed\b[^|]*-i|\bdd\b|\bcp\b|\bmv\b|\bln\b|\brsync\b|\btruncate\b|\binstall\b|\brm\b|\bunlink\b)'; then
    echo "block"
  else
    echo "allow"
  fi
}

# config_guard_decision <tool_name> <file_path_or_command> <event_json> [config_path]
#   -> "block"|"allow"
# THE single decision entry the guard calls — encapsulates routing + the block/allow
# polarity so the guard has no untested glue.
config_guard_decision() {
  local tool="$1" fp="$2" event="$3" config_path="${4:-$HOME/.claude/ironclaude-hooks-config.json}"
  case "$tool" in
    Edit|MultiEdit)
      if [ "$(is_config_path "$fp")" = "yes" ]; then echo "block"; return; fi ;;
    Write)
      if [ "$(is_config_path "$fp")" = "yes" ]; then
        if [ "$(config_tool_write_verdict "$event" "$config_path")" != "allow" ]; then echo "block"; return; fi
      fi ;;
    Bash)
      if [ "$(config_bash_write_vector "$fp")" = "block" ]; then echo "block"; return; fi ;;
  esac
  echo "allow"
}
