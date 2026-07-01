"""Unit tests for LocalGrader."""
import json
import pytest
from unittest.mock import MagicMock, patch

from ironclaude.grader import LocalGrader
from ironclaude.ollama_client import OllamaConnectionError, OllamaTimeoutError


SIMPLE_SCHEMA = {
    "type": "object",
    "properties": {"valid": {"type": "boolean"}},
    "required": ["valid"],
}


def _make_grader():
    """LocalGrader with a pre-injected mock client (no real network calls)."""
    grader = LocalGrader(config_path="/nonexistent/config.json")
    mock_client = MagicMock()
    grader._client = mock_client
    return grader, mock_client


class TestHappyPath:
    def test_valid_json_returned_as_dict(self):
        grader, mock_client = _make_grader()
        mock_client.post_generate.return_value = '{"valid": true}'
        result = grader.grade("sys", "user", SIMPLE_SCHEMA)
        assert result == {"valid": True}

    def test_payload_includes_prompt_model_options_format(self):
        grader, mock_client = _make_grader()
        grader._cfg = {"model": "test-model:7b"}
        mock_client.post_generate.return_value = '{"valid": true}'
        grader.grade("the system prompt", "the user prompt", SIMPLE_SCHEMA)
        payload = mock_client.post_generate.call_args[0][0]
        assert payload["model"] == "test-model:7b"
        assert "the system prompt" in payload["prompt"]
        assert "the user prompt" in payload["prompt"]
        assert payload["stream"] is False
        assert payload["options"]["temperature"] == 0.1
        assert payload["options"]["num_predict"] == -1
        assert payload["format"] == SIMPLE_SCHEMA

    def test_no_schema_omits_format_from_payload(self):
        grader, mock_client = _make_grader()
        mock_client.post_generate.return_value = '{"anything": 1}'
        grader.grade("sys", "user")
        payload = mock_client.post_generate.call_args[0][0]
        assert "format" not in payload


class TestErrorPaths:
    def test_ollama_connection_error_returns_infrastructure_error(self):
        grader, mock_client = _make_grader()
        mock_client.post_generate.side_effect = OllamaConnectionError("refused")
        result = grader.grade("sys", "user", SIMPLE_SCHEMA)
        assert result["infrastructure_error"] is True
        assert "refused" in result["error_detail"]

    def test_ollama_timeout_error_returns_infrastructure_error(self):
        grader, mock_client = _make_grader()
        mock_client.post_generate.side_effect = OllamaTimeoutError("timed out")
        result = grader.grade("sys", "user", SIMPLE_SCHEMA)
        assert result["infrastructure_error"] is True
        assert "timed out" in result["error_detail"]

    def test_empty_response_returns_infrastructure_error(self):
        grader, mock_client = _make_grader()
        mock_client.post_generate.return_value = ""
        result = grader.grade("sys", "user", SIMPLE_SCHEMA)
        assert result["infrastructure_error"] is True
        assert "empty" in result["error_detail"].lower()

    def test_non_json_response_returns_infrastructure_error(self):
        grader, mock_client = _make_grader()
        mock_client.post_generate.return_value = "This is not JSON at all"
        result = grader.grade("sys", "user", SIMPLE_SCHEMA)
        assert result["infrastructure_error"] is True
        assert "non-json" in result["error_detail"].lower()

    def test_consistent_error_shape_across_all_paths(self):
        cases = [
            ("connection", OllamaConnectionError("fail"), None),
            ("timeout", OllamaTimeoutError("timeout"), None),
        ]
        for label, exc, _ in cases:
            grader, mock_client = _make_grader()
            mock_client.post_generate.side_effect = exc
            result = grader.grade("sys", "user", SIMPLE_SCHEMA)
            assert result.get("infrastructure_error") is True, label
            assert isinstance(result.get("error_detail"), str), label
            assert len(result["error_detail"]) > 0, label


class TestThinkTagStripping:
    def test_think_tags_stripped_before_parse(self):
        grader, mock_client = _make_grader()
        mock_client.post_generate.return_value = '<think>reasoning here</think>{"valid": true}'
        result = grader.grade("sys", "user", SIMPLE_SCHEMA)
        assert result == {"valid": True}

    def test_multiline_think_tags_stripped(self):
        grader, mock_client = _make_grader()
        mock_client.post_generate.return_value = '<think>\nline 1\nline 2\n</think>{"valid": false}'
        result = grader.grade("sys", "user", SIMPLE_SCHEMA)
        assert result == {"valid": False}

    def test_think_stripped_but_remaining_non_json_returns_infrastructure_error(self):
        grader, mock_client = _make_grader()
        mock_client.post_generate.return_value = "<think>reasoning</think>not valid json"
        result = grader.grade("sys", "user", SIMPLE_SCHEMA)
        assert result["infrastructure_error"] is True
        assert "non-json" in result["error_detail"].lower()


class TestSchemaValidation:
    def test_missing_required_field_returns_infrastructure_error(self):
        grader, mock_client = _make_grader()
        mock_client.post_generate.return_value = '{"other": "field"}'
        result = grader.grade("sys", "user", SIMPLE_SCHEMA)
        assert result["infrastructure_error"] is True
        assert "missing required" in result["error_detail"].lower()

    def test_all_required_fields_present_returns_parsed(self):
        grader, mock_client = _make_grader()
        mock_client.post_generate.return_value = '{"valid": true, "extra": "ok"}'
        result = grader.grade("sys", "user", SIMPLE_SCHEMA)
        assert result["valid"] is True

    def test_no_schema_skips_required_field_check(self):
        grader, mock_client = _make_grader()
        mock_client.post_generate.return_value = '{"whatever": 42}'
        result = grader.grade("sys", "user")
        assert result == {"whatever": 42}


class TestNonDictVerdict:
    def test_bare_true_returns_infrastructure_error(self):
        grader, mock_client = _make_grader()
        mock_client.post_generate.return_value = "true"
        result = grader.grade("sys", "user", SIMPLE_SCHEMA)
        assert result["infrastructure_error"] is True
        assert "non-dict" in result["error_detail"].lower()

    def test_bare_number_returns_infrastructure_error(self):
        grader, mock_client = _make_grader()
        mock_client.post_generate.return_value = "42"
        result = grader.grade("sys", "user", SIMPLE_SCHEMA)
        assert result["infrastructure_error"] is True
        assert "non-dict" in result["error_detail"].lower()

    def test_bare_list_does_not_raise(self):
        grader, mock_client = _make_grader()
        mock_client.post_generate.return_value = "[1, 2, 3]"
        result = grader.grade("sys", "user", SIMPLE_SCHEMA)
        assert result["infrastructure_error"] is True


class TestMarkdownFenceStripping:
    def test_json_fence_stripped_before_parse(self):
        grader, mock_client = _make_grader()
        mock_client.post_generate.return_value = '```json\n{"valid": true}\n```'
        result = grader.grade("sys", "user", SIMPLE_SCHEMA)
        assert result == {"valid": True}

    def test_bare_fence_stripped_before_parse(self):
        grader, mock_client = _make_grader()
        mock_client.post_generate.return_value = '```\n{"valid": false}\n```'
        result = grader.grade("sys", "user", SIMPLE_SCHEMA)
        assert result == {"valid": False}


class TestConfigLoading:
    def test_config_absent_uses_localhost_defaults(self, tmp_path):
        with patch("ironclaude.grader.OllamaClient") as MockClient:
            MockClient.return_value.post_generate.return_value = '{"valid": true}'
            grader = LocalGrader(config_path=str(tmp_path / "nonexistent.json"))
            grader.grade("sys", "user", SIMPLE_SCHEMA)
        MockClient.assert_called_once_with(
            url="http://localhost:11434",
            fallback_url=None,
            timeout=120,
        )
