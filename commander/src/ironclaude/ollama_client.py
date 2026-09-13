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


class OllamaHTTPError(OllamaError):
    """Server returned a 4xx/5xx status (client/config error, not an outage).

    Distinct from OllamaConnectionError so a bad request or missing model is not
    masked as connectivity loss and does not trigger the fallback retry.
    """

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class OllamaTimeoutError(OllamaError):
    """Read or connect timeout."""


import threading
import time as _time

_BREAKER_BASE_BACKOFF = 5.0
_BREAKER_MAX_BACKOFF = 300.0
_BREAKER_FAILURE_THRESHOLD = 3
_BREAKER_DEGRADED_GRACE = 120.0


class _UrlBreaker:
    __slots__ = ("open_until", "backoff", "probing", "failures", "open_since", "last_slow")

    def __init__(self) -> None:
        self.open_until = 0.0
        self.backoff = _BREAKER_BASE_BACKOFF
        self.probing = False
        self.failures = 0
        self.open_since = 0.0
        self.last_slow = 0.0


class _CircuitBreakerRegistry:
    """Per-URL circuit breaker. Opens only after _BREAKER_FAILURE_THRESHOLD
    consecutive failures; exactly ONE
    caller is admitted as the half-open prober once open_until passes; exponential
    backoff (base 5s x2, cap 300s) on repeated probe failure. Thread-safe (the
    daemon has a second grader thread). The lock is never held across a network
    call — callers do allow() (claims the probe slot), then the request, then
    record_success/record_failure."""

    def __init__(self, now=_time.monotonic) -> None:
        self._now = now
        self._lock = threading.Lock()
        self._breakers: dict[str, _UrlBreaker] = {}

    def allow(self, url: str) -> bool:
        with self._lock:
            b = self._breakers.get(url)
            if b is None:
                return True                       # closed
            if b.open_until == 0.0:
                return True
            if self._now() < b.open_until:
                return False                      # open, not yet
            if b.probing:
                return False                      # another caller is probing
            b.probing = True                      # claim the probe slot
            return True

    def record_success(self, url: str) -> None:
        with self._lock:
            self._breakers.pop(url, None)         # closed = absent/reset

    def record_failure(self, url: str) -> None:
        with self._lock:
            b = self._breakers.get(url)
            if b is None:
                b = _UrlBreaker()
                self._breakers[url] = b
            b.failures += 1
            b.probing = False
            if b.failures < _BREAKER_FAILURE_THRESHOLD:
                return
            if b.open_until == 0.0:
                b.open_since = self._now()
            else:
                b.backoff = min(b.backoff * 2, _BREAKER_MAX_BACKOFF)
            b.open_until = self._now() + b.backoff

    def record_slow(self, url: str) -> None:
        """Reachable-but-slow (busy) = UP: reset any accumulated unreachable state, stamp last_slow. Never opens."""
        with self._lock:
            b = _UrlBreaker()
            b.last_slow = self._now()
            self._breakers[url] = b

    def busy_urls(self) -> list[str]:
        with self._lock:
            now = self._now()
            return [
                u for u, b in self._breakers.items()
                if b.open_until == 0.0 and b.last_slow and now - b.last_slow < _BREAKER_DEGRADED_GRACE
            ]

    def open_urls(self) -> list[str]:
        with self._lock:
            now = self._now()
            return [u for u, b in self._breakers.items() if now < b.open_until]

    def degraded_urls(self) -> list[str]:
        with self._lock:
            now = self._now()
            return [
                u for u, b in self._breakers.items()
                if b.open_until != 0.0
                and (now < b.open_until or b.probing)
                and now - b.open_since >= _BREAKER_DEGRADED_GRACE
            ]

    def backoff_for(self, url: str):
        with self._lock:
            b = self._breakers.get(url)
            return None if b is None or b.open_until == 0.0 else b.backoff

    def reset(self) -> None:
        with self._lock:
            self._breakers.clear()


_BREAKERS = _CircuitBreakerRegistry()


def ollama_degraded_urls() -> list[str]:
    """URLs whose breaker has been open past the degraded grace period (for
    heartbeat observability)."""
    return _BREAKERS.degraded_urls()


def ollama_busy_urls() -> list[str]:
    """URLs recently marked reachable-but-slow (busy), not unreachable."""
    return _BREAKERS.busy_urls()


def _http_error(url: str, err: "requests.HTTPError", verb: str = "request", backend: str = "Ollama") -> OllamaHTTPError:
    """Build an OllamaHTTPError carrying the response status code (if available)."""
    status = getattr(getattr(err, "response", None), "status_code", None)
    status_txt = status if status is not None else "?"
    return OllamaHTTPError(
        f"{backend} {verb} returned HTTP {status_txt} at {url}: {err}",
        status_code=status,
    )


def _raise_for_status_closing(resp) -> None:
    """Call resp.raise_for_status(); on HTTPError, close the (possibly streamed)
    response before propagating so the connection is not leaked."""
    try:
        resp.raise_for_status()
    except requests.HTTPError:
        resp.close()
        raise


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
        connect_timeout: int = 3,
        probe_timeout: int = 3,
    ) -> None:
        self._url = url.rstrip("/")
        self._fallback_url = fallback_url.rstrip("/") if fallback_url else None
        self._timeout = timeout
        # Fixed short connect timeout, independent of fallback: fail fast to
        # fallback (or surface unreachable) rather than waiting the full
        # read-timeout budget on an unreachable primary.
        self._connect_timeout = connect_timeout
        self._probe_timeout = probe_timeout

    def post_generate(self, payload: dict) -> str:
        """POST /api/generate. Returns response["response"] text.

        For stream=True payloads, drains the streaming response and returns ''.
        Raises OllamaConnectionError or OllamaTimeoutError on failure.
        """
        return self._post("/api/generate", payload)

    def _attempt(self, do_request):
        """Try primary then fallback, consulting the per-URL breaker.
        do_request(url) returns the result or raises an Ollama* error.
        HTTPError (server responded 4xx/5xx) is not an outage: mark the endpoint
        healthy and re-raise (no fallback, no trip). When every candidate URL is
        exhausted, raise an error of the LAST failure's type whose message names
        ALL endpoints (preserves the existing combined-error contract)."""
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
                _BREAKERS.record_success(url)     # endpoint responded -> healthy; clears probe
                raise
            except OllamaConnectionError as e:
                _BREAKERS.record_failure(url)
                last_err = e
                continue
            except OllamaTimeoutError as e:
                if self._probe_reachable(url):
                    _BREAKERS.record_slow(url)    # reachable but slow = busy, not a failure
                else:
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
            raise OllamaConnectionError(f"Ollama circuit open for all endpoints: {joined}")
        raise type(last_err)(f"Ollama failed at all endpoints ({joined}): {last_err}")

    def post_chat(self, payload: dict) -> tuple:
        """POST /api/chat. Returns (response_content_str, tool_calls_list).

        tool_calls_list is a list of {"name": str, "arguments": dict} dicts.
        Returns raw content without think-tag stripping (caller's responsibility).
        Raises OllamaConnectionError or OllamaTimeoutError on failure.
        """
        return self._attempt(lambda url: self._do_chat(url, payload))

    def _do_chat(self, url: str, payload: dict) -> tuple:
        try:
            resp = requests.post(
                f"{url}/api/chat", json=payload,
                timeout=(self._connect_timeout, self._timeout), stream=False,
            )
            resp.raise_for_status()
            return self._read_chat_response(resp)
        except requests.HTTPError as e:
            raise _http_error(url, e) from e
        except requests.ConnectTimeout as e:
            raise OllamaConnectionError(
                f"Ollama connect timed out at {url} (connect={self._connect_timeout}s)"
            ) from e
        except requests.Timeout:
            raise OllamaTimeoutError(
                f"Ollama timed out at {url} (connect={self._connect_timeout}s, read={self._timeout}s)"
            )
        except requests.ConnectionError as e:
            raise OllamaConnectionError(f"Ollama unreachable at {url}: {e}") from e
        except requests.RequestException as e:
            raise OllamaConnectionError(f"Ollama request failed at {url}: {e}") from e

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

    def create_model(self, model: str, from_model: str, parameters: dict) -> None:
        """POST /api/create to derive a model variant (e.g. a num_ctx override).

        Idempotent on the Ollama side — re-creating an identical variant reuses
        existing layers. Raises OllamaConnectionError / OllamaTimeoutError on failure.
        """
        payload = {
            "model": model,
            "from": from_model,
            "parameters": parameters,
            "stream": False,
        }
        try:
            resp = requests.post(
                f"{self._url}/api/create",
                json=payload,
                timeout=(self._connect_timeout, self._timeout),
                stream=False,
            )
            resp.raise_for_status()
        except requests.HTTPError as e:
            raise _http_error(self._url, e, verb="create_model") from e
        except requests.ConnectTimeout as e:
            raise OllamaConnectionError(
                f"Ollama create_model connect timed out at {self._url} (connect={self._connect_timeout}s)"
            ) from e
        except requests.Timeout:
            raise OllamaTimeoutError(
                f"Ollama create_model timed out at {self._url} "
                f"(connect={self._connect_timeout}s, read={self._timeout}s)"
            )
        except requests.ConnectionError as e:
            raise OllamaConnectionError(
                f"Ollama create_model failed at {self._url}: {e}"
            ) from e
        except requests.RequestException as e:
            raise OllamaConnectionError(f"Ollama create_model request failed: {e}") from e

    def get_ps(self) -> dict:
        """GET /api/ps. Returns parsed JSON dict.

        Raises OllamaConnectionError or OllamaTimeoutError on failure.
        """
        return self._get("/api/ps")

    def _probe_reachable(self, url: str) -> bool:
        """Cheap liveness probe OUTSIDE the breaker path; never raises; never touches _BREAKERS."""
        try:
            resp = requests.get(f"{url}/api/version", timeout=(self._connect_timeout, self._probe_timeout))
            return resp.status_code < 500
        except Exception:
            return False

    # ── internals ─────────────────────────────────────────────────────────

    def _post(self, path: str, payload: dict) -> str:
        is_streaming = payload.get("stream", False)
        return self._attempt(lambda url: self._do_post(url, path, payload, is_streaming))

    def _do_post(self, url: str, path: str, payload: dict, is_streaming: bool) -> str:
        try:
            resp = requests.post(
                f"{url}{path}", json=payload,
                timeout=(self._connect_timeout, self._timeout), stream=is_streaming,
            )
            _raise_for_status_closing(resp)
            return self._read_post_response(resp, is_streaming)
        except requests.HTTPError as e:
            raise _http_error(url, e) from e
        except requests.ConnectTimeout as e:
            raise OllamaConnectionError(
                f"Ollama connect timed out at {url} (connect={self._connect_timeout}s)"
            ) from e
        except requests.Timeout:
            raise OllamaTimeoutError(
                f"Ollama timed out at {url} (connect={self._connect_timeout}s, read={self._timeout}s)"
            )
        except requests.ConnectionError as e:
            raise OllamaConnectionError(f"Ollama unreachable at {url}: {e}") from e
        except requests.RequestException as e:
            raise OllamaConnectionError(f"Ollama request failed at {url}: {e}") from e

    def _get(self, path: str) -> dict:
        return self._attempt(lambda url: self._do_get(url, path))

    def _do_get(self, url: str, path: str) -> dict:
        try:
            resp = requests.get(f"{url}{path}", timeout=(self._connect_timeout, self._timeout))
            resp.raise_for_status()
            return resp.json()
        except requests.HTTPError as e:
            raise _http_error(url, e) from e
        except requests.ConnectTimeout as e:
            raise OllamaConnectionError(
                f"Ollama connect timed out at {url} (connect={self._connect_timeout}s)"
            ) from e
        except requests.Timeout:
            raise OllamaTimeoutError(
                f"Ollama timed out at {url} (connect={self._connect_timeout}s, read={self._timeout}s)"
            )
        except requests.ConnectionError as e:
            raise OllamaConnectionError(f"Ollama unreachable at {url}: {e}") from e
        except requests.RequestException as e:
            raise OllamaConnectionError(f"Ollama request failed at {url}: {e}") from e

    @staticmethod
    def _read_post_response(resp, is_streaming: bool) -> str:
        if is_streaming:
            for _ in resp.iter_content(chunk_size=None):
                pass
            return ""
        return resp.json().get("response", "")
