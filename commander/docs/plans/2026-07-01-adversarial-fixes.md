# Adversarial Review Fixes Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** Fix 8 findings (FW-1 through FW-9, excluding FW-8) from the full-week blind adversarial review.

**Architecture:** Direct targeted fixes grouped by file. TDD where applicable: write/update failing test first, then fix implementation, then verify. 5 source files modified, 1 new test file created, 1 data file cleaned up. DO NOT PUSH.

**Tech Stack:** Python, pytest, regex, os.path

---

## Task 1: FW-1 + FW-2 — Shadow Grader Path Validation Fix

**Files:**
- Modify: `commander/src/ironclaude/shadow_grader.py:122-131`
- Modify: `commander/tests/test_shadow_grader.py` (add tests after line ~228)

**Step 1: Write 2 new failing tests (RED)**

Add two tests to `TestExecuteTool` class in `commander/tests/test_shadow_grader.py`, after the last existing test method:

```python
def test_path_prefix_collision_rejected(self, grader, tmp_path):
    """Path matching home dir prefix but different directory is rejected (FW-1)."""
    import os
    home = os.path.expanduser("~")
    evil_path = home + "evil/secret.txt"
    with pytest.raises(ValueError, match="path not under allowed roots"):
        grader._validate_path(evil_path, repo_path=str(tmp_path))

def test_symlink_outside_allowed_roots_rejected(self, grader, tmp_path):
    """Symlink pointing outside allowed roots is rejected (FW-2)."""
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "secret.txt"
    secret.write_text("sensitive")
    inside = tmp_path / "allowed_repo"
    inside.mkdir()
    link = inside / "escape.txt"
    link.symlink_to(secret)
    with pytest.raises(ValueError, match="path not under allowed roots"):
        grader._validate_path(str(link), repo_path=str(inside))
```

**Step 2: Run tests to verify they fail**

Run:
```bash
cd commander && PYTHONUNBUFFERED=1 python -m pytest tests/test_shadow_grader.py::TestExecuteTool::test_path_prefix_collision_rejected tests/test_shadow_grader.py::TestExecuteTool::test_symlink_outside_allowed_roots_rejected -v
```

Expected: FAIL — current code uses `startswith` without `os.sep` and `abspath` without symlink resolution.

**Step 3: Fix `_validate_path` in shadow_grader.py (GREEN)**

In `commander/src/ironclaude/shadow_grader.py`, replace the path validation block (lines ~122-131):

Current:
```python
abs_path = os.path.abspath(path)
allowed = [os.path.expanduser("~"), "/tmp/"]
if repo_path:
    allowed.append(os.path.abspath(repo_path))
if not any(abs_path.startswith(root) for root in allowed):
    raise ValueError("path not under allowed roots")
```

Fixed:
```python
real_path = os.path.realpath(path)
allowed = [os.path.expanduser("~"), "/tmp"]
if repo_path:
    allowed.append(os.path.realpath(repo_path))
if not any(real_path == root or real_path.startswith(root + os.sep) for root in allowed):
    raise ValueError("path not under allowed roots")
```

Changes: (1) `abspath` → `realpath` resolves symlinks (FW-2). (2) `startswith(root)` → `== root or startswith(root + os.sep)` prevents prefix collision (FW-1). (3) `/tmp/` → `/tmp` for uniform handling.

**Step 4: Run tests to verify they pass**

Run:
```bash
cd commander && PYTHONUNBUFFERED=1 python -m pytest tests/test_shadow_grader.py -v
```

Expected: ALL tests pass including new ones.

**Step 5: Stage changes**

Run:
```bash
git -C /Users/roberthyatt/Code/ironclaude add commander/src/ironclaude/shadow_grader.py commander/tests/test_shadow_grader.py
```

---

## Task 2: FW-3 + FW-4 — Complexity Gate Success Check Fix

**Files:**
- Modify: `commander/src/ironclaude/orchestrator_mcp.py:1751`
- Modify: `commander/tests/test_orchestrator_mcp.py` (add test after line 5923)

**Step 1: Write 1 new failing test (RED)**

Add `test_rejects_open_ended_verb_with_unsuccessful` to `TestOllamaComplexityGate` class in `commander/tests/test_orchestrator_mcp.py`, after `test_passes_open_ended_verb_with_success` (after line 5923):

```python
def test_rejects_open_ended_verb_with_unsuccessful(self, tools):
    """'unsuccessful' substring should not bypass the open-ended verb gate (FW-3)."""
    ok, reason = tools._check_ollama_objective_complexity(
        "Analyze why login was unsuccessful"
    )
    assert not ok
    assert "analyze" in reason.lower()
```

Note: existing `test_passes_open_ended_verb_with_success` (line 5917) already uses `"Success: ..."` with colon — it passes with both old and new code. No update needed.

**Step 2: Run test to verify it fails**

Run:
```bash
cd commander && PYTHONUNBUFFERED=1 python -m pytest tests/test_orchestrator_mcp.py::TestOllamaComplexityGate::test_rejects_open_ended_verb_with_unsuccessful -v
```

Expected: FAIL — current substring check `"success" not in obj_lower` returns False because "unsuccessful" contains "success", so the gate passes the objective.

**Step 3: Fix regex in orchestrator_mcp.py (GREEN)**

In `commander/src/ironclaude/orchestrator_mcp.py` at line 1751, replace:

```python
if re.search(rf'\b{verb}\b', obj_lower) and "success" not in obj_lower:
```

With:

```python
if re.search(rf'\b{verb}\b', obj_lower) and not re.search(r'\bsuccess\s*:', obj_lower):
```

The regex `\bsuccess\s*:` matches the `"Success: ..."` pattern used in objectives. It correctly rejects "unsuccessful", "success criteria", "no success" as none contain `success:`.

**Step 4: Run all complexity gate tests to verify**

Run:
```bash
cd commander && PYTHONUNBUFFERED=1 python -m pytest tests/test_orchestrator_mcp.py::TestOllamaComplexityGate -v
```

Expected: ALL 9 tests pass (8 existing + 1 new).

**Step 5: Stage changes**

Run:
```bash
git -C /Users/roberthyatt/Code/ironclaude add commander/src/ironclaude/orchestrator_mcp.py commander/tests/test_orchestrator_mcp.py
```

---

## Task 3: FW-5 — validate_safe_id Regression Test

**Files:**
- Create: `commander/tests/test_protocol.py`

**Step 1: Create test file with 2 tests**

Create `commander/tests/test_protocol.py`:

```python
"""Regression tests for protocol validation functions."""

import pytest
from ironclaude.protocol import validate_safe_id


class TestValidateSafeId:
    def test_accepts_valid_id(self):
        """Valid ID with alphanumerics, hyphens, and underscores does not raise."""
        validate_safe_id("abc-123_DEF")

    def test_rejects_newline(self):
        """ID containing newline raises ValueError (\\Z anchor regression guard)."""
        with pytest.raises(ValueError, match="Unsafe ID rejected"):
            validate_safe_id("valid\n")
```

**Step 2: Run tests to verify they pass**

Run:
```bash
cd commander && PYTHONUNBUFFERED=1 python -m pytest tests/test_protocol.py -v
```

Expected: PASS — tests verify existing correct behavior. No RED phase needed since we are testing existing implementation without changing it.

**Step 3: Stage changes**

Run:
```bash
git -C /Users/roberthyatt/Code/ironclaude add commander/tests/test_protocol.py
```

---

## Task 4: FW-7 — Fallback Timeout Tuple Fix

**Files:**
- Modify: `commander/src/ironclaude/ollama_client.py:84,177`
- Modify: `commander/tests/test_ollama_client.py:39-44,168-172`

Note: The adversarial report identified line 84 (`_chat_via_fallback`), but line 177 (`_post_via_fallback`) has the identical bug. Both are fixed here.

**Step 1: Add timeout tuple assertions to both fallback tests (RED)**

In `commander/tests/test_ollama_client.py`, update `TestPostGenerate.test_primary_fails_uses_fallback` (line 39-44) — add after `assert mock_post.call_count == 2`:

```python
fallback_call = mock_post.call_args_list[1]
assert fallback_call.kwargs["timeout"] == (2, 30)
```

And update `TestPostChat.test_primary_fails_uses_fallback` (line 168-172) — add after `assert mock_post.call_count == 2`:

```python
fallback_call = mock_post.call_args_list[1]
assert fallback_call.kwargs["timeout"] == (2, 30)
```

**Step 2: Run tests to verify they fail**

Run:
```bash
cd commander && PYTHONUNBUFFERED=1 python -m pytest tests/test_ollama_client.py::TestPostGenerate::test_primary_fails_uses_fallback tests/test_ollama_client.py::TestPostChat::test_primary_fails_uses_fallback -v
```

Expected: FAIL — current fallback passes `timeout=30` (int), not `timeout=(2, 30)` (tuple).

**Step 3: Fix both fallback methods (GREEN)**

In `commander/src/ironclaude/ollama_client.py`:

At line 84 (`_chat_via_fallback`), replace:
```python
timeout=self._timeout,
```
With:
```python
timeout=(self._connect_timeout, self._timeout),
```

At line 177 (`_post_via_fallback`), replace:
```python
timeout=self._timeout,
```
With:
```python
timeout=(self._connect_timeout, self._timeout),
```

**Step 4: Run all ollama client tests to verify**

Run:
```bash
cd commander && PYTHONUNBUFFERED=1 python -m pytest tests/test_ollama_client.py -v
```

Expected: ALL tests pass.

**Step 5: Stage changes**

Run:
```bash
git -C /Users/roberthyatt/Code/ironclaude add commander/src/ironclaude/ollama_client.py commander/tests/test_ollama_client.py
```

---

## Task 5: FW-9 — Summarization Model Constant Extraction

**Files:**
- Modify: `commander/src/ironclaude/orchestrator_mcp.py:2794,2799`

No tests required: pure refactor extracting a constant with no behavior change. Existing `TestListClaudeSessions` tests cover this code path.

**Step 1: Extract module-level constant**

Add near the top of `commander/src/ironclaude/orchestrator_mcp.py` (with other module-level constants):

```python
_DEFAULT_SUMMARIZATION_MODEL = "gemma4:9b"
```

**Step 2: Replace both occurrences**

At line 2794, replace:
```python
summarization_model = "gemma4:9b"
```
With:
```python
summarization_model = _DEFAULT_SUMMARIZATION_MODEL
```

At line 2799, replace:
```python
summarization_model = self._ollama_cfg_cache.get("summarization_model", "gemma4:9b")
```
With:
```python
summarization_model = self._ollama_cfg_cache.get("summarization_model", _DEFAULT_SUMMARIZATION_MODEL)
```

**Step 3: Run tests to verify no regression**

Run:
```bash
cd commander && PYTHONUNBUFFERED=1 python -m pytest tests/test_orchestrator_mcp.py::TestListClaudeSessions -v
```

Expected: ALL tests pass.

**Step 4: Stage changes**

Run:
```bash
git -C /Users/roberthyatt/Code/ironclaude add commander/src/ironclaude/orchestrator_mcp.py
```

---

## Task 6: FW-6 — Plan JSON Absolute Path Cleanup

**Files:**
- Modify: `docs/plans/2026-06-20-shadow-grader.plan.json`

No tests required: data file cleanup, no executable code.

**Step 1: Replace absolute paths with relative form**

In `docs/plans/2026-06-20-shadow-grader.plan.json`, replace all 20 instances of:
```
/Users/roberthyatt/Code/ironclaude/commander
```
With:
```
cd commander &&
```

Specifically, command fields like:
```json
"command": "cd /Users/roberthyatt/Code/ironclaude/commander && pytest ..."
```
Become:
```json
"command": "cd commander && pytest ..."
```

**Step 2: Stage changes**

Run:
```bash
git -C /Users/roberthyatt/Code/ironclaude add -f commander/docs/plans/2026-06-20-shadow-grader.plan.json
```

Note: `docs/` is gitignored, requires `git add -f`.

---

## Task 7: Full Test Suite Verification

**Files:** (none modified)

No tests required: verification-only task.

**Step 1: Run full test suite**

Run:
```bash
cd commander && PYTHONUNBUFFERED=1 python -m pytest -v
```

Expected: ALL tests pass. Zero failures, zero errors.

**Step 2: Verify staged changes**

Run:
```bash
git -C /Users/roberthyatt/Code/ironclaude diff --staged --stat
```

Expected: 7 files changed (5 source/test files + 1 new test file + 1 data file).
