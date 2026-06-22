# Shadow Grader Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** Add gemma4 shadow grading (with tool-calling) to all Opus grading events and post concordance reports to Slack.

**Architecture:** Authoritative Opus grader gains tool-use permission (Read/Bash before verdict). After each Opus call, a daemon thread runs ShadowGrader (gemma4 via Ollama chat API + tool execution loop) and posts a concordance report headlined by tool call sequences. Zero latency added to critical path.

**Tech Stack:** Python threading, Ollama `/api/chat`, subprocess (rg, git), MagicMock pytest tests.

---

## Task 1: OllamaClient.post_chat

**Files:**
- Modify: `commander/src/ironclaude/ollama_client.py`
- Modify: `commander/tests/test_ollama_client.py`

**Step 1: Write tests for post_chat**

Add class `TestPostChat` to `commander/tests/test_ollama_client.py` after the existing `TestCreateModel` class:

```python
def _make_chat_response(content="", tool_calls=None):
    """Build a mock Ollama /api/chat response."""
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    msg = {"role": "assistant", "content": content}
    if tool_calls is not None:
        msg["tool_calls"] = tool_calls
    resp.json.return_value = {"message": msg, "done": True}
    return resp


class TestPostChat:
    @patch("ironclaude.ollama_client.requests.post")
    def test_success_no_tool_calls(self, mock_post, client):
        mock_post.return_value = _make_chat_response(content='{"grade": "A"}')
        content, tcs = client.post_chat({"model": "gemma4", "messages": [], "stream": False})
        assert content == '{"grade": "A"}'
        assert tcs == []

    @patch("ironclaude.ollama_client.requests.post")
    def test_success_with_tool_calls(self, mock_post, client):
        raw_tcs = [{"function": {"name": "read_file", "arguments": {"path": "/foo"}}}]
        mock_post.return_value = _make_chat_response(tool_calls=raw_tcs)
        content, tcs = client.post_chat({"model": "gemma4", "messages": [], "stream": False})
        assert tcs == [{"name": "read_file", "arguments": {"path": "/foo"}}]

    @patch("ironclaude.ollama_client.requests.post")
    def test_posts_to_api_chat(self, mock_post, client_no_fallback):
        mock_post.return_value = _make_chat_response()
        client_no_fallback.post_chat({"model": "gemma4", "messages": [{"role": "user", "content": "hi"}], "stream": False})
        args, kwargs = mock_post.call_args
        assert args[0] == "http://primary:11434/api/chat"

    @patch("ironclaude.ollama_client.requests.post")
    def test_primary_fails_uses_fallback(self, mock_post, client):
        mock_post.side_effect = [requests.ConnectionError("refused"), _make_chat_response(content="ok")]
        content, _ = client.post_chat({"model": "gemma4", "messages": [], "stream": False})
        assert content == "ok"
        assert mock_post.call_count == 2

    @patch("ironclaude.ollama_client.requests.post")
    def test_both_fail_raises(self, mock_post, client):
        mock_post.side_effect = requests.ConnectionError("refused")
        with pytest.raises(OllamaConnectionError):
            client.post_chat({"model": "gemma4", "messages": [], "stream": False})

    @patch("ironclaude.ollama_client.requests.post")
    def test_timeout_raises(self, mock_post, client_no_fallback):
        mock_post.side_effect = requests.Timeout()
        with pytest.raises(OllamaTimeoutError):
            client_no_fallback.post_chat({"model": "gemma4", "messages": [], "stream": False})
```

**Step 2: Run tests to confirm they fail**

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && .venv/bin/pytest tests/test_ollama_client.py::TestPostChat -v 2>&1 | tail -20
```

Expected: `ERRORS` — `AttributeError: 'OllamaClient' object has no attribute 'post_chat'`

**Step 3: Implement post_chat in OllamaClient**

In `commander/src/ironclaude/ollama_client.py`, add `post_chat` and `_read_chat_response` and `_chat_via_fallback` methods. Insert after `post_generate` (after line 51):

```python
    def post_chat(self, payload: dict) -> tuple:
        """POST /api/chat. Returns (response_content_str, tool_calls_list).

        tool_calls_list is a list of {"name": str, "arguments": dict} dicts.
        Returns raw content without think-tag stripping (caller's responsibility).
        Raises OllamaConnectionError or OllamaTimeoutError on failure.
        """
        timeout = (self._connect_timeout, self._timeout)
        try:
            resp = requests.post(
                f"{self._url}/api/chat",
                json=payload,
                timeout=timeout,
                stream=False,
            )
            resp.raise_for_status()
            return self._read_chat_response(resp)
        except (requests.ConnectionError, requests.HTTPError) as e:
            if self._fallback_url:
                return self._chat_via_fallback(payload, str(e))
            raise OllamaConnectionError(f"Ollama unreachable at {self._url}: {e}") from e
        except requests.Timeout:
            raise OllamaTimeoutError(f"Ollama timed out after {self._timeout}s")
        except requests.RequestException as e:
            raise OllamaConnectionError(f"Ollama request failed: {e}") from e

    def _chat_via_fallback(self, payload: dict, primary_error: str) -> tuple:
        try:
            resp = requests.post(
                f"{self._fallback_url}/api/chat",
                json=payload,
                timeout=self._timeout,
                stream=False,
            )
            resp.raise_for_status()
            return self._read_chat_response(resp)
        except requests.RequestException as e2:
            raise OllamaConnectionError(
                f"Ollama failed at {self._url} (and fallback {self._fallback_url}): {e2}"
            ) from e2

    @staticmethod
    def _read_chat_response(resp) -> tuple:
        """Parse /api/chat response into (content_str, tool_calls_list)."""
        data = resp.json()
        msg = data.get("message", {})
        content = msg.get("content", "")
        tool_calls_raw = msg.get("tool_calls") or []
        tool_calls = []
        for tc in tool_calls_raw:
            fn = tc.get("function", {})
            tool_calls.append({
                "name": fn.get("name", ""),
                "arguments": fn.get("arguments", {}),
            })
        return content, tool_calls
```

**Step 4: Run tests to confirm they pass**

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && .venv/bin/pytest tests/test_ollama_client.py -v 2>&1 | tail -20
```

Expected: All `TestPostChat` tests PASS. All pre-existing tests PASS.

**Step 5: Stage changes**

```bash
git add commander/src/ironclaude/ollama_client.py commander/tests/test_ollama_client.py
```

Expected: Files staged (professional mode blocks commit).

---

## Task 2: ShadowGrader class

**Files:**
- Create: `commander/src/ironclaude/shadow_grader.py`
- Create: `commander/tests/test_shadow_grader.py`

**Step 1: Write tests for ShadowGrader**

Create `commander/tests/test_shadow_grader.py`:

```python
"""Unit tests for ShadowGrader (Ollama chat-based grading with tool calling)."""
import json
import subprocess
import pytest
from unittest.mock import MagicMock, patch, mock_open

from ironclaude.shadow_grader import ShadowGrader
from ironclaude.ollama_client import OllamaConnectionError, OllamaTimeoutError


def _make_shadow_grader():
    """ShadowGrader with pre-injected mock OllamaClient."""
    grader = ShadowGrader(config_path="/nonexistent/config.json")
    mock_client = MagicMock()
    grader._client = mock_client
    grader._model = "gemma4:12b-it-qat"
    return grader, mock_client


class TestGradeWithTools:
    def test_no_tool_calls_parses_json_verdict(self):
        grader, mock_client = _make_shadow_grader()
        verdict_json = '{"grade": "B", "approved": true, "feedback": "looks good"}'
        mock_client.post_chat.return_value = (verdict_json, [])
        result = grader.grade_with_tools("sys", "user")
        assert result["grade"] == "B"
        assert result["approved"] is True
        assert result["tool_calls"] == []

    def test_single_tool_call_then_verdict(self):
        grader, mock_client = _make_shadow_grader()
        tool_call = [{"name": "read_file", "arguments": {"path": "/tmp/test.txt"}}]
        verdict = '{"grade": "A", "approved": true, "feedback": "verified"}'
        mock_client.post_chat.side_effect = [
            ("", tool_call),         # first call: tool call
            (verdict, []),            # second call: final verdict
        ]
        grader._execute_tool = MagicMock(return_value="file contents here")
        result = grader.grade_with_tools("sys", "user", repo_path="/tmp")
        assert result["grade"] == "A"
        assert len(result["tool_calls"]) == 1
        assert result["tool_calls"][0]["name"] == "read_file"

    def test_infrastructure_error_returns_error_dict(self):
        grader, mock_client = _make_shadow_grader()
        mock_client.post_chat.side_effect = OllamaConnectionError("refused")
        result = grader.grade_with_tools("sys", "user")
        assert result["infrastructure_error"] is True
        assert "refused" in result["error_detail"]
        assert result["tool_calls"] == []

    def test_non_json_verdict_returns_error(self):
        grader, mock_client = _make_shadow_grader()
        mock_client.post_chat.return_value = ("not json at all", [])
        result = grader.grade_with_tools("sys", "user")
        assert result["infrastructure_error"] is True
        assert result["tool_calls"] == []

    def test_think_tags_stripped_before_parse(self):
        grader, mock_client = _make_shadow_grader()
        response = '<think>reasoning</think>{"grade": "C", "approved": false, "feedback": "missing evidence"}'
        mock_client.post_chat.return_value = (response, [])
        result = grader.grade_with_tools("sys", "user")
        assert result["grade"] == "C"
        assert result["approved"] is False

    def test_max_tool_steps_forces_final_verdict(self):
        grader, mock_client = _make_shadow_grader()
        tool_call = [{"name": "read_file", "arguments": {"path": "/tmp/f"}}]
        # First MAX_TOOL_STEPS calls return tool calls; last call returns verdict
        verdict = '{"grade": "B", "approved": true, "feedback": "done"}'
        side_effects = [(("", tool_call)) for _ in range(5)] + [(verdict, [])]
        mock_client.post_chat.side_effect = side_effects
        grader._execute_tool = MagicMock(return_value="content")
        result = grader.grade_with_tools("sys", "user", repo_path="/tmp")
        assert result["grade"] == "B"
        assert len(result["tool_calls"]) == 5

    def test_missing_grade_field_returns_infrastructure_error(self):
        grader, mock_client = _make_shadow_grader()
        mock_client.post_chat.return_value = ('{"approved": true, "feedback": "ok"}', [])
        result = grader.grade_with_tools("sys", "user")
        assert result["infrastructure_error"] is True
        assert "missing" in result["error_detail"].lower()


class TestExecuteTool:
    def test_read_file_returns_content(self):
        grader = ShadowGrader(config_path="/nonexistent/config.json")
        with patch("builtins.open", mock_open(read_data="file content")):
            result = grader._execute_tool("read_file", {"path": "/tmp/test.txt"}, "/tmp")
        assert "file content" in result

    def test_path_traversal_rejected(self):
        grader = ShadowGrader(config_path="/nonexistent/config.json")
        result = grader._execute_tool("read_file", {"path": "/tmp/../etc/passwd"}, "/tmp")
        parsed = json.loads(result)
        assert "error" in parsed
        assert "traversal" in parsed["error"].lower()

    def test_path_outside_allowed_roots_rejected(self):
        grader = ShadowGrader(config_path="/nonexistent/config.json")
        result = grader._execute_tool("read_file", {"path": "/etc/hosts"}, "/tmp/myrepo")
        parsed = json.loads(result)
        assert "error" in parsed

    def test_grep_files_runs_rg(self):
        grader = ShadowGrader(config_path="/nonexistent/config.json")
        mock_result = MagicMock()
        mock_result.stdout = "match1\nmatch2\n"
        with patch("subprocess.run", return_value=mock_result):
            result = grader._execute_tool("grep_files", {"pattern": "TODO", "directory": "/tmp"}, "/tmp")
        assert "match1" in result

    def test_git_diff_runs_git(self):
        grader = ShadowGrader(config_path="/nonexistent/config.json")
        mock_result = MagicMock()
        mock_result.stdout = "diff --git a/file.py b/file.py\n+new line\n"
        with patch("subprocess.run", return_value=mock_result):
            result = grader._execute_tool("git_diff", {"repo_path": "/tmp"}, "/tmp")
        assert "diff" in result

    def test_unknown_tool_returns_error_json(self):
        grader = ShadowGrader(config_path="/nonexistent/config.json")
        result = grader._execute_tool("nonexistent_tool", {}, None)
        parsed = json.loads(result)
        assert "error" in parsed
        assert "unknown" in parsed["error"].lower()
```

**Step 2: Run tests to confirm they fail**

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && .venv/bin/pytest tests/test_shadow_grader.py -v 2>&1 | tail -20
```

Expected: `ModuleNotFoundError: No module named 'ironclaude.shadow_grader'`

**Step 3: Create shadow_grader.py**

Create `commander/src/ironclaude/shadow_grader.py`:

```python
"""Shadow grading via Ollama chat API with tool-calling support."""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess

from ironclaude.ollama_client import OllamaClient, OllamaError

logger = logging.getLogger(__name__)

_DEFAULT_CONFIG_PATH = os.path.expanduser("~/.claude/ironclaude-hooks-config.json")
_DEFAULT_SHADOW_MODEL = "gemma4:12b-it-qat"
_THINK_TAG_RE = re.compile(r"<think>.*?</think>", re.DOTALL)
_SPECIAL_TOKEN_RE = re.compile(r"<\|[^>]*>")

SHADOW_TOOLS = [
    {"type": "function", "function": {
        "name": "read_file",
        "description": "Read a file by absolute path to examine its contents before grading",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Absolute file path"}},
            "required": ["path"],
        },
    }},
    {"type": "function", "function": {
        "name": "grep_files",
        "description": "Search for a text pattern in files under a directory",
        "parameters": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string"},
                "directory": {"type": "string", "description": "Absolute directory path"},
            },
            "required": ["pattern", "directory"],
        },
    }},
    {"type": "function", "function": {
        "name": "git_diff",
        "description": "Show uncommitted changes in a git repository",
        "parameters": {
            "type": "object",
            "properties": {"repo_path": {"type": "string", "description": "Absolute repo path"}},
            "required": ["repo_path"],
        },
    }},
]

MAX_TOOL_STEPS = 5


class ShadowGrader:
    """Ollama chat-based grader with tool-calling support for shadow comparison.

    Runs gemma4 with the same system/user prompts Opus receives, plus read-only
    tool access. Records which tools were called for concordance comparison.
    Never raises — returns infrastructure_error dict on any failure.
    """

    def __init__(self, config_path: str | None = None) -> None:
        self._config_path = config_path or _DEFAULT_CONFIG_PATH
        self._client: OllamaClient | None = None
        self._model: str = _DEFAULT_SHADOW_MODEL

    @staticmethod
    def _build_error(detail: str, tool_calls: list | None = None) -> dict:
        return {"infrastructure_error": True, "error_detail": detail, "tool_calls": tool_calls or []}

    def _get_client(self) -> OllamaClient:
        if self._client is None:
            try:
                with open(self._config_path) as f:
                    cfg = json.load(f)
            except (FileNotFoundError, json.JSONDecodeError) as e:
                logger.warning("Ollama config unavailable (%s): using localhost defaults", e)
                cfg = {}
            ollama_cfg = cfg.get("ollama", {})
            self._model = cfg.get("shadow_model") or ollama_cfg.get("model", _DEFAULT_SHADOW_MODEL)
            self._client = OllamaClient(
                url=ollama_cfg.get("url", "http://localhost:11434"),
                fallback_url=ollama_cfg.get("fallback_url"),
                timeout=cfg.get("timeout_seconds", 120),
            )
        return self._client

    def _validate_path(self, path: str, repo_path: str | None) -> None:
        """Reject path traversal; require path under allowed roots."""
        if ".." in path:
            raise ValueError("path traversal not allowed")
        abs_path = os.path.abspath(path)
        allowed = [os.path.expanduser("~"), "/tmp/"]
        if repo_path:
            allowed.append(os.path.abspath(repo_path))
        if not any(abs_path.startswith(root) for root in allowed):
            raise ValueError(f"path not under allowed roots")

    def _execute_tool(self, name: str, arguments: dict, repo_path: str | None) -> str:
        """Execute a tool call. Returns result string or JSON error string."""
        try:
            if name == "read_file":
                path = arguments.get("path", "")
                self._validate_path(path, repo_path)
                with open(path) as f:
                    return f.read()[:8000]
            elif name == "grep_files":
                pattern = arguments.get("pattern", "")
                directory = arguments.get("directory", "")
                self._validate_path(directory, repo_path)
                result = subprocess.run(
                    ["rg", "--max-count=20", pattern, directory],
                    capture_output=True, text=True, timeout=10,
                )
                return result.stdout[:4000] or "(no matches)"
            elif name == "git_diff":
                rp = arguments.get("repo_path", "")
                self._validate_path(rp, repo_path)
                result = subprocess.run(
                    ["git", "diff"],
                    capture_output=True, text=True, timeout=10, cwd=rp,
                )
                return result.stdout[:8000] or "(no changes)"
            else:
                return json.dumps({"error": f"unknown tool: {name}"})
        except ValueError as e:
            return json.dumps({"error": str(e)})
        except (OSError, subprocess.TimeoutExpired, subprocess.SubprocessError) as e:
            logger.warning("Tool %s execution failed: %s", name, e)
            return json.dumps({"error": str(e)})

    def grade_with_tools(
        self,
        system_prompt: str,
        user_prompt: str,
        repo_path: str | None = None,
    ) -> dict:
        """Grade using Ollama chat API with tool-calling loop.

        Returns {"grade", "approved", "feedback", "tool_calls": [...]} on success,
        or {"infrastructure_error": True, "error_detail": str, "tool_calls": []} on failure.
        """
        try:
            client = self._get_client()
        except Exception as e:
            return self._build_error(f"Failed to init Ollama client: {e}")

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        payload = {
            "model": self._model,
            "messages": messages,
            "stream": False,
            "options": {"temperature": 0.1},
            "tools": SHADOW_TOOLS,
        }

        recorded_tool_calls = []

        for step in range(MAX_TOOL_STEPS + 1):
            try:
                content, tool_calls = client.post_chat(payload)
            except OllamaError as e:
                return self._build_error(str(e), recorded_tool_calls)

            if tool_calls:
                messages.append({
                    "role": "assistant",
                    "content": content,
                    "tool_calls": [
                        {"function": {"name": tc["name"], "arguments": tc["arguments"]}}
                        for tc in tool_calls
                    ],
                })
                for tc in tool_calls:
                    recorded_tool_calls.append({"name": tc["name"], "args": tc["arguments"]})
                    tool_result = self._execute_tool(tc["name"], tc["arguments"], repo_path)
                    messages.append({"role": "tool", "content": tool_result})
                payload = {**payload, "messages": messages}

                if step >= MAX_TOOL_STEPS:
                    # Force final verdict — remove tools to prevent more calls
                    messages.append({
                        "role": "user",
                        "content": (
                            "Maximum investigation steps reached. Respond with ONLY valid JSON: "
                            '{"grade": "A|B|C|D|F", "approved": true|false, "feedback": "..."}'
                        ),
                    })
                    payload = {**payload, "messages": messages, "tools": []}
                    try:
                        content, _ = client.post_chat(payload)
                    except OllamaError as e:
                        return self._build_error(str(e), recorded_tool_calls)
                    break
            else:
                break

        # Strip think tags and parse JSON verdict
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
                    return self._build_error(f"Non-JSON response: {content[:200]}", recorded_tool_calls)
            else:
                return self._build_error(f"Non-JSON response: {content[:200]}", recorded_tool_calls)

        required = ["grade", "approved", "feedback"]
        missing = [k for k in required if k not in parsed]
        if missing:
            return self._build_error(
                f"Response missing required fields {missing}: {content[:200]}",
                recorded_tool_calls,
            )

        return {
            "grade": parsed["grade"],
            "approved": parsed["approved"],
            "feedback": parsed["feedback"],
            "tool_calls": recorded_tool_calls,
        }
```

**Step 4: Run tests to confirm they pass**

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && .venv/bin/pytest tests/test_shadow_grader.py -v 2>&1 | tail -30
```

Expected: All `TestGradeWithTools` and `TestExecuteTool` tests PASS.

**Step 5: Stage changes**

```bash
git add commander/src/ironclaude/shadow_grader.py commander/tests/test_shadow_grader.py
```

---

## Task 3: OrchestratorTools shadow helpers + delta capture

**Files:**
- Modify: `commander/src/ironclaude/orchestrator_mcp.py`
- Modify: `commander/tests/test_orchestrator_mcp.py`

**Step 1: Write tests for helper methods**

Add the following test classes to `commander/tests/test_orchestrator_mcp.py`. Add them after the last existing test class/function. First add the import at the top of the file (verify it's not already present):

```python
# Add to existing imports at top of test_orchestrator_mcp.py
from ironclaude.orchestrator_mcp import OrchestratorTools
```

Then add these test classes:

```python
class TestParseToolCallsFromDelta:
    def _make_tools(self, tmp_path, db_conn, registry, mock_tmux):
        return OrchestratorTools(registry, mock_tmux)

    def test_empty_string_returns_empty(self, tmp_path, db_conn, registry, mock_tmux):
        tools = OrchestratorTools(registry, mock_tmux)
        assert tools._parse_tool_calls_from_delta("") == []

    def test_single_bullet_tool_call(self, tmp_path, db_conn, registry, mock_tmux):
        tools = OrchestratorTools(registry, mock_tmux)
        delta = '● Read(file_path="/repo/CLAUDE.md")\n  Reading file...\n'
        result = tools._parse_tool_calls_from_delta(delta)
        assert len(result) == 1
        assert result[0]["tool"] == "Read"
        assert "/repo/CLAUDE.md" in result[0]["args"]

    def test_multiple_tool_calls(self, tmp_path, db_conn, registry, mock_tmux):
        tools = OrchestratorTools(registry, mock_tmux)
        delta = '● Read(file_path="/a.py")\n● Bash(command="git log")\n'
        result = tools._parse_tool_calls_from_delta(delta)
        assert len(result) == 2
        assert result[0]["tool"] == "Read"
        assert result[1]["tool"] == "Bash"

    def test_no_tool_calls_returns_empty(self, tmp_path, db_conn, registry, mock_tmux):
        tools = OrchestratorTools(registry, mock_tmux)
        delta = 'Just some regular output\n{"grade": "A", "approved": true}'
        result = tools._parse_tool_calls_from_delta(delta)
        assert result == []


class TestComputeConcordance:
    def test_exact_match_grade_and_pass_fail(self, db_conn, registry, mock_tmux):
        tools = OrchestratorTools(registry, mock_tmux)
        opus = {"grade": "B", "approved": True}
        shadow = {"grade": "B", "approved": True, "tool_calls": []}
        assert tools._compute_concordance(opus, shadow) == "A"

    def test_same_pass_fail_different_grade(self, db_conn, registry, mock_tmux):
        tools = OrchestratorTools(registry, mock_tmux)
        opus = {"grade": "B", "approved": True}
        shadow = {"grade": "A", "approved": True, "tool_calls": []}
        assert tools._compute_concordance(opus, shadow) == "B"

    def test_different_pass_fail(self, db_conn, registry, mock_tmux):
        tools = OrchestratorTools(registry, mock_tmux)
        opus = {"grade": "B", "approved": True}
        shadow = {"grade": "C", "approved": False, "tool_calls": []}
        assert tools._compute_concordance(opus, shadow) == "C"

    def test_infrastructure_error_returns_f(self, db_conn, registry, mock_tmux):
        tools = OrchestratorTools(registry, mock_tmux)
        opus = {"grade": "A", "approved": True}
        shadow = {"infrastructure_error": True, "error_detail": "timeout", "tool_calls": []}
        assert tools._compute_concordance(opus, shadow) == "F"


class TestFormatShadowSlackMessage:
    def test_contains_tool_calls_before_verdicts(self, db_conn, registry, mock_tmux):
        tools = OrchestratorTools(registry, mock_tmux)
        opus_result = {"grade": "B", "approved": True, "feedback": "looks good"}
        opus_tool_calls = [{"tool": "Read", "args": 'file_path="/a.py"'}]
        shadow_result = {"grade": "A", "approved": True, "feedback": "excellent", "tool_calls": []}
        msg = tools._format_shadow_slack_message(
            "spawn_worker", "worker-1", opus_result, opus_tool_calls, shadow_result, "B"
        )
        tool_idx = msg.index("Tool Calls")
        verdict_idx = msg.index("Verdicts")
        assert tool_idx < verdict_idx

    def test_concordance_a_label(self, db_conn, registry, mock_tmux):
        tools = OrchestratorTools(registry, mock_tmux)
        opus_result = {"grade": "A", "approved": True, "feedback": "great"}
        shadow_result = {"grade": "A", "approved": True, "feedback": "great", "tool_calls": []}
        msg = tools._format_shadow_slack_message(
            "kill_worker", "w", opus_result, [], shadow_result, "A"
        )
        assert "exact match" in msg.lower()

    def test_concordance_c_shows_diverge(self, db_conn, registry, mock_tmux):
        tools = OrchestratorTools(registry, mock_tmux)
        opus_result = {"grade": "B", "approved": True, "feedback": "ok"}
        shadow_result = {"grade": "C", "approved": False, "feedback": "nope", "tool_calls": []}
        msg = tools._format_shadow_slack_message(
            "approve_plan", "w2", opus_result, [], shadow_result, "C"
        )
        assert "DIVERGE" in msg

    def test_infrastructure_error_shown(self, db_conn, registry, mock_tmux):
        tools = OrchestratorTools(registry, mock_tmux)
        opus_result = {"grade": "A", "approved": True, "feedback": "fine"}
        shadow_result = {"infrastructure_error": True, "error_detail": "Ollama timed out", "tool_calls": []}
        msg = tools._format_shadow_slack_message(
            "spawn_worker", "w3", opus_result, [], shadow_result, "F"
        )
        assert "Ollama timed out" in msg
```

**Step 2: Run tests to confirm they fail**

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && .venv/bin/pytest tests/test_orchestrator_mcp.py::TestParseToolCallsFromDelta tests/test_orchestrator_mcp.py::TestComputeConcordance tests/test_orchestrator_mcp.py::TestFormatShadowSlackMessage -v 2>&1 | tail -30
```

Expected: `AttributeError` — methods don't exist yet.

**Step 3: Add imports and instance vars to orchestrator_mcp.py**

At the top of `commander/src/ironclaude/orchestrator_mcp.py`, add to existing imports (after `from ironclaude.grader import LocalGrader`):

```python
from ironclaude.shadow_grader import ShadowGrader
```

In `OrchestratorTools.__init__` (around line 398 where `self._local_grader` is set), add after `self._local_grader = LocalGrader(config_path=self._ollama_config_path)`:

```python
        self._shadow_grader = ShadowGrader(config_path=self._ollama_config_path)
        self._last_grader_delta: str = ""
```

**Step 4: Update _do_grader_send_and_poll to store delta**

In `_do_grader_send_and_poll`, find the 3 successful return points and add `self._last_grader_delta = delta` before each one. Also set `self._last_grader_delta = ""` on the timeout path.

For the batch mode return (inside the `if isinstance(results, list) and results:` block, before `self.tmux.send_keys(self._grader_session, "/clear")`):
```python
                        self._last_grader_delta = delta
                        self.tmux.send_keys(self._grader_session, "/clear")
```

For the JSON match return (inside `if json_match:` block, before `self.tmux.send_keys(self._grader_session, "/clear")` — first occurrence after json.loads):
```python
                    self._last_grader_delta = delta
                    self.tmux.send_keys(self._grader_session, "/clear")
```

For the fallback parse return (inside `if grade_m and approved_m:` block):
```python
                        self._last_grader_delta = delta
                        self.tmux.send_keys(self._grader_session, "/clear")
```

For the timeout path (before the final `self.tmux.send_keys(self._grader_session, "/clear")` after the while loop exits):
```python
        self._last_grader_delta = ""
        self.tmux.send_keys(self._grader_session, "/clear")
```

**Step 5: Add helper methods to OrchestratorTools**

Add these methods to `OrchestratorTools` class, after `_call_local_grader` (around line 747):

```python
    def _parse_tool_calls_from_delta(self, delta: str) -> list:
        """Extract Claude Code tool invocations from tmux log delta (best-effort).

        Returns list of {"tool": str, "args": str} dicts.
        """
        if not delta:
            return []
        tool_calls = []
        for match in re.finditer(r'[●•]\s*(\w+)\(([^)]*)\)', delta):
            tool_calls.append({"tool": match.group(1), "args": match.group(2)})
        if not tool_calls:
            for match in re.finditer(
                r'\{"type"\s*:\s*"tool_use"\s*,\s*"name"\s*:\s*"(\w+)"[^}]*\}', delta
            ):
                tool_calls.append({"tool": match.group(1), "args": ""})
        return tool_calls

    def _compute_concordance(self, opus: dict, shadow: dict) -> str:
        """Compute A/B/C/F concordance between Opus and shadow grade results."""
        if shadow.get("infrastructure_error"):
            return "F"
        if opus.get("grade") == shadow.get("grade") and opus.get("approved") == shadow.get("approved"):
            return "A"
        if opus.get("approved") == shadow.get("approved"):
            return "B"
        return "C"

    def _format_shadow_slack_message(
        self,
        context: str,
        worker_id: str,
        opus_result: dict,
        opus_tool_calls: list,
        shadow_result: dict,
        concordance: str,
    ) -> str:
        """Build Slack concordance report with tool calls as the primary signal."""
        lines = [f"\U0001f52c Shadow Grader — {context} | {worker_id}", ""]

        lines.append("Tool Calls (primary signal):")
        if opus_tool_calls:
            for tc in opus_tool_calls[:8]:
                lines.append(f"  Opus:    ● {tc['tool']}({tc['args'][:80]})")
            lines.append(f"  [{len(opus_tool_calls)} call{'s' if len(opus_tool_calls) != 1 else ''}]")
        else:
            lines.append("  Opus:    (no tool calls detected)")

        if shadow_result.get("infrastructure_error"):
            lines.append("  gemma4:  (infrastructure error — see below)")
        else:
            shadow_tcs = shadow_result.get("tool_calls", [])
            if shadow_tcs:
                for tc in shadow_tcs[:8]:
                    args_str = json.dumps(tc.get("args", {}), separators=(",", ":"))[:80]
                    lines.append(f"  gemma4:  {tc['name']}({args_str})")
                lines.append(f"  [{len(shadow_tcs)} call{'s' if len(shadow_tcs) != 1 else ''}]")
            else:
                lines.append("  gemma4:  (no tool calls)")

        lines.append("")
        lines.append("Verdicts:")
        opus_mark = "✓" if opus_result.get("approved") else "✗"
        opus_status = "approved" if opus_result.get("approved") else "rejected"
        lines.append(f"  Opus:    {opus_result.get('grade', '?')} {opus_mark} {opus_status}")
        lines.append(f"  \"{opus_result.get('feedback', '')}\"")

        if shadow_result.get("infrastructure_error"):
            lines.append("  gemma4:  (infrastructure error)")
            lines.append(f"  \"{shadow_result.get('error_detail', '')}\"")
        else:
            shadow_mark = "✓" if shadow_result.get("approved") else "✗"
            shadow_status = "approved" if shadow_result.get("approved") else "rejected"
            lines.append(f"  gemma4:  {shadow_result.get('grade', '?')} {shadow_mark} {shadow_status}")
            lines.append(f"  \"{shadow_result.get('feedback', '')}\"")

        lines.append("")
        concordance_labels = {
            "A": "A — exact match",
            "B": "B — same pass/fail, different grade",
            "C": "C — DIVERGE on pass/fail",
            "F": "F — gemma4 failed",
        }
        lines.append(f"Concordance: {concordance_labels.get(concordance, concordance)}")
        if concordance == "F" and shadow_result.get("error_detail"):
            lines.append(f"  Detail: {shadow_result['error_detail']}")

        return "\n".join(lines)

    def _run_shadow_and_report(
        self,
        context: str,
        worker_id: str,
        repo: str | None,
        opus_result: dict,
        opus_tool_calls: list,
        system_prompt: str,
        user_prompt: str,
    ) -> None:
        """Background thread: run shadow grade, compute concordance, post to Slack."""
        try:
            shadow_result = self._shadow_grader.grade_with_tools(
                system_prompt, user_prompt, repo_path=repo
            )
            concordance = self._compute_concordance(opus_result, shadow_result)
            msg = self._format_shadow_slack_message(
                context, worker_id, opus_result, opus_tool_calls, shadow_result, concordance
            )
            if self._slack is not None:
                self._slack.post_message(msg)
            else:
                logger.info("Shadow concordance (no Slack): %s", msg)
        except Exception as e:
            logger.warning("Shadow grader thread failed for %s/%s: %s", context, worker_id, e)

    def _fire_shadow_thread(
        self,
        context: str,
        worker_id: str,
        repo: str | None,
        opus_result: dict,
        opus_tool_calls: list,
        system_prompt: str,
        user_prompt: str,
    ) -> None:
        """Fire shadow grading in a background daemon thread."""
        import threading
        t = threading.Thread(
            target=self._run_shadow_and_report,
            args=(context, worker_id, repo, opus_result, opus_tool_calls, system_prompt, user_prompt),
            daemon=True,
        )
        t.start()
```

**Step 6: Run tests to confirm they pass**

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && .venv/bin/pytest tests/test_orchestrator_mcp.py::TestParseToolCallsFromDelta tests/test_orchestrator_mcp.py::TestComputeConcordance tests/test_orchestrator_mcp.py::TestFormatShadowSlackMessage -v 2>&1 | tail -30
```

Expected: All new tests PASS. Run the full orchestrator test suite and confirm no regressions:

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && .venv/bin/pytest tests/test_orchestrator_mcp.py -v 2>&1 | tail -20
```

Expected: All existing tests PASS.

**Step 7: Stage changes**

```bash
git add commander/src/ironclaude/orchestrator_mcp.py commander/tests/test_orchestrator_mcp.py
```

---

## Task 4: Wire shadow into spawn_worker / kill_worker / approve_plan

**Files:**
- Modify: `commander/src/ironclaude/orchestrator_mcp.py`

**Step 1: Write wiring tests**

Add the following test class to `commander/tests/test_orchestrator_mcp.py`:

```python
class TestShadowThreadFiring:
    """Verify _fire_shadow_thread is called after each Opus grading event."""

    def _make_tools(self, db_conn, registry, mock_tmux):
        tools = OrchestratorTools(registry, mock_tmux)
        _mock_grader_approve(tools)
        tools._call_local_grader = MagicMock(return_value={
            "grade": "A", "approved": True, "feedback": "ok", "confidence": "low"
        })
        tools._fire_shadow_thread = MagicMock()
        tools._last_grader_delta = ""
        return tools

    def test_spawn_worker_opus_path_fires_shadow(self, tmp_path, db_conn, registry, mock_tmux):
        tools = self._make_tools(db_conn, registry, mock_tmux)
        mock_tmux.has_session.return_value = False
        mock_tmux.spawn_session.return_value = True
        mock_tmux.read_log_tail.return_value = "❯ waiting for your input"
        tools.spawn_worker(
            worker_id="test-w1",
            worker_type="claude-sonnet",
            repo=str(tmp_path),
            objective="Add a test function to src/foo.py",
        )
        assert tools._fire_shadow_thread.called
        call_args = tools._fire_shadow_thread.call_args[0]
        assert call_args[0] == "spawn_worker"
        assert call_args[1] == "test-w1"

    def test_kill_worker_fires_shadow(self, tmp_path, db_conn, registry, mock_tmux):
        tools = self._make_tools(db_conn, registry, mock_tmux)
        registry.register_worker(
            "test-w2", "claude-sonnet", repo=str(tmp_path), description="do thing"
        )
        registry.update_worker_status("test-w2", "running")
        mock_tmux.list_pane_pid.return_value = 9999
        tools.kill_worker(
            worker_id="test-w2",
            original_objective="Add a test function",
            evidence="Diff shows the function added. Tests pass.",
        )
        assert tools._fire_shadow_thread.called
        call_args = tools._fire_shadow_thread.call_args[0]
        assert call_args[0] == "kill_worker"
        assert call_args[1] == "test-w2"

    def test_approve_plan_fires_shadow(self, tmp_path, db_conn, registry, mock_tmux):
        tools = self._make_tools(db_conn, registry, mock_tmux)
        registry.register_worker(
            "test-w3", "claude-sonnet", repo=str(tmp_path), description="write feature"
        )
        registry.update_worker_status("test-w3", "running")
        session = "ic-test-w3"
        mock_tmux.has_session.return_value = True
        tools.approve_plan(
            worker_id="test-w3",
            rationale="Brain asked clarifying questions about edge cases and constraints.",
        )
        assert tools._fire_shadow_thread.called
        call_args = tools._fire_shadow_thread.call_args[0]
        assert call_args[0] == "approve_plan"
        assert call_args[1] == "test-w3"
```

**Step 2: Run tests to confirm they fail**

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && .venv/bin/pytest tests/test_orchestrator_mcp.py::TestShadowThreadFiring -v 2>&1 | tail -20
```

Expected: Tests FAIL — `_fire_shadow_thread` not called yet.

**Step 3: Update Opus grader prompts in spawn_worker**

In `spawn_worker` (around line 1542), find:
```
You are grading a spawn_worker decision. Respond with valid JSON only — no markdown, no explanation:
```
Replace with:
```
You are grading a spawn_worker decision. You may use Read and Bash to investigate before responding.
When ready, output ONLY a valid JSON object on a single line:
```

**Step 4: Update Opus grader prompt in kill_worker**

In `kill_worker` (around line 2490), find:
```
You are grading a kill_worker decision. Respond with valid JSON only — no markdown, no explanation:
```
Replace with:
```
You are grading a kill_worker decision. You may use Read and Bash to investigate before responding.
When ready, output ONLY a valid JSON object on a single line:
```

**Step 5: Update Opus grader prompt in approve_plan**

In `approve_plan` (around line 2080), find:
```
You are grading a plan approval request. Respond with valid JSON only — no markdown, no explanation:
```
Replace with:
```
You are grading a plan approval request. You may use Read and Bash to investigate before responding.
When ready, output ONLY a valid JSON object on a single line:
```

**Step 6: Wire shadow thread into spawn_worker**

In `spawn_worker`, there are two places where `_call_grader` is called (the `if local_result.get("infrastructure_error"):` branch at ~line 1602 and the `if confidence_idx > threshold_idx:` branch at ~line 1609). After each one, add:

```python
            grade_result = self._call_grader(system_prompt, user_prompt)
            opus_tool_calls = self._parse_tool_calls_from_delta(self._last_grader_delta)
            self._fire_shadow_thread(
                "spawn_worker", worker_id, repo, grade_result, opus_tool_calls,
                system_prompt, user_prompt,
            )
```

Apply this pattern to both `_call_grader` call sites inside `spawn_worker`.

**Step 7: Wire shadow thread into kill_worker**

In `kill_worker`, find where `grade_result = self._call_grader(system_prompt, user_prompt)` is called (around line 2508). Add before this call a repo lookup, then fire shadow after:

```python
            _shadow_worker = self.registry.get_worker(worker_id)
            _shadow_repo = _shadow_worker.get("repo") if _shadow_worker else None
            grade_result = self._call_grader(system_prompt, user_prompt)
            opus_tool_calls = self._parse_tool_calls_from_delta(self._last_grader_delta)
            self._fire_shadow_thread(
                "kill_worker", worker_id, _shadow_repo, grade_result, opus_tool_calls,
                system_prompt, user_prompt,
            )
```

**Step 8: Wire shadow thread into approve_plan**

In `approve_plan`, find where `grade_result = self._call_grader(system_prompt, user_prompt)` is called (around line 2112). Add before this call a repo lookup, then fire shadow after:

```python
        _shadow_worker = self.registry.get_worker(worker_id)
        _shadow_repo = _shadow_worker.get("repo") if _shadow_worker else None
        grade_result = self._call_grader(system_prompt, user_prompt)
        opus_tool_calls = self._parse_tool_calls_from_delta(self._last_grader_delta)
        self._fire_shadow_thread(
            "approve_plan", worker_id, _shadow_repo, grade_result, opus_tool_calls,
            system_prompt, user_prompt,
        )
```

**Step 9: Run wiring tests to confirm they pass**

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && .venv/bin/pytest tests/test_orchestrator_mcp.py::TestShadowThreadFiring -v 2>&1 | tail -20
```

Expected: All 3 wiring tests PASS.

**Step 10: Run full test suite**

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && .venv/bin/pytest tests/ -v --ignore=tests/test_signal_handler_destructive.py 2>&1 | tail -30
```

Expected: All tests PASS. Note any failures and investigate before proceeding.

**Step 11: Stage changes**

```bash
git add commander/src/ironclaude/orchestrator_mcp.py commander/tests/test_orchestrator_mcp.py
```

Expected: Files staged (professional mode blocks commit).
