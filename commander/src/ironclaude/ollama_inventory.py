"""Ollama model discovery and classification.

Probes Ollama's HTTP API to discover locally available models and classifies
them by parameter count, architecture (dense/MoE), and known strengths.
Results are cached in-memory; use force_refresh to re-probe.
"""

from __future__ import annotations

import logging
import os
import re

import requests

logger = logging.getLogger("ironclaude.ollama_inventory")

_MOE_FAMILIES = {"mixtral", "deepseek-v2", "deepseek-v3", "qwen-moe", "dbrx"}

_FAMILY_STRENGTHS = {
    "gemma4": "structured text extraction, code understanding",
    "qwen3": "reliable JSON output, reasoning",
    "llama4": "general purpose, instruction following",
    "codellama": "code generation and analysis",
    "deepseek-coder": "code generation",
}

_PARAM_SIZE_RE = re.compile(r'^([\d.]+)([BM])$', re.IGNORECASE)


class OllamaInventory:

    def __init__(self, host: str | None = None):
        self._host = host or os.environ.get("OLLAMA_HOST", "http://localhost:11434")
        self._cache: dict | None = None

    def get_inventory(self, force_refresh: bool = False) -> dict:
        if self._cache is not None and not force_refresh:
            return self._cache
        self._cache = self._probe()
        return self._cache

    def _probe(self) -> dict:
        try:
            resp = requests.get(f"{self._host}/api/tags", timeout=2)
            resp.raise_for_status()
        except (requests.ConnectionError, requests.Timeout):
            logger.warning("Ollama not reachable at %s", self._host)
            return {"ollama_reachable": False, "models": []}
        except requests.RequestException as e:
            logger.warning("Ollama request failed: %s", e)
            return {"ollama_reachable": False, "models": []}

        try:
            data = resp.json()
        except requests.JSONDecodeError:
            logger.warning("Ollama returned invalid JSON")
            return {"ollama_reachable": True, "models": []}

        models = []
        for entry in data.get("models", []):
            try:
                classified = self._classify(entry)
                models.append(classified)
            except (KeyError, TypeError) as e:
                logger.warning("Skipping model %s: %s", entry.get("name", "unknown"), e)

        return {"ollama_reachable": True, "models": models}

    def _classify(self, entry: dict) -> dict:
        details = entry["details"]
        param_size_raw = details["parameter_size"]
        param_count_b = self._parse_param_size(param_size_raw)
        family = details.get("family", "")

        if param_count_b < 3:
            tier = "simple"
        elif param_count_b <= 14:
            tier = "moderate"
        else:
            tier = "complex"

        architecture = "moe" if family in _MOE_FAMILIES else "dense"
        strengths = _FAMILY_STRENGTHS.get(family)

        return {
            "name": entry["name"],
            "parameter_size": param_size_raw,
            "parameter_count_b": param_count_b,
            "family": family,
            "quantization": details.get("quantization_level", ""),
            "capability_tier": tier,
            "architecture": architecture,
            "known_strengths": strengths,
        }

    @staticmethod
    def _parse_param_size(raw: str) -> float:
        match = _PARAM_SIZE_RE.match(raw)
        if not match:
            return 0.0
        value = float(match.group(1))
        unit = match.group(2).upper()
        if unit == "M":
            return value / 1000.0
        return value
