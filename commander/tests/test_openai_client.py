"""Tests for the OpenAiClient chat-completions transport."""
from unittest.mock import MagicMock, patch

import pytest
import requests

from ironclaude.openai_client import OpenAiClient
from ironclaude.ollama_client import OllamaError, OllamaHTTPError, _BREAKERS


def _ok_response(content='{"grade":"A"}'):
    resp = MagicMock()
    resp.json.return_value = {"choices": [{"message": {"content": content}}]}
    resp.raise_for_status = MagicMock()
    return resp


def test_post_generate_hits_chat_completions_with_bearer_and_returns_content():
    resp = _ok_response('{"grade":"A"}')
    with patch("requests.post", return_value=resp) as mock_post:
        result = OpenAiClient(base_url="http://h/v1").post_generate(
            {"model": "m", "messages": [{"role": "user", "content": "x"}]}
        )
    assert result == '{"grade":"A"}'
    # URL is first positional arg
    called_url = mock_post.call_args[0][0]
    assert called_url == "http://h/v1/chat/completions"
    headers = mock_post.call_args[1]["headers"]
    assert headers["Authorization"].startswith("Bearer ")


def test_connection_error_raises_ollama_error_subclass():
    with patch("requests.post", side_effect=requests.ConnectionError("refused")):
        with pytest.raises(OllamaError) as exc_info:
            OpenAiClient(base_url="http://h/v1").post_generate(
                {"model": "m", "messages": [{"role": "user", "content": "x"}]}
            )
    assert isinstance(exc_info.value, OllamaError)


def test_get_models_probes_models_endpoint():
    resp = MagicMock()
    resp.json.return_value = {"data": []}
    resp.raise_for_status = MagicMock()
    with patch("requests.get", return_value=resp) as mock_get:
        out = OpenAiClient(base_url="http://h/v1").get_models()
    assert out == {"data": []}
    assert mock_get.call_args[0][0] == "http://h/v1/models"


def test_empty_base_url_raises_ollama_error_subclass():
    with pytest.raises(OllamaError) as exc_info:
        OpenAiClient("")
    assert isinstance(exc_info.value, OllamaError)


def test_none_base_url_raises_ollama_error_subclass():
    with pytest.raises(OllamaError) as exc_info:
        OpenAiClient(None)
    assert isinstance(exc_info.value, OllamaError)


def _bad_json_response():
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.side_effect = ValueError("no json")
    return resp


def test_post_generate_non_json_body_raises_ollama_error_subclass():
    resp = _bad_json_response()
    with patch("requests.post", return_value=resp):
        with pytest.raises(OllamaError) as exc_info:
            OpenAiClient(base_url="http://h/v1").post_generate(
                {"model": "m", "messages": [{"role": "user", "content": "x"}]}
            )
    assert isinstance(exc_info.value, OllamaError)


def test_post_chat_non_json_body_raises_ollama_error_subclass():
    resp = _bad_json_response()
    with patch("requests.post", return_value=resp):
        with pytest.raises(OllamaError) as exc_info:
            OpenAiClient(base_url="http://h/v1").post_chat(
                {"model": "m", "messages": [{"role": "user", "content": "x"}]}
            )
    assert isinstance(exc_info.value, OllamaError)


def _http_error_response(status_code=503):
    resp = MagicMock()
    http_err = requests.HTTPError("boom")
    http_err.response = MagicMock(status_code=status_code)
    resp.raise_for_status.side_effect = http_err
    return resp


def test_openai_post_generate_http_error_message_prefixed_openai():
    # Guards openai_client.py:135 (_post_chat) passing backend="OpenAI" to _http_error.
    _BREAKERS.reset()  # a prior same-url ConnectionError test opens the breaker
    resp = _http_error_response()
    with patch("requests.post", return_value=resp):
        with pytest.raises(OllamaHTTPError) as exc_info:
            OpenAiClient(base_url="http://h/v1").post_generate(
                {"model": "m", "messages": [{"role": "user", "content": "x"}]}
            )
    assert str(exc_info.value).startswith("OpenAI request returned HTTP")


def test_openai_get_models_http_error_message_prefixed_openai():
    # Guards openai_client.py:155 (_do_get_models) passing backend="OpenAI".
    _BREAKERS.reset()
    resp = _http_error_response()
    with patch("requests.get", return_value=resp):
        with pytest.raises(OllamaHTTPError) as exc_info:
            OpenAiClient(base_url="http://h/v1").get_models()
    assert str(exc_info.value).startswith("OpenAI request returned HTTP")
