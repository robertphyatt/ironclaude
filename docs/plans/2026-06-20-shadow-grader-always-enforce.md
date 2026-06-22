# Shadow Grader Always-Enforce Verdict — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** After the tool-calling loop in `grade_with_tools()`, ALWAYS make a dedicated verdict call with `tools: []` and `format: GRADER_VERDICT_SCHEMA` — eliminating the regex fallback chain.

**Architecture:** Two surgical edits to `shadow_grader.py`: (1) remove the inline `client.post_chat()` from the max-steps block (keep the user hint + break), and (2) replace the post-loop parse/regex/retry chain with a single format-constrained verdict call. Three existing tests need an extra `side_effect` entry for the new post-loop call, and one test is renamed.

**Tech Stack:** Python, pytest, `unittest.mock.MagicMock.side_effect`

---

## Task 1: Write new test (RED)

**Files:**
- Modify: `commander/tests/test_shadow_grader.py`

**Step 1: Add `test_verdict_call_always_uses_format_schema` inside `TestGradeWithTools`**

Add after `test_system_prompt_prepends_gemma4_guidance` (line 94):

```python
def test_verdict_call_always_uses_format_schema(self):
    from ironclaude.shadow_grader import GRADER_VERDICT_SCHEMA
    grader, mock_client = _make_shadow_grader()
    verdict_json = '{"grade": "A", "approved": true, "feedback": "well done"}'
    mock_client.post_chat.side_effect = [
        (verdict_json, []),   # initial loop call — no tool calls, break
        (verdict_json, []),   # post-loop verdict call with format constraint
    ]
    result = grader.grade_with_tools("sys", "user")
    assert result["grade"] == "A"
    assert mock_client.post_chat.call_count == 2
    verdict_call_payload = mock_client.post_chat.call_args_list[1][0][0]
    assert verdict_call_payload["format"] == GRADER_VERDICT_SCHEMA
    assert verdict_call_payload["tools"] == []
```

**Step 2: Run new test — verify FAIL**

```bash
cd commander && python -m pytest tests/test_shadow_grader.py::TestGradeWithTools::test_verdict_call_always_uses_format_schema -v
```

Expected: `FAILED` — `AssertionError: assert 1 == 2` (currently only 1 API call in the no-tool-calls path)

**Step 3: Stage**

```bash
git add commander/tests/test_shadow_grader.py
```

---

## Task 2: Restructure `grade_with_tools()` (GREEN)

**Files:**
- Modify: `commander/src/ironclaude/shadow_grader.py`

**Step 1: Remove the inline verdict call from the max-steps block**

In `shadow_grader.py`, locate this block (lines 222–235):

```python
                if step >= MAX_TOOL_STEPS:
                    messages.append({
                        "role": "user",
                        "content": (
                            "Maximum investigation steps reached. Respond with ONLY valid JSON: "
                            '{"grade": "A|B|C|D|F", "approved": true|false, "feedback": "..."}'
                        ),
                    })
                    payload = {**payload, "messages": messages, "tools": [], "format": GRADER_VERDICT_SCHEMA}
                    try:
                        content, _ = client.post_chat(payload)
                    except OllamaError as e:
                        return self._build_error(str(e), recorded_tool_calls)
                    break
```

Replace with (keep hint, remove the 5 lines that make the inline call):

```python
                if step >= MAX_TOOL_STEPS:
                    messages.append({
                        "role": "user",
                        "content": (
                            "Maximum investigation steps reached. Respond with ONLY valid JSON: "
                            '{"grade": "A|B|C|D|F", "approved": true|false, "feedback": "..."}'
                        ),
                    })
                    break
```

**Step 2: Replace the post-loop parse/fallback block with always-enforce verdict**

Locate these lines (239–272), starting right after the `for` loop and `else: break`:

```python
        content = _THINK_TAG_RE.sub("", content)
        content = _SPECIAL_TOKEN_RE.sub("", content).strip()

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

Replace with:

```python
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

**Step 3: Run Task 1's test — verify PASS**

```bash
cd commander && python -m pytest tests/test_shadow_grader.py::TestGradeWithTools::test_verdict_call_always_uses_format_schema -v
```

Expected: `PASSED`

**Step 4: Stage**

```bash
git add commander/src/ironclaude/shadow_grader.py
```

---

## Task 3: Fix existing tests that expect the old call count

**Files:**
- Modify: `commander/tests/test_shadow_grader.py`

Three tests used `side_effect` sequences sized for the old call count (one fewer call). One test has a misleading name. Fix all four.

**Step 1: Update `test_single_tool_call_then_verdict` — add 3rd side_effect entry**

Locate (lines 34–37):

```python
        mock_client.post_chat.side_effect = [
            ("", tool_call),
            (verdict, []),
        ]
```

Replace with:

```python
        mock_client.post_chat.side_effect = [
            ("", tool_call),
            (verdict, []),
            (verdict, []),   # post-loop always-enforce verdict call
        ]
```

**Step 2: Update `test_max_tool_steps_forces_final_verdict` — add 7th side_effect entry**

Locate (line 71):

```python
        side_effects = [("", tool_call) for _ in range(5)] + [(verdict, [])]
```

Replace with:

```python
        side_effects = [("", tool_call) for _ in range(5)] + [(verdict, []), (verdict, [])]
```

(Call 6 is the no-tool-call response at step 5; call 7 is the post-loop verdict call.)

**Step 3: Update `test_string_arguments_parsed_to_dict` — add 3rd side_effect entry**

Locate (lines 114–117):

```python
        mock_client.post_chat.side_effect = [
            ("", tool_call),
            (verdict, []),
        ]
```

Replace with:

```python
        mock_client.post_chat.side_effect = [
            ("", tool_call),
            (verdict, []),
            (verdict, []),   # post-loop always-enforce verdict call
        ]
```

**Step 4: Rename `test_non_json_triggers_format_constrained_retry` → `test_format_constrained_verdict_recovers_non_json`**

Locate line 96:

```python
    def test_non_json_triggers_format_constrained_retry(self):
```

Replace with:

```python
    def test_format_constrained_verdict_recovers_non_json(self):
```

(Assertions and side_effect sequence unchanged — call 1 returns non-JSON with no tools → break → post-loop verdict call returns valid JSON. The semantics are now "verdict call always happens, recovers even if first response was bad".)

**Step 5: Run the four modified tests — verify PASS**

```bash
cd commander && python -m pytest tests/test_shadow_grader.py::TestGradeWithTools::test_single_tool_call_then_verdict tests/test_shadow_grader.py::TestGradeWithTools::test_max_tool_steps_forces_final_verdict tests/test_shadow_grader.py::TestGradeWithTools::test_string_arguments_parsed_to_dict tests/test_shadow_grader.py::TestGradeWithTools::test_format_constrained_verdict_recovers_non_json -v
```

Expected: `4 passed`

**Step 6: Stage**

```bash
git add commander/tests/test_shadow_grader.py
```

---

## Task 4: Full test suite verification

**Files:**
- Read: `commander/tests/test_shadow_grader.py`

**Step 1: Run full suite**

```bash
cd commander && python -m pytest tests/test_shadow_grader.py -v
```

Expected: `14 passed` (13 original + 1 new)

**Step 2: Verify `test_non_json_verdict_returns_error` explicitly**

This test uses `return_value` (both calls return `"not json at all"`). Post-loop verdict call also returns non-JSON → `json.loads()` fails → `infrastructure_error`. Confirm it still passes:

```bash
cd commander && python -m pytest tests/test_shadow_grader.py::TestGradeWithTools::test_non_json_verdict_returns_error -v
```

Expected: `PASSED`
