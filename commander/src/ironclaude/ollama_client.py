"""Thin HTTP transport for Ollama API calls.

Handles config-driven URL selection, primary->fallback retry, and exception
normalization. Domain logic (think-tag stripping, JSON parsing, VRAM math)
stays in callers.
"""
from __future__ import annotations

import requests


class OllamaError(Exception):
    """Base class for Ollama transport failures."""


class OllamaConnectionError(OllamaError):
    """Connection refused, HTTP error, or both URLs exhausted."""


class OllamaTimeoutError(OllamaError):
    """Read or connect timeout."""


class OllamaClient:
    """Thin HTTP transport for Ollama /api/generate and /api/ps endpoints.

    Responsible for: URL selection (primary/fallback), connect-timeout tuning,
    fallback retry, and exception normalization. Not responsible for: response
    parsing, think-tag stripping, schema validation, or domain logic.
    """

    def __init__(
        self,
        url: str,
        fallback_url: str | None = None,
        timeout: int = 120,
    ) -> None:
        self._url = url.rstrip("/")
        self._fallback_url = fallback_url.rstrip("/") if fallback_url else None
        self._timeout = timeout
        # Short connect timeout when fallback configured: fail fast to fallback
        # rather than waiting the full timeout on an unreachable primary.
        self._connect_timeout = 2 if fallback_url else timeout

    def post_generate(self, payload: dict) -> str:
        """POST /api/generate. Returns response["response"] text.

        For stream=True payloads, drains the streaming response and returns ''.
        Raises OllamaConnectionError or OllamaTimeoutError on failure.
        """
        return self._post("/api/generate", payload)

    def get_ps(self) -> dict:
        """GET /api/ps. Returns parsed JSON dict.

        Raises OllamaConnectionError or OllamaTimeoutError on failure.
        """
        return self._get("/api/ps")

    # ── internals ─────────────────────────────────────────────────────────

    def _post(self, path: str, payload: dict) -> str:
        is_streaming = payload.get("stream", False)
        timeout = (self._connect_timeout, self._timeout)

        try:
            resp = requests.post(
                f"{self._url}{path}",
                json=payload,
                timeout=timeout,
                stream=is_streaming,
            )
            resp.raise_for_status()
            return self._read_post_response(resp, is_streaming)
        except (requests.ConnectionError, requests.HTTPError) as e:
            if self._fallback_url:
                return self._post_via_fallback(path, payload, is_streaming, str(e))
            raise OllamaConnectionError(f"Ollama unreachable at {self._url}: {e}") from e
        except requests.Timeout:
            raise OllamaTimeoutError(f"Ollama timed out after {self._timeout}s")
        except requests.RequestException as e:
            raise OllamaConnectionError(f"Ollama request failed: {e}") from e

    def _post_via_fallback(
        self, path: str, payload: dict, is_streaming: bool, primary_error: str
    ) -> str:
        try:
            resp = requests.post(
                f"{self._fallback_url}{path}",
                json=payload,
                timeout=self._timeout,
                stream=is_streaming,
            )
            resp.raise_for_status()
            return self._read_post_response(resp, is_streaming)
        except requests.RequestException as e2:
            raise OllamaConnectionError(
                f"Ollama failed at {self._url} (and fallback {self._fallback_url}): {e2}"
            ) from e2

    def _get(self, path: str) -> dict:
        timeout = (self._connect_timeout, self._timeout)
        try:
            resp = requests.get(f"{self._url}{path}", timeout=timeout)
            resp.raise_for_status()
            return resp.json()
        except (requests.ConnectionError, requests.HTTPError) as e:
            if self._fallback_url:
                try:
                    resp = requests.get(f"{self._fallback_url}{path}", timeout=self._timeout)
                    resp.raise_for_status()
                    return resp.json()
                except requests.RequestException as e2:
                    raise OllamaConnectionError(
                        f"Ollama failed at {self._url} (and fallback {self._fallback_url}): {e2}"
                    ) from e2
            raise OllamaConnectionError(f"Ollama unreachable at {self._url}: {e}") from e
        except requests.Timeout:
            raise OllamaTimeoutError(f"Ollama timed out after {self._timeout}s")
        except requests.RequestException as e:
            raise OllamaConnectionError(f"Ollama request failed: {e}") from e

    @staticmethod
    def _read_post_response(resp, is_streaming: bool) -> str:
        if is_streaming:
            for _ in resp.iter_content(chunk_size=None):
                pass
            return ""
        return resp.json().get("response", "")
