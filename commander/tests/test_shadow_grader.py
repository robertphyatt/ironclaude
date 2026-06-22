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
            ("", tool_call),
            (verdict, []),
            (verdict, []),   # post-loop always-enforce verdict call
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
        verdict = '{"grade": "B", "approved": true, "feedback": "done"}'
        side_effects = [("", tool_call) for _ in range(5)] + [(verdict, []), (verdict, [])]
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
        assert "tools" not in verdict_call_payload

    def test_format_constrained_verdict_recovers_non_json(self):
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
        assert "tools" not in second_call_payload

    def test_string_arguments_parsed_to_dict(self):
        grader, mock_client = _make_shadow_grader()
        tool_call = [{"name": "read_file", "arguments": '{"path": "/tmp/test.txt"}'}]
        verdict = '{"grade": "A", "approved": true, "feedback": "ok"}'
        mock_client.post_chat.side_effect = [
            ("", tool_call),
            (verdict, []),
            (verdict, []),   # post-loop always-enforce verdict call
        ]
        grader._execute_tool = MagicMock(return_value="file contents")
        result = grader.grade_with_tools("sys", "user", repo_path="/tmp")
        assert result["grade"] == "A"
        grader._execute_tool.assert_called_once_with("read_file", {"path": "/tmp/test.txt"}, "/tmp")

    def test_normal_exit_appends_verdict_instruction(self):
        grader, mock_client = _make_shadow_grader()
        verdict_json = '{"grade": "B", "approved": true, "feedback": "ok"}'
        mock_client.post_chat.side_effect = [
            (verdict_json, []),   # loop: no tool calls → break
            (verdict_json, []),   # verdict call
        ]
        grader.grade_with_tools("sys", "user")
        verdict_payload = mock_client.post_chat.call_args_list[1][0][0]
        user_msgs = [m["content"] for m in verdict_payload["messages"] if m["role"] == "user"]
        assert any("Investigation complete" in c for c in user_msgs)

    def test_max_steps_exit_no_duplicate_verdict_instruction(self):
        grader, mock_client = _make_shadow_grader()
        tool_call = [{"name": "read_file", "arguments": {"path": "/tmp/f"}}]
        verdict = '{"grade": "B", "approved": true, "feedback": "done"}'
        # 6 tool-call responses triggers the step >= MAX_TOOL_STEPS branch
        side_effects = [("", tool_call) for _ in range(6)] + [(verdict, [])]
        mock_client.post_chat.side_effect = side_effects
        grader._execute_tool = MagicMock(return_value="content")
        grader.grade_with_tools("sys", "user", repo_path="/tmp")
        verdict_payload = mock_client.post_chat.call_args_list[-1][0][0]
        user_msgs = [m["content"] for m in verdict_payload["messages"] if m["role"] == "user"]
        assert any("Maximum investigation steps" in c for c in user_msgs)
        assert not any("Investigation complete" in c for c in user_msgs)

    def test_markdown_fence_stripped_from_verdict(self):
        grader, mock_client = _make_shadow_grader()
        fenced = '```json\n{"grade": "A", "approved": true, "feedback": "well done"}\n```'
        mock_client.post_chat.side_effect = [
            (fenced, []),   # loop: no tool calls → break
            (fenced, []),   # verdict call
        ]
        result = grader.grade_with_tools("sys", "user")
        assert result["grade"] == "A"
        assert result["approved"] is True


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
