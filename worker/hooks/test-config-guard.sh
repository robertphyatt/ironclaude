#!/bin/bash
# test-config-guard.sh — tests the REAL config-guard.sh helpers (sourced — enforced logic
# == tested logic), including the single decision entry config_guard_decision (routing +
# polarity). Mirrors test-guard-security.sh assert_eq style. Do NOT set -e.
PASS=0; FAIL=0
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1090
source "$SCRIPT_DIR/config-guard.sh" 2>/dev/null || true

assert_eq() {
  local desc="$1" expected="$2" actual="$3"
  if [ "$actual" = "$expected" ]; then echo "PASS: $desc"; ((PASS++));
  else echo "FAIL: $desc"; echo "  expected: $expected"; echo "  actual:   $actual"; ((FAIL++)); fi
}

echo "=== config_write_verdict: benign ALLOWED ==="
assert_eq "benign url change, no guardrail keys" "allow" \
  "$(config_write_verdict '{"validation_backend":"ollama","ollama":{"url":"http://a"}}' '{"validation_backend":"ollama","ollama":{"url":"http://b"}}')"
assert_eq "benign url change preserving enforced policy" "allow" \
  "$(config_write_verdict '{"tier_up_review_policy":"enforced","ollama":{"url":"http://a"}}' '{"tier_up_review_policy":"enforced","ollama":{"url":"http://b"}}')"
assert_eq "validation_backend change" "allow" \
  "$(config_write_verdict '{"validation_backend":"ollama"}' '{"validation_backend":"haiku"}')"
assert_eq "timeout_seconds change" "allow" \
  "$(config_write_verdict '{"timeout_seconds":600}' '{"timeout_seconds":60}')"
assert_eq "corrupt current + benign-only proposed" "allow" \
  "$(config_write_verdict 'not json' '{"validation_backend":"ollama","ollama":{"url":"http://b"}}')"
assert_eq "nested guardrail-named key under benign (inert)" "allow" \
  "$(config_write_verdict '{}' '{"ollama":{"tier_up_review_policy":"off"}}')"

echo "=== config_write_verdict: guardrail/unknown BLOCKED ==="
assert_eq "change tier_up_review_policy value" "block" \
  "$(config_write_verdict '{"tier_up_review_policy":"enforced"}' '{"tier_up_review_policy":"off"}')"
assert_eq "add tier_up_review_policy where absent" "block" \
  "$(config_write_verdict '{"ollama":{"url":"http://a"}}' '{"ollama":{"url":"http://a"},"tier_up_review_policy":"off"}')"
assert_eq "remove tier_up_review_policy" "block" \
  "$(config_write_verdict '{"tier_up_review_policy":"enforced","ollama":{"url":"http://a"}}' '{"ollama":{"url":"http://a"}}')"
assert_eq "add debug_allow_config_writes" "block" \
  "$(config_write_verdict '{}' '{"debug_allow_config_writes":true}')"
assert_eq "change debug_allow_config_writes value" "block" \
  "$(config_write_verdict '{"debug_allow_config_writes":true}' '{"debug_allow_config_writes":false}')"
assert_eq "add unknown non-benign key" "block" \
  "$(config_write_verdict '{}' '{"foo":1}')"
assert_eq "malformed proposed JSON" "block" "$(config_write_verdict '{}' 'not json')"
assert_eq "non-object proposed (array)" "block" "$(config_write_verdict '{}' '[1,2,3]')"

echo "=== is_config_path: routing (CASE-INSENSITIVE for APFS) ==="
assert_eq "canonical config path" "yes" "$(is_config_path "$HOME/.claude/ironclaude-hooks-config.json")"
assert_eq "basename-ending path" "yes" "$(is_config_path "/anywhere/ironclaude-hooks-config.json")"
assert_eq "UPPERCASE config path (macOS bypass guard)" "yes" "$(is_config_path "$HOME/.claude/IRONCLAUDE-HOOKS-CONFIG.JSON")"
assert_eq "MixedCase config path" "yes" "$(is_config_path "/x/Ironclaude-Hooks-Config.json")"
assert_eq "unrelated path" "no" "$(is_config_path "/tmp/other.json")"

echo "=== config_bash_write_vector: BEST-EFFORT deny (case-insensitive) ==="
assert_eq "rm config" "block" "$(config_bash_write_vector 'rm /Users/x/.claude/ironclaude-hooks-config.json')"
assert_eq "unlink config" "block" "$(config_bash_write_vector 'unlink /Users/x/.claude/ironclaude-hooks-config.json')"
assert_eq "redirect-truncate config" "block" "$(config_bash_write_vector 'echo x > /Users/x/.claude/ironclaude-hooks-config.json')"
assert_eq "UPPERCASE rm config" "block" "$(config_bash_write_vector 'rm /Users/x/.claude/IRONCLAUDE-HOOKS-CONFIG.json')"
assert_eq "cat config (read)" "allow" "$(config_bash_write_vector 'cat /Users/x/.claude/ironclaude-hooks-config.json')"
# KNOWN, DOCUMENTED GAP: opaque interpreter writes are NOT caught (best-effort only).
assert_eq "KNOWN GAP: python3 -c write not caught" "allow" \
  "$(config_bash_write_vector 'python3 -c open("/Users/x/.claude/ironclaude-hooks-config.json","w")')"

echo "=== config_tool_write_verdict: event extraction + on-disk read ==="
_TMPCFG=$(mktemp)
printf '%s' '{"validation_backend":"ollama","ollama":{"url":"http://a"}}' > "$_TMPCFG"
assert_eq "tool verdict: benign content over benign config" "allow" \
  "$(config_tool_write_verdict '{"tool_input":{"content":"{\"validation_backend\":\"ollama\",\"ollama\":{\"url\":\"http://b\"}}"}}' "$_TMPCFG")"
printf '%s' '{"tier_up_review_policy":"enforced","ollama":{"url":"http://a"}}' > "$_TMPCFG"
assert_eq "tool verdict: guardrail-changing content blocked" "block" \
  "$(config_tool_write_verdict '{"tool_input":{"content":"{\"tier_up_review_policy\":\"off\",\"ollama\":{\"url\":\"http://a\"}}"}}' "$_TMPCFG")"
assert_eq "tool verdict: missing content blocked" "block" \
  "$(config_tool_write_verdict '{"tool_input":{}}' "$_TMPCFG")"

echo "=== config_guard_decision: routing + polarity (the guard's single entry) ==="
printf '%s' '{"validation_backend":"ollama","ollama":{"url":"http://a"}}' > "$_TMPCFG"
_CFG="$HOME/.claude/ironclaude-hooks-config.json"
assert_eq "Write config + benign content" "allow" \
  "$(config_guard_decision Write "$_CFG" '{"tool_input":{"content":"{\"ollama\":{\"url\":\"http://b\"}}"}}' "$_TMPCFG")"
printf '%s' '{"tier_up_review_policy":"enforced"}' > "$_TMPCFG"
assert_eq "Write config + guardrail content" "block" \
  "$(config_guard_decision Write "$_CFG" '{"tool_input":{"content":"{\"tier_up_review_policy\":\"off\"}"}}' "$_TMPCFG")"
assert_eq "Write UPPERCASE config + guardrail content" "block" \
  "$(config_guard_decision Write "$HOME/.claude/IRONCLAUDE-HOOKS-CONFIG.JSON" '{"tool_input":{"content":"{\"tier_up_review_policy\":\"off\"}"}}' "$_TMPCFG")"
assert_eq "Edit config" "block" "$(config_guard_decision Edit "$_CFG" '{}')"
assert_eq "MultiEdit config" "block" "$(config_guard_decision MultiEdit "$_CFG" '{}')"
assert_eq "Bash rm config" "block" "$(config_guard_decision Bash 'rm '"$_CFG" '{}')"
assert_eq "Bash cat config (read)" "allow" "$(config_guard_decision Bash 'cat '"$_CFG" '{}')"
assert_eq "Write non-config path" "allow" "$(config_guard_decision Write /tmp/other.json '{"tool_input":{"content":"{\"tier_up_review_policy\":\"off\"}"}}')"
assert_eq "Edit non-config path" "allow" "$(config_guard_decision Edit /tmp/other.json '{}')"
assert_eq "Read config (unrouted tool)" "allow" "$(config_guard_decision Read "$_CFG" '{}')"
rm -f "$_TMPCFG"

echo ""
echo "Results: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ] && exit 0 || exit 1
