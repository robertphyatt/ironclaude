#!/bin/bash
# test-openai-backend.sh — tests the REAL plan-validator.sh call_validation_llm() openai
# arm and the _resolve_spot() pure helper against the shared resolution-cases.json
# fixture. Mirrors test-config-guard.sh assert_eq style. Do NOT set -e.
PASS=0; FAIL=0
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
# shellcheck disable=SC1090
source "$SCRIPT_DIR/../plan-validator.sh" 2>/dev/null || true

assert_eq() {
  local desc="$1" expected="$2" actual="$3"
  if [ "$actual" = "$expected" ]; then echo "PASS: $desc"; ((PASS++));
  else echo "FAIL: $desc"; echo "  expected: $expected"; echo "  actual:   $actual"; ((FAIL++)); fi
}

assert_contains() {
  local desc="$1" needle="$2" haystack="$3"
  if [[ "$haystack" == *"$needle"* ]]; then echo "PASS: $desc"; ((PASS++));
  else echo "FAIL: $desc"; echo "  expected to contain: $needle"; echo "  actual:   $haystack"; ((FAIL++)); fi
}

# =============================================================================
# Stub curl on PATH so no real network call happens. Records the request body
# (the -d/--data payload) to $CURL_CAPTURE_FILE, when set, so a test can jq
# the model out of it. Response shape follows the URL: ollama's /api/generate
# wraps content in .response, openai-compatible endpoints in .choices[0].message.content.
# =============================================================================
STUBBIN=$(mktemp -d)
cat > "$STUBBIN/curl" <<'EOF'
#!/bin/bash
body=""
url=""
prev=""
for arg in "$@"; do
  if [ "$prev" = "-d" ] || [ "$prev" = "--data" ]; then
    body="$arg"
  fi
  if [ "$prev" = "--max-time" ] && [ -n "$CURL_MAXTIME_FILE" ]; then
    printf '%s' "$arg" > "$CURL_MAXTIME_FILE"
  fi
  if [ "$prev" = "--connect-timeout" ] && [ -n "$CURL_CONNECT_FILE" ]; then
    printf '%s' "$arg" > "$CURL_CONNECT_FILE"
  fi
  case "$arg" in
    http*) url="$arg" ;;
  esac
  prev="$arg"
done
if [ -n "$CURL_CAPTURE_FILE" ]; then
  printf '%s' "$body" > "$CURL_CAPTURE_FILE"
fi
case "$url" in
  */api/generate) echo '{"response":"{\"ok\":true}"}' ;;
  *) echo '{"choices":[{"message":{"content":"{\"ok\":true}"}}]}' ;;
esac
EOF
chmod +x "$STUBBIN/curl"
export PATH="$STUBBIN:$PATH"

# =============================================================================
# call_validation_llm(): openai backend end-to-end (via stubbed curl)
# =============================================================================
TMPCFG=$(mktemp)
printf '%s' '{"backend":"openai","openai":{"base_url":"http://llm-host/v1","model":"example-model-a","max_tokens":1024}}' > "$TMPCFG"
export IC_OLLAMA_CONFIG_PATH="$TMPCFG"

echo "=== call_validation_llm: openai backend ==="
RESULT=$(call_validation_llm "test prompt" "")
assert_contains "openai backend returns parsed .choices[0].message.content" "ok" "$RESULT"

rm -f "$TMPCFG"

# =============================================================================
# call_validation_llm(): openai.timeout_seconds does NOT drive --max-time —
# the hook transport budget is bounded independently of the inference timeout,
# so a busy/dead box can't hang the hook for 300s.
# =============================================================================
TMPCFG5=$(mktemp)
printf '%s' '{"backend":"openai","openai":{"base_url":"http://h/v1","model":"m","timeout_seconds":300}}' > "$TMPCFG5"
export IC_OLLAMA_CONFIG_PATH="$TMPCFG5"
CAPTURE_MT_A=$(mktemp)
export CURL_MAXTIME_FILE="$CAPTURE_MT_A"

echo "=== call_validation_llm: openai.timeout_seconds=300 does NOT drive --max-time (hook budget default 8) ==="
call_validation_llm "test prompt" "" >/dev/null
MAXTIME_A=$(cat "$CAPTURE_MT_A" 2>/dev/null)
assert_eq "openai arm --max-time ignores openai.timeout_seconds, defaults to hook budget 8" "8" "$MAXTIME_A"

rm -f "$TMPCFG5" "$CAPTURE_MT_A"
unset CURL_MAXTIME_FILE

# =============================================================================
# call_validation_llm(): openai backend falls back to default hook budget 8s
# when no hook_validation_budget_seconds is set anywhere
# =============================================================================
TMPCFG6=$(mktemp)
printf '%s' '{"backend":"openai","openai":{"base_url":"http://h/v1","model":"m"}}' > "$TMPCFG6"
export IC_OLLAMA_CONFIG_PATH="$TMPCFG6"
CAPTURE_MT_B=$(mktemp)
export CURL_MAXTIME_FILE="$CAPTURE_MT_B"

echo "=== call_validation_llm: openai backend defaults --max-time to 8 without override ==="
call_validation_llm "test prompt" "" >/dev/null
MAXTIME_B=$(cat "$CAPTURE_MT_B" 2>/dev/null)
assert_eq "openai arm defaults --max-time to 8" "8" "$MAXTIME_B"

rm -f "$TMPCFG6" "$CAPTURE_MT_B"
unset CURL_MAXTIME_FILE

# =============================================================================
# call_validation_llm(): ollama backend honors spots.validation.model override
# =============================================================================
TMPCFG3=$(mktemp)
printf '%s' '{"backend":"ollama","ollama":{"url":"http://x","model":"m"},"spots":{"validation":{"model":"spot-x"}}}' > "$TMPCFG3"
export IC_OLLAMA_CONFIG_PATH="$TMPCFG3"
CAPTURE_A=$(mktemp)
export CURL_CAPTURE_FILE="$CAPTURE_A"

echo "=== call_validation_llm: ollama backend honors spots.validation.model override ==="
call_validation_llm "test prompt" "" >/dev/null
MODEL_A=$(jq -r '.model' "$CAPTURE_A" 2>/dev/null)
assert_eq "ollama arm sends spots.validation.model override" "spot-x" "$MODEL_A"

rm -f "$TMPCFG3" "$CAPTURE_A"
unset CURL_CAPTURE_FILE

# =============================================================================
# call_validation_llm(): ollama backend retains llama3.2:1b default (no override, no model)
# =============================================================================
TMPCFG4=$(mktemp)
printf '%s' '{"backend":"ollama","ollama":{"url":"http://x"}}' > "$TMPCFG4"
export IC_OLLAMA_CONFIG_PATH="$TMPCFG4"
CAPTURE_B=$(mktemp)
export CURL_CAPTURE_FILE="$CAPTURE_B"

echo "=== call_validation_llm: ollama backend retains llama3.2:1b default ==="
call_validation_llm "test prompt" "" >/dev/null
MODEL_B=$(jq -r '.model' "$CAPTURE_B" 2>/dev/null)
assert_eq "ollama arm default model unchanged without override" "llama3.2:1b" "$MODEL_B"

rm -f "$TMPCFG4" "$CAPTURE_B"
unset CURL_CAPTURE_FILE

# =============================================================================
# call_validation_llm(): ollama.timeout_seconds does NOT drive --max-time —
# hook budget default 8 applies regardless of inference timeout
# =============================================================================
TMPCFG_OT=$(mktemp)
printf '%s' '{"backend":"ollama","ollama":{"url":"http://x","model":"m","timeout_seconds":300}}' > "$TMPCFG_OT"
export IC_OLLAMA_CONFIG_PATH="$TMPCFG_OT"
CAPTURE_OT=$(mktemp)
export CURL_MAXTIME_FILE="$CAPTURE_OT"

echo "=== call_validation_llm: ollama.timeout_seconds=300 does NOT drive --max-time (hook budget default 8) ==="
call_validation_llm "test prompt" "" >/dev/null
MAXTIME_OT=$(cat "$CAPTURE_OT" 2>/dev/null)
assert_eq "ollama arm --max-time ignores ollama.timeout_seconds, defaults to hook budget 8" "8" "$MAXTIME_OT"

rm -f "$TMPCFG_OT" "$CAPTURE_OT"
unset CURL_MAXTIME_FILE

# =============================================================================
# call_validation_llm(): top-level hook_validation_budget_seconds drives --max-time
# =============================================================================
TMPCFG_HB=$(mktemp)
printf '%s' '{"backend":"openai","openai":{"base_url":"http://h/v1","model":"m"},"hook_validation_budget_seconds":12}' > "$TMPCFG_HB"
export IC_OLLAMA_CONFIG_PATH="$TMPCFG_HB"
CAPTURE_HB=$(mktemp)
export CURL_MAXTIME_FILE="$CAPTURE_HB"

echo "=== call_validation_llm: top-level hook_validation_budget_seconds=12 drives --max-time ==="
call_validation_llm "test prompt" "" >/dev/null
MAXTIME_HB=$(cat "$CAPTURE_HB" 2>/dev/null)
assert_eq "top-level hook_validation_budget_seconds=12 -> --max-time 12" "12" "$MAXTIME_HB"

rm -f "$TMPCFG_HB" "$CAPTURE_HB"
unset CURL_MAXTIME_FILE

# =============================================================================
# call_validation_llm(): block-level openai.hook_validation_budget_seconds
# wins over top-level hook_validation_budget_seconds
# =============================================================================
TMPCFG_HB2=$(mktemp)
printf '%s' '{"backend":"openai","openai":{"base_url":"http://h/v1","model":"m","hook_validation_budget_seconds":11},"hook_validation_budget_seconds":12}' > "$TMPCFG_HB2"
export IC_OLLAMA_CONFIG_PATH="$TMPCFG_HB2"
CAPTURE_HB2=$(mktemp)
export CURL_MAXTIME_FILE="$CAPTURE_HB2"

echo "=== call_validation_llm: block-level openai.hook_validation_budget_seconds wins over top-level ==="
call_validation_llm "test prompt" "" >/dev/null
MAXTIME_HB2=$(cat "$CAPTURE_HB2" 2>/dev/null)
assert_eq "block-level hook_validation_budget_seconds=11 wins over top-level 12" "11" "$MAXTIME_HB2"

rm -f "$TMPCFG_HB2" "$CAPTURE_HB2"
unset CURL_MAXTIME_FILE

# =============================================================================
# call_validation_llm(): no connect_timeout_seconds configured -> --connect-timeout
# defaults to 3
# =============================================================================
TMPCFG_CT_DEFAULT=$(mktemp)
printf '%s' '{"backend":"openai","openai":{"base_url":"http://h/v1","model":"m"}}' > "$TMPCFG_CT_DEFAULT"
export IC_OLLAMA_CONFIG_PATH="$TMPCFG_CT_DEFAULT"
CAPTURE_CT_DEFAULT=$(mktemp)
export CURL_CONNECT_FILE="$CAPTURE_CT_DEFAULT"

echo "=== call_validation_llm: no connect_timeout_seconds configured -> --connect-timeout defaults to 3 ==="
call_validation_llm "test prompt" "" >/dev/null
CONNECT_DEFAULT=$(cat "$CAPTURE_CT_DEFAULT" 2>/dev/null)
assert_eq "no connect key -> --connect-timeout 3" "3" "$CONNECT_DEFAULT"

rm -f "$TMPCFG_CT_DEFAULT" "$CAPTURE_CT_DEFAULT"
unset CURL_CONNECT_FILE

# =============================================================================
# call_validation_llm(): openai.connect_timeout_seconds=4 -> --connect-timeout 4
# =============================================================================
TMPCFG_CT4=$(mktemp)
printf '%s' '{"backend":"openai","openai":{"base_url":"http://h/v1","model":"m","connect_timeout_seconds":4}}' > "$TMPCFG_CT4"
export IC_OLLAMA_CONFIG_PATH="$TMPCFG_CT4"
CAPTURE_CT4=$(mktemp)
export CURL_CONNECT_FILE="$CAPTURE_CT4"

echo "=== call_validation_llm: openai.connect_timeout_seconds=4 -> --connect-timeout 4 ==="
call_validation_llm "test prompt" "" >/dev/null
CONNECT_4=$(cat "$CAPTURE_CT4" 2>/dev/null)
assert_eq "openai.connect_timeout_seconds=4 -> --connect-timeout 4" "4" "$CONNECT_4"

rm -f "$TMPCFG_CT4" "$CAPTURE_CT4"
unset CURL_CONNECT_FILE

# =============================================================================
# call_validation_llm(): ollama arm with fallback_url set -> --connect-timeout
# stays fallback-independent (NOT hardcoded to 2)
# =============================================================================
TMPCFG_FB=$(mktemp)
printf '%s' '{"backend":"ollama","ollama":{"url":"http://x","model":"m","fallback_url":"http://y"}}' > "$TMPCFG_FB"
export IC_OLLAMA_CONFIG_PATH="$TMPCFG_FB"
CAPTURE_FB=$(mktemp)
export CURL_CONNECT_FILE="$CAPTURE_FB"

echo "=== call_validation_llm: ollama arm with fallback_url set -> --connect-timeout stays fallback-independent ==="
call_validation_llm "test prompt" "" >/dev/null
CONNECT_FB=$(cat "$CAPTURE_FB" 2>/dev/null)
assert_eq "ollama fallback_url set -> --connect-timeout still 3 (not hardcoded 2)" "3" "$CONNECT_FB"

rm -f "$TMPCFG_FB" "$CAPTURE_FB"
unset CURL_CONNECT_FILE

# =============================================================================
# call_validation_llm(): config-path resolution honors IC_OLLAMA_CONFIG_PATH
# =============================================================================
TMPCFG2=$(mktemp)
printf '%s' '{}' > "$TMPCFG2"
export IC_OLLAMA_CONFIG_PATH="$TMPCFG2"

echo "=== call_validation_llm: inline per-consumer default ==="
RESOLVED_DEFAULT=$(_resolve_spot "$(cat "$TMPCFG2")" "validation")
assert_eq "empty config -> resolved backend haiku (shell default)" "haiku" "$(printf '%s' "$RESOLVED_DEFAULT" | awk '{print $1}')"

rm -f "$TMPCFG2"
unset IC_OLLAMA_CONFIG_PATH

# =============================================================================
# _resolve_spot(): conformance against worker/config-schema/resolution-cases.json
# =============================================================================
echo "=== _resolve_spot: shared fixture conformance ==="
FIXTURE="$REPO_ROOT/worker/config-schema/resolution-cases.json"
if [ ! -f "$FIXTURE" ]; then
  echo "FAIL: fixture not found at $FIXTURE"; ((FAIL++))
else
  CASE_COUNT=$(jq 'length' "$FIXTURE")
  i=0
  while [ "$i" -lt "$CASE_COUNT" ]; do
    NAME=$(jq -r ".[$i].name" "$FIXTURE")
    CASE_CONFIG=$(jq -c ".[$i].config" "$FIXTURE")
    SPOT=$(jq -r ".[$i].spot" "$FIXTURE")
    EXP_BACKEND=$(jq -r ".[$i].expect.backend" "$FIXTURE")
    EXP_MODEL=$(jq -r ".[$i].expect.model" "$FIXTURE")
    EXP_URL=$(jq -r ".[$i].expect.url" "$FIXTURE")

    ACTUAL=$(_resolve_spot "$CASE_CONFIG" "$SPOT")
    ACT_BACKEND=$(printf '%s' "$ACTUAL" | awk '{print $1}')
    ACT_MODEL=$(printf '%s' "$ACTUAL" | awk '{print $2}')
    ACT_URL=$(printf '%s' "$ACTUAL" | awk '{print $3}')

    assert_eq "$NAME: backend" "$EXP_BACKEND" "$ACT_BACKEND"
    assert_eq "$NAME: model" "$EXP_MODEL" "$ACT_MODEL"
    assert_eq "$NAME: url" "$EXP_URL" "$ACT_URL"

    EXP_CONNECT=$(jq -r ".[$i].expect.connect_timeout // empty" "$FIXTURE")
    EXP_PROBE=$(jq -r ".[$i].expect.probe_timeout // empty" "$FIXTURE")
    EXP_BUDGET=$(jq -r ".[$i].expect.hook_validation_budget // empty" "$FIXTURE")

    if [ -n "$EXP_CONNECT" ] || [ -n "$EXP_PROBE" ] || [ -n "$EXP_BUDGET" ]; then
      ACT_BUDGETS=$(_resolve_budgets "$CASE_CONFIG" "$ACT_BACKEND")
      ACT_CONNECT=$(printf '%s' "$ACT_BUDGETS" | awk '{print $1}')
      ACT_PROBE=$(printf '%s' "$ACT_BUDGETS" | awk '{print $2}')
      ACT_BUDGET=$(printf '%s' "$ACT_BUDGETS" | awk '{print $3}')
      [ -n "$EXP_CONNECT" ] && assert_eq "$NAME: connect_timeout" "$EXP_CONNECT" "$ACT_CONNECT"
      [ -n "$EXP_PROBE" ] && assert_eq "$NAME: probe_timeout" "$EXP_PROBE" "$ACT_PROBE"
      [ -n "$EXP_BUDGET" ] && assert_eq "$NAME: hook_validation_budget" "$EXP_BUDGET" "$ACT_BUDGET"
    fi

    i=$((i + 1))
  done
fi

rm -rf "$STUBBIN"

echo ""
echo "Results: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ] && exit 0 || exit 1
