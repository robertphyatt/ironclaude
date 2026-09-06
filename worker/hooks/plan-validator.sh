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
# CONFIG SPOT RESOLUTION (shared rule — see worker/config-schema/llm-backend.md)
# =============================================================================

# Pure helper: resolve backend/model/url for a given config spot.
# Args: $1 = config JSON (content, not a path), $2 = spot name (e.g. "validation")
# Output: "<backend> <model> <url>" (model/url may be empty strings)
# Implements the resolution rule verbatim:
#   backend = spots.<spot>.backend // .backend // .validation_backend // "haiku"
#   model   = spots.<spot>.model // <legacy alias> // <resolved-backend-block>.model
#   url     = <resolved-backend-block>'s connection field (ollama.url / openai.base_url)
# Legacy aliases (backend-agnostic, outranked by spots.<spot>.model):
#   shadow_model (top-level, spot=shadow); ollama.summarization_model (nested, spot=summarization)
_resolve_spot() {
  local config_json="$1"
  local spot="$2"
  local backend model url

  backend=$(printf '%s' "$config_json" | jq -r --arg spot "$spot" \
    '.spots[$spot].backend // .backend // .validation_backend // "haiku"' 2>/dev/null) || backend="haiku"
  [ -n "$backend" ] && [ "$backend" != "null" ] || backend="haiku"

  case "$backend" in
    ollama)
      model=$(printf '%s' "$config_json" | jq -r --arg spot "$spot" '
        .spots[$spot].model
        // (if $spot == "shadow" then .shadow_model else null end)
        // (if $spot == "summarization" then .ollama.summarization_model else null end)
        // .ollama.model
        // empty' 2>/dev/null)
      url=$(printf '%s' "$config_json" | jq -r '.ollama.url // empty' 2>/dev/null)
      ;;
    openai)
      model=$(printf '%s' "$config_json" | jq -r --arg spot "$spot" '
        .spots[$spot].model
        // (if $spot == "shadow" then .shadow_model else null end)
        // (if $spot == "summarization" then .ollama.summarization_model else null end)
        // .openai.model
        // empty' 2>/dev/null)
      url=$(printf '%s' "$config_json" | jq -r '.openai.base_url // empty' 2>/dev/null)
      ;;
    *)
      model=$(printf '%s' "$config_json" | jq -r --arg spot "$spot" '.spots[$spot].model // empty' 2>/dev/null)
      url=""
      ;;
  esac

  echo "$backend $model $url"
}

# =============================================================================
# CENTRALIZED LLM VALIDATION
# =============================================================================

# Call validation LLM (Ollama, OpenAI-compatible, or Haiku based on config)
# Args: $1 = prompt to send, $2 = JSON schema string (standard JSON Schema format)
# Output: JSON response string
# Sets: VALIDATION_LLM_BACKEND (global) = backend name used
# Sets: VALIDATION_LLM_RESPONSE (global) = raw LLM response
# Returns: 0 on success, 1 on failure
# IRONCLAUDE_LLM_PATH: hook_validator; destination machine.
call_validation_llm() {
  local prompt="$1"
  local schema="${2:-}"
  local config="${IC_OLLAMA_CONFIG_PATH:-$HOME/.claude/ironclaude-hooks-config.json}"
  local timeout_sec=60
  local backend="haiku"

  # Initialize exports for logging
  export VALIDATION_LLM_BACKEND=""
  export VALIDATION_LLM_RESPONSE=""

  # Load config if exists
  if [ -f "$config" ]; then
    local resolved
    resolved=$(_resolve_spot "$(cat "$config" 2>/dev/null)" "validation")
    backend=$(printf '%s' "$resolved" | awk '{print $1}')
    [ -n "$backend" ] || backend="haiku"
    timeout_sec=$(jq -r '.timeout_seconds // 60' "$config" 2>/dev/null) || timeout_sec=60
  fi

  case "$backend" in
    "ollama")
      local url model fallback_url
      url=$(jq -r '.ollama.url // "http://localhost:11434"' "$config" 2>/dev/null)
      model=$(jq -r '.spots.validation.model // .ollama.model // "llama3.2:1b"' "$config" 2>/dev/null)
      fallback_url=$(jq -r '.ollama.fallback_url // empty' "$config" 2>/dev/null) || true
      # Honor block-level ollama.timeout_seconds (mirrors the openai arm), else top-level.
      timeout_sec=$(jq -r '.ollama.timeout_seconds // .timeout_seconds // 60' "$config" 2>/dev/null) || timeout_sec=60

      # Set backend for logging
      export VALIDATION_LLM_BACKEND="ollama:${model}"

      # Build JSON payload with caller-provided schema enforcement
      local payload
      if [ -n "$schema" ]; then
        payload=$(jq -n \
          --arg model "$model" \
          --arg prompt "$prompt" \
          --argjson schema "$schema" \
          '{model: $model, prompt: $prompt, stream: false, format: $schema, options: {temperature: 0.1, num_predict: -1}}')
      else
        payload=$(jq -n \
          --arg model "$model" \
          --arg prompt "$prompt" \
          '{model: $model, prompt: $prompt, stream: false, format: "json", options: {temperature: 0.1, num_predict: -1}}')
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

      # Strip think tags — gemma4/other thinking models may prefix JSON with <think>...</think>
      if [ -n "$result" ]; then
        result=$(printf '%s' "$result" | python3 -c "import sys, re; print(re.sub(r'<think>.*?</think>', '', sys.stdin.read(), flags=re.DOTALL).strip())" 2>/dev/null) || true
      fi

      export VALIDATION_LLM_RESPONSE="$result"
      echo "$result"
      ;;

    "openai")
      local base_url model_o maxtok
      base_url=$(jq -r '.openai.base_url // empty' "$config" 2>/dev/null)
      model_o=$(jq -r '.spots.validation.model // .openai.model // empty' "$config" 2>/dev/null)
      maxtok=$(jq -r '.openai.max_tokens // 1024' "$config" 2>/dev/null)
      timeout_sec=$(jq -r '.openai.timeout_seconds // .timeout_seconds // 60' "$config" 2>/dev/null) || timeout_sec=60

      # Set backend for logging
      export VALIDATION_LLM_BACKEND="openai:${model_o}"

      local payload
      if [ -n "$schema" ] && [ "$schema" != "{}" ]; then
        payload=$(jq -nc --arg m "$model_o" --arg p "$prompt" --argjson mt "$maxtok" --argjson sc "$schema" \
          '{model:$m, messages:[{role:"user",content:$p}], max_tokens:$mt, temperature:0.1, response_format:{type:"json_schema",json_schema:{name:"verdict",schema:$sc}}}')
      else
        payload=$(jq -nc --arg m "$model_o" --arg p "$prompt" --argjson mt "$maxtok" \
          '{model:$m, messages:[{role:"user",content:$p}], max_tokens:$mt, temperature:0.1}')
      fi

      local result
      local connect_timeout="$timeout_sec"
      result=$(curl -s --connect-timeout "$connect_timeout" --max-time "$timeout_sec" \
        -H "Authorization: Bearer ollama" -H "Content-Type: application/json" \
        "$base_url/chat/completions" -d "$payload" 2>/dev/null | jq -r '.choices[0].message.content // empty' 2>/dev/null) || true

      # Strip think tags — same reasoning-model prefix handling as the ollama arm
      if [ -n "$result" ]; then
        result=$(printf '%s' "$result" | python3 -c "import sys, re; print(re.sub(r'<think>.*?</think>', '', sys.stdin.read(), flags=re.DOTALL).strip())" 2>/dev/null) || true
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
