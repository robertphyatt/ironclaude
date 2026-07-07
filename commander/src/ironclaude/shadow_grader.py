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
_MARKDOWN_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)

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
- After investigating, produce ONLY a JSON verdict with keys: grade, approved, feedback, confidence_in_disagreement
- grade must be one of: A, B, C, D, F
- approved must be true or false
- feedback must explain your reasoning
- confidence_in_disagreement must be one of: low, medium, high — how confident you are in your own grade and approval decision"""

GRADER_VERDICT_SCHEMA = {
    "type": "object",
    "properties": {
        "grade": {"type": "string", "enum": ["A", "B", "C", "D", "F"]},
        "approved": {"type": "boolean"},
        "feedback": {"type": "string"},
        "confidence_in_disagreement": {"type": "string", "enum": ["low", "medium", "high"]},
    },
    "required": ["grade", "approved", "feedback", "confidence_in_disagreement"],
}


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
                timeout=cfg.get("timeout_seconds", 600),
            )
        return self._client

    def _validate_path(self, path: str, repo_path: str | None) -> None:
        """Reject path traversal; require path under allowed roots."""
        if ".." in path:
            raise ValueError("path traversal not allowed")
        if not repo_path:
            raise ValueError("path not under allowed roots")
        real_path = os.path.realpath(path)
        allowed = [os.path.realpath(repo_path)]
        if not any(real_path == root or real_path.startswith(root + os.sep) for root in allowed):
            raise ValueError("path not under allowed roots")

    def _execute_tool(self, name: str, arguments: dict, repo_path: str | None) -> str:
        """Execute a tool call. Returns result string or JSON error string."""
        if not isinstance(arguments, dict):
            arguments = {}
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
                    ["rg", "--max-count=20", "-e", pattern, "--", directory],
                    capture_output=True, text=True, timeout=10,
                )
                return result.stdout[:4000] or "(no matches)"
            elif name == "git_diff":
                rp = arguments.get("repo_path", "")
                self._validate_path(rp, repo_path)
                result = subprocess.run(
                    ["git", "--no-ext-diff", "-c", "core.fsmonitor=", "-c", "diff.textconv=", "diff"],
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
        test_mode: bool = False,
    ) -> dict:
        """Grade using Ollama chat API with tool-calling loop.

        Returns {"grade", "approved", "feedback", "tool_calls": [...]} on success,
        or {"infrastructure_error": True, "error_detail": str, "tool_calls": []} on failure.
        """
        if test_mode:
            return {
                "grade": "B",
                "approved": True,
                "feedback": "test_mode",
                "confidence_in_disagreement": "low",
                "tool_calls": [],
            }

        if repo_path is None:
            return self._build_error("grade_with_tools requires repo_path (tool calls disabled)")

        try:
            client = self._get_client()
        except Exception as e:
            return self._build_error(f"Failed to init Ollama client: {e}")

        messages = [
            {"role": "system", "content": GEMMA4_SYSTEM_PROMPT + "\n\n" + system_prompt},
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
        max_steps_reached = False

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
                    args = tc["arguments"]
                    if isinstance(args, str):
                        try:
                            args = json.loads(args)
                        except json.JSONDecodeError:
                            args = {}
                    recorded_tool_calls.append({"name": tc["name"], "args": args})
                    tool_result = self._execute_tool(tc["name"], args, repo_path)
                    messages.append({"role": "tool", "content": tool_result})
                payload = {**payload, "messages": messages}

                if step >= MAX_TOOL_STEPS:
                    messages.append({
                        "role": "user",
                        "content": (
                            "Maximum investigation steps reached. Respond with ONLY valid JSON: "
                            '{"grade": "A|B|C|D|F", "approved": true|false, "feedback": "...", '
                            '"confidence_in_disagreement": "low|medium|high"}'
                        ),
                    })
                    max_steps_reached = True
                    break
            else:
                break

        if not max_steps_reached:
            messages.append({
                "role": "user",
                "content": (
                    'Investigation complete. Provide your verdict as JSON: '
                    '{"grade": "A|B|C|D|F", "approved": true|false, "feedback": "...", '
                    '"confidence_in_disagreement": "low|medium|high"}'
                ),
            })

        verdict_payload = {
            "model": self._model,
            "messages": messages,
            "stream": False,
            "options": {"temperature": 0.1},
            "format": GRADER_VERDICT_SCHEMA,
        }
        logger.debug(
            "shadow_grader verdict call: format_enforced=True msg_count=%d",
            len(messages),
        )
        try:
            content, _ = client.post_chat(verdict_payload)
        except OllamaError as e:
            return self._build_error(str(e), recorded_tool_calls)

        logger.debug("shadow_grader verdict raw: %r", content[:200])

        content = _THINK_TAG_RE.sub("", content)
        content = _SPECIAL_TOKEN_RE.sub("", content).strip()
        fence_matches = _MARKDOWN_FENCE_RE.findall(content)
        if fence_matches:
            content = fence_matches[-1]
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            return self._build_error(f"Non-JSON response: {content[:200]}", recorded_tool_calls)

        if not isinstance(parsed, dict):
            return self._build_error(f"Non-dict verdict: {content[:200]}", recorded_tool_calls)

        required = ["grade", "approved", "feedback", "confidence_in_disagreement"]
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
            "confidence_in_disagreement": parsed["confidence_in_disagreement"],
            "tool_calls": recorded_tool_calls,
        }
