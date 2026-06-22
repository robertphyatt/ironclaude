# Shadow Grader Always-Enforce Verdict Design

> **Created:** 2026-06-20
> **Status:** Design Complete
> **Scope Mode:** hold — scope fixed, maximum rigor within it

## Summary

After the tool-calling loop in `grade_with_tools()` ends, a dedicated final verdict call with `tools: []` and `format: GRADER_VERDICT_SCHEMA` is ALWAYS made — not as a fallback after parse failure. This eliminates the regex fallback chain (`re.search`) and the parse-first strategy introduced in d1195. The `GRADER_VERDICT_SCHEMA` constant already exists (lines 76-84) and is already used at the max-steps path. This design unifies both exit paths to always flow through the post-loop verdict call.

**Trade-off accepted:** +1 API call overhead per grading (even when gemma4 would have returned valid JSON naturally). Reliability via grammar-constrained decoding is the priority.

## Loop Restructure

### Current (parse-first, enforce-on-failure)

Two exit paths with different verdict strategies:

- **Normal exit** (`else: break`): Use whatever `content` the model returned, try `json.loads()`, regex fallback, then format-enforced retry only if both fail.
- **Max-steps exit** (`if step >= MAX_TOOL_STEPS`): Make a format-enforced call inline before breaking, then fall through to `json.loads()` on that result.

### New (always-enforce)

One unified exit path:

Both exits — normal and max-steps — just `break` out of the loop. Max-steps appends a user hint message first. After the loop, a single post-loop verdict call with `tools: []` and `format: GRADER_VERDICT_SCHEMA` is always made.

```python
for step in range(MAX_TOOL_STEPS + 1):
    try:
        content, tool_calls = client.post_chat(payload)
    except OllamaError as e:
        return self._build_error(str(e), recorded_tool_calls)

    if not tool_calls:
        break  # Normal exit — fall through to verdict call

    # Append assistant message with tool_calls
    messages.append({
        "role": "assistant",
        "content": content,
        "tool_calls": [
            {"function": {"name": tc["name"], "arguments": tc["arguments"]}}
            for tc in tool_calls
        ],
    })
    for tc in tool_calls:
        args = tc["arguments"]
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                args = {}
        recorded_tool_calls.append({"name": tc["name"], "args": args})
        tool_result = self._execute_tool(tc["name"], args, repo_path)
        messages.append({"role": "tool", "content": tool_result})

    if step >= MAX_TOOL_STEPS:
        messages.append({
            "role": "user",
            "content": (
                "Maximum investigation steps reached. Respond with ONLY valid JSON: "
                '{"grade": "A|B|C|D|F", "approved": true|false, "feedback": "..."}'
            ),
        })
        break  # Max-steps exit — fall through to verdict call

    payload = {**payload, "messages": messages}

# Post-loop: ALWAYS make dedicated verdict call with grammar enforcement
verdict_payload = {
    "model": self._model,
    "messages": messages,
    "stream": False,
    "options": {"temperature": 0.1},
    "tools": [],
    "format": GRADER_VERDICT_SCHEMA,
}
try:
    content, _ = client.post_chat(verdict_payload)
except OllamaError as e:
    return self._build_error(str(e), recorded_tool_calls)

content = _THINK_TAG_RE.sub("", content)
content = _SPECIAL_TOKEN_RE.sub("", content).strip()
try:
    parsed = json.loads(content)
except json.JSONDecodeError:
    return self._build_error(f"Non-JSON response: {content[:200]}", recorded_tool_calls)
```

## Removals

- `re.search(r'\{[^{}]*"grade"\s*:\s*"[ABCDF]"[^{}]*\}', content)` — regex fallback gone
- The format-enforced `client.post_chat()` call inside the max-steps block — moved post-loop
- The `re` module import can stay (used by `_THINK_TAG_RE` and `_SPECIAL_TOKEN_RE`), but `re.search` is no longer used

## Verdict Call Messages Content

For the normal exit (model returned no tool calls on first iteration): `messages = [system, user]`. The model's first response content is discarded — the grammar-constrained verdict call is the authoritative result. No intermediate content is preserved.

For tool-using paths: `messages` includes all assistant/tool messages from the investigation. The verdict call sees the full tool investigation context.

## Test Changes

### Tests using `side_effect` that need extra entries

| Test | Old call count | New call count | Change |
|------|---------------|---------------|--------|
| `test_single_tool_call_then_verdict` | 2 | 3 | Add 3rd entry: `(verdict, [])` |
| `test_max_tool_steps_forces_final_verdict` | 6 | 7 | Add 7th entry: `(verdict, [])` |
| `test_string_arguments_parsed_to_dict` | 2 | 3 | Add 3rd entry: `(verdict, [])` |

### Tests using `return_value` (no change needed)

`return_value` applies to all calls. With always-enforce adding a 2nd call, both calls return the same mock value. All assertions remain valid:
- `test_no_tool_calls_parses_json_verdict`: both calls return valid JSON — passes
- `test_non_json_verdict_returns_error`: both calls return non-JSON — verdict call fails `json.loads()` → infrastructure_error — passes
- `test_think_tags_stripped_before_parse`: both calls return think-tagged JSON — stripped on verdict call result — passes
- `test_missing_grade_field_returns_infrastructure_error`: both calls return missing-field JSON — missing field check triggers — passes
- `test_system_prompt_prepends_gemma4_guidance`: `call_args` is the last call (verdict call); verdict_payload has `messages[0]` = system message with GEMMA4_SYSTEM_PROMPT prepended — passes

### Test rename

`test_non_json_triggers_format_constrained_retry` → `test_format_constrained_verdict_recovers_non_json`

Semantics changed: format-constrained verdict call is no longer triggered by non-JSON (it's always made). The test still verifies that when first call returns non-JSON and verdict call returns valid JSON, result is correct — but the name must reflect always-enforce semantics. Assertions unchanged.

### New test

`test_verdict_call_always_uses_format_schema` — verifies that even when model returns valid JSON on the first call (no tools), a second format-constrained verdict call is still made. Checks `mock_client.post_chat.call_count == 2` and that the second call's payload has `format: GRADER_VERDICT_SCHEMA` and `tools: []`.

## Implementation Notes

- All changes in `commander/src/ironclaude/shadow_grader.py` only
- No changes to `ollama_client.py` or `orchestrator_mcp.py`
- `GRADER_VERDICT_SCHEMA` constant stays at module level (already correct)
- `GEMMA4_SYSTEM_PROMPT` constant stays at module level (already correct)
- The `re` module import stays — still used by `_THINK_TAG_RE` and `_SPECIAL_TOKEN_RE`
- `re.search` call on line 245 is removed along with the regex fallback block
