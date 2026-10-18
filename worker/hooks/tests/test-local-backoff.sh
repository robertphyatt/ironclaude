#!/bin/bash
# test-local-backoff.sh — the local-box backoff in plan-validator.sh call_validation_llm (design
# 2026-10-17 v120-completeness R4c): a failed local-box attempt skips the box for
# local_backoff_minutes (default 15, 0 off); expiry retries; success clears; the marker directory
# must be a real 0700 directory owned by this user. A stub curl counts box calls. Do NOT set -e.
PASS=0; FAIL=0
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
cat > "$STUBBIN/curl" <<'EOF'
#!/bin/bash
echo called >> "$STUB_LOG_DIR/curl.calls"
case "${STUB_CURL_MODE:-fail}" in
  fail) exit 7 ;;
  ok)   printf '%s' '{"choices":[{"message":{"content":"{\"ok\":true,\"src\":\"local\"}"}}]}' ;;
esac
EOF
cat > "$STUBBIN/claude" <<'EOF'
#!/bin/bash
cat > /dev/null
printf '%s' '[{"type":"result","subtype":"success","structured_output":{"ok":true,"src":"haiku"}}]'
EOF
chmod +x "$STUBBIN/curl" "$STUBBIN/claude"
export PATH="$STUBBIN:$PATH" IC_HOOK_CLIENT=claude STUB_CURL_MODE=fail
mkdir -p "$WORK/state"
export IRONCLAUDE_LOCAL_BACKOFF_DIR="$WORK/state/local-backoff"
MARKER="$IRONCLAUDE_LOCAL_BACKOFF_DIR/local-box-backoff.json"
SCHEMA='{"type":"object","properties":{"ok":{"type":"boolean"}},"required":["ok"]}'
OPENAI='{"backend":"openai","openai":{"base_url":"http://box.invalid/v1","model":"m"}}'
LOCAL='{"ok":true,"src":"local"}'
HAIKU='{"ok":true,"src":"haiku"}'
cfg() { printf '%s' "$1" > "$WORK/cfg.json"; export IC_OLLAMA_CONFIG_PATH="$WORK/cfg.json"; }
run() { rm -f "$STUB_LOG_DIR/curl.calls"; call_validation_llm "grade this" "$SCHEMA" > "$WORK/out"; OUT=$(jq -c . "$WORK/out" 2>/dev/null); }
box_calls() { if [ -f "$STUB_LOG_DIR/curl.calls" ]; then wc -l < "$STUB_LOG_DIR/curl.calls" | tr -d ' '; else echo 0; fi; }
window() { echo $(( $(jq -r '.until' "$1" 2>/dev/null) - $(date +%s) )); }
in_range() { local w; w=$(window "$1"); [ "$w" -ge "$2" ] && [ "$w" -le "$3" ] && echo yes || echo no; }
live_marker() { printf '{"endpoint":"%s","until":%s}' "$2" "$(( $(date +%s) + $3 ))" > "$1"; }

echo "=== no marker: the box is tried (positive control) and its failure writes the marker ==="
cfg "$OPENAI"; run
assert_eq "no marker -> box tried once" "1" "$(box_calls)"
assert_eq "box down -> haiku answers" "$HAIKU" "$OUT"
assert_eq "failure writes the marker for the endpoint" "http://box.invalid/v1" "$(jq -r .endpoint "$MARKER" 2>/dev/null)"
assert_eq "marker expires in 15 minutes by default" "yes" "$(in_range "$MARKER" 890 900)"
assert_eq "marker directory is 0700" "drwx------" "$(ls -ld "$IRONCLAUDE_LOCAL_BACKOFF_DIR" 2>/dev/null | cut -c1-10)"

echo "=== live marker: the box is skipped ==="
run
assert_eq "live marker -> box not called" "0" "$(box_calls)"
assert_eq "live marker -> haiku answers" "$HAIKU" "$OUT"

echo "=== another endpoint is tried, and its success leaves this endpoint's marker ==="
cfg '{"backend":"openai","openai":{"base_url":"http://other.invalid/v1","model":"m"}}'
STUB_CURL_MODE=ok; run
assert_eq "other endpoint -> box tried" "1" "$(box_calls)"
assert_eq "other endpoint success keeps box.invalid's marker" "http://box.invalid/v1" "$(jq -r .endpoint "$MARKER" 2>/dev/null)"
cfg "$OPENAI"

echo "=== expiry: the next call tries the box and a failure renews the marker ==="
live_marker "$MARKER" "http://box.invalid/v1" -1
STUB_CURL_MODE=fail; run
assert_eq "expired marker -> box tried" "1" "$(box_calls)"
assert_eq "failure renews the marker" "yes" "$(in_range "$MARKER" 890 900)"

echo "=== success clears the marker ==="
live_marker "$MARKER" "http://box.invalid/v1" -1
STUB_CURL_MODE=ok; run
assert_eq "success -> local answer" "$LOCAL" "$OUT"
assert_eq "success clears the marker" "absent" "$([ -e "$MARKER" ] && echo present || echo absent)"
STUB_CURL_MODE=fail

echo "=== the per-backend value overrides the top-level value ==="
cfg '{"backend":"openai","local_backoff_minutes":30,"openai":{"base_url":"http://box.invalid/v1","model":"m","local_backoff_minutes":2}}'
run
assert_eq "openai.local_backoff_minutes=2 -> 2-minute marker" "yes" "$(in_range "$MARKER" 110 120)"
rm -f "$MARKER"

echo "=== 0 disables the backoff ==="
cfg '{"backend":"openai","local_backoff_minutes":0,"openai":{"base_url":"http://box.invalid/v1","model":"m"}}'
run
assert_eq "0 -> a failure writes no marker" "absent" "$([ -e "$MARKER" ] && echo present || echo absent)"
live_marker "$MARKER" "http://box.invalid/v1" 600
run
assert_eq "0 -> a live marker is ignored and the box is tried" "1" "$(box_calls)"
rm -f "$MARKER"

echo "=== the ollama arm ==="
cfg '{"backend":"ollama","ollama":{"url":"http://ollama.invalid:11434/","model":"m"}}'
run
assert_eq "ollama failure marks the url without its trailing slash" "http://ollama.invalid:11434" "$(jq -r .endpoint "$MARKER" 2>/dev/null)"
run
assert_eq "ollama live marker -> box not called" "0" "$(box_calls)"
rm -f "$MARKER"

echo "=== the marker directory must be a real 0700 directory owned by this user ==="
cfg "$OPENAI"
mkdir "$WORK/open"; chmod 770 "$WORK/open"
live_marker "$WORK/open/local-box-backoff.json" "http://box.invalid/v1" 600
BEFORE=$(cat "$WORK/open/local-box-backoff.json")
export IRONCLAUDE_LOCAL_BACKOFF_DIR="$WORK/open"; run
assert_eq "group-accessible directory -> marker ignored, box tried" "1" "$(box_calls)"
assert_eq "group-accessible directory -> marker not rewritten" "$BEFORE" "$(cat "$WORK/open/local-box-backoff.json")"
mkdir -m 700 "$WORK/real"; ln -s "$WORK/real" "$WORK/link"
live_marker "$WORK/real/local-box-backoff.json" "http://box.invalid/v1" 600
BEFORE=$(cat "$WORK/real/local-box-backoff.json")
export IRONCLAUDE_LOCAL_BACKOFF_DIR="$WORK/link"; run
assert_eq "symlinked directory -> marker ignored, box tried" "1" "$(box_calls)"
assert_eq "symlinked directory -> marker not rewritten" "$BEFORE" "$(cat "$WORK/real/local-box-backoff.json")"
FOREIGN=$(cd /tmp && pwd -P)
FOREIGN_BEFORE=$([ -e "$FOREIGN/local-box-backoff.json" ] && echo present || echo absent)
export IRONCLAUDE_LOCAL_BACKOFF_DIR="$FOREIGN"; run
assert_eq "foreign-owned directory -> box tried" "1" "$(box_calls)"
assert_eq "foreign-owned directory -> nothing written" "$FOREIGN_BEFORE" "$([ -e "$FOREIGN/local-box-backoff.json" ] && echo present || echo absent)"

echo "=== the default directory is \$HOME/.ironclaude/state/local-backoff ==="
unset IRONCLAUDE_LOCAL_BACKOFF_DIR
OLD_HOME="$HOME"; export HOME="$WORK/home"; mkdir -p "$HOME"
run
assert_eq "default marker path" "http://box.invalid/v1" "$(jq -r .endpoint "$HOME/.ironclaude/state/local-backoff/local-box-backoff.json" 2>/dev/null)"
export HOME="$OLD_HOME"

rm -rf "$WORK"
unset IC_OLLAMA_CONFIG_PATH IC_HOOK_CLIENT STUB_CURL_MODE
echo ""
echo "Results: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ] && exit 0 || exit 1
