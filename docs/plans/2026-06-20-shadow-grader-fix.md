# Shadow Grader Fix Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** Fix gemma4 shadow grader to produce correct tool-calling behavior and valid JSON output via system prompt guidance, JSON schema grammar enforcement, and arguments type safety.

**Architecture:** Three changes to `shadow_grader.py`, no changes to `ollama_client.py`. System prompt prepend guides tool selection. Ollama `format` parameter enforces JSON schema on tools-stripped verdict calls only (grammar-constrained decoding suppresses tool emission). Arguments type-check handles both dict and string formats from gemma4.

**Tech Stack:** Python, Ollama `/api/chat` API, pytest

---

## Task 1: Add Constants and Prepend System Prompt

**Files:**
- Modify: `commander/src/ironclaude/shadow_grader.py:18,149-152`
- Modify: `commander/tests/test_shadow_grader.py`

**Step 1: Write test for system prompt prepend**

Add test to `TestGradeWithTools` in `commander/tests/test_shadow_grader.py`:

```python
def test_system_prompt_prepends_gemma4_guidance(self):
    from ironclaude.shadow_grader import GEMMA4_SYSTEM_PROMPT
    grader, mock_client = _make_shadow_grader()
    verdict_json = '{"grade": "B", "approved": true, "feedback": "ok"}'
    mock_client.post_chat.return_value = (verdict_json, [])
    grader.grade_with_tools("original system prompt", "user")
    call_payload = mock_client.post_chat.call_args[0][0]
    system_msg = call_payload["messages"][0]["content"]
    assert system_msg.startswith(GEMMA4_SYSTEM_PROMPT)
    assert "original system prompt" in system_msg
```

**Step 2: Run test — verify it fails**

```bash
cd commander && python -m pytest tests/test_shadow_grader.py::TestGradeWithTools::test_system_prompt_prepends_gemma4_guidance -v
```

Expected: FAIL (ImportError — `GEMMA4_SYSTEM_PROMPT` does not exist yet)

**Step 3: Add GEMMA4_SYSTEM_PROMPT and GRADER_VERDICT_SCHEMA constants**

Insert after line 51 (`MAX_TOOL_STEPS = 5`) in `commander/src/ironclaude/shadow_grader.py`:

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

**Step 4: Prepend GEMMA4_SYSTEM_PROMPT in grade_with_tools()**

Change the messages construction (currently lines 149-152):

FROM:
```python
messages = [
    {"role": "system", "content": system_prompt},
    {"role": "user", "content": user_prompt},
]
```

TO:
```python
messages = [
    {"role": "system", "content": GEMMA4_SYSTEM_PROMPT + "\n\n" + system_prompt},
    {"role": "user", "content": user_prompt},
]
```

**Step 5: Run test — verify it passes**

```bash
cd commander && python -m pytest tests/test_shadow_grader.py::TestGradeWithTools::test_system_prompt_prepends_gemma4_guidance -v
```

Expected: PASS

**Step 6: Stage changes**

```bash
git add commander/src/ironclaude/shadow_grader.py commander/tests/test_shadow_grader.py
```

---

## Task 2: Add Format Enforcement on Verdict Calls

**Files:**
- Modify: `commander/src/ironclaude/shadow_grader.py:184-214`
- Modify: `commander/tests/test_shadow_grader.py`

**Step 1: Write test for format-constrained verdict enforcement**

Add test to `TestGradeWithTools` in `commander/tests/test_shadow_grader.py`:

```python
def test_non_json_triggers_format_constrained_retry(self):
    grader, mock_client = _make_shadow_grader()
    valid_json = '{"grade": "B", "approved": true, "feedback": "ok"}'
    mock_client.post_chat.side_effect = [
        ("not json at all", []),
        (valid_json, []),
    ]
    result = grader.grade_with_tools("sys", "user")
    assert result["grade"] == "B"
    assert result["approved"] is True
    second_call_payload = mock_client.post_chat.call_args_list[1][0][0]
    assert "format" in second_call_payload
    assert second_call_payload["tools"] == []
```

**Step 2: Run test — verify it fails**

```bash
cd commander && python -m pytest tests/test_shadow_grader.py::TestGradeWithTools::test_non_json_triggers_format_constrained_retry -v
```

Expected: FAIL (currently returns infrastructure_error instead of retrying)

**Step 3: Add format to max-steps forced call**

In `grade_with_tools()`, change the max-steps payload construction.

FROM:
```python
payload = {**payload, "messages": messages, "tools": []}
```

TO:
```python
payload = {**payload, "messages": messages, "tools": [], "format": GRADER_VERDICT_SCHEMA}
```

**Step 4: Add verdict-enforcement block after regex fallback**

Replace the post-loop JSON parsing section. Currently (approx lines 204-214):

```python
try:
    parsed = json.loads(content)
except json.JSONDecodeError:
    match = re.search(r'\{[^{}]*"grade"\s*:\s*"[ABCDF]"[^{}]*\}', content)
    if match:
        try:
            parsed = json.loads(match.group())
        except json.JSONDecodeError:
            return self._build_error(f"Non-JSON response: {content[:200]}", recorded_tool_calls)
    else:
        return self._build_error(f"Non-JSON response: {content[:200]}", recorded_tool_calls)
```

Replace with:

```python
try:
    parsed = json.loads(content)
except json.JSONDecodeError:
    match = re.search(r'\{[^{}]*"grade"\s*:\s*"[ABCDF]"[^{}]*\}', content)
    if match:
        try:
            parsed = json.loads(match.group())
        except json.JSONDecodeError:
            parsed = None
    else:
        parsed = None

    if parsed is None:
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

**Step 5: Run test — verify it passes**

```bash
cd commander && python -m pytest tests/test_shadow_grader.py::TestGradeWithTools::test_non_json_triggers_format_constrained_retry -v
```

Expected: PASS

**Step 6: Stage changes**

```bash
git add commander/src/ironclaude/shadow_grader.py commander/tests/test_shadow_grader.py
```

---

## Task 3: Add Arguments Type Safety in Tool-Call Loop

**Files:**
- Modify: `commander/src/ironclaude/shadow_grader.py:178-181`
- Modify: `commander/tests/test_shadow_grader.py`

**Step 1: Write test for string arguments handling**

Add test to `TestGradeWithTools` in `commander/tests/test_shadow_grader.py`:

```python
def test_string_arguments_parsed_to_dict(self):
    grader, mock_client = _make_shadow_grader()
    tool_call = [{"name": "read_file", "arguments": '{"path": "/tmp/test.txt"}'}]
    verdict = '{"grade": "A", "approved": true, "feedback": "ok"}'
    mock_client.post_chat.side_effect = [
        ("", tool_call),
        (verdict, []),
    ]
    grader._execute_tool = MagicMock(return_value="file contents")
    result = grader.grade_with_tools("sys", "user", repo_path="/tmp")
    assert result["grade"] == "A"
    grader._execute_tool.assert_called_once_with("read_file", {"path": "/tmp/test.txt"}, "/tmp")
```

**Step 2: Run test — verify it fails**

```bash
cd commander && python -m pytest tests/test_shadow_grader.py::TestGradeWithTools::test_string_arguments_parsed_to_dict -v
```

Expected: FAIL (`_execute_tool` receives string instead of dict, `.get("path")` fails silently)

**Step 3: Add type check in tool-call processing**

In `grade_with_tools()`, replace the tool-call processing loop.

FROM:
```python
for tc in tool_calls:
    recorded_tool_calls.append({"name": tc["name"], "args": tc["arguments"]})
    tool_result = self._execute_tool(tc["name"], tc["arguments"], repo_path)
    messages.append({"role": "tool", "content": tool_result})
```

TO:
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

**Step 4: Run test — verify it passes**

```bash
cd commander && python -m pytest tests/test_shadow_grader.py::TestGradeWithTools::test_string_arguments_parsed_to_dict -v
```

Expected: PASS

**Step 5: Stage changes**

```bash
git add commander/src/ironclaude/shadow_grader.py commander/tests/test_shadow_grader.py
```

---

## Task 4: Run Full Test Suite — Verify All Tests Pass

**Files:**
- Read-only: `commander/tests/test_shadow_grader.py`

No tests required: verification-only task.

**Step 1: Run full test suite**

```bash
cd commander && python -m pytest tests/test_shadow_grader.py -v
```

Expected: All 16 tests pass (13 original + 3 new):
- `TestGradeWithTools::test_no_tool_calls_parses_json_verdict` — PASS
- `TestGradeWithTools::test_single_tool_call_then_verdict` — PASS
- `TestGradeWithTools::test_infrastructure_error_returns_error_dict` — PASS
- `TestGradeWithTools::test_non_json_verdict_returns_error` — PASS
- `TestGradeWithTools::test_think_tags_stripped_before_parse` — PASS
- `TestGradeWithTools::test_max_tool_steps_forces_final_verdict` — PASS
- `TestGradeWithTools::test_missing_grade_field_returns_infrastructure_error` — PASS
- `TestGradeWithTools::test_system_prompt_prepends_gemma4_guidance` — PASS
- `TestGradeWithTools::test_non_json_triggers_format_constrained_retry` — PASS
- `TestGradeWithTools::test_string_arguments_parsed_to_dict` — PASS
- `TestExecuteTool::test_read_file_returns_content` — PASS
- `TestExecuteTool::test_path_traversal_rejected` — PASS
- `TestExecuteTool::test_path_outside_allowed_roots_rejected` — PASS
- `TestExecuteTool::test_grep_files_runs_rg` — PASS
- `TestExecuteTool::test_git_diff_runs_git` — PASS
- `TestExecuteTool::test_unknown_tool_returns_error_json` — PASS

**Step 2: Verify test_non_json_verdict_returns_error still passes**

This existing test is the most sensitive to our changes. It uses `return_value` (not `side_effect`), so every `post_chat` call returns `("not json at all", [])`. The verdict-enforcement retry gets the same non-JSON, fails parsing, and returns `infrastructure_error`.

```bash
cd commander && python -m pytest tests/test_shadow_grader.py::TestGradeWithTools::test_non_json_verdict_returns_error -v
```

Expected: PASS
