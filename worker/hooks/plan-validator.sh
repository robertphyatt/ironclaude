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
#   - call_validation_llm() — centralized LLM call per model class. Haiku class (default):
#     local box (Ollama/OpenAI-compatible), then Claude Haiku or the newest Codex Luna,
#     then fail open. Sonnet class: Claude Sonnet or the newest Codex Terra only, then fail open.
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

# Pure helper: resolve the bounded-transport budgets (connect timeout, probe
# timeout, hook wall budget) for a given backend, block-then-top precedence.
# Args: $1 = config JSON (content, not a path), $2 = backend name (e.g. "ollama"/"openai")
# Output: "<connect_timeout> <probe_timeout> <hook_validation_budget>"
# Implements: KEY = .<backend>.KEY // .KEY // default (independent of any
# per-spot inference timeout — a busy/dead box must not hang a hook).
_resolve_budgets() {
  local cfg_json="$1" backend="$2"
  local ct pt hb
  ct=$(printf '%s' "$cfg_json" | jq -r --arg b "$backend" '.[$b].connect_timeout_seconds // .connect_timeout_seconds // 3' 2>/dev/null) || ct=3
  pt=$(printf '%s' "$cfg_json" | jq -r --arg b "$backend" '.[$b].probe_timeout_seconds // .probe_timeout_seconds // 3' 2>/dev/null) || pt=3
  hb=$(printf '%s' "$cfg_json" | jq -r --arg b "$backend" '.[$b].hook_validation_budget_seconds // .hook_validation_budget_seconds // 8' 2>/dev/null) || hb=8
  [ -n "$ct" ] && [ "$ct" != "null" ] || ct=3
  [ -n "$pt" ] && [ "$pt" != "null" ] || pt=3
  [ -n "$hb" ] && [ "$hb" != "null" ] || hb=8
  echo "$ct $pt $hb"
}

# =============================================================================
# LOCAL-BOX BACKOFF (design 2026-10-17 v120-completeness R4c; see llm-backend.md)
# =============================================================================

# One marker, shared with commander ironclaude/local_backoff.py (a Commander test compares the
# default, the directory, the marker name and the directory check). After a failed local-box attempt
# the box is skipped for local_backoff_minutes (default 15; 0 disables); the next call after expiry
# tries it again, a success clears the marker and a failure renews it.
IC_LOCAL_BACKOFF_DEFAULT_MINUTES=15
IC_LOCAL_BACKOFF_MARKER_NAME="local-box-backoff.json"

_local_backoff_dir() {
  local d="${IRONCLAUDE_LOCAL_BACKOFF_DIR:-$HOME/.ironclaude/state/local-backoff}"
  echo "${d%/}"
}

# Same check as brain-gated-actions.sh gate_root_problem: a real directory owned by this user with no
# group or other permission bits, else the marker is neither trusted nor written.
_local_backoff_dir_problem() {
  local root="$1" perms
  if [ -L "$root" ]; then echo "is a symlink"; return 0; fi
  if [ ! -d "$root" ]; then echo "is not a directory"; return 0; fi
  if [ ! -O "$root" ]; then echo "is not owned by the current user"; return 0; fi
  perms=$(ls -ld "$root") || { echo "has unreadable permissions"; return 0; }
  if [ "${perms:4:6}" != "------" ]; then echo "has group or other permission bits"; fi
  return 0
}

# Pure helper: backoff minutes from config JSON content ($1) for backend $2, block-then-top like
# _resolve_budgets; a non-number or a negative value means the default.
_resolve_local_backoff_minutes() {
  local m
  m=$(printf '%s' "$1" | jq -r --arg b "$2" --argjson d "$IC_LOCAL_BACKOFF_DEFAULT_MINUTES" \
    '(.[$b].local_backoff_minutes // .local_backoff_minutes // $d) | if type == "number" and . >= 0 then floor else $d end' 2>/dev/null) || m=""
  [[ "$m" =~ ^[0-9]+$ ]] || m="$IC_LOCAL_BACKOFF_DEFAULT_MINUTES"
  echo "$m"
}

# Returns 0 while a live marker for endpoint $1 exists and the backoff ($2 minutes) is on.
_local_backoff_active() {
  local dir file
  [ -n "$1" ] && [ "${2:-0}" -gt 0 ] || return 1
  dir=$(_local_backoff_dir)
  [ -z "$(_local_backoff_dir_problem "$dir")" ] || return 1
  file="$dir/$IC_LOCAL_BACKOFF_MARKER_NAME"
  [ -f "$file" ] || return 1
  jq -e --arg u "$1" --argjson now "$(date +%s)" \
    '.endpoint == $u and (.until | type) == "number" and .until > $now' "$file" >/dev/null 2>&1
}

# Record a local-box attempt for endpoint $1 ($2 minutes, $3 ok|fail). ok clears a marker for this
# endpoint; fail writes {endpoint, until=now+minutes} atomically when the backoff is on.
_local_backoff_record() {
  local dir file tmp
  [ -n "$1" ] || return 0
  dir=$(_local_backoff_dir)
  file="$dir/$IC_LOCAL_BACKOFF_MARKER_NAME"
  if [ "$3" = "ok" ]; then
    [ -z "$(_local_backoff_dir_problem "$dir")" ] || return 0
    [ -f "$file" ] && jq -e --arg u "$1" '.endpoint == $u' "$file" >/dev/null 2>&1 && rm -f "$file"
    return 0
  fi
  [ "${2:-0}" -gt 0 ] || return 0
  mkdir -p "$(dirname "$dir")" 2>/dev/null || return 0
  [ -e "$dir" ] || [ -L "$dir" ] || mkdir -m 700 "$dir" 2>/dev/null || return 0
  [ -z "$(_local_backoff_dir_problem "$dir")" ] || return 0
  tmp=$(mktemp "$dir/.marker.XXXXXX") || return 0
  jq -cn --arg u "$1" --argjson until "$(( $(date +%s) + $2 * 60 ))" '{endpoint:$u,until:$until}' > "$tmp" \
    && mv -f "$tmp" "$file"
  rm -f "$tmp"
  return 0
}

# =============================================================================
# HAIKU-CLASS FAST MODEL (chain step after the local box; see llm-backend.md)
# =============================================================================

# Default for fast_model_timeout_seconds, set from measured lean-child latency
# (docs/plans/2026-10-16-haiku-class-fast-model-fallback-findings.md).
IC_FAST_MODEL_TIMEOUT_DEFAULT=30

# Pure helper: fast-model cap from config JSON content ($1); default when unset/invalid.
_resolve_fast_model_timeout() {
  local t
  t=$(printf '%s' "$1" | jq -r --argjson d "$IC_FAST_MODEL_TIMEOUT_DEFAULT" \
    '.fast_model_timeout_seconds // $d | if type == "number" and . > 0 then floor else $d end' 2>/dev/null) || t=""
  [[ "$t" =~ ^[0-9]+$ ]] || t="$IC_FAST_MODEL_TIMEOUT_DEFAULT"
  echo "$t"
}

# Default for sonnet_model_timeout_seconds, set from measured lean Sonnet latency
# (docs/plans/2026-10-16-per-call-difficulty-tiering-findings.md).
IC_SONNET_MODEL_TIMEOUT_DEFAULT=30

# Pure helper: Sonnet-class cap from config JSON content ($1); default when unset/invalid.
_resolve_sonnet_model_timeout() {
  local t
  t=$(printf '%s' "$1" | jq -r --argjson d "$IC_SONNET_MODEL_TIMEOUT_DEFAULT" \
    '.sonnet_model_timeout_seconds // $d | if type == "number" and . > 0 then floor else $d end' 2>/dev/null) || t=""
  [[ "$t" =~ ^[0-9]+$ ]] || t="$IC_SONNET_MODEL_TIMEOUT_DEFAULT"
  echo "$t"
}

# Newest installed gpt-<version>-<word> (word: luna|terra) from the Codex models cache. There is no
# pinned fallback (design 2026-10-17 v120-completeness R2): with no model of the family listed, print
# nothing, name the family and the cache path on stderr, and return 1 so the caller treats Codex as
# unavailable for this call. Same rule as commander provider_config.newest_codex_slug and
# state-manager advisor-review.ts newestAtTier.
_newest_codex_model() {
  local word="$1" cache="${CODEX_HOME:-$HOME/.codex}/models_cache.json" slug=""
  if [ -r "$cache" ]; then
    slug=$(jq -r --arg w "$word" '[.models[]? | .slug? | select(type == "string" and test("^gpt-[0-9]+(\\.[0-9]+)*-" + $w + "$"))]
      | sort_by(capture("^gpt-(?<v>[0-9.]+)-").v | split(".") | map(tonumber)) | last // empty' "$cache" 2>/dev/null) || slug=""
  fi
  if [ -z "$slug" ]; then
    printf '%s\n' "[plan-validator] Codex model family ${word} has no installed model in ${cache}; Codex is unavailable for this call" >&2
    return 1
  fi
  echo "$slug"
}

# Haiku class on Codex: the newest Luna. Sonnet class on Codex: the newest Terra.
_newest_luna() { _newest_codex_model luna; }
_newest_terra() { _newest_codex_model terra; }

# claude --output-format json: an event ARRAY (answer in the type=="result" element's
# structured_output) or a single result object. Prints the compact object, or nothing.
_extract_claude_structured() {
  jq -c 'if type == "array" then (map(select(type == "object" and .type == "result")) | last | .structured_output?)
         elif type == "object" and (.type == "result" or has("structured_output")) then .structured_output
         else null end
         | select(type == "object")' 2>/dev/null
}

# codex exec --json JSONL: the LAST item.completed agent_message text, parsed as a JSON
# object. Non-JSON lines are skipped. Prints the compact object, or nothing.
_extract_codex_agent_json() {
  jq -Rc 'fromjson? | select(type == "object" and .type == "item.completed" and (.item | type) == "object" and .item.type == "agent_message") | .item.text' 2>/dev/null \
    | tail -n 1 | jq -r '. // empty' 2>/dev/null | jq -c 'select(type == "object")' 2>/dev/null
}

# The shared Codex no-tools set: the same pairs, in the same order, as commander
# fast_model.CODEX_NO_TOOLS_ARGS and the advisor broker's CODEX_NO_TOOLS_ARGS (a Commander test
# compares all three). The Codex child is a text-only classifier over untrusted text.
IC_CODEX_NO_TOOLS_ARGS=(
  -c features.shell_tool=false -c features.unified_exec=false -c features.view_image=false
  -c features.apps=false -c features.browser_use=false -c features.computer_use=false
  -c features.multi_agent=false -c features.image_generation=false
  -c features.memories=false -c features.plugins=false -c features.hooks=false
  -c features.goals=false -c features.tool_suggest=false
  -c agents.enabled=false -c 'web_search="disabled"'
)

# Strict copy of a JSON schema (stdin) for `codex exec --output-schema`, mirroring commander
# fast_model.strict_codex_schema (a Commander test compares the two): every object node gets
# additionalProperties:false and lists every property in required; a formerly optional property
# becomes nullable. Recurses through properties, items, anyOf and oneOf.
_strict_codex_schema() {
  jq -c '
    def nullable:
      (if (.type | type) == "string" and .type != "null" then .type = [.type, "null"]
       elif (.type | type) == "array" and (.type | any(. == "null") | not) then .type += ["null"]
       else . end)
      | (if (.enum | type) == "array" and (.enum | any(. == null) | not) then .enum += [null] else . end);
    def strict:
      if type != "object" then .
      else
        (if (.items | type) == "object" then .items |= strict else . end)
        | (if (.anyOf | type) == "array" then .anyOf |= map(strict) else . end)
        | (if (.oneOf | type) == "array" then .oneOf |= map(strict) else . end)
        | (if (.properties | type) == "object" then
             ((.required // []) | if type == "array" then . else [] end) as $req
             | .properties |= with_entries(.key as $k
                 | .value |= (strict | if ($req | any(. == $k)) then . else nullable end))
             | .required = (.properties | keys_unsorted)
           else . end)
        | (if (.properties | type) == "object" or .type == "object" then .additionalProperties = false else . end)
      end;
    strict'
}

# Run one fast-model child (Haiku or Sonnet class) under its own cap.
# Args: $1 prompt, $2 JSON schema ('{"type":"object"}' when empty), $3 cap seconds, $4 Codex model slug (Codex only), $5 Claude model (haiku|sonnet; default haiku)
# Output: the plain JSON object. Returns 1 on timeout, non-zero exit or unparseable output.
# The child is exec'd from `bash -c`, so the PID that portable_timeout kills (in any
# branch, including the pure-bash fallback) IS the claude/codex process.
_call_fast_model() {
  local prompt="$1" schema="${2:-}" cap="$3" codex_model="$4" claude_model="${5:-haiku}" work out="" rc=0
  [ -n "$schema" ] && [ "$schema" != "{}" ] || schema='{"type":"object"}'
  work=$(mktemp -d "${TMPDIR:-/tmp}/ic-fast-model.XXXXXX") || return 1
  printf '%s' "$prompt" > "$work/prompt"
  # Codex child runs with every tool feature off (mirrors claude --tools ""): its prompts carry untrusted text.
  if ic_is_codex; then
    printf '%s' "$schema" | _strict_codex_schema > "$work/schema.json"
    portable_timeout "$cap" bash -c 'cd /tmp && exec "$@" < "$0"' "$work/prompt" \
      codex exec --json --ephemeral --ignore-user-config --ignore-rules --skip-git-repo-check \
      -s read-only -c 'model_reasoning_effort="low"' "${IC_CODEX_NO_TOOLS_ARGS[@]}" \
      -m "$codex_model" --output-schema "$work/schema.json" - \
      > "$work/out" 2>/dev/null || rc=$?
    [ "$rc" -eq 0 ] && out=$(_extract_codex_agent_json < "$work/out")
  else
    # Same strip as commander fast_model._ENV_STRIP: routing vars (an Ollama worker exports ANTHROPIC_BASE_URL) and the worker's CLAUDE_CODE_EFFORT_LEVEL, which beats --effort low; env execs claude, so the killed PID is still the child.
    portable_timeout "$cap" bash -c 'cd /tmp && exec "$@" < "$0"' "$work/prompt" \
      env -u ANTHROPIC_API_KEY -u ANTHROPIC_AUTH_TOKEN -u ANTHROPIC_BASE_URL \
      -u CLAUDE_CODE_USE_BEDROCK -u CLAUDE_CODE_USE_VERTEX -u CLAUDE_CODE_EFFORT_LEVEL \
      claude --model "$claude_model" --print --restricted --effort low --tools "" --no-session-persistence \
      --strict-mcp-config --settings '{"disableAllHooks":true}' --output-format json --json-schema "$schema" \
      > "$work/out" 2>/dev/null || rc=$?
    [ "$rc" -eq 0 ] && out=$(_extract_claude_structured < "$work/out")
  fi
  rm -rf "$work"
  [ "$rc" -eq 0 ] && [ -n "$out" ] || return 1
  printf '%s\n' "$out"
}

# =============================================================================
# CENTRALIZED LLM VALIDATION
# =============================================================================

# Call validation LLM (Ollama, OpenAI-compatible, or Haiku based on config)
# Args: $1 = prompt to send, $2 = JSON schema string (standard JSON Schema format), $3 = model class: haiku (default) | sonnet
# Output: JSON response string
# Sets: VALIDATION_LLM_BACKEND (global) = backend name used
# Sets: VALIDATION_LLM_RESPONSE (global) = raw LLM response
# Returns: 0 on success, 1 on failure
# IRONCLAUDE_LLM_PATH: hook_validator; destination machine.
call_validation_llm() {
  local prompt="$1"
  local schema="${2:-}"
  local tier="${3:-haiku}"
  local config="${IC_OLLAMA_CONFIG_PATH:-$HOME/.claude/ironclaude-hooks-config.json}"
  local timeout_sec=60
  local backend="haiku"

  # Initialize exports for logging
  export VALIDATION_LLM_BACKEND=""
  export VALIDATION_LLM_RESPONSE=""

  # Sonnet-class calls never use the local box (design T2) and do not step up (T3).
  if [ "$tier" = "sonnet" ]; then
    local sonnet_cap terra="" sonnet_result
    sonnet_cap=$(_resolve_sonnet_model_timeout "$(cat "$config" 2>/dev/null)")
    if ic_is_codex; then
      if ! terra=$(_newest_terra); then
        export VALIDATION_LLM_BACKEND="terra:unavailable"
        return 1
      fi
      export VALIDATION_LLM_BACKEND="terra:${terra}"
    else
      export VALIDATION_LLM_BACKEND="sonnet"
    fi
    sonnet_result=$(_call_fast_model "$prompt" "$schema" "$sonnet_cap" "$terra" sonnet) || return 1
    export VALIDATION_LLM_RESPONSE="$sonnet_result"
    echo "$sonnet_result"
    return 0
  fi

  # Load config if exists
  if [ -f "$config" ]; then
    local resolved
    resolved=$(_resolve_spot "$(cat "$config" 2>/dev/null)" "validation")
    backend=$(printf '%s' "$resolved" | awk '{print $1}')
    [ -n "$backend" ] || backend="haiku"
    timeout_sec=$(jq -r '.timeout_seconds // 60' "$config" 2>/dev/null) || timeout_sec=60
  fi

  # Bounded transport budgets — independent of the inference timeout above.
  # A busy/dead box must not hang a hook for the (long) inference timeout.
  local connect_to hook_budget _budgets
  _budgets=$(_resolve_budgets "$(cat "$config" 2>/dev/null)" "$backend")
  connect_to=$(printf '%s' "$_budgets" | awk '{print $1}')
  hook_budget=$(printf '%s' "$_budgets" | awk '{print $3}')
  [ -n "$connect_to" ] || connect_to=3
  [ -n "$hook_budget" ] || hook_budget=8

  # Local-box backoff (R4c): skip a box that failed within local_backoff_minutes.
  local local_url="" backoff_min=0
  case "$backend" in
    ollama) local_url=$(jq -r '.ollama.url // "http://localhost:11434"' "$config" 2>/dev/null) || local_url="" ;;
    openai) local_url=$(jq -r '.openai.base_url // empty' "$config" 2>/dev/null) || local_url="" ;;
  esac
  local_url="${local_url%/}"
  if [ -n "$local_url" ]; then
    backoff_min=$(_resolve_local_backoff_minutes "$(cat "$config" 2>/dev/null)" "$backend")
    if _local_backoff_active "$local_url" "$backoff_min"; then
      backend="backed-off"   # the case below makes no local call; the fast model answers
    fi
  fi

  local result=""
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

      # Try primary URL. Connect/wall budgets are the bounded hook transport
      # budgets resolved above — fixed and fallback-independent, never the
      # (long) inference timeout, so a busy/dead box can't hang the hook.
      result=""
      result=$(curl -s --connect-timeout "$connect_to" --max-time "$hook_budget" "$url/api/generate" -d "$payload" 2>/dev/null | jq -r '.response // empty' 2>/dev/null) || true

      # If primary failed and fallback exists, try fallback
      if [ -z "$result" ] && [ -n "$fallback_url" ]; then
        export VALIDATION_LLM_BACKEND="ollama:${model}(fallback)"
        result=$(curl -s --connect-timeout "$connect_to" --max-time "$hook_budget" "$fallback_url/api/generate" -d "$payload" 2>/dev/null | jq -r '.response // empty' 2>/dev/null) || true
      fi

      # Strip think tags — gemma4/other thinking models may prefix JSON with <think>...</think>
      if [ -n "$result" ]; then
        result=$(printf '%s' "$result" | python3 -c "import sys, re; print(re.sub(r'<think>.*?</think>', '', sys.stdin.read(), flags=re.DOTALL).strip())" 2>/dev/null) || true
      fi

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
        local schema_maxtok
        # json_schema grammar-bound request: floor the budget at 8192 so the
        # bounded JSON can never be truncated into non-JSON (config may raise it).
        schema_maxtok=$(jq -r '[(.openai.max_tokens // 0), 8192] | max' "$config" 2>/dev/null) || schema_maxtok=8192
        payload=$(jq -nc --arg m "$model_o" --arg p "$prompt" --argjson mt "$schema_maxtok" --argjson sc "$schema" \
          '{model:$m, messages:[{role:"user",content:$p}], max_tokens:$mt, temperature:0.1, response_format:{type:"json_schema",json_schema:{name:"verdict",schema:$sc}}}')
      else
        payload=$(jq -nc --arg m "$model_o" --arg p "$prompt" --argjson mt "$maxtok" \
          '{model:$m, messages:[{role:"user",content:$p}], max_tokens:$mt, temperature:0.1}')
      fi

      result=$(curl -s --connect-timeout "$connect_to" --max-time "$hook_budget" \
        -H "Authorization: Bearer ollama" -H "Content-Type: application/json" \
        "$base_url/chat/completions" -d "$payload" 2>/dev/null | jq -r '.choices[0].message.content // empty' 2>/dev/null) || true

      # Strip think tags — same reasoning-model prefix handling as the ollama arm
      if [ -n "$result" ]; then
        result=$(printf '%s' "$result" | python3 -c "import sys, re; print(re.sub(r'<think>.*?</think>', '', sys.stdin.read(), flags=re.DOTALL).strip())" 2>/dev/null) || true
      fi

      ;;

    *)
      # "haiku" (and any unknown backend): there is no local box; go straight to the fast model.
      result=""
      ;;
  esac

  if [ -n "$local_url" ] && [ "$backend" != "backed-off" ]; then
    if [ -n "$result" ] && printf '%s' "$result" | jq -e . >/dev/null 2>&1; then
      _local_backoff_record "$local_url" "$backoff_min" ok
    else
      _local_backoff_record "$local_url" "$backoff_min" fail
    fi
  fi

  # R1 chain. A usable local answer is valid JSON (jq -e .). Empty, failed or non-JSON
  # local output falls back to the Haiku-class model. jq -e also rejects a bare
  # null/false, which no caller schema allows, so those fall back too.
  if [ -n "$result" ] && printf '%s' "$result" | jq -e . >/dev/null 2>&1; then
    export VALIDATION_LLM_RESPONSE="$result"
    echo "$result"
    return 0
  fi

  local fast_cap luna="" fast_result
  fast_cap=$(_resolve_fast_model_timeout "$(cat "$config" 2>/dev/null)")
  if ic_is_codex; then
    if ! luna=$(_newest_luna); then
      export VALIDATION_LLM_BACKEND="luna:unavailable"
      return 1
    fi
    export VALIDATION_LLM_BACKEND="luna:${luna}"
  else
    export VALIDATION_LLM_BACKEND="haiku"
  fi
  fast_result=$(_call_fast_model "$prompt" "$schema" "$fast_cap" "$luna" haiku) || return 1
  export VALIDATION_LLM_RESPONSE="$fast_result"
  echo "$fast_result"
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
