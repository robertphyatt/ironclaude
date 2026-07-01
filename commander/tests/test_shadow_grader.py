"""Unit tests for ShadowGrader (Ollama chat-based grading with tool calling)."""
import json
import os
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

    def test_non_dict_verdict_returns_infrastructure_error(self):
        """A verdict that is valid JSON but not a dict (e.g. bare true/number) must not raise."""
        grader, mock_client = _make_shadow_grader()
        mock_client.post_chat.return_value = ("true", [])
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

    def test_git_diff_uses_no_ext_diff(self):
        """git_diff must pass --no-ext-diff to prevent RCE via diff.external config."""
        grader = ShadowGrader(config_path="/nonexistent/config.json")
        mock_result = MagicMock()
        mock_result.stdout = "diff output\n"
        with patch("subprocess.run", return_value=mock_result) as mock_run:
            grader._execute_tool("git_diff", {"repo_path": "/tmp"}, "/tmp")
        cmd = mock_run.call_args[0][0]
        assert "--no-ext-diff" in cmd

    def test_validate_path_resolves_tmp_symlink(self):
        """On macOS, /tmp -> /private/tmp; _validate_path must resolve the root too (FW-6)."""
        grader = ShadowGrader(config_path="/nonexistent/config.json")

        def _macos_realpath(p):
            if p == "/tmp":
                return "/private/tmp"
            if p.startswith("/tmp/"):
                return "/private" + p
            return p

        with patch("os.path.realpath", side_effect=_macos_realpath):
            grader._validate_path("/private/tmp/test.txt", repo_path="/tmp")

    def test_unknown_tool_returns_error_json(self):
        grader = ShadowGrader(config_path="/nonexistent/config.json")
        result = grader._execute_tool("nonexistent_tool", {}, None)
        parsed = json.loads(result)
        assert "error" in parsed
        assert "unknown" in parsed["error"].lower()

    def test_path_prefix_collision_rejected(self):
        """Path matching home dir prefix but different directory is rejected (FW-1)."""
        import os
        grader = ShadowGrader(config_path="/nonexistent/config.json")
        home = os.path.expanduser("~")
        evil_path = home + "evil/secret.txt"
        with pytest.raises(ValueError, match="path not under allowed roots"):
            grader._validate_path(evil_path, repo_path="/tmp/myrepo")

    def test_symlink_outside_allowed_roots_rejected(self, tmp_path):
        """Symlink pointing outside allowed roots is rejected (FW-2)."""
        outside = tmp_path / "outside"
        outside.mkdir()
        secret = outside / "secret.txt"
        secret.write_text("sensitive")
        inside = tmp_path / "allowed_repo"
        inside.mkdir()
        link = inside / "escape.txt"
        link.symlink_to(secret)
        grader = ShadowGrader(config_path="/nonexistent/config.json")
        with pytest.raises(ValueError, match="path not under allowed roots"):
            grader._validate_path(str(link), repo_path=str(inside))

    def test_grep_files_uses_e_flag_and_double_dash(self):
        """CR-4: rg pattern must be passed after -e and directory after -- to block flag injection."""
        grader = ShadowGrader(config_path="/nonexistent/config.json")
        mock_result = MagicMock()
        mock_result.stdout = "match\n"
        with patch("subprocess.run", return_value=mock_result) as mock_run:
            grader._execute_tool("grep_files", {"pattern": "--pre=evil", "directory": "/tmp"}, "/tmp")
        cmd = mock_run.call_args[0][0]
        assert "-e" in cmd
        assert cmd[cmd.index("-e") + 1] == "--pre=evil"
        assert "--" in cmd
        assert cmd[cmd.index("--") + 1] == "/tmp"

    def test_home_path_outside_repo_rejected(self):
        """Read scope: a path under $HOME but outside repo_path must be rejected."""
        grader = ShadowGrader(config_path="/nonexistent/config.json")
        home = os.path.expanduser("~")
        with patch("builtins.open", mock_open(read_data="secret")):
            result = grader._execute_tool(
                "read_file", {"path": home + "/.ssh/id_rsa"}, "/tmp/myrepo"
            )
        parsed = json.loads(result)
        assert "error" in parsed

    def test_path_inside_repo_accepted(self, tmp_path):
        """Read scope: a path inside repo_path is accepted."""
        repo = tmp_path / "repo"
        repo.mkdir()
        target = repo / "file.txt"
        target.write_text("inside repo contents")
        grader = ShadowGrader(config_path="/nonexistent/config.json")
        result = grader._execute_tool("read_file", {"path": str(target)}, str(repo))
        assert "inside repo contents" in result

    def test_validate_path_none_repo_rejected(self):
        """Read scope: repo_path=None must be rejected (no blanket ~ or /tmp roots)."""
        grader = ShadowGrader(config_path="/nonexistent/config.json")
        with pytest.raises(ValueError):
            grader._validate_path("/tmp/anything.txt", repo_path=None)

    def test_git_diff_disables_fsmonitor_and_textconv(self):
        """git_diff must neutralize repo-local core.fsmonitor and diff.textconv config."""
        grader = ShadowGrader(config_path="/nonexistent/config.json")
        mock_result = MagicMock()
        mock_result.stdout = "diff output\n"
        with patch("subprocess.run", return_value=mock_result) as mock_run:
            grader._execute_tool("git_diff", {"repo_path": "/tmp"}, "/tmp")
        cmd = mock_run.call_args[0][0]
        assert "-c" in cmd
        assert "core.fsmonitor=" in cmd
        assert "diff.textconv=" in cmd

    def test_arguments_none_does_not_raise(self):
        """arguments=None (malformed local-LLM tool call) must not raise AttributeError."""
        grader = ShadowGrader(config_path="/nonexistent/config.json")
        result = grader._execute_tool("read_file", None, "/tmp")
        parsed = json.loads(result)
        assert "error" in parsed


class TestGetClient:
    def test_default_timeout_is_300_when_config_missing(self):
        grader = ShadowGrader(config_path="/nonexistent/config.json")
        with patch("ironclaude.shadow_grader.OllamaClient") as mock_cls:
            mock_cls.return_value = MagicMock()
            grader._get_client()
        assert mock_cls.call_args.kwargs["timeout"] == 300
