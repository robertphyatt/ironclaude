"""Unit tests for OllamaClient transport layer."""

import pytest
import requests
from unittest.mock import MagicMock, patch

from ironclaude.ollama_client import (
    OllamaClient,
    OllamaError,
    OllamaConnectionError,
    OllamaTimeoutError,
)


def _make_response(text="result"):
    resp = MagicMock()
    resp.json.return_value = {"response": text}
    resp.raise_for_status = MagicMock()
    return resp


@pytest.fixture
def client():
    return OllamaClient(url="http://primary:11434", fallback_url="http://fallback:11434", timeout=30)


@pytest.fixture
def client_no_fallback():
    return OllamaClient(url="http://primary:11434", timeout=30)


class TestPostGenerate:
    @patch("ironclaude.ollama_client.requests.post")
    def test_success(self, mock_post, client):
        mock_post.return_value = _make_response("hello")
        result = client.post_generate({"model": "gemma4", "prompt": "test", "stream": False})
        assert result == "hello"

    @patch("ironclaude.ollama_client.requests.post")
    def test_primary_fails_uses_fallback(self, mock_post, client):
        mock_post.side_effect = [requests.ConnectionError("refused"), _make_response("from_fallback")]
        result = client.post_generate({"model": "gemma4", "prompt": "test", "stream": False})
        assert result == "from_fallback"
        assert mock_post.call_count == 2

    @patch("ironclaude.ollama_client.requests.post")
    def test_both_fail_raises_connection_error(self, mock_post, client):
        mock_post.side_effect = requests.ConnectionError("refused")
        with pytest.raises(OllamaConnectionError) as exc_info:
            client.post_generate({"model": "gemma4", "prompt": "test", "stream": False})
        msg = str(exc_info.value)
        assert "primary:11434" in msg
        assert "fallback:11434" in msg

    @patch("ironclaude.ollama_client.requests.post")
    def test_timeout_raises_timeout_error(self, mock_post, client):
        mock_post.side_effect = requests.Timeout()
        with pytest.raises(OllamaTimeoutError):
            client.post_generate({"model": "gemma4", "prompt": "test", "stream": False})

    @patch("ironclaude.ollama_client.requests.post")
    def test_streaming_drains_and_returns_empty(self, mock_post, client):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.iter_content.return_value = [b"chunk1", b"chunk2"]
        mock_post.return_value = resp
        result = client.post_generate({"model": "gemma4", "keep_alive": 0, "stream": True})
        assert result == ""
        resp.iter_content.assert_called_once_with(chunk_size=None)

    @patch("ironclaude.ollama_client.requests.post")
    def test_no_fallback_no_retry(self, mock_post, client_no_fallback):
        mock_post.side_effect = requests.ConnectionError("refused")
        with pytest.raises(OllamaConnectionError):
            client_no_fallback.post_generate({"model": "gemma4", "prompt": "test"})
        assert mock_post.call_count == 1


class TestGetPs:
    @patch("ironclaude.ollama_client.requests.get")
    def test_success(self, mock_get, client):
        resp = MagicMock()
        resp.json.return_value = {"models": [{"name": "gemma4", "size": 8_000_000_000}]}
        resp.raise_for_status = MagicMock()
        mock_get.return_value = resp
        result = client.get_ps()
        assert result["models"][0]["name"] == "gemma4"

    @patch("ironclaude.ollama_client.requests.get")
    def test_connection_error_raises(self, mock_get, client_no_fallback):
        mock_get.side_effect = requests.ConnectionError("refused")
        with pytest.raises(OllamaConnectionError):
            client_no_fallback.get_ps()


class TestConnectTimeoutShortcut:
    def test_short_connect_timeout_with_fallback(self):
        c = OllamaClient(url="http://a:11434", fallback_url="http://b:11434", timeout=120)
        assert c._connect_timeout == 2

    def test_full_connect_timeout_without_fallback(self):
        c = OllamaClient(url="http://a:11434", timeout=120)
        assert c._connect_timeout == 120


class TestCreateModel:
    @patch("ironclaude.ollama_client.requests.post")
    def test_create_model_posts_to_api_create(self, mock_post, client_no_fallback):
        """create_model POSTs /api/create with from + parameters, stream=False."""
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {"status": "success"}
        mock_post.return_value = resp

        client_no_fallback.create_model(
            "ic-gemma4-12b-131072", "gemma4:12b-it-qat", {"num_ctx": 131072}
        )

        args, kwargs = mock_post.call_args
        assert args[0] == "http://primary:11434/api/create"
        payload = kwargs["json"]
        assert payload["model"] == "ic-gemma4-12b-131072"
        assert payload["from"] == "gemma4:12b-it-qat"
        assert payload["parameters"] == {"num_ctx": 131072}
        assert payload["stream"] is False

    @patch("ironclaude.ollama_client.requests.post")
    def test_create_model_connection_error_raises(self, mock_post, client_no_fallback):
        mock_post.side_effect = requests.ConnectionError("refused")
        with pytest.raises(OllamaConnectionError):
            client_no_fallback.create_model("v", "base", {"num_ctx": 1})


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
