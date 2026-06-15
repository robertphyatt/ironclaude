# LLM Grading: Replace Regex/Keyword Semantic Judgment

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** Replace three semantic keyword/regex patterns in `main.py` and `brain_client.py` with LLM grading calls, extracting the Ollama grading logic into a new shared `grader.py` module.

**Architecture:** New `LocalGrader` class in `grader.py` extracts Ollama call logic from `OrchestratorTools._call_local_grader`. `IroncladeDaemon` and `BrainClient` instantiate `LocalGrader` directly; `OrchestratorTools._call_local_grader` delegates to it. Each semantic check replaced with a focused `grade()` call with its own system prompt and JSON schema.

**Tech Stack:** Python 3.11+, `ironclaude.ollama_client.OllamaClient`, `unittest.mock` for tests.

---

## Task 1: Create `grader.py` — LocalGrader foundation

**Files:**
- Create: `commander/src/ironclaude/grader.py`
- Create: `commander/tests/test_grader.py`

**Step 1: Write tests (RED)**

Create `commander/tests/test_grader.py`:

```python
"""Tests for LocalGrader in grader.py."""
from unittest.mock import MagicMock, patch
import pytest
from ironclaude.grader import LocalGrader
from ironclaude.ollama_client import OllamaConnectionError, OllamaTimeoutError


class TestLocalGraderGrade:
    def setup_method(self):
        self.grader = LocalGrader(config_path="/nonexistent/config.json")
        self._inject_mock_client("{}")

    def _inject_mock_client(self, response_text):
        mock = MagicMock()
        mock.post_generate.return_value = response_text
        self.grader._client = mock
        self.grader._cfg = {}

    def test_happy_path_returns_parsed_dict(self):
        self._inject_mock_client('{"valid": true}')
        assert self.grader.grade("sys", "user") == {"valid": True}

    def test_ollama_connection_error_returns_infrastructure_error(self):
        mock = MagicMock()
        mock.post_generate.side_effect = OllamaConnectionError("refused")
        self.grader._client = mock
        self.grader._cfg = {}
        result = self.grader.grade("sys", "user")
        assert result["infrastructure_error"] is True
        assert "refused" in result["error_detail"]

    def test_ollama_timeout_error_returns_infrastructure_error(self):
        mock = MagicMock()
        mock.post_generate.side_effect = OllamaTimeoutError("timed out")
        self.grader._client = mock
        self.grader._cfg = {}
        result = self.grader.grade("sys", "user")
        assert result["infrastructure_error"] is True

    def test_empty_response_returns_infrastructure_error(self):
        self._inject_mock_client("")
        result = self.grader.grade("sys", "user")
        assert result["infrastructure_error"] is True
        assert "empty" in result["error_detail"].lower()

    def test_non_json_response_returns_infrastructure_error(self):
        self._inject_mock_client("this is not json")
        result = self.grader.grade("sys", "user")
        assert result["infrastructure_error"] is True

    def test_think_tags_stripped_before_parse(self):
        self._inject_mock_client('<think>reasoning here</think>{"seeking": false}')
        result = self.grader.grade("sys", "user")
        assert result == {"seeking": False}

    def test_schema_required_field_missing_returns_infrastructure_error(self):
        self._inject_mock_client('{"grade": "A"}')
        schema = {"type": "object", "properties": {}, "required": ["grade", "approved"]}
        result = self.grader.grade("sys", "user", schema)
        assert result["infrastructure_error"] is True
        assert "approved" in result["error_detail"]

    def test_schema_all_required_fields_present_returns_dict(self):
        self._inject_mock_client('{"grade": "A", "approved": true}')
        schema = {"type": "object", "properties": {}, "required": ["grade", "approved"]}
        result = self.grader.grade("sys", "user", schema)
        assert result == {"grade": "A", "approved": True}

    def test_no_schema_skips_required_field_check(self):
        self._inject_mock_client('{"anything": "goes"}')
        assert self.grader.grade("sys", "user", schema=None) == {"anything": "goes"}

    def test_config_absent_uses_localhost_defaults(self):
        grader = LocalGrader(config_path="/absolutely/nonexistent/path.json")
        with patch("ironclaude.grader.OllamaClient") as mock_cls:
            mock_cls.return_value.post_generate.return_value = '{"ok": true}'
            result = grader.grade("sys", "user")
        mock_cls.assert_called_once_with(
            url="http://localhost:11434",
            fallback_url=None,
            timeout=120,
        )
        assert result == {"ok": True}

    def test_all_error_paths_return_consistent_shape(self):
        for exc in [OllamaConnectionError("down"), OllamaTimeoutError("slow")]:
            mock = MagicMock()
            mock.post_generate.side_effect = exc
            self.grader._client = mock
            self.grader._cfg = {}
            result = self.grader.grade("sys", "user")
            assert result["infrastructure_error"] is True
            assert isinstance(result["error_detail"], str)

    def test_grade_never_raises(self):
        mock = MagicMock()
        mock.post_generate.side_effect = OllamaConnectionError("boom")
        self.grader._client = mock
        self.grader._cfg = {}
        result = self.grader.grade("sys", "user")
        assert "infrastructure_error" in result
```

**Step 2: Run tests — expect ImportError (RED)**

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && make test -k test_grader 2>&1 | head -30
```

Expected: `ImportError: No module named 'ironclaude.grader'` or similar collection error.

**Step 3: Implement `grader.py` (GREEN)**

Create `commander/src/ironclaude/grader.py`:

```python
"""Standalone LLM grading via Ollama.

Extracted from OrchestratorTools._call_local_grader so that
IroncladeDaemon (main.py) and BrainClient can grade content without
importing the full orchestrator.
"""

from __future__ import annotations

import json
import logging
import os
import re

from ironclaude.ollama_client import OllamaClient, OllamaError

logger = logging.getLogger("ironclaude.grader")

_DEFAULT_CONFIG_PATH = os.path.expanduser("~/.claude/ironclaude-hooks-config.json")
_DEFAULT_MODEL = "gemma4:12b-it-qat"
_THINK_TAG_RE = re.compile(r"<think>.*?</think>", re.DOTALL)


class LocalGrader:
    """Grade content via a local Ollama model.

    Config loaded lazily from ~/.claude/ironclaude-hooks-config.json on first
    grade() call, falling back to localhost:11434 defaults if absent.
    """

    def __init__(self, config_path: str | None = None) -> None:
        self._config_path = config_path or _DEFAULT_CONFIG_PATH
        self._client: OllamaClient | None = None
        self._cfg: dict = {}

    @staticmethod
    def _build_infrastructure_error(detail: str) -> dict:
        return {"infrastructure_error": True, "error_detail": detail}

    def _get_client(self) -> OllamaClient:
        if self._client is None:
            try:
                with open(self._config_path) as f:
                    cfg = json.load(f)
            except (FileNotFoundError, json.JSONDecodeError) as e:
                logger.warning("Ollama config unavailable (%s): using localhost defaults", e)
                cfg = {}
            ollama_cfg = cfg.get("ollama", {})
            self._cfg = ollama_cfg
            self._client = OllamaClient(
                url=ollama_cfg.get("url", "http://localhost:11434"),
                fallback_url=ollama_cfg.get("fallback_url"),
                timeout=cfg.get("timeout_seconds", 120),
            )
        return self._client

    def grade(self, system_prompt: str, user_prompt: str, schema: dict | None = None) -> dict:
        """Grade content via Ollama. Returns parsed dict or infrastructure_error dict. Never raises."""
        client = self._get_client()
        model = self._cfg.get("model", _DEFAULT_MODEL)
        payload: dict = {
            "model": model,
            "prompt": f"{system_prompt}\n\n{user_prompt}",
            "stream": False,
            "options": {"temperature": 0.1, "num_predict": -1},
        }
        if schema is not None:
            payload["format"] = schema
        try:
            result_text = client.post_generate(payload)
        except OllamaError as e:
            return self._build_infrastructure_error(str(e))
        if not result_text:
            return self._build_infrastructure_error("Ollama returned empty response")
        result_text = _THINK_TAG_RE.sub("", result_text).strip()
        try:
            parsed = json.loads(result_text)
        except json.JSONDecodeError:
            return self._build_infrastructure_error(
                f"Non-JSON response ({len(result_text)} chars): {result_text[:200]}"
            )
        if schema:
            required = schema.get("required", [])
            missing = [k for k in required if k not in parsed]
            if missing:
                return self._build_infrastructure_error(
                    f"Response missing required fields {missing}: {result_text[:200]}"
                )
        return parsed
```

**Step 4: Run tests — expect all pass (GREEN)**

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && make test -k test_grader 2>&1 | tail -20
```

Expected: All 12 tests pass, 0 failures.

**Step 5: Stage changes**

```bash
git add commander/src/ironclaude/grader.py commander/tests/test_grader.py
```

Expected: Files staged (professional mode blocks commit).

---

## Task 2: Refactor `orchestrator_mcp.py` — delegate `_call_local_grader`

**Files:**
- Modify: `commander/src/ironclaude/orchestrator_mcp.py`
- Modify: `commander/tests/test_orchestrator_mcp.py`

**Step 1: Write delegation test (RED)**

Add this class to `commander/tests/test_orchestrator_mcp.py` (append after the last test class):

```python
class TestCallLocalGraderDelegation:
    def test_delegates_to_local_grader(self, tools):
        mock_grade = MagicMock(return_value={"grade": "A", "approved": True})
        tools._local_grader = MagicMock()
        tools._local_grader.grade = mock_grade
        schema = {"type": "object"}
        result = tools._call_local_grader("sys_prompt", "user_prompt", schema)
        mock_grade.assert_called_once_with("sys_prompt", "user_prompt", schema)
        assert result == {"grade": "A", "approved": True}
```

**Step 2: Run test — expect AttributeError (RED)**

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && make test -k TestCallLocalGraderDelegation 2>&1 | tail -20
```

Expected: `AttributeError: 'OrchestratorTools' object has no attribute '_local_grader'`

**Step 3: Add import and `_local_grader` to `orchestrator_mcp.py`**

In `commander/src/ironclaude/orchestrator_mcp.py`, add import after line 35 (`from ironclaude.ollama_client import OllamaClient, OllamaError`):

```python
from ironclaude.grader import LocalGrader
```

In `OrchestratorTools.__init__` (around line 397, after `self._ollama_config_path = ...`), add:

```python
self._local_grader = LocalGrader(config_path=self._ollama_config_path)
```

**Step 4: Replace `_call_local_grader` body (lines 729–777)**

Replace the entire `_call_local_grader` method body with:

```python
def _call_local_grader(self, system_prompt: str, user_prompt: str, format_schema: dict) -> dict:
    """Call Ollama for local grading via LocalGrader.

    Returns grading result dict on success, or
    {"infrastructure_error": True, "error_detail": "..."} on infrastructure failures.
    Callers must check for infrastructure_error and handle accordingly.
    """
    return self._local_grader.grade(system_prompt, user_prompt, format_schema)
```

**Step 5: Run all orchestrator tests (GREEN)**

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && make test -k test_orchestrator_mcp 2>&1 | tail -20
```

Expected: All existing tests pass plus `TestCallLocalGraderDelegation::test_delegates_to_local_grader` passes.

**Step 6: Stage changes**

```bash
git add commander/src/ironclaude/orchestrator_mcp.py commander/tests/test_orchestrator_mcp.py
```

---

## Task 3: Replace semantic checks in `main.py`

**Files:**
- Modify: `commander/src/ironclaude/main.py`
- Create: `commander/tests/test_main_validate.py`

**Step 1: Write tests for new behavior (RED)**

Create `commander/tests/test_main_validate.py`:

```python
"""Tests for LLM-graded semantic checks in IroncladeDaemon."""
import time
from unittest.mock import MagicMock, patch
import pytest
from ironclaude.main import IroncladeDaemon


def _make_daemon():
    """Minimal IroncladeDaemon with mocked dependencies."""
    config = {"tmp_dir": "/tmp/ic-test"}
    daemon = IroncladeDaemon.__new__(IroncladeDaemon)
    daemon._grader = MagicMock()
    daemon._prompt_waiting_cache = {}
    return daemon


class TestValidateBrainMessage:
    def test_empty_message_returns_invalid(self):
        d = _make_daemon()
        valid, reason = d._validate_brain_message("")
        assert valid is False
        assert "Empty" in reason
        d._grader.grade.assert_not_called()

    def test_whitespace_only_returns_invalid(self):
        d = _make_daemon()
        valid, reason = d._validate_brain_message("   \n  ")
        assert valid is False

    def test_grader_valid_true_returns_true(self):
        d = _make_daemon()
        d._grader.grade.return_value = {"valid": True}
        valid, reason = d._validate_brain_message("d1083: completed task")
        assert valid is True
        assert reason == ""

    def test_grader_valid_false_returns_false_with_reason(self):
        d = _make_daemon()
        d._grader.grade.return_value = {"valid": False, "reason": "Missing directive"}
        valid, reason = d._validate_brain_message("The tests are passing.")
        assert valid is False
        assert reason == "Missing directive"

    def test_grader_valid_false_no_reason_returns_fallback(self):
        d = _make_daemon()
        d._grader.grade.return_value = {"valid": False}
        valid, reason = d._validate_brain_message("Some message.")
        assert valid is False
        assert reason  # non-empty fallback

    def test_infrastructure_error_fails_open(self):
        d = _make_daemon()
        d._grader.grade.return_value = {"infrastructure_error": True, "error_detail": "Ollama down"}
        valid, reason = d._validate_brain_message("d1083: completed task")
        assert valid is True
        assert reason == ""


class TestDetectPromptWaiting:
    def test_grader_waiting_true_returns_true(self):
        d = _make_daemon()
        d._grader.grade.return_value = {"waiting": True}
        assert d._detect_prompt_waiting("AskUserQuestion\nWhich approach?") is True

    def test_grader_waiting_false_returns_false(self):
        d = _make_daemon()
        d._grader.grade.return_value = {"waiting": False}
        assert d._detect_prompt_waiting("Running tests...") is False

    def test_infrastructure_error_fails_safe_returns_false(self):
        d = _make_daemon()
        d._grader.grade.return_value = {"infrastructure_error": True, "error_detail": "down"}
        assert d._detect_prompt_waiting("some log content") is False

    def test_infrastructure_error_not_cached(self):
        d = _make_daemon()
        d._grader.grade.return_value = {"infrastructure_error": True, "error_detail": "down"}
        log_tail = "some log content"
        d._detect_prompt_waiting(log_tail)
        d._detect_prompt_waiting(log_tail)
        assert d._grader.grade.call_count == 2  # not cached

    def test_cache_hit_skips_grader(self):
        d = _make_daemon()
        d._grader.grade.return_value = {"waiting": True}
        log_tail = "AskUserQuestion visible"
        d._detect_prompt_waiting(log_tail)
        d._detect_prompt_waiting(log_tail)
        assert d._grader.grade.call_count == 1  # second call uses cache

    def test_cache_expires_after_ttl(self):
        d = _make_daemon()
        d._grader.grade.return_value = {"waiting": True}
        log_tail = "some log"
        d._detect_prompt_waiting(log_tail)
        # Force expiry by backdating cache entry
        cache_key = hash(log_tail)
        ts, result = d._prompt_waiting_cache[cache_key]
        d._prompt_waiting_cache[cache_key] = (ts - 200, result)  # TTL=120s, so 200s ago = expired
        d._detect_prompt_waiting(log_tail)
        assert d._grader.grade.call_count == 2

    def test_log_tail_truncated_to_2000_chars(self):
        d = _make_daemon()
        d._grader.grade.return_value = {"waiting": False}
        long_log = "x" * 5000
        d._detect_prompt_waiting(long_log)
        call_args = d._grader.grade.call_args
        user_prompt = call_args[0][1]  # second positional arg
        assert len(user_prompt) <= 2010  # 2000 chars + small prefix
```

**Step 2: Run tests — expect ImportError or AttributeError (RED)**

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && make test -k test_main_validate 2>&1 | tail -20
```

Expected: Collection errors or `AttributeError: 'IroncladeDaemon' object has no attribute '_grader'`

**Step 3: Add import to `main.py`**

In `commander/src/ironclaude/main.py`, add after line 41 (`from ironclaude.plugins import PluginRegistry, discover_plugins`):

```python
from ironclaude.grader import LocalGrader
```

**Step 4: Add grading constants to `main.py`**

Add after line 43 (`logger = logging.getLogger("ironclaude")`), before `CHECKIN_CADENCE`:

```python
_BRAIN_MSG_VALIDATION_SYSTEM = (
    "You validate Brain messages before they are posted to Slack.\n\n"
    "A valid Brain message must have BOTH of the following:\n"
    "1. A directive reference — any of: #N, dN, or 'directive N' (e.g. #1083, d1076, directive 42)\n"
    "2. A reason clause — text explaining what the Brain is reporting (status, result, error, progress, update)\n\n"
    "Examples of VALID messages:\n"
    '- "d1083: Completed the auth refactor. All tests passing."\n'
    '- "Working on #1074 — stuck on Docker connectivity issue, investigating now."\n'
    '- "Directive 42 complete. Merged PR #89 to main."\n\n'
    "Examples of INVALID messages:\n"
    '- "What should I do next?" (no directive reference, permission-seeking)\n'
    '- "The tests are passing." (no directive reference)\n'
    '- "d1083" (directive reference present but no reason clause)\n\n'
    "Respond ONLY with valid JSON:\n"
    '{"valid": true} if the message has both a directive reference and a reason clause\n'
    '{"valid": false, "reason": "specific reason why invalid"} if either is missing'
)
_BRAIN_MSG_SCHEMA = {
    "type": "object",
    "properties": {"valid": {"type": "boolean"}, "reason": {"type": "string"}},
    "required": ["valid"],
}

_PROMPT_WAITING_SYSTEM = (
    "You detect whether a worker process is waiting for user input by examining its terminal log output.\n\n"
    "A worker is WAITING FOR INPUT if its recent output contains:\n"
    "- AskUserQuestion tool output (structured question/options UI)\n"
    '- "Submit answers" prompt\n'
    '- "options:" field with numbered choices\n'
    "- Explicit questions directed at the user (e.g. 'Which approach', 'How would you like')\n"
    "- Numbered option lists (1., 2., 3.) presented as choices\n"
    '- "question:" field in structured prompt UI\n\n'
    "A worker is NOT waiting for input if:\n"
    "- Output shows active tool execution (file reads, edits, bash commands)\n"
    "- Output is code, test results, or log data\n"
    "- Output is a status update, report, or error message\n"
    "- Output ended with an autonomous action (edit saved, command ran)\n\n"
    "Focus only on the MOST RECENT portion of the log.\n\n"
    "Respond ONLY with valid JSON: {\"waiting\": true} or {\"waiting\": false}"
)
_PROMPT_WAITING_SCHEMA = {
    "type": "object",
    "properties": {"waiting": {"type": "boolean"}},
    "required": ["waiting"],
}

PROMPT_WAITING_CACHE_TTL = 120  # seconds
```

**Step 5: Remove old regex constants from `main.py`**

Delete lines 86–103 (the `PROMPT_PATTERNS`, `_DIRECTIVE_REF_RE`, and `_REASON_KEYWORDS` definitions):

```python
# DELETE these lines:
PROMPT_PATTERNS = [
    "AskUserQuestion",
    "Submit answers",
    "options:",
    "Which approach",
    "How would you like",
    "question:",
    re.compile(r"^\s*[1-4]\.", re.MULTILINE),
]

_DIRECTIVE_REF_RE = re.compile(r'(?:#|d|directive\s*#?)\d+', re.IGNORECASE)

_REASON_KEYWORDS = frozenset({
    'because', 'status', 'update', 'completed', 'blocked', 'spawning',
    'investigating', 'working on', 'progress', 'finished', 'started',
    'failed', 'error', 'waiting', 'result', 'found', 'fixed',
    'implementing', 'reviewing', 'testing', 'deployed', 'merged',
})
```

**Step 6: Add `_grader` and `_prompt_waiting_cache` to `IroncladeDaemon.__init__`**

In `commander/src/ironclaude/main.py`, add before `self._load_staleness_state()` at line 557:

```python
        self._grader = LocalGrader()
        self._prompt_waiting_cache: dict[int, tuple[float, bool]] = {}
```

**Step 7: Replace `_validate_brain_message` (lines 590–603)**

Replace the entire method:

```python
    def _validate_brain_message(self, text: str) -> tuple[bool, str]:
        """Validate Brain message is contextually appropriate via LLM grading."""
        if not text.strip():
            return False, "Empty message"
        result = self._grader.grade(
            _BRAIN_MSG_VALIDATION_SYSTEM,
            f"Validate this Brain message:\n{text}",
            _BRAIN_MSG_SCHEMA,
        )
        if result.get("infrastructure_error"):
            logger.warning(
                "Brain message validator unavailable: %s — allowing through",
                result.get("error_detail"),
            )
            return True, ""
        if result.get("valid", True):
            return True, ""
        return False, result.get("reason", "Message does not meet Brain message requirements")
```

**Step 8: Replace `_detect_prompt_waiting` (lines 1411–1419)**

Replace the entire method:

```python
    def _detect_prompt_waiting(self, log_tail: str) -> bool:
        """Determine via LLM whether worker log tail shows prompt-waiting state."""
        cache_key = hash(log_tail)
        cached = self._prompt_waiting_cache.get(cache_key)
        if cached is not None:
            ts, result = cached
            if time.time() - ts < PROMPT_WAITING_CACHE_TTL:
                return result
        result_dict = self._grader.grade(
            _PROMPT_WAITING_SYSTEM,
            f"Worker log tail:\n{log_tail[-2000:]}",
            _PROMPT_WAITING_SCHEMA,
        )
        if result_dict.get("infrastructure_error"):
            logger.debug(
                "Prompt-waiting check unavailable: %s — defaulting to False",
                result_dict.get("error_detail"),
            )
            return False
        waiting = bool(result_dict.get("waiting", False))
        self._prompt_waiting_cache[cache_key] = (time.time(), waiting)
        return waiting
```

**Step 9: Run tests (GREEN)**

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && make test -k test_main_validate 2>&1 | tail -20
```

Expected: All 11 tests pass.

**Step 10: Run full test suite to verify no regressions**

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && make test 2>&1 | tail -30
```

Expected: All tests pass.

**Step 11: Stage changes**

```bash
git add commander/src/ironclaude/main.py commander/tests/test_main_validate.py
```

---

## Task 4: Replace semantic check in `brain_client.py`

**Files:**
- Modify: `commander/src/ironclaude/brain_client.py`
- Modify: `commander/tests/test_brain_client.py`

**Step 1: Write tests for new behavior (RED)**

Append this class to `commander/tests/test_brain_client.py`:

```python
class TestCheckPermissionSeeking:
    def setup_method(self):
        self.client = BrainClient()
        self.client._grader = MagicMock()
        self.client._permission_correction_timestamps = []

    def test_seeking_false_returns_none(self):
        self.client._grader.grade.return_value = {"seeking": False}
        assert self.client._check_permission_seeking("Running the tests now.") is None

    def test_seeking_true_below_throttle_returns_correction(self):
        self.client._grader.grade.return_value = {"seeking": True}
        result = self.client._check_permission_seeking("Shall I proceed?")
        assert result == BrainClient.CORRECTION_MESSAGE

    def test_seeking_true_at_throttle_limit_returns_none(self):
        now = time.time()
        self.client._grader.grade.return_value = {"seeking": True}
        self.client._permission_correction_timestamps = [now, now, now]  # at limit (3)
        assert self.client._check_permission_seeking("Shall I continue?") is None

    def test_infrastructure_error_fails_open_returns_none(self):
        self.client._grader.grade.return_value = {
            "infrastructure_error": True, "error_detail": "Ollama down"
        }
        assert self.client._check_permission_seeking("Shall I proceed?") is None

    def test_correction_appended_to_timestamps(self):
        self.client._grader.grade.return_value = {"seeking": True}
        self.client._check_permission_seeking("Want me to investigate?")
        assert len(self.client._permission_correction_timestamps) == 1

    def test_expired_timestamps_pruned_before_throttle_check(self):
        now = time.time()
        old_time = now - 700  # older than 600s window
        self.client._permission_correction_timestamps = [old_time, old_time, old_time]
        self.client._grader.grade.return_value = {"seeking": True}
        result = self.client._check_permission_seeking("Shall I proceed?")
        assert result == BrainClient.CORRECTION_MESSAGE
```

**Step 2: Run tests — expect AttributeError (RED)**

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && make test -k TestCheckPermissionSeeking 2>&1 | tail -20
```

Expected: `AttributeError: 'BrainClient' object has no attribute '_grader'`

**Step 3: Add import to `brain_client.py`**

In `commander/src/ironclaude/brain_client.py`, add after line 23 (`from pathlib import Path`):

```python
from ironclaude.grader import LocalGrader
```

**Step 4: Add grading constants to `brain_client.py`**

Add after line 26 (`logger = logging.getLogger("ironclaude.brain")`):

```python
_PERMISSION_SEEKING_SYSTEM = (
    "You detect whether a Brain response contains permission-seeking language.\n\n"
    "Permission-seeking means the Brain is asking the user for approval or confirmation "
    "before acting, instead of acting autonomously.\n\n"
    "Examples of PERMISSION-SEEKING (seeking=true):\n"
    '- "Shall I proceed with the refactor?"\n'
    '- "Would you like me to update the config?"\n'
    '- "Should I spawn a worker for this?"\n'
    '- "Do you want me to run the tests now?"\n'
    '- "Let me know if you want me to continue."\n\n'
    "Examples of NOT permission-seeking (seeking=false):\n"
    '- "Running the tests now."\n'
    '- "Spawning a worker for task d1083."\n'
    '- "d1083: Completed the auth refactor."\n'
    '- "What is the current status of the deployment?" (asking for info, not permission)\n\n'
    "Focus especially on the FINAL SENTENCE — that is where permission-seeking appears most often.\n\n"
    'Respond ONLY with valid JSON: {"seeking": true} if permission-seeking, {"seeking": false} if not.'
)
_PERMISSION_SEEKING_SCHEMA = {
    "type": "object",
    "properties": {"seeking": {"type": "boolean"}},
    "required": ["seeking"],
}
```

**Step 5: Remove `_PERMISSION_SEEKING_RE` from `BrainClient`**

In `commander/src/ironclaude/brain_client.py`, delete lines 66–77:

```python
# DELETE these lines:
    _PERMISSION_SEEKING_RE = re.compile(
        r'\bshall I\b'
        r'|\bshould I\b'
        r'|\bwould you like me to\b'
        r'|\bdo you want\b'
        r'|\bwant me to\b'
        r'|\blet me know if\b'
        r'|\bwould you like\b'
        r'|\bshall we\b'
        r'|\bshould we\b',
        re.IGNORECASE,
    )
```

**Step 6: Add `self._grader` to `BrainClient.__init__`**

In `commander/src/ironclaude/brain_client.py`, add after line 151 (`self._previous_session_context: str | None = None`):

```python
        self._grader = LocalGrader()
```

**Step 7: Replace `_check_permission_seeking` (lines 479–504)**

Replace the entire method:

```python
    def _check_permission_seeking(self, text: str) -> str | None:
        """Detect permission-seeking language in a brain response via LLM grading.

        Enforces a 3-per-10-minute throttle and returns the correction string if
        action should be taken, or None otherwise.
        """
        result = self._grader.grade(
            _PERMISSION_SEEKING_SYSTEM,
            f"Brain response to evaluate:\n{text}",
            _PERMISSION_SEEKING_SCHEMA,
        )
        if result.get("infrastructure_error"):
            logger.debug("Permission-seeking check unavailable: %s", result.get("error_detail"))
            return None
        if not result.get("seeking", False):
            return None
        now = time.time()
        cutoff = now - self.PERMISSION_CORRECTION_WINDOW
        self._permission_correction_timestamps = [
            t for t in self._permission_correction_timestamps if t >= cutoff
        ]
        if len(self._permission_correction_timestamps) >= self.MAX_PERMISSION_CORRECTIONS:
            logger.info("Permission-seeking detected but correction throttled (limit reached)")
            return None
        self._permission_correction_timestamps.append(now)
        logger.info("Permission-seeking detected in brain response, sending correction")
        return self.CORRECTION_MESSAGE
```

**Step 8: Run new tests (GREEN)**

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && make test -k TestCheckPermissionSeeking 2>&1 | tail -20
```

Expected: All 6 tests pass.

**Step 9: Run full test suite to verify no regressions**

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && make test 2>&1 | tail -30
```

Expected: All tests pass.

**Step 10: Stage changes**

```bash
git add commander/src/ironclaude/brain_client.py commander/tests/test_brain_client.py
```
