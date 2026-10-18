#!/bin/bash
# test-fast-model-fallback.sh — the Haiku-class fast-model step and chain in
# plan-validator.sh call_validation_llm(): local box -> Claude Haiku (or the newest
# installed Codex Luna) -> fail open. Stub claude/codex/curl on PATH record argv.
# Do NOT set -e.
PASS=0; FAIL=0
export IRONCLAUDE_LOCAL_BACKOFF_DIR=/dev/null  # local-box backoff off (not a directory): each call tests its own chain; nothing touches the real ~/.ironclaude
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1090
source "$SCRIPT_DIR/../hook-logger.sh"
# shellcheck disable=SC1090
source "$SCRIPT_DIR/../plan-validator.sh"

assert_eq() {
  local desc="$1" expected="$2" actual="$3"
  if [ "$actual" = "$expected" ]; then echo "PASS: $desc"; ((PASS++));
  else echo "FAIL: $desc"; echo "  expected: $expected"; echo "  actual:   $actual"; ((FAIL++)); fi
}

WORK=$(mktemp -d)
STUBBIN="$WORK/bin"; mkdir -p "$STUBBIN"
export STUB_LOG_DIR="$WORK/log"; mkdir -p "$STUB_LOG_DIR"

cat > "$STUBBIN/claude" <<'EOF'
#!/bin/bash
: > "$STUB_LOG_DIR/claude.argv"
for a in "$@"; do printf '%s\n' "$a" >> "$STUB_LOG_DIR/claude.argv"; done
printf '%s\n' "$2" >> "$STUB_LOG_DIR/claude.models"
pwd -P > "$STUB_LOG_DIR/claude.cwd"
for v in ANTHROPIC_API_KEY ANTHROPIC_AUTH_TOKEN ANTHROPIC_BASE_URL CLAUDE_CODE_USE_BEDROCK CLAUDE_CODE_USE_VERTEX CLAUDE_CODE_EFFORT_LEVEL IC_KEEP_PROBE; do printf '%s=%s\n' "$v" "${!v-unset}"; done > "$STUB_LOG_DIR/claude.env"
cat > "$STUB_LOG_DIR/claude.stdin"
case "${STUB_CLAUDE_MODE:-array}" in
  array)  printf '%s' '[{"type":"system","subtype":"init"},{"type":"assistant"},{"type":"result","subtype":"success","structured_output":{"ok":true,"reason":"from-array"}}]' ;;
  object) printf '%s' '{"type":"result","subtype":"success","structured_output":{"ok":true,"reason":"from-object"}}' ;;
  noso)   printf '%s' '[{"type":"result","subtype":"error_max_turns","is_error":true}]' ;;
  fail)   exit 1 ;;
  hang)   echo "$$" > "$STUB_LOG_DIR/claude.pid"; exec /bin/sleep 30 ;;
esac
EOF

cat > "$STUBBIN/codex" <<'EOF'
#!/bin/bash
: > "$STUB_LOG_DIR/codex.argv"
for a in "$@"; do printf '%s\n' "$a" >> "$STUB_LOG_DIR/codex.argv"; done
prev=""; for a in "$@"; do [ "$prev" = "--output-schema" ] && cp "$a" "$STUB_LOG_DIR/codex.schema"; prev="$a"; done
cat > "$STUB_LOG_DIR/codex.stdin"
case "${STUB_CODEX_MODE:-ok}" in
  ok)
    printf '%s\n' '{"type":"thread.started","thread_id":"t-1"}'
    printf '%s\n' 'not a json line'
    printf '%s\n' '{"type":"item.completed","item":{"type":"agent_message","text":"{\"ok\":false,\"reason\":\"early\"}"}}'
    printf '%s\n' '{"type":"item.completed","item":{"type":"agent_message","text":"{\"ok\":true,\"reason\":\"last\"}"}}'
    printf '%s\n' '{"type":"turn.completed"}' ;;
  fail) exit 1 ;;
esac
EOF

cat > "$STUBBIN/curl" <<'EOF'
#!/bin/bash
echo called >> "$STUB_LOG_DIR/curl.calls"
case "${STUB_CURL_MODE:-fail}" in
  fail)    exit 7 ;;
  nonjson) printf '%s' '{"choices":[{"message":{"content":"not json"}}]}' ;;
  ok)      printf '%s' '{"choices":[{"message":{"content":"{\"ok\":true,\"reason\":\"local\"}"}}]}' ;;
esac
EOF
chmod +x "$STUBBIN/claude" "$STUBBIN/codex" "$STUBBIN/curl"
export PATH="$STUBBIN:$PATH"

SCHEMA='{"type":"object","properties":{"ok":{"type":"boolean"},"reason":{"type":"string"}},"required":["ok","reason"]}'
reset_logs() { rm -f "$STUB_LOG_DIR"/*; }
cfg() { printf '%s' "$1" > "$WORK/cfg.json"; export IC_OLLAMA_CONFIG_PATH="$WORK/cfg.json"; }
# Direct call (not $(...)) so VALIDATION_LLM_BACKEND is visible here.
run_cvl() {
  reset_logs
  call_validation_llm "grade this" "$SCHEMA" > "$WORK/out"; RC=$?
  OUT=$(jq -c . "$WORK/out" 2>/dev/null); RAW=$(cat "$WORK/out"); BACKEND="$VALIDATION_LLM_BACKEND"
}

# Like run_cvl, with stderr kept in $WORK/err and an optional class ($1, default haiku).
run_cvl_err() {
  reset_logs
  call_validation_llm "grade this" "$SCHEMA" "${1:-haiku}" > "$WORK/out" 2> "$WORK/err"; RC=$?
  BACKEND="$VALIDATION_LLM_BACKEND"
}
called() { [ -e "$STUB_LOG_DIR/$1" ] && echo yes || echo no; }
export IC_HOOK_CLIENT=claude
unset STUB_CLAUDE_MODE STUB_CODEX_MODE STUB_CURL_MODE

echo "=== Claude event-array output is parsed to the plain object ==="
cfg '{"backend":"haiku"}'; STUB_CLAUDE_MODE=array; export STUB_CLAUDE_MODE
run_cvl
assert_eq "array -> structured_output object" '{"ok":true,"reason":"from-array"}' "$OUT"
assert_eq "array -> rc 0" "0" "$RC"
assert_eq "array -> backend label haiku" "haiku" "$BACKEND"
assert_eq "backend haiku never calls curl" "no" "$(called curl.calls)"
assert_eq "prompt delivered on stdin" "grade this" "$(cat "$STUB_LOG_DIR/claude.stdin")"
assert_eq "claude child cwd is /tmp" "/private/tmp" "$(cat "$STUB_LOG_DIR/claude.cwd")"
assert_eq "claude isolation flags (exact argv)" \
  "--model|haiku|--print|--restricted|--effort|low|--tools||--no-session-persistence|--strict-mcp-config|--settings|{\"disableAllHooks\":true}|--output-format|json|--json-schema|$SCHEMA|" \
  "$(tr '\n' '|' < "$STUB_LOG_DIR/claude.argv")"

echo "=== Claude child runs without provider-routing variables or the worker's effort ==="
export ANTHROPIC_API_KEY=k ANTHROPIC_AUTH_TOKEN=t ANTHROPIC_BASE_URL=http://127.0.0.1:9 CLAUDE_CODE_USE_BEDROCK=1 CLAUDE_CODE_USE_VERTEX=1 CLAUDE_CODE_EFFORT_LEVEL=high IC_KEEP_PROBE=kept
run_cvl
assert_eq "routing vars stripped from the claude child" \
  "ANTHROPIC_API_KEY=unset|ANTHROPIC_AUTH_TOKEN=unset|ANTHROPIC_BASE_URL=unset|CLAUDE_CODE_USE_BEDROCK=unset|CLAUDE_CODE_USE_VERTEX=unset|CLAUDE_CODE_EFFORT_LEVEL=unset|IC_KEEP_PROBE=kept|" \
  "$(tr '\n' '|' < "$STUB_LOG_DIR/claude.env")"
unset ANTHROPIC_API_KEY ANTHROPIC_AUTH_TOKEN ANTHROPIC_BASE_URL CLAUDE_CODE_USE_BEDROCK CLAUDE_CODE_USE_VERTEX CLAUDE_CODE_EFFORT_LEVEL IC_KEEP_PROBE

echo "=== Claude single result object is parsed ==="
STUB_CLAUDE_MODE=object; run_cvl
assert_eq "object -> structured_output object" '{"ok":true,"reason":"from-object"}' "$OUT"

echo "=== Claude result without structured_output fails open ==="
STUB_CLAUDE_MODE=noso; run_cvl
assert_eq "no structured_output -> rc 1" "1" "$RC"
assert_eq "no structured_output -> empty stdout" "" "$RAW"

echo "=== openai failure falls back to Haiku ==="
cfg '{"backend":"openai","openai":{"base_url":"http://h/v1","model":"m"}}'
STUB_CURL_MODE=fail; STUB_CLAUDE_MODE=array; export STUB_CURL_MODE
run_cvl
assert_eq "openai failed -> curl was tried" "yes" "$(called curl.calls)"
assert_eq "openai failed -> haiku answer" '{"ok":true,"reason":"from-array"}' "$OUT"
assert_eq "openai failed -> backend label haiku" "haiku" "$BACKEND"

echo "=== openai non-JSON answer falls back to Haiku ==="
STUB_CURL_MODE=nonjson; run_cvl
assert_eq "openai non-JSON -> haiku answer" '{"ok":true,"reason":"from-array"}' "$OUT"

echo "=== openai JSON answer is used; no fast-model call ==="
STUB_CURL_MODE=ok; run_cvl
assert_eq "openai ok -> local answer" '{"ok":true,"reason":"local"}' "$OUT"
assert_eq "openai ok -> claude not called" "no" "$(called claude.argv)"
assert_eq "openai ok -> backend label openai:m" "openai:m" "$BACKEND"

echo "=== both steps failing returns failure (fail open) ==="
STUB_CURL_MODE=fail; STUB_CLAUDE_MODE=fail; run_cvl
assert_eq "both fail -> rc 1" "1" "$RC"
assert_eq "both fail -> empty stdout" "" "$RAW"

echo "=== Codex client uses codex exec with the newest installed Luna ==="
export IC_HOOK_CLIENT=codex CODEX_HOME="$WORK/codexhome"; mkdir -p "$CODEX_HOME"
printf '%s' '{"models":[{"slug":"gpt-5.9-luna"},{"slug":"gpt-5.10-luna"},{"slug":"gpt-6-sol"},{"slug":null},{}]}' > "$CODEX_HOME/models_cache.json"
cfg '{"backend":"haiku"}'; STUB_CODEX_MODE=ok; export STUB_CODEX_MODE
run_cvl
assert_eq "codex -> last agent_message object" '{"ok":true,"reason":"last"}' "$OUT"
assert_eq "codex -> backend label luna:<numeric-newest>" "luna:gpt-5.10-luna" "$BACKEND"
assert_eq "codex -> claude not called" "no" "$(called claude.argv)"
assert_eq "codex isolation flags (argv head)" \
  'exec|--json|--ephemeral|--ignore-user-config|--ignore-rules|--skip-git-repo-check|-s|read-only|-c|model_reasoning_effort="low"|-c|features.shell_tool=false|-c|features.unified_exec=false|-c|features.view_image=false|-c|features.apps=false|-c|features.browser_use=false|-c|features.computer_use=false|-c|features.multi_agent=false|-c|features.image_generation=false|-c|features.memories=false|-c|features.plugins=false|-c|features.hooks=false|-c|features.goals=false|-c|features.tool_suggest=false|-c|agents.enabled=false|-c|web_search="disabled"|-m|gpt-5.10-luna|--output-schema|' \
  "$(head -n 43 "$STUB_LOG_DIR/codex.argv" | tr '\n' '|')"
assert_eq "codex argv ends with - (stdin prompt)" "-" "$(tail -n 1 "$STUB_LOG_DIR/codex.argv")"
assert_eq "codex schema file carries the caller schema" "$(printf '%s' "$SCHEMA" | jq -c '. + {additionalProperties:false}')" "$(jq -c . "$STUB_LOG_DIR/codex.schema")"
assert_eq "codex prompt delivered on stdin" "grade this" "$(cat "$STUB_LOG_DIR/codex.stdin")"
NESTED='{"type":"object","properties":{"ok":{"type":"boolean"},"note":{"type":"string"},"opts":{"type":"array","items":{"type":"object","properties":{"v":{"type":"string"},"w":{"enum":["a","b"]}},"required":["v"]}}},"required":["ok","opts"]}'
reset_logs; call_validation_llm "grade this" "$NESTED" > "$WORK/out"
assert_eq "codex schema is strict at every level" \
  "$(printf '%s' '{"type":"object","properties":{"ok":{"type":"boolean"},"note":{"type":["string","null"]},"opts":{"type":"array","items":{"type":"object","properties":{"v":{"type":"string"},"w":{"enum":["a","b",null]}},"required":["v","w"],"additionalProperties":false}}},"required":["ok","note","opts"],"additionalProperties":false}' | jq -S -c .)" \
  "$(jq -S -c . "$STUB_LOG_DIR/codex.schema")"

echo "=== Codex with no installed Luna is unavailable for the call (no pinned fallback) ==="
rm -f "$CODEX_HOME/models_cache.json"; run_cvl_err
assert_eq "no cache -> rc 1 (Codex unavailable, fail open)" "1" "$RC"
assert_eq "no cache -> backend label luna:unavailable" "luna:unavailable" "$BACKEND"
assert_eq "no cache -> codex not called" "no" "$(called codex.argv)"
assert_eq "no cache -> stderr names the family and the cache path" "1" \
  "$(grep -cF "[plan-validator] Codex model family luna has no installed model in $CODEX_HOME/models_cache.json; Codex is unavailable for this call" "$WORK/err")"
printf '%s' '{"models":[{"slug":"gpt-6-sol"}]}' > "$CODEX_HOME/models_cache.json"; run_cvl_err
assert_eq "cache without a Luna -> backend label luna:unavailable" "luna:unavailable" "$BACKEND"
printf '%s' '{"models":[{"slug":"gpt-5.9-luna"},{"slug":"gpt-5.10-luna"}]}' > "$CODEX_HOME/models_cache.json"

echo "=== Codex failure fails open ==="
STUB_CODEX_MODE=fail; run_cvl
assert_eq "codex fail -> rc 1" "1" "$RC"
export IC_HOOK_CLIENT=claude; unset CODEX_HOME STUB_CODEX_MODE

echo "=== fast_model_timeout_seconds resolution ==="
assert_eq "unset -> measured default" "30" "$(_resolve_fast_model_timeout '{}')"
assert_eq "empty config -> measured default" "30" "$(_resolve_fast_model_timeout '')"
assert_eq "configured 7 -> 7" "7" "$(_resolve_fast_model_timeout '{"fast_model_timeout_seconds":7}')"
assert_eq "non-number -> measured default" "30" "$(_resolve_fast_model_timeout '{"fast_model_timeout_seconds":"x"}')"

echo "=== timeout cap is enforced and the child is killed (timeout binary branch) ==="
cfg '{"backend":"haiku","fast_model_timeout_seconds":2}'; STUB_CLAUDE_MODE=hang
start=$SECONDS; run_cvl; elapsed=$((SECONDS - start)); sleep 1
assert_eq "cap -> rc 1" "1" "$RC"
assert_eq "cap -> returned within 6s" "yes" "$([ "$elapsed" -le 6 ] && echo yes || echo no)"
assert_eq "cap -> child process killed" "dead" "$(kill -0 "$(cat "$STUB_LOG_DIR/claude.pid")" 2>/dev/null && echo alive || echo dead)"

echo "=== timeout cap is enforced and the child is killed (pure-bash fallback branch) ==="
FALLBACK_PATH="$STUBBIN:/usr/bin:/bin"
assert_eq "precondition: no timeout/gtimeout on reduced PATH" "absent" \
  "$(PATH="$FALLBACK_PATH" bash -c 'command -v timeout || command -v gtimeout' >/dev/null && echo present || echo absent)"
OLD_PATH="$PATH"; PATH="$FALLBACK_PATH"
start=$SECONDS; run_cvl; elapsed=$((SECONDS - start)); sleep 1
PATH="$OLD_PATH"
assert_eq "fallback cap -> rc 1" "1" "$RC"
assert_eq "fallback cap -> returned within 6s" "yes" "$([ "$elapsed" -le 6 ] && echo yes || echo no)"
assert_eq "fallback cap -> child process killed" "dead" "$(kill -0 "$(cat "$STUB_LOG_DIR/claude.pid")" 2>/dev/null && echo alive || echo dead)"

# ── Per-call class: the third argument. sonnet never uses the local box. ──
run_cvl_tier() {
  reset_logs
  call_validation_llm "grade this" "$SCHEMA" "$1" > "$WORK/out"; RC=$?
  OUT=$(jq -c . "$WORK/out" 2>/dev/null); RAW=$(cat "$WORK/out"); BACKEND="$VALIDATION_LLM_BACKEND"
}
export IC_HOOK_CLIENT=claude STUB_CURL_MODE=ok STUB_CLAUDE_MODE=array

echo "=== sonnet class skips an openai box and asks Claude Sonnet ==="
cfg '{"backend":"openai","openai":{"base_url":"http://h/v1","model":"m"}}'
run_cvl_tier sonnet
assert_eq "sonnet -> claude answer" '{"ok":true,"reason":"from-array"}' "$OUT"
assert_eq "sonnet -> openai box never called" "no" "$(called curl.calls)"
assert_eq "sonnet -> backend label sonnet" "sonnet" "$BACKEND"
assert_eq "sonnet isolation flags (exact argv)" \
  "--model|sonnet|--print|--restricted|--effort|low|--tools||--no-session-persistence|--strict-mcp-config|--settings|{\"disableAllHooks\":true}|--output-format|json|--json-schema|$SCHEMA|" \
  "$(tr '\n' '|' < "$STUB_LOG_DIR/claude.argv")"

echo "=== sonnet class skips an ollama box too ==="
cfg '{"backend":"ollama","ollama":{"url":"http://x","model":"m"}}'
run_cvl_tier sonnet
assert_eq "sonnet -> ollama box never called" "no" "$(called curl.calls)"
assert_eq "sonnet (ollama config) -> claude answer" '{"ok":true,"reason":"from-array"}' "$OUT"

echo "=== explicit haiku class equals the default (positive control) ==="
cfg '{"backend":"openai","openai":{"base_url":"http://h/v1","model":"m"}}'
run_cvl_tier haiku
assert_eq "haiku -> local answer" '{"ok":true,"reason":"local"}' "$OUT"
assert_eq "haiku -> claude not called" "no" "$(called claude.argv)"
assert_eq "haiku -> backend label openai:m" "openai:m" "$BACKEND"

echo "=== sonnet failure fails open with no Haiku retry and no local box ==="
STUB_CLAUDE_MODE=fail; run_cvl_tier sonnet
assert_eq "sonnet fail -> rc 1" "1" "$RC"
assert_eq "sonnet fail -> empty stdout" "" "$RAW"
assert_eq "sonnet fail -> one claude call, at sonnet" "sonnet" "$(cat "$STUB_LOG_DIR/claude.models")"
assert_eq "sonnet fail -> local box never called" "no" "$(called curl.calls)"
STUB_CLAUDE_MODE=array

echo "=== Codex sonnet class uses the newest installed Terra ==="
export IC_HOOK_CLIENT=codex CODEX_HOME="$WORK/codexhome2" STUB_CODEX_MODE=ok; mkdir -p "$CODEX_HOME"
printf '%s' '{"models":[{"slug":"gpt-5.9-terra"},{"slug":"gpt-5.10-terra"},{"slug":"gpt-6-luna"},{"slug":"gpt-6-sol"},{"slug":null},{}]}' > "$CODEX_HOME/models_cache.json"
run_cvl_tier sonnet
assert_eq "codex sonnet -> last agent_message object" '{"ok":true,"reason":"last"}' "$OUT"
assert_eq "codex sonnet -> backend label terra:<numeric-newest>" "terra:gpt-5.10-terra" "$BACKEND"
assert_eq "codex sonnet -> local box never called" "no" "$(called curl.calls)"
assert_eq "codex sonnet isolation flags (argv head)" \
  'exec|--json|--ephemeral|--ignore-user-config|--ignore-rules|--skip-git-repo-check|-s|read-only|-c|model_reasoning_effort="low"|-c|features.shell_tool=false|-c|features.unified_exec=false|-c|features.view_image=false|-c|features.apps=false|-c|features.browser_use=false|-c|features.computer_use=false|-c|features.multi_agent=false|-c|features.image_generation=false|-c|features.memories=false|-c|features.plugins=false|-c|features.hooks=false|-c|features.goals=false|-c|features.tool_suggest=false|-c|agents.enabled=false|-c|web_search="disabled"|-m|gpt-5.10-terra|--output-schema|' \
  "$(head -n 43 "$STUB_LOG_DIR/codex.argv" | tr '\n' '|')"
assert_eq "codex sonnet schema is strict" "$(printf '%s' "$SCHEMA" | jq -c '. + {additionalProperties:false}')" "$(jq -c . "$STUB_LOG_DIR/codex.schema")"
cfg '{"backend":"haiku"}'; run_cvl_tier haiku
assert_eq "codex haiku class still uses the newest Luna (positive control)" "luna:gpt-6-luna" "$BACKEND"
rm -f "$CODEX_HOME/models_cache.json"; run_cvl_err sonnet
assert_eq "codex sonnet, no cache -> rc 1" "1" "$RC"
assert_eq "codex sonnet, no cache -> backend label terra:unavailable" "terra:unavailable" "$BACKEND"
assert_eq "codex sonnet, no cache -> codex not called" "no" "$(called codex.argv)"
assert_eq "codex sonnet, no cache -> stderr names the family and the cache path" "1" \
  "$(grep -cF "[plan-validator] Codex model family terra has no installed model in $CODEX_HOME/models_cache.json; Codex is unavailable for this call" "$WORK/err")"
export IC_HOOK_CLIENT=claude; unset CODEX_HOME STUB_CODEX_MODE

echo "=== sonnet_model_timeout_seconds resolution ==="
assert_eq "sonnet cap unset -> measured default" "30" "$(_resolve_sonnet_model_timeout '{}')"
assert_eq "sonnet cap empty config -> measured default" "30" "$(_resolve_sonnet_model_timeout '')"
assert_eq "sonnet cap configured 7 -> 7" "7" "$(_resolve_sonnet_model_timeout '{"sonnet_model_timeout_seconds":7}')"
assert_eq "sonnet cap non-number -> measured default" "30" "$(_resolve_sonnet_model_timeout '{"sonnet_model_timeout_seconds":"x"}')"
assert_eq "sonnet cap ignores fast_model_timeout_seconds" "30" "$(_resolve_sonnet_model_timeout '{"fast_model_timeout_seconds":7}')"

echo "=== sonnet cap is enforced and the child is killed ==="
cfg '{"backend":"haiku","sonnet_model_timeout_seconds":2}'; STUB_CLAUDE_MODE=hang
start=$SECONDS; run_cvl_tier sonnet; elapsed=$((SECONDS - start)); sleep 1
assert_eq "sonnet cap -> rc 1" "1" "$RC"
assert_eq "sonnet cap -> returned within 6s" "yes" "$([ "$elapsed" -le 6 ] && echo yes || echo no)"
assert_eq "sonnet cap -> child process killed" "dead" "$(kill -0 "$(cat "$STUB_LOG_DIR/claude.pid")" 2>/dev/null && echo alive || echo dead)"
unset STUB_CURL_MODE

rm -rf "$WORK"
unset IC_OLLAMA_CONFIG_PATH IC_HOOK_CLIENT STUB_CLAUDE_MODE STUB_CURL_MODE
echo ""
echo "Results: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ] && exit 0 || exit 1
