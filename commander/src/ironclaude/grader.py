"""Standalone LLM grading module using a local Ollama instance.

Extracted from OrchestratorTools._call_local_grader so that main.py and
brain_client.py can share the same grading infrastructure without importing
from orchestrator_mcp.
"""
from __future__ import annotations

import json
import logging
import os
import re

from ironclaude import paths
from ironclaude.communication_profiles import (
    CommunicationProfileError,
    apply_communication_profile,
)
from ironclaude.backend_resolver import make_client, resolve_backend
from ironclaude.ollama_client import OllamaClient, OllamaError

logger = logging.getLogger(__name__)

_DEFAULT_MODEL = "gemma4:12b-it-qat"
_THINK_TAG_RE = re.compile(r"<think>.*?</think>", re.DOTALL)
# Strip leaked chat-template control tokens (e.g. <|tool_response>, <|im_end|>)
# that small local models sometimes emit around their JSON output.
_SPECIAL_TOKEN_RE = re.compile(r"<\|[^>]*>")
# Strip markdown code fences (```json ... ```) that some models wrap JSON in.
_MARKDOWN_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


class LocalGrader:
    """Thin wrapper around OllamaClient for LLM-based grading.

    Handles config loading, think-tag stripping, JSON parsing, and schema
    validation. Returns infrastructure_error dict on any failure — never raises
    for handled error cases (OllamaError, empty response, non-JSON, missing fields).
    """

    def __init__(self, config_path: str | None = None, timeout: int | None = None,
                 keep_alive: str | None = None) -> None:
        self._config_path = config_path or paths.hooks_config()
        self._client: OllamaClient | None = None
        self._cfg: dict = {}
        self._timeout_override = timeout
        self._keep_alive = keep_alive     # message-path graders set "30m"; grader path leaves None
        self._client_mtime: float | None = None
        # Resolved backend for the "grader" spot. Defaults to ollama so a cached
        # client injected by tests (which never triggers a rebuild) keeps the
        # ollama payload path.
        self._backend: str = "ollama"
        self._openai_model: str | None = None
        self._openai_max_tokens: int | None = None
        self._spot_model: str | None = None
        self._spot_thinking: bool = False
        # Fast lane (per-call read_timeout): a SEPARATE cached client, keyed by
        # (config mtime, read_timeout), built from the same resolved config as
        # self._client but with a short read timeout. Never overwrites
        # self._client/self._client_mtime — the 600s-timeout normal path stays
        # byte-identical when read_timeout is not passed to grade().
        self._fast_client: OllamaClient | None = None
        self._fast_client_key: tuple[float | None, float] | None = None
        self._fast_state: tuple | None = None

    @staticmethod
    def _build_infrastructure_error(detail: str) -> dict:
        return {"infrastructure_error": True, "error_detail": detail}

    def _read_config(self) -> dict:
        """Load the config JSON (or {} on missing/invalid file). Never raises."""
        try:
            with open(self._config_path) as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError) as e:
            logger.warning("Ollama config unavailable (%s): using localhost defaults", e)
            return {}

    @staticmethod
    def _resolve_for_grader(cfg: dict):
        """resolve_backend cfg for the 'grader' spot, plus its spot overrides."""
        resolved = resolve_backend(cfg, "grader")
        spot_model = ((cfg.get("spots") or {}).get("grader") or {}).get("model")
        spot_thinking = bool(((cfg.get("spots") or {}).get("grader") or {}).get("thinking", False))
        return resolved, spot_model, spot_thinking

    @staticmethod
    def _build_client(cfg: dict, resolved, timeout) -> tuple:
        """Construct a client for `resolved`/`cfg` with the given read timeout.

        Returns (client, backend, openai_model, openai_max_tokens, effective_cfg).
        Pure — does not touch `self`, so it is safe to share between the normal
        (cached self._client) and fast (per-call read_timeout) paths.
        """
        if resolved.backend == "openai":
            client = make_client(resolved, timeout=timeout)
            return client, "openai", resolved.model, resolved.max_tokens, cfg.get("openai", {})
        # Ollama path — construct OllamaClient directly (by name) so the
        # existing test seams that patch `grader.OllamaClient` stay intact.
        ollama_cfg = cfg.get("ollama", {})
        client = OllamaClient(
            url=ollama_cfg.get("url", "http://localhost:11434"),
            fallback_url=ollama_cfg.get("fallback_url"),
            timeout=timeout,
            connect_timeout=(resolved.connect_timeout or 3),
            probe_timeout=(resolved.probe_timeout or 3),
        )
        return client, "ollama", None, None, ollama_cfg

    def _get_client(self) -> OllamaClient:
        try:
            mtime = os.stat(self._config_path).st_mtime
        except OSError:
            mtime = None
        # Rebuild only when there is no client yet, or the file exists AND its mtime
        # changed. A deleted config keeps the last client (no per-call rebuild spam).
        if self._client is None or (mtime is not None and mtime != self._client_mtime):
            cfg = self._read_config()
            resolved, self._spot_model, self._spot_thinking = self._resolve_for_grader(cfg)
            timeout = (
                self._timeout_override if self._timeout_override is not None
                else (resolved.timeout if resolved.timeout is not None else 120)
            )
            self._client, self._backend, self._openai_model, self._openai_max_tokens, self._cfg = (
                self._build_client(cfg, resolved, timeout)
            )
            self._client_mtime = mtime
        return self._client

    def _get_fast_client_state(self, read_timeout: float) -> tuple:
        """Build/cache a SEPARATE client bounded to `read_timeout`, from the same
        resolved config as the normal client. Keyed by (config mtime, read_timeout)
        so a config hot-reload or a differing bound rebuilds it; otherwise cached.
        Never touches self._client/self._client_mtime.

        Returns (client, backend, openai_model, openai_max_tokens, effective_cfg,
        spot_model, spot_thinking).
        """
        try:
            mtime = os.stat(self._config_path).st_mtime
        except OSError:
            mtime = None
        key = (mtime, read_timeout)
        if self._fast_client is None or self._fast_client_key != key:
            cfg = self._read_config()
            resolved, spot_model, spot_thinking = self._resolve_for_grader(cfg)
            client, backend, openai_model, openai_max_tokens, eff_cfg = self._build_client(
                cfg, resolved, read_timeout
            )
            self._fast_client = client
            self._fast_client_key = key
            self._fast_state = (backend, openai_model, openai_max_tokens, eff_cfg, spot_model, spot_thinking)
        return (self._fast_client,) + self._fast_state

    def grade(
        self,
        system_prompt: str,
        user_prompt: str,
        schema: dict | None = None,
        *,
        read_timeout: float | None = None,
    ) -> dict:
        """Grade content using a local Ollama model.

        `read_timeout`, when given, bounds the underlying client's read timeout
        to that many seconds (a SEPARATE cached client from the normal one) so a
        caller with a hard wall-clock bound (see main.py `_grade_bounded`) can
        abandon a stalled call and have the daemon thread it left running end
        near that bound instead of the full (e.g. 600s) inference read timeout.
        Omitting it uses the normal cached client, unchanged.

        Returns parsed JSON dict on success, or
        {"infrastructure_error": True, "error_detail": "..."} on failure.
        """
        # IRONCLAUDE_LLM_PATH: local_grader
        try:
            profiled_system_prompt = apply_communication_profile(
                "local_grader", system_prompt
            )
        except CommunicationProfileError as exc:
            return self._build_infrastructure_error(str(exc))

        try:
            if read_timeout is None:
                client = self._get_client()
                backend = self._backend
                openai_model = self._openai_model
                openai_max_tokens = self._openai_max_tokens
                cfg = self._cfg
                spot_model = self._spot_model
                spot_thinking = self._spot_thinking
            else:
                (
                    client, backend, openai_model, openai_max_tokens, cfg,
                    spot_model, spot_thinking,
                ) = self._get_fast_client_state(read_timeout)
        except OllamaError as e:
            return self._build_infrastructure_error(str(e))
        if backend == "openai":
            model = openai_model or _DEFAULT_MODEL
            payload = {
                "model": model,
                "messages": [
                    {"role": "user", "content": f"{profiled_system_prompt}\n\n{user_prompt}"}
                ],
                "max_tokens": openai_max_tokens or 1024,
                "temperature": 0.1,
            }
            if schema is not None:
                payload["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {"name": "verdict", "schema": schema},
                }
            if not spot_thinking:
                # A STRICT OpenAI endpoint that rejects chat_template_kwargs (400) degrades to infrastructure_error; set spots.grader.thinking:true to send neither field for such endpoints.
                payload["reasoning_effort"] = "none"
                payload["chat_template_kwargs"] = {"enable_thinking": False}
        else:
            model = spot_model or cfg.get("model", _DEFAULT_MODEL)
            payload = {
                "model": model,
                "prompt": f"{profiled_system_prompt}\n\n{user_prompt}",
                "stream": False,
                "options": {"temperature": 0.1, "num_predict": -1},
            }
            if self._keep_alive is not None:
                payload["keep_alive"] = self._keep_alive
            if schema is not None:
                payload["format"] = schema

        try:
            result_text = client.post_generate(payload)
        except OllamaError as e:
            detail = str(e)
            logger.warning(detail)
            return self._build_infrastructure_error(detail)

        if not result_text:
            detail = "Ollama returned empty response"
            logger.warning(detail)
            return self._build_infrastructure_error(detail)

        logger.debug("Ollama raw response (%d chars): %.500s", len(result_text), result_text)

        result_text = _THINK_TAG_RE.sub("", result_text)
        result_text = _SPECIAL_TOKEN_RE.sub("", result_text).strip()

        fence_match = _MARKDOWN_FENCE_RE.search(result_text)
        if fence_match:
            result_text = fence_match.group(1)

        try:
            parsed = json.loads(result_text)
        except json.JSONDecodeError:
            detail = f"Non-JSON response ({len(result_text)} chars): {result_text[:200]}"
            logger.warning(detail)
            return self._build_infrastructure_error(detail)

        if not isinstance(parsed, dict):
            return self._build_infrastructure_error("Non-dict verdict: " + result_text[:200])

        if schema:
            required = schema.get("required", [])
            missing = [k for k in required if k not in parsed]
            if missing:
                detail = f"Response missing required fields {missing}: {result_text[:200]}"
                logger.warning(detail)
                return self._build_infrastructure_error(detail)

        return parsed


def truncate_middle(text: str, head: int = 1500, tail: int = 500) -> str:
    """Bound classifier input: keep the head and tail, elide the middle."""
    if len(text) <= head + tail:
        return text
    return f"{text[:head]}\n…[{len(text) - head - tail} chars elided]…\n{text[-tail:]}"
