"""Thin HTTP transport for an OpenAI-compatible chat-completions backend.

Mirrors OllamaClient's transport contract: primary->fallback retry, per-URL
circuit breaking, and exception normalization. Deliberately raises the SAME
Ollama* error types so existing `except OllamaError` seams catch failures from
either backend unchanged. Domain logic (think-tag stripping, JSON parsing) stays
in callers.

The backend is selected by model NAME only — reasoning-effort variants are
encoded in the model suffix (e.g. `example-model-b:low`), never as a request field.
No real API key is required; a dummy bearer token is sent.
"""
from __future__ import annotations

import requests

from .ollama_client import (
    OllamaConnectionError,
    OllamaError,  # noqa: F401  (re-exported for callers importing from here)
    OllamaHTTPError,
    OllamaTimeoutError,
    _BREAKERS,
    _http_error,
)

# Dummy bearer token — no real key is required or validated by the backend.
_DUMMY_BEARER = "ollama"


class OpenAiClient:
    """Thin HTTP transport for an OpenAI-compatible /v1/chat/completions endpoint.

    Responsible for: URL selection (primary/fallback), connect-timeout tuning,
    fallback retry, and exception normalization to the Ollama* error hierarchy.
    Not responsible for: response parsing beyond envelope extraction, think-tag
    stripping, schema validation, or domain logic.
    """

    def __init__(
        self,
        base_url: str,
        fallback_base_url: str | None = None,
        timeout: int = 120,
    ) -> None:
        if not base_url:
            raise OllamaConnectionError("OpenAI base_url is empty/missing; cannot construct client")
        self._url = base_url.rstrip("/")
        self._fallback_url = fallback_base_url.rstrip("/") if fallback_base_url else None
        self._timeout = timeout
        # Short connect timeout when fallback configured: fail fast to fallback
        # rather than waiting the full timeout on an unreachable primary.
        self._connect_timeout = 2 if fallback_base_url else timeout

    # ── public API ────────────────────────────────────────────────────────

    def post_generate(self, payload: dict) -> str:
        """POST /chat/completions. Returns choices[0].message.content text.

        The caller builds the full payload (model, messages, max_tokens,
        temperature, optional response_format). Raises an Ollama* error on
        failure so existing error seams catch it.
        """
        return self._attempt(lambda url: self._do_chat_generate(url, payload))

    def post_chat(self, payload: dict) -> tuple:
        """POST /chat/completions. Returns (content_str, tool_calls_list).

        tool_calls_list normalizes each entry to
        {"name": str, "arguments": ..., "id": str}. Raises an Ollama* error on
        failure.
        """
        return self._attempt(lambda url: self._do_chat_full(url, payload))

    def get_models(self) -> dict:
        """GET /models. Reachability probe; returns parsed JSON dict.

        Raises an Ollama* error on failure.
        """
        return self._attempt(lambda url: self._do_get_models(url))

    # ── retry / breaker orchestration (mirrors OllamaClient._attempt) ──────

    def _attempt(self, do_request):
        urls = [self._url] + ([self._fallback_url] if self._fallback_url else [])
        last_err = None
        tried = False
        for url in urls:
            if not _BREAKERS.allow(url):
                continue
            tried = True
            try:
                result = do_request(url)
            except OllamaHTTPError:
                _BREAKERS.record_success(url)     # endpoint responded -> healthy
                raise
            except (OllamaConnectionError, OllamaTimeoutError) as e:
                _BREAKERS.record_failure(url)
                last_err = e
                continue
            except BaseException:
                _BREAKERS.record_failure(url)     # never leak the half-open probe slot
                raise
            _BREAKERS.record_success(url)
            return result
        joined = ", ".join(urls)
        if not tried:
            raise OllamaConnectionError(f"OpenAI circuit open for all endpoints: {joined}")
        raise type(last_err)(f"OpenAI failed at all endpoints ({joined}): {last_err}")

    # ── request implementations ────────────────────────────────────────────

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {_DUMMY_BEARER}"}

    def _do_chat_generate(self, url: str, payload: dict) -> str:
        resp = self._post_chat(url, payload)
        return self._read_content(resp)

    def _do_chat_full(self, url: str, payload: dict) -> tuple:
        resp = self._post_chat(url, payload)
        return self._read_chat_response(resp)

    def _post_chat(self, url: str, payload: dict):
        try:
            resp = requests.post(
                f"{url}/chat/completions",
                json=payload,
                headers=self._headers(),
                timeout=(self._connect_timeout, self._timeout),
                stream=False,
            )
            resp.raise_for_status()
            return resp
        except requests.HTTPError as e:
            raise _http_error(url, e, backend="OpenAI") from e
        except requests.ConnectionError as e:
            raise OllamaConnectionError(f"OpenAI unreachable at {url}: {e}") from e
        except requests.Timeout:
            raise OllamaTimeoutError(
                f"OpenAI timed out at {url} (connect={self._connect_timeout}s, read={self._timeout}s)"
            )
        except requests.RequestException as e:
            raise OllamaConnectionError(f"OpenAI request failed at {url}: {e}") from e

    def _do_get_models(self, url: str) -> dict:
        try:
            resp = requests.get(
                f"{url}/models",
                headers=self._headers(),
                timeout=(self._connect_timeout, self._timeout),
            )
            resp.raise_for_status()
            return resp.json()
        except requests.HTTPError as e:
            raise _http_error(url, e, backend="OpenAI") from e
        except requests.ConnectionError as e:
            raise OllamaConnectionError(f"OpenAI unreachable at {url}: {e}") from e
        except requests.Timeout:
            raise OllamaTimeoutError(
                f"OpenAI timed out at {url} (connect={self._connect_timeout}s, read={self._timeout}s)"
            )
        except requests.RequestException as e:
            raise OllamaConnectionError(f"OpenAI request failed at {url}: {e}") from e

    # ── response parsing ────────────────────────────────────────────────────

    @staticmethod
    def _read_content(resp) -> str:
        try:
            data = resp.json()
        except ValueError as e:
            raise OllamaConnectionError(f"OpenAI returned a non-JSON response body: {e}") from e
        choices = data.get("choices") or []
        if not choices:
            return ""
        return choices[0].get("message", {}).get("content", "") or ""

    @staticmethod
    def _read_chat_response(resp) -> tuple:
        try:
            data = resp.json()
        except ValueError as e:
            raise OllamaConnectionError(f"OpenAI returned a non-JSON response body: {e}") from e
        choices = data.get("choices") or []
        if not choices:
            return "", []
        message = choices[0].get("message", {})
        content = message.get("content", "") or ""
        tool_calls_raw = message.get("tool_calls") or []
        tool_calls = []
        for tc in tool_calls_raw:
            fn = tc.get("function", {})
            tool_calls.append({
                "name": fn.get("name", ""),
                "arguments": fn.get("arguments", {}),
                "id": tc.get("id"),
            })
        return content, tool_calls
