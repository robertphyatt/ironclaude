# Shadow Grader Fix Design — Tool Guidance, JSON Schema Enforcement, Tool-Call Handling

> **Created:** 2026-06-20
> **Status:** Design Complete
> **Scope Mode:** hold — scope fixed, maximum rigor within it

## Summary

The gemma4 shadow grader (`shadow_grader.py`) produces three categories of failure: (1) useless tool calls (reading `.git/HEAD`, grepping `TODO`), (2) non-JSON or malformed verdict output, and (3) tool-call format mismatches. Three targeted changes fix these — a system prompt constant for tool-use guidance, Ollama `format` parameter for grammar-constrained JSON output, and improved tool-call loop handling.

All changes are in `shadow_grader.py`. No changes to `ollama_client.py` — `post_chat()` accepts a full payload dict and passes it through to `requests.post(json=payload)`, so `format` reaches Ollama without client modifications.

## Change 1: GEMMA4_SYSTEM_PROMPT Constant

### What

New module-level constant prepended to the system prompt passed to `grade_with_tools()`.

### Why

Without explicit tool-use guidance, gemma4 makes low-value tool calls (reading `.git/HEAD`, grepping generic patterns). The system prompt teaches it the correct investigation sequence.

### Implementation

```python
GEMMA4_SYSTEM_PROMPT = """\
You are a code review grader with tool-calling capability.

AVAILABLE TOOLS:
1. read_file — Read source files to examine code
2. git_diff — Show uncommitted changes in a repository
3. grep_files — Search for patterns in files

INVESTIGATION SEQUENCE:
Step 1: Use read_file on source files mentioned in the objective
Step 2: Use git_diff to verify claimed code changes
Step 3: Use grep_files with specific, evidence-based patterns if needed

RULES:
- Do NOT read .git/HEAD or other git internal files
- Do NOT use find or shell commands — only the three tools above
- Do NOT grep for generic patterns like 'TODO' — search for patterns directly relevant to the grading criteria
- After investigating, produce ONLY a JSON verdict with keys: grade, approved, feedback
- grade must be one of: A, B, C, D, F
- approved must be true or false
- feedback must explain your reasoning"""
```

### Integration Point

In `grade_with_tools()`, prepend to the system message:

```python
messages = [
    {"role": "system", "content": GEMMA4_SYSTEM_PROMPT + "\n\n" + system_prompt},
    {"role": "user", "content": user_prompt},
]
```

### Safety Check

Prompt text verified against `safety.md` banned terms list. No occurrences of: "graceful degradation", "fallback", "error recovery", "retry logic", "soft failure", "defensive fallback", "safe default", "best effort", "degrade gracefully".

## Change 2: JSON Schema Grammar Enforcement via `format` Parameter

### What

New module-level constant `GRADER_VERDICT_SCHEMA` containing the JSON schema. Applied to Ollama `/api/chat` calls via the `format` key — but ONLY on tools-stripped verdict calls, never alongside active `tools`.

### Why

Ollama's `format` parameter drives grammar-constrained decoding. When active, the model is forced to emit tokens matching the schema starting from the first token. This is incompatible with tool-call emission — grammar constraints suppress the tool-calling output format. Therefore `format` must NOT be present in payloads where `tools` is active.

### Schema Constant

```python
GRADER_VERDICT_SCHEMA = {
    "type": "object",
    "properties": {
        "grade": {"type": "string", "enum": ["A", "B", "C", "D", "F"]},
        "approved": {"type": "boolean"},
        "feedback": {"type": "string"},
    },
    "required": ["grade", "approved", "feedback"],
}
```

### Where Format Is Applied

Two locations, both tools-stripped:

1. **Max-steps forced call** (line ~192 current code): When `step >= MAX_TOOL_STEPS` and the model is still calling tools, the current code strips `tools` from the payload and makes a final call. Add `format` here:

```python
payload = {**payload, "messages": messages, "tools": [], "format": GRADER_VERDICT_SCHEMA}
```

2. **Parse-failure verdict enforcement** (new code after the loop): When the model stops calling tools but the content is not valid JSON, make ONE additional call with format enforcement and no tools. This replaces the current immediate `infrastructure_error` return for non-JSON responses.

### Parse-First, Enforce-On-Failure Strategy

This approach was chosen over "always make a separate verdict call" because:
- When the model returns valid JSON naturally (common case), no extra API call is made
- All 7 existing tests pass without modification — success paths have identical call counts
- The `test_non_json_verdict_returns_error` test uses `return_value` (not `side_effect`), so the retry gets the same non-JSON response, fails parsing again, and returns `infrastructure_error` as before

Post-loop flow:
```
content = strip_think_tags(content)
content = strip_special_tokens(content)
try:
    parsed = json.loads(content)
except JSONDecodeError:
    regex fallback attempt
    if regex fails:
        → make ONE verdict-enforcement call: tools=[], format=GRADER_VERDICT_SCHEMA
        → strip + parse result
        → if still fails: return infrastructure_error
```

## Change 3: Improved Tool-Call Loop Handling

### What

Two adjustments to the tool-call loop in `grade_with_tools()`:

1. **Verify `arguments` type**: Ollama native API returns tool call arguments as a Python dict. If gemma4 returns arguments as a JSON string instead of a dict, `_execute_tool()` calls like `arguments.get("path")` would fail silently (strings don't have `.get()`). Add a type check: if `arguments` is a string, parse it with `json.loads()`.

2. **No structural changes to tool message format**: The current format (`{"role": "tool", "content": result}`) is sufficient for Ollama's sequential tool-call processing. Ollama matches tool results to tool calls by position, not by ID.

### Implementation

In the tool-call processing block, before recording and executing:

```python
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
```

## Architecture — No Changes to ollama_client.py

`OllamaClient.post_chat()` (line 53-77) accepts a full payload dict and passes it through via `requests.post(json=payload)`. The `format` key is part of the payload dict, not a separate parameter — it reaches Ollama without any client-side changes.

## Testing Strategy

### Existing Tests (must all pass)

All 13 tests in `test_shadow_grader.py` must pass unchanged:
- `TestGradeWithTools` (7 tests): no_tool_calls, single_tool_call, infrastructure_error, non_json_verdict, think_tags, max_tool_steps, missing_grade_field
- `TestExecuteTool` (6 tests): read_file, path_traversal, path_outside_roots, grep, git_diff, unknown_tool

### Test Compatibility Analysis

| Test | Impact | Why it passes |
|------|--------|---------------|
| `test_no_tool_calls_parses_json_verdict` | None | Valid JSON on first try → no format call needed |
| `test_single_tool_call_then_verdict` | None | Valid JSON after tools → no format call needed |
| `test_non_json_verdict_returns_error` | Extra call (transparent) | `return_value` mock returns same non-JSON on retry → still returns infrastructure_error |
| `test_think_tags_stripped_before_parse` | None | Valid JSON after stripping → no format call needed |
| `test_max_tool_steps_forces_final_verdict` | None | Test's side_effects return valid verdict at step 5 without hitting forced path |
| `test_missing_grade_field_returns_infrastructure_error` | None | JSON parses but fields missing → infrastructure_error (no retry for missing fields, only for non-JSON) |
| `test_infrastructure_error_returns_error_dict` | None | OllamaError on first call → immediate return |

### Verify-Against-Live Flag

The `arguments` type (dict vs string) from gemma4's tool calls cannot be definitively resolved from code alone. The type-check (Change 3) handles both cases. Flag for live verification: run shadow grader against a real task and log `type(tc["arguments"])` to confirm which format gemma4 uses.

## Implementation Notes

- `GEMMA4_SYSTEM_PROMPT` goes at module level near `SHADOW_TOOLS` (around line 18)
- `GRADER_VERDICT_SCHEMA` goes at module level after `GEMMA4_SYSTEM_PROMPT`
- The verdict-enforcement call is a new code block inserted between the current regex-fallback failure and the `infrastructure_error` return
- `orchestrator_mcp.py` is NOT modified — it calls `grade_with_tools(system_prompt, user_prompt, repo_path)`, and the system prompt prepend happens inside `grade_with_tools()`
