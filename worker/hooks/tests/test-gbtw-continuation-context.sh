#!/usr/bin/env bash
set -euo pipefail

HOOKS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP_ROOT="$(mktemp -d)"
trap 'rm -rf "$TMP_ROOT"' EXIT
source "$HOOKS_DIR/continuation-context.sh"

assert_json() {
    local label="$1" transcript="$2" event="$3" final="$4" assertion="$5"
    local result
    result="$(_gbtw_continuation_context "$event" "$transcript" "$final")"
    if jq -e "$assertion" >/dev/null <<<"$result"; then
        printf 'PASS %s\n' "$label"
    else
        printf 'FAIL %s: %s\n' "$label" "$result" >&2
        exit 1
    fi
}

cat >"$TMP_ROOT/claude.jsonl" <<'JSONL'
{"type":"user","message":{"role":"user","content":"Build a report; exclude private names."}}
{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"I can publish the report. Choose internal or public distribution; private names remain excluded."}]}}
{"type":"user","message":{"role":"user","content":"Proceed with internal distribution."}}
{"type":"assistant","message":{"role":"assistant","content":[{"type":"tool_use","id":"write-1","name":"Write","input":{"path":"report.md","body":"Draft"}}]}}
{"type":"user","message":{"role":"user","content":[{"type":"tool_result","tool_use_id":"write-1","content":"report.md written"}]}}
{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"Report written; publication awaits review."}]}}
JSONL
assert_json 'Claude exchange, referent, tool outcome, event final' "$TMP_ROOT/claude.jsonl" \
    '{"last_assistant_message":"Authoritative Stop final"}' 'Ignored fallback' \
    '.provider == "claude" and .final_text == "Authoritative Stop final" and
     (.exchanges | length) == 2 and
     .exchanges[0].request == "Build a report; exclude private names." and
     (.exchanges[0].assistant | any(contains("Choose internal or public"))) and
     .exchanges[1].request == "Proceed with internal distribution." and
     (.activity | any(.kind == "tool_result" and .id == "write-1" and .text == "report.md written")) and
     (.activity | any(.kind == "tool_call" and .id == "write-1" and .name == "Write")) and
     .uncertainty.user_boundary_absent == false'

cat >"$TMP_ROOT/codex.jsonl" <<'JSONL'
{"type":"event_msg","payload":{"type":"user_message","message":"Duplicate event text"}}
{"type":"response_item","payload":{"type":"message","role":"user","content":[{"type":"input_text","text":"Inspect status."}]}}
{"type":"response_item","payload":{"type":"function_call","call_id":"fn-1","name":"functions.exec","arguments":"{\"cmd\":\"git status\"}"}}
{"type":"response_item","payload":{"type":"function_call_output","call_id":"fn-1","output":"clean"}}
{"type":"response_item","payload":{"type":"custom_tool_call","call_id":"custom-1","name":"exec","input":"text('hello')"}}
{"type":"response_item","payload":{"type":"custom_tool_call_output","call_id":"custom-1","output":[{"type":"input_text","text":"Script completed"}]}}
{"type":"response_item","payload":{"type":"message","role":"assistant","phase":"analysis","content":[{"type":"output_text","text":"Hidden reasoning must stay out"}]}}
{"type":"response_item","payload":{"type":"message","role":"assistant","channel":"summary","content":[{"type":"output_text","text":"Hidden summary must stay out"}]}}
{"type":"response_item","payload":{"type":"message","role":"assistant","content":[{"type":"output_text","text":"Status inspected."}]}}
JSONL
assert_json 'Codex function/custom records and no event duplication' "$TMP_ROOT/codex.jsonl" '{}' 'Fallback final' \
    '.provider == "codex" and (.exchanges | length) == 1 and
     .exchanges[0].request == "Inspect status." and .final_text == "Fallback final" and
     (.activity | any(.kind == "tool_call" and .id == "fn-1" and .name == "functions.exec" and (.text | contains("git status")))) and
     (.activity | any(.kind == "tool_result" and .id == "fn-1" and .text == "clean")) and
     (.activity | any(.kind == "tool_call" and .id == "custom-1" and .name == "exec" and .text == "text(\u0027hello\u0027)")) and
     (.activity | any(.kind == "tool_result" and .id == "custom-1" and .text == "Script completed")) and
     ([.activity[].text] | join(" ") | contains("Hidden reasoning") | not) and
     ([.activity[].text] | join(" ") | contains("Hidden summary") | not)'

cat >"$TMP_ROOT/fallback.jsonl" <<'JSONL'
{"type":"event_msg","payload":{"type":"user_message","message":"Fallback request"}}
{broken
42
"junk"
[]
{"type":"response_item","payload":{"type":"message","role":"assistant","content":[{"type":"output_text","text":"Pending answer"}]}}
JSONL
assert_json 'event fallback, malformed and scalar rows' "$TMP_ROOT/fallback.jsonl" '{}' '' \
    '.exchanges[-1].request == "Fallback request" and .final_text == "Pending answer" and
     .uncertainty.malformed_rows == true'
assert_json 'missing transcript uncertainty' "$TMP_ROOT/missing.jsonl" '{}' '' \
    '.uncertainty.transcript_unavailable == true and .uncertainty.user_boundary_absent == true'

# First row is outside 1 MiB tail. Neither its request nor its boundary may be invented.
printf '%s\n' '{"type":"user","message":{"content":"Outside byte window"}}' >"$TMP_ROOT/big.jsonl"
head -c 1048600 /dev/zero | tr '\0' 'x' >>"$TMP_ROOT/big.jsonl"
printf '\n{"type":"assistant","message":{"content":"Only visible assistant"}}\n' >>"$TMP_ROOT/big.jsonl"
assert_json 'byte window and absent boundary' "$TMP_ROOT/big.jsonl" '{}' '' \
    '.uncertainty.transcript_truncated == true and .uncertainty.partial_first_row == true and
     .uncertainty.user_boundary_absent == true and
     (.exchanges | length) == 0'

jq -cn --arg request "$(printf 'r%.0s' {1..4000})" \
    '{type:"response_item",payload:{type:"message",role:"user",content:[{type:"input_text",text:$request}]}}' \
    >"$TMP_ROOT/clip.jsonl"
for i in {1..140}; do
    jq -cn --arg i "$i" '{type:"response_item",payload:{type:"function_call_output",output:$i}}' >>"$TMP_ROOT/clip.jsonl"
done
assert_json 'record, field, final bounds' "$TMP_ROOT/clip.jsonl" '{}' "$(printf 'f%.0s' {1..4000})" \
    '.uncertainty.record_limit == true and .uncertainty.final_clipped == true and
     .uncertainty.field_clipped == true and .uncertainty.activity_omitted == true and
     .exchanges[0].request != null and (.exchanges[0].request | length) == 900 and
     (.final_text | length) <= 1800 and (.activity | length) <= 8 and
     (.exchanges | length) <= 4'

printf '%s\n' '{"type":"response_item","payload":{"type":"message","role":"user","content":[{"type":"input_text","text":"Proceed"}]}}' >"$TMP_ROOT/isolated.jsonl"
assert_json 'isolated request lacks prior referent' "$TMP_ROOT/isolated.jsonl" '{}' '' \
    '.exchanges[0].request == "Proceed" and .uncertainty.prior_request_unavailable == true'

cat >"$TMP_ROOT/omissions.jsonl" <<'JSONL'
{"type":"user","message":{"content":"Prior task"}}
{"type":"assistant","message":{"content":"First option"}}
{"type":"assistant","message":{"content":"Second option"}}
{"type":"assistant","message":{"content":"Third option"}}
{"type":"user","message":{"content":"Proceed"}}
JSONL
assert_json 'assistant omission and explicit empty final' "$TMP_ROOT/omissions.jsonl" \
    '{"last_assistant_message":""}' 'Fallback should lose' \
    '.uncertainty.assistant_messages_omitted == true and
     .exchanges[0].assistant == ["Second option", "Third option"] and .final_text == ""'

printf 'PASS continuation context tests\n'
