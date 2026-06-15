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

from ironclaude.ollama_client import OllamaClient, OllamaError

logger = logging.getLogger(__name__)

_DEFAULT_CONFIG_PATH = os.path.expanduser("~/.claude/ironclaude-hooks-config.json")
_DEFAULT_MODEL = "gemma4:12b-it-qat"
_THINK_TAG_RE = re.compile(r"<think>.*?</think>", re.DOTALL)


class LocalGrader:
    """Thin wrapper around OllamaClient for LLM-based grading.

    Handles config loading, think-tag stripping, JSON parsing, and schema
    validation. Returns infrastructure_error dict on any failure — never raises
    for handled error cases (OllamaError, empty response, non-JSON, missing fields).
    """

    def __init__(self, config_path: str | None = None) -> None:
        self._config_path = config_path or _DEFAULT_CONFIG_PATH
        self._client: OllamaClient | None = None
        self._cfg: dict = {}

    @staticmethod
    def _build_infrastructure_error(detail: str) -> dict:
        return {"infrastructure_error": True, "error_detail": detail}

    def _get_client(self) -> OllamaClient:
        if self._client is None:
            try:
                with open(self._config_path) as f:
                    cfg = json.load(f)
            except (FileNotFoundError, json.JSONDecodeError) as e:
                logger.warning("Ollama config unavailable (%s): using localhost defaults", e)
                cfg = {}
            ollama_cfg = cfg.get("ollama", {})
            self._cfg = ollama_cfg
            self._client = OllamaClient(
                url=ollama_cfg.get("url", "http://localhost:11434"),
                fallback_url=ollama_cfg.get("fallback_url"),
                timeout=cfg.get("timeout_seconds", 120),
            )
        return self._client

    def grade(self, system_prompt: str, user_prompt: str, schema: dict | None = None) -> dict:
        """Grade content using a local Ollama model.

        Returns parsed JSON dict on success, or
        {"infrastructure_error": True, "error_detail": "..."} on failure.
        """
        client = self._get_client()
        model = self._cfg.get("model", _DEFAULT_MODEL)
        payload: dict = {
            "model": model,
            "prompt": f"{system_prompt}\n\n{user_prompt}",
            "stream": False,
            "options": {"temperature": 0.1, "num_predict": -1},
        }
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

        result_text = _THINK_TAG_RE.sub("", result_text).strip()

        try:
            parsed = json.loads(result_text)
        except json.JSONDecodeError:
            detail = f"Non-JSON response ({len(result_text)} chars): {result_text[:200]}"
            logger.warning(detail)
            return self._build_infrastructure_error(detail)

        if schema:
            required = schema.get("required", [])
            missing = [k for k in required if k not in parsed]
            if missing:
                detail = f"Response missing required fields {missing}: {result_text[:200]}"
                logger.warning(detail)
                return self._build_infrastructure_error(detail)

        return parsed
