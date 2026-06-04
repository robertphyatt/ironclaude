"""Tests for Ollama model discovery and classification."""

from unittest.mock import patch, MagicMock

import pytest
import requests

from ironclaude.ollama_inventory import OllamaInventory


@pytest.fixture
def inventory():
    """Create an OllamaInventory instance with default host."""
    return OllamaInventory()


def _make_api_tags_response(models):
    """Build a mock /api/tags JSON response."""
    return {"models": models}


def _make_model_entry(name, family, parameter_size, quantization_level="Q4_K_M"):
    """Build a single model entry matching Ollama /api/tags format."""
    return {
        "name": name,
        "model": name,
        "modified_at": "2026-01-01T00:00:00Z",
        "size": 16000000000,
        "digest": "abc123def456",
        "details": {
            "parent_model": "",
            "format": "gguf",
            "family": family,
            "families": [family],
            "parameter_size": parameter_size,
            "quantization_level": quantization_level,
        },
    }


class TestProbe:
    def test_successful_probe_returns_classified_models(self, inventory):
        """Probe with valid response returns reachable=True and classified models."""
        response_data = _make_api_tags_response([
            _make_model_entry("gemma4:27b", "gemma4", "27B"),
            _make_model_entry("llama3.2:3b", "llama", "3B"),
        ])
        mock_resp = MagicMock()
        mock_resp.json.return_value = response_data
        mock_resp.raise_for_status = MagicMock()

        with patch("requests.get", return_value=mock_resp):
            result = inventory.get_inventory()

        assert result["ollama_reachable"] is True
        assert len(result["models"]) == 2
        assert result["models"][0]["name"] == "gemma4:27b"
        assert result["models"][0]["capability_tier"] == "complex"
        assert result["models"][1]["name"] == "llama3.2:3b"
        assert result["models"][1]["capability_tier"] == "moderate"

    def test_ollama_unreachable_returns_false(self, inventory):
        """ConnectionError returns reachable=False, empty models."""
        with patch("requests.get", side_effect=requests.ConnectionError("refused")):
            result = inventory.get_inventory()

        assert result["ollama_reachable"] is False
        assert result["models"] == []

    def test_timeout_returns_unreachable(self, inventory):
        """Timeout returns same structure as unreachable."""
        with patch("requests.get", side_effect=requests.Timeout("timed out")):
            result = inventory.get_inventory()

        assert result["ollama_reachable"] is False
        assert result["models"] == []

    def test_malformed_json_returns_empty_models(self, inventory):
        """Invalid JSON response returns reachable=True, empty models."""
        mock_resp = MagicMock()
        mock_resp.json.side_effect = requests.JSONDecodeError("bad json", "", 0)
        mock_resp.raise_for_status = MagicMock()

        with patch("requests.get", return_value=mock_resp):
            result = inventory.get_inventory()

        assert result["ollama_reachable"] is True
        assert result["models"] == []

    def test_partial_model_failure_skips_bad_entry(self, inventory):
        """Model entry with missing details is skipped; others classified."""
        good = _make_model_entry("qwen3:8b", "qwen3", "8B")
        bad = {"name": "broken-model", "model": "broken-model"}
        response_data = _make_api_tags_response([good, bad])
        mock_resp = MagicMock()
        mock_resp.json.return_value = response_data
        mock_resp.raise_for_status = MagicMock()

        with patch("requests.get", return_value=mock_resp):
            result = inventory.get_inventory()

        assert result["ollama_reachable"] is True
        assert len(result["models"]) == 1
        assert result["models"][0]["name"] == "qwen3:8b"


class TestClassify:
    def test_parameter_size_parsing_billions(self, inventory):
        """'7B' parses to 7.0, '1.5B' to 1.5, '70B' to 70.0."""
        response_data = _make_api_tags_response([
            _make_model_entry("m1:latest", "llama", "7B"),
            _make_model_entry("m2:latest", "llama", "1.5B"),
            _make_model_entry("m3:latest", "llama", "70B"),
        ])
        mock_resp = MagicMock()
        mock_resp.json.return_value = response_data
        mock_resp.raise_for_status = MagicMock()

        with patch("requests.get", return_value=mock_resp):
            result = inventory.get_inventory(force_refresh=True)

        assert result["models"][0]["parameter_count_b"] == 7.0
        assert result["models"][1]["parameter_count_b"] == 1.5
        assert result["models"][2]["parameter_count_b"] == 70.0

    def test_parameter_size_parsing_millions(self, inventory):
        """'400M' parses to 0.4."""
        response_data = _make_api_tags_response([
            _make_model_entry("tiny:latest", "llama", "400M"),
        ])
        mock_resp = MagicMock()
        mock_resp.json.return_value = response_data
        mock_resp.raise_for_status = MagicMock()

        with patch("requests.get", return_value=mock_resp):
            result = inventory.get_inventory(force_refresh=True)

        assert result["models"][0]["parameter_count_b"] == 0.4

    def test_capability_tier_boundaries(self, inventory):
        """<3B=simple, 3-14B=moderate, >14B=complex."""
        response_data = _make_api_tags_response([
            _make_model_entry("small:latest", "llama", "2B"),
            _make_model_entry("mid:latest", "llama", "3B"),
            _make_model_entry("mid2:latest", "llama", "14B"),
            _make_model_entry("big:latest", "llama", "15B"),
        ])
        mock_resp = MagicMock()
        mock_resp.json.return_value = response_data
        mock_resp.raise_for_status = MagicMock()

        with patch("requests.get", return_value=mock_resp):
            result = inventory.get_inventory(force_refresh=True)

        assert result["models"][0]["capability_tier"] == "simple"
        assert result["models"][1]["capability_tier"] == "moderate"
        assert result["models"][2]["capability_tier"] == "moderate"
        assert result["models"][3]["capability_tier"] == "complex"

    def test_moe_detection(self, inventory):
        """Known MoE family detected; unknown defaults to dense."""
        response_data = _make_api_tags_response([
            _make_model_entry("mixtral:latest", "mixtral", "47B"),
            _make_model_entry("llama:latest", "llama", "7B"),
        ])
        mock_resp = MagicMock()
        mock_resp.json.return_value = response_data
        mock_resp.raise_for_status = MagicMock()

        with patch("requests.get", return_value=mock_resp):
            result = inventory.get_inventory(force_refresh=True)

        assert result["models"][0]["architecture"] == "moe"
        assert result["models"][1]["architecture"] == "dense"

    def test_known_strengths(self, inventory):
        """Known family returns strengths; unknown returns None."""
        response_data = _make_api_tags_response([
            _make_model_entry("gemma4:27b", "gemma4", "27B"),
            _make_model_entry("unknown:latest", "novelmodel", "7B"),
        ])
        mock_resp = MagicMock()
        mock_resp.json.return_value = response_data
        mock_resp.raise_for_status = MagicMock()

        with patch("requests.get", return_value=mock_resp):
            result = inventory.get_inventory(force_refresh=True)

        assert result["models"][0]["known_strengths"] is not None
        assert "structured text extraction" in result["models"][0]["known_strengths"]
        assert result["models"][1]["known_strengths"] is None


class TestCaching:
    def test_cache_hit(self, inventory):
        """Second call without force_refresh returns cached result."""
        response_data = _make_api_tags_response([
            _make_model_entry("m:latest", "llama", "7B"),
        ])
        mock_resp = MagicMock()
        mock_resp.json.return_value = response_data
        mock_resp.raise_for_status = MagicMock()

        with patch("requests.get", return_value=mock_resp) as mock_get:
            inventory.get_inventory()
            inventory.get_inventory()

        assert mock_get.call_count == 1

    def test_force_refresh_re_probes(self, inventory):
        """force_refresh=True calls the API again."""
        response_data = _make_api_tags_response([
            _make_model_entry("m:latest", "llama", "7B"),
        ])
        mock_resp = MagicMock()
        mock_resp.json.return_value = response_data
        mock_resp.raise_for_status = MagicMock()

        with patch("requests.get", return_value=mock_resp) as mock_get:
            inventory.get_inventory()
            inventory.get_inventory(force_refresh=True)

        assert mock_get.call_count == 2
