#!/bin/bash
#
# NOTE: Intentionally NOT using set -euo pipefail here because:
#   - This is a library file sourced by other hooks
#   - Contains conditional checks where grep "no match" is expected
#   - Must not cause side effects when sourced
#   - Errors are handled explicitly in each function
#
# plan-validator.sh - Shared LLM validation logic for professional mode hooks
#
# Usage: source this file, then call call_validation_llm "$PROMPT" "$SCHEMA"
#
# State management (plan flags, progress tracking) has been moved to
# state-manager.sh / SQLite. This file only provides:
#   - call_validation_llm() — centralized LLM call (Ollama or Haiku)
#   - error_exit() — consistent error handling

# =============================================================================
# CENTRALIZED LLM VALIDATION
# =============================================================================

# Call validation LLM (Ollama or Haiku based on config)
# Args: $1 = prompt to send, $2 = JSON schema string (standard JSON Schema format)
# Output: JSON response string
# Sets: VALIDATION_LLM_BACKEND (global) = backend name used
# Sets: VALIDATION_LLM_RESPONSE (global) = raw LLM response
# Returns: 0 on success, 1 on failure
call_validation_llm() {
  local prompt="$1"
  local schema="${2:-}"
  local config="$HOME/.claude/ironclaude-hooks-config.json"
  local timeout_sec=60
  local backend="haiku"

  # Initialize exports for logging
  export VALIDATION_LLM_BACKEND=""
  export VALIDATION_LLM_RESPONSE=""

  # Load config if exists
  if [ -f "$config" ]; then
    backend=$(jq -r '.validation_backend // "haiku"' "$config" 2>/dev/null) || backend="haiku"
    timeout_sec=$(jq -r '.timeout_seconds // 60' "$config" 2>/dev/null) || timeout_sec=60
  fi

  case "$backend" in
    "ollama")
      local url model fallback_url
      url=$(jq -r '.ollama.url // "http://localhost:11434"' "$config" 2>/dev/null)
      model=$(jq -r '.ollama.model // "llama3.2:1b"' "$config" 2>/dev/null)
      fallback_url=$(jq -r '.ollama.fallback_url // empty' "$config" 2>/dev/null) || true

      # Set backend for logging
      export VALIDATION_LLM_BACKEND="ollama:${model}"

      # Build JSON payload with caller-provided schema enforcement
      local payload
      if [ -n "$schema" ]; then
        payload=$(jq -n \
          --arg model "$model" \
          --arg prompt "$prompt" \
          --argjson schema "$schema" \
          '{model: $model, prompt: $prompt, stream: false, format: $schema}')
      else
        payload=$(jq -n \
          --arg model "$model" \
          --arg prompt "$prompt" \
          '{model: $model, prompt: $prompt, stream: false, format: "json"}')
      fi

      # Try primary URL (2s connect timeout if fallback configured, full timeout otherwise)
      local result=""
      local connect_timeout="$timeout_sec"
      if [ -n "$fallback_url" ]; then
        connect_timeout=2
      fi
      result=$(curl -s --connect-timeout "$connect_timeout" --max-time "$timeout_sec" "$url/api/generate" -d "$payload" 2>/dev/null | jq -r '.response // empty' 2>/dev/null) || true

      # If primary failed and fallback exists, try fallback
      if [ -z "$result" ] && [ -n "$fallback_url" ]; then
        export VALIDATION_LLM_BACKEND="ollama:${model}(fallback)"
        result=$(curl -s --max-time "$timeout_sec" "$fallback_url/api/generate" -d "$payload" 2>/dev/null | jq -r '.response // empty' 2>/dev/null) || true
      fi

      export VALIDATION_LLM_RESPONSE="$result"
      echo "$result"
      ;;

    "haiku"|*)
      export VALIDATION_LLM_BACKEND="haiku"

      # Haiku with JSON schema enforcement via --output-format json --json-schema
      local result
      if [ -n "$schema" ]; then
        result=$(portable_timeout "$timeout_sec" bash -c \
          'cd /tmp && echo "$1" | claude --model haiku --print --tools "" --output-format json --json-schema "$2"' \
          _ "$prompt" "$schema" 2>/dev/null) || return 1
      else
        result=$(portable_timeout "$timeout_sec" bash -c \
          'cd /tmp && echo "$1" | claude --model haiku --print --tools ""' \
          _ "$prompt" 2>/dev/null) || return 1
      fi
      export VALIDATION_LLM_RESPONSE="$result"
      echo "$result"
      ;;
  esac
}

# =============================================================================
# ERROR HELPER
# =============================================================================

# Consistent error exit with context
# Args: $1 = exit code, $2 = message, $3 = context (optional)
error_exit() {
  local code="$1"
  local message="$2"
  local context="${3:-}"
  local full_msg="$message"
  if [ -n "$context" ]; then
    full_msg="$message | Context: $context"
  fi
  full_msg="$full_msg | Hook: $(basename "$0")"
  log_error "PLAN-VALIDATOR" "$full_msg"
  exit "$code"
}
