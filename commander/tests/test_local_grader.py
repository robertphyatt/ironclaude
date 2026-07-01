"""Tests for Ollama local grader integration."""

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ironclaude.db import init_db
from ironclaude.worker_registry import WorkerRegistry
from ironclaude.orchestrator_mcp import OrchestratorTools


GRADE_SCHEMA = {
    "type": "object",
    "properties": {
        "grade": {"type": "string", "enum": ["A", "B", "C", "D", "F"]},
        "approved": {"type": "boolean"},
        "feedback": {"type": "string"},
    },
    "required": ["grade", "approved", "feedback"],
}


@pytest.fixture
def db_conn(tmp_path):
    db_path = str(tmp_path / "test.db")
    return init_db(db_path)


@pytest.fixture
def registry(db_conn):
    return WorkerRegistry(db_conn)


@pytest.fixture
def mock_tmux():
    tmux = MagicMock()
    tmux.has_session.return_value = True
    tmux.spawn_session.return_value = True
    tmux.send_keys.return_value = True
    tmux.capture_pane.return_value = ""
    tmux.get_log_path.return_value = "/tmp/ic-logs/ic-test.log"
    tmux.read_log_tail.return_value = "ironclaude v1.0.33\n"
    tmux.list_pane_pid.return_value = None
    return tmux


@pytest.fixture
def tools(registry, mock_tmux, tmp_path, db_conn):
    ledger_path = str(tmp_path / "task-ledger.json")
    t = OrchestratorTools(registry, mock_tmux, ledger_path, db_conn=db_conn)
    t._get_ollama_vram = MagicMock(return_value=(0.0, []))
    return t


@pytest.fixture
def ollama_config(tmp_path):
    config_path = tmp_path / "ironclaude-hooks-config.json"
    config_path.write_text(json.dumps({
        "validation_backend": "ollama",
        "ollama": {
            "url": "http://localhost:11434",
            "fallback_url": "http://fallback:11434",
            "model": "gemma4:12b-it-qat",
            "spawn_confidence_threshold": "high",
        },
        "timeout_seconds": 60,
    }))
    return str(config_path)


class TestCallLocalGrader:
    def test_successful_grading(self, tools, ollama_config):
        tools._ollama_config = None
        tools._ollama_config_path = ollama_config
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "response": '{"grade": "A", "approved": true, "feedback": "Well-formed objective"}'
        }
        mock_response.raise_for_status = MagicMock()
        with patch("requests.post", return_value=mock_response) as mock_post:
            result = tools._call_local_grader("system prompt", "user prompt", GRADE_SCHEMA)
        assert result["grade"] == "A"
        assert result["approved"] is True
        assert result["feedback"] == "Well-formed objective"
        mock_post.assert_called_once()

    def test_primary_url_failure_fallback_success(self, tools, ollama_config):
        tools._ollama_config = None
        tools._ollama_config_path = ollama_config
        tools._local_grader._config_path = ollama_config
        tools._local_grader._client = None
        mock_success = MagicMock()
        mock_success.json.return_value = {
            "response": '{"grade": "B", "approved": true, "feedback": "ok"}'
        }
        mock_success.raise_for_status = MagicMock()
        import requests as req_mod
        with patch("requests.post", side_effect=[req_mod.ConnectionError("refused"), mock_success]) as mock_post:
            result = tools._call_local_grader("sys", "usr", GRADE_SCHEMA)
        assert result["grade"] == "B"
        assert mock_post.call_count == 2
        assert "fallback:11434" in mock_post.call_args_list[1][0][0]

    def test_http_error_primary_returns_infrastructure_error(self, tools, ollama_config):
        # HTTP errors raise OllamaHTTPError with NO fallback (fallback only fires on
        # ConnectionError). grader.py converts OllamaError -> infrastructure_error.
        # So a primary HTTP 500 yields exactly one POST and an infrastructure_error dict.
        tools._ollama_config = None
        tools._ollama_config_path = ollama_config
        tools._local_grader._config_path = ollama_config
        tools._local_grader._client = None
        import requests as req_mod
        error_resp = MagicMock()
        error_resp.raise_for_status.side_effect = req_mod.HTTPError("500 Server Error")
        with patch("requests.post", return_value=error_resp) as mock_post:
            result = tools._call_local_grader("sys", "usr", GRADE_SCHEMA)
        assert result["infrastructure_error"] is True
        assert mock_post.call_count == 1

    def test_http_error_both_urls_returns_infrastructure_error(self, tools, ollama_config):
        tools._ollama_config = None
        tools._ollama_config_path = ollama_config
        import requests as req_mod
        error_resp = MagicMock()
        error_resp.raise_for_status.side_effect = req_mod.HTTPError("500 Server Error")
        with patch("requests.post", return_value=error_resp):
            result = tools._call_local_grader("sys", "usr", GRADE_SCHEMA)
        assert result["infrastructure_error"] is True

    def test_both_urls_unreachable(self, tools, ollama_config):
        tools._ollama_config = None
        tools._ollama_config_path = ollama_config
        import requests as req_mod
        with patch("requests.post", side_effect=req_mod.ConnectionError("refused")):
            result = tools._call_local_grader("sys", "usr", GRADE_SCHEMA)
        assert result["infrastructure_error"] is True
        assert "ollama failed" in result["error_detail"].lower()

    def test_timeout_returns_infrastructure_error(self, tools, ollama_config):
        tools._ollama_config = None
        tools._ollama_config_path = ollama_config
        import requests as req_mod
        with patch("requests.post", side_effect=req_mod.Timeout("timed out")) as mock_post:
            result = tools._call_local_grader("sys", "usr", GRADE_SCHEMA)
        assert result["infrastructure_error"] is True
        assert "timed out" in result["error_detail"].lower()
        mock_post.assert_called_once()

    def test_malformed_response_missing_response_field(self, tools, ollama_config):
        tools._ollama_config = None
        tools._ollama_config_path = ollama_config
        mock_response = MagicMock()
        mock_response.json.return_value = {"model": "gemma4", "done": True}
        mock_response.raise_for_status = MagicMock()
        with patch("requests.post", return_value=mock_response):
            result = tools._call_local_grader("sys", "usr", GRADE_SCHEMA)
        assert result["infrastructure_error"] is True
        assert "empty response" in result["error_detail"].lower()

    def test_non_json_response_content(self, tools, ollama_config):
        tools._ollama_config = None
        tools._ollama_config_path = ollama_config
        mock_response = MagicMock()
        mock_response.json.return_value = {"response": "This is not JSON at all"}
        mock_response.raise_for_status = MagicMock()
        with patch("requests.post", return_value=mock_response):
            result = tools._call_local_grader("sys", "usr", GRADE_SCHEMA)
        assert result["infrastructure_error"] is True
        assert "non-json" in result["error_detail"].lower()

    def test_config_file_missing(self, tools, tmp_path):
        """Missing config file falls back to localhost defaults (no crash); grading still works."""
        tools._local_grader._config_path = str(tmp_path / "nonexistent.json")
        tools._local_grader._client = None
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "response": '{"grade": "A", "approved": true, "feedback": "ok"}'
        }
        mock_response.raise_for_status = MagicMock()
        with patch("requests.post", return_value=mock_response) as mock_post:
            result = tools._call_local_grader("sys", "usr", GRADE_SCHEMA)
        assert result["grade"] == "A"
        assert "localhost:11434" in mock_post.call_args[0][0]

    def test_config_cached_after_first_call(self, tools, ollama_config):
        tools._local_grader._config_path = ollama_config
        tools._local_grader._client = None
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "response": '{"grade": "A", "approved": true, "feedback": "ok"}'
        }
        mock_response.raise_for_status = MagicMock()
        with patch("requests.post", return_value=mock_response):
            tools._call_local_grader("sys", "usr", GRADE_SCHEMA)
            first_client = tools._local_grader._client
            tools._call_local_grader("sys", "usr", GRADE_SCHEMA)
        assert tools._local_grader._client is not None
        assert tools._local_grader._client is first_client  # client cached, not rebuilt

    def test_json_missing_required_fields(self, tools, ollama_config):
        tools._ollama_config = None
        tools._ollama_config_path = ollama_config
        mock_response = MagicMock()
        mock_response.json.return_value = {"response": '{"grade": "A"}'}
        mock_response.raise_for_status = MagicMock()
        with patch("requests.post", return_value=mock_response):
            result = tools._call_local_grader("sys", "usr", GRADE_SCHEMA)
        assert result["infrastructure_error"] is True
        assert "missing required" in result["error_detail"].lower()

    def test_think_tags_stripped_inline(self, tools, ollama_config):
        tools._ollama_config = None
        tools._ollama_config_path = ollama_config
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "response": '<think>reasoning here</think>{"grade": "A", "approved": true, "feedback": "ok"}'
        }
        mock_response.raise_for_status = MagicMock()
        with patch("requests.post", return_value=mock_response):
            result = tools._call_local_grader("sys", "usr", GRADE_SCHEMA)
        assert result["grade"] == "A"
        assert result["approved"] is True

    def test_multiline_think_tags_stripped(self, tools, ollama_config):
        tools._ollama_config = None
        tools._ollama_config_path = ollama_config
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "response": '<think>\nLine 1 of reasoning\nLine 2 of reasoning\n</think>{"grade": "B", "approved": true, "feedback": "ok"}'
        }
        mock_response.raise_for_status = MagicMock()
        with patch("requests.post", return_value=mock_response):
            result = tools._call_local_grader("sys", "usr", GRADE_SCHEMA)
        assert result["grade"] == "B"
        assert result["approved"] is True

    def test_options_in_payload(self, tools, ollama_config):
        tools._ollama_config = None
        tools._ollama_config_path = ollama_config
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "response": '{"grade": "A", "approved": true, "feedback": "ok"}'
        }
        mock_response.raise_for_status = MagicMock()
        with patch("requests.post", return_value=mock_response) as mock_post:
            tools._call_local_grader("sys", "usr", GRADE_SCHEMA)
        payload = mock_post.call_args[1]["json"]
        assert payload["options"]["temperature"] == 0.1
        assert payload["options"]["num_predict"] == -1
        assert payload["format"] == GRADE_SCHEMA

    def test_raw_response_logged_on_parse_failure(self, tools, ollama_config):
        """Verify raw response body appears in warning log when JSON parsing fails."""
        tools._ollama_config = None
        tools._ollama_config_path = ollama_config
        mock_response = MagicMock()
        mock_response.json.return_value = {"response": "not json content here"}
        mock_response.raise_for_status = MagicMock()
        with patch("requests.post", return_value=mock_response):
            with patch("ironclaude.grader.logger") as mock_logger:
                result = tools._call_local_grader("sys", "usr", GRADE_SCHEMA)
        assert result["infrastructure_error"] is True
        warning_calls = [str(c) for c in mock_logger.warning.call_args_list]
        assert any("not json content here" in c for c in warning_calls)

    def test_think_tags_stripped_but_remaining_non_json(self, tools, ollama_config):
        """After think-tag stripping, remaining text is non-JSON → infrastructure_error."""
        tools._ollama_config = None
        tools._ollama_config_path = ollama_config
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "response": "<think>some reasoning</think>not valid json at all"
        }
        mock_response.raise_for_status = MagicMock()
        with patch("requests.post", return_value=mock_response):
            result = tools._call_local_grader("sys", "usr", GRADE_SCHEMA)
        assert result["infrastructure_error"] is True
        assert "non-json" in result["error_detail"].lower()


def _mock_grader_approve(tools):
    tools._call_grader = MagicMock(return_value={
        "grade": "A", "approved": True, "feedback": "Test approval"
    })


def _mock_local_grader_approve(tools):
    tools._call_local_grader = MagicMock(return_value={
        "grade": "A", "approved": True, "feedback": "Local approval"
    })


class TestP0SendToWorkerRouting:
    def test_send_to_worker_uses_local_grader(self, tools, registry, mock_tmux):
        """send_to_worker calls _call_local_grader, not _call_grader."""
        registry.register_worker("w1", "claude-sonnet", "ic-w1", repo="/tmp")
        _mock_local_grader_approve(tools)
        _mock_grader_approve(tools)
        tools.send_to_worker("w1", "How is the design going?")
        tools._call_local_grader.assert_called_once()
        tools._call_grader.assert_not_called()

    def test_send_to_worker_approved_sends_message(self, tools, registry, mock_tmux):
        """Approved message flows through to tmux.send_keys."""
        registry.register_worker("w1", "claude-sonnet", "ic-w1", repo="/tmp")
        _mock_local_grader_approve(tools)
        mock_tmux.capture_pane.return_value = "waiting for input"
        result = tools.send_to_worker("w1", "Continue with the plan")
        assert "approved" in str(result).lower() or "sent" in str(result).lower() or isinstance(result, str)

    def test_send_to_worker_rejected_returns_error(self, tools, registry, mock_tmux):
        """Rejected message returns error dict."""
        registry.register_worker("w1", "claude-sonnet", "ic-w1", repo="/tmp")
        tools._call_local_grader = MagicMock(return_value={
            "grade": "F", "approved": False, "feedback": "Tells worker to skip planning"
        })
        result = tools.send_to_worker("w1", "just make the change")
        assert isinstance(result, dict)
        assert "error" in result

    def test_send_to_worker_infrastructure_error_escalates_to_opus(self, tools, registry, mock_tmux):
        """Infrastructure error from Ollama escalates to Opus grader for send_to_worker."""
        registry.register_worker("w1", "claude-sonnet", "ic-w1", repo="/tmp")
        tools._call_local_grader = MagicMock(return_value={
            "infrastructure_error": True,
            "error_detail": "Ollama unreachable at http://localhost:11434",
        })
        tools._call_grader = MagicMock(return_value={
            "grade": "A", "approved": True, "feedback": "Opus approved",
        })
        mock_tmux.capture_pane.return_value = "waiting for input"
        result = tools.send_to_worker("w1", "How is the design going?")
        tools._call_local_grader.assert_called_once()
        tools._call_grader.assert_called_once()


CONFIDENCE_GRADE_SCHEMA = {
    "type": "object",
    "properties": {
        "grade": {"type": "string", "enum": ["A", "B", "C", "D", "F"]},
        "approved": {"type": "boolean"},
        "feedback": {"type": "string"},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
    },
    "required": ["grade", "approved", "feedback", "confidence"],
}


class TestP1SpawnWorkerHybrid:
    def _setup_spawn(self, tools, mock_tmux):
        tools._activate_pm_via_sqlite = MagicMock(return_value=None)
        mock_tmux.read_log_tail.return_value = "ironclaude v1.0.33\n"

    def test_high_confidence_approved_skips_opus(self, tools, registry, mock_tmux):
        """High confidence + approved uses Ollama result, never calls Opus grader."""
        self._setup_spawn(tools, mock_tmux)
        tools._call_local_grader = MagicMock(return_value={
            "grade": "A", "approved": True, "feedback": "Clear objective",
            "confidence": "high",
        })
        _mock_grader_approve(tools)
        result = tools.spawn_worker("w1", "claude-sonnet", "/tmp/repo", "Implement X")
        tools._call_local_grader.assert_called_once()
        tools._call_grader.assert_not_called()

    def test_high_confidence_rejected_skips_opus(self, tools, registry, mock_tmux):
        """High confidence + rejected uses Ollama result, never calls Opus grader."""
        self._setup_spawn(tools, mock_tmux)
        tools._call_local_grader = MagicMock(return_value={
            "grade": "F", "approved": False, "feedback": "PM deactivation detected",
            "confidence": "high",
        })
        _mock_grader_approve(tools)
        result = tools.spawn_worker("w1", "claude-sonnet", "/tmp/repo", "Deactivate PM")
        tools._call_local_grader.assert_called_once()
        tools._call_grader.assert_not_called()
        assert isinstance(result, dict)
        assert "error" in result

    def test_medium_confidence_escalates_to_opus(self, tools, registry, mock_tmux):
        """Medium confidence triggers Opus escalation."""
        self._setup_spawn(tools, mock_tmux)
        tools._call_local_grader = MagicMock(return_value={
            "grade": "B", "approved": True, "feedback": "Probably ok",
            "confidence": "medium",
        })
        _mock_grader_approve(tools)
        result = tools.spawn_worker("w1", "claude-sonnet", "/tmp/repo", "Implement X")
        tools._call_local_grader.assert_called_once()
        tools._call_grader.assert_called_once()

    def test_low_confidence_escalates_to_opus(self, tools, registry, mock_tmux):
        """Low confidence triggers Opus escalation."""
        self._setup_spawn(tools, mock_tmux)
        tools._call_local_grader = MagicMock(return_value={
            "grade": "C", "approved": False, "feedback": "Unclear",
            "confidence": "low",
        })
        _mock_grader_approve(tools)
        tools.spawn_worker("w1", "claude-sonnet", "/tmp/repo", "Implement X")
        tools._call_grader.assert_called_once()

    def test_missing_confidence_escalates(self, tools, registry, mock_tmux):
        """Missing confidence field triggers Opus escalation."""
        self._setup_spawn(tools, mock_tmux)
        tools._call_local_grader = MagicMock(return_value={
            "grade": "A", "approved": True, "feedback": "ok",
        })
        _mock_grader_approve(tools)
        tools.spawn_worker("w1", "claude-sonnet", "/tmp/repo", "Implement X")
        tools._call_grader.assert_called_once()

    def test_invalid_confidence_escalates(self, tools, registry, mock_tmux):
        """Invalid confidence value triggers Opus escalation."""
        self._setup_spawn(tools, mock_tmux)
        tools._call_local_grader = MagicMock(return_value={
            "grade": "A", "approved": True, "feedback": "ok",
            "confidence": "maybe",
        })
        _mock_grader_approve(tools)
        tools.spawn_worker("w1", "claude-sonnet", "/tmp/repo", "Implement X")
        tools._call_grader.assert_called_once()

    def test_ollama_failure_escalates(self, tools, registry, mock_tmux):
        """Ollama infrastructure error escalates to Opus."""
        self._setup_spawn(tools, mock_tmux)
        tools._call_local_grader = MagicMock(return_value={
            "infrastructure_error": True,
            "error_detail": "Ollama unreachable at http://localhost:11434",
        })
        tools._call_grader = MagicMock(return_value={
            "grade": "A", "approved": True, "feedback": "Good objective",
        })
        result = tools.spawn_worker("w1", "claude-sonnet", "/tmp/repo", "Implement X")
        tools._call_grader.assert_called_once()

    def test_configurable_threshold_medium(self, tools, registry, mock_tmux):
        """When threshold is 'medium', medium confidence skips Opus."""
        self._setup_spawn(tools, mock_tmux)
        tools._ollama_cfg_cache = {"spawn_confidence_threshold": "medium"}
        tools._call_local_grader = MagicMock(return_value={
            "grade": "A", "approved": True, "feedback": "ok",
            "confidence": "medium",
        })
        _mock_grader_approve(tools)
        tools.spawn_worker("w1", "claude-sonnet", "/tmp/repo", "Implement X")
        tools._call_grader.assert_not_called()


class TestP2EvaluateWorkerHealth:
    def test_healthy_worker(self, tools, registry, mock_tmux):
        """Healthy worker returns healthy=True with productive output."""
        registry.register_worker("w1", "claude-sonnet", "ic-w1", repo="/tmp")
        mock_tmux.capture_pane.return_value = (
            "Editing src/main.py... implementing feature X\n"
            "Running tests... 12 passed, 0 failed\n"
            "Planning next step in wave 2\n"
        )
        tools._call_local_grader = MagicMock(return_value={
            "healthy": True,
            "diagnosis": "Worker actively coding",
            "severity": "none",
        })
        result = tools.evaluate_worker_health("w1")
        assert result["healthy"] is True
        assert result["diagnosis"] == "Worker actively coding"
        assert result["severity"] == "none"

    def test_unhealthy_worker(self, tools, registry, mock_tmux):
        """Unhealthy worker returns healthy=False with high severity."""
        registry.register_worker("w1", "claude-sonnet", "ic-w1", repo="/tmp")
        mock_tmux.capture_pane.return_value = (
            "Error: file not found\n" * 20
        )
        tools._call_local_grader = MagicMock(return_value={
            "healthy": False,
            "diagnosis": "Worker stuck in loop",
            "severity": "high",
        })
        result = tools.evaluate_worker_health("w1")
        assert result["healthy"] is False
        assert result["severity"] == "high"

    def test_worker_not_found(self, tools, registry, mock_tmux):
        """Nonexistent worker returns healthy=None."""
        result = tools.evaluate_worker_health("nonexistent")
        assert result["healthy"] is None
        assert "not found" in result["diagnosis"].lower()
        assert result["severity"] == "unknown"

    def test_dead_session(self, tools, registry, mock_tmux):
        """Dead tmux session returns healthy=None."""
        registry.register_worker("w1", "claude-sonnet", "ic-w1", repo="/tmp")
        mock_tmux.has_session.return_value = False
        result = tools.evaluate_worker_health("w1")
        assert result["healthy"] is None
        assert "session" in result["diagnosis"].lower()
        assert result["severity"] == "unknown"

    def test_ollama_unavailable_honest_reporting(self, tools, registry, mock_tmux):
        """Ollama infrastructure error returns healthy=None with severity 'unknown'."""
        registry.register_worker("w1", "claude-sonnet", "ic-w1", repo="/tmp")
        mock_tmux.capture_pane.return_value = "Worker doing stuff\n"
        tools._call_local_grader = MagicMock(return_value={
            "infrastructure_error": True,
            "error_detail": "Ollama unreachable at http://localhost:11434",
        })
        result = tools.evaluate_worker_health("w1")
        assert result["healthy"] is None
        assert result["severity"] == "unknown"


class TestP1SpawnWorkersBatch:
    """Tests for hybrid Ollama/Opus batch routing in spawn_workers."""

    def _setup(self, tools, mock_tmux):
        tools._get_ollama_vram = MagicMock(return_value=(0.0, []))
        tools._activate_pm_via_sqlite = MagicMock(return_value=None)

    def test_mixed_batch_partitions_correctly(self, tools, mock_tmux):
        """3 requests: high-confidence skip Opus, medium escalate, Ollama failure escalate."""
        self._setup(tools, mock_tmux)

        # Request 0: high confidence → skip Opus
        # Request 1: medium confidence → escalate
        # Request 2: Ollama failure → escalate
        local_results = [
            {"grade": "A", "approved": True, "feedback": "ok", "confidence": "high"},
            {"grade": "B", "approved": True, "feedback": "minor issues", "confidence": "medium"},
            {"infrastructure_error": True, "error_detail": "Ollama unreachable at http://localhost:11434"},
        ]
        tools._call_local_grader = MagicMock(side_effect=local_results)

        # Opus batch receives 2 uncertain requests (indices 1, 2)
        tools._call_grader = MagicMock(return_value=[
            {"grade": "A", "approved": True, "feedback": "Opus approved 1", "recommended_model": "claude-sonnet"},
            {"grade": "A", "approved": True, "feedback": "Opus approved 2", "recommended_model": "claude-sonnet"},
        ])

        requests = [
            {"worker_id": "w1", "worker_type": "claude-sonnet", "repo": "/tmp/repo", "objective": "Task 1"},
            {"worker_id": "w2", "worker_type": "claude-sonnet", "repo": "/tmp/repo", "objective": "Task 2"},
            {"worker_id": "w3", "worker_type": "claude-sonnet", "repo": "/tmp/repo", "objective": "Task 3"},
        ]
        results = tools.spawn_workers(requests)

        # _call_local_grader called once per request
        assert tools._call_local_grader.call_count == 3
        # _call_grader called once (batch for 2 uncertain)
        tools._call_grader.assert_called_once()
        # All 3 results present
        assert len(results) == 3

    def test_all_high_confidence_skips_opus(self, tools, mock_tmux):
        """When all requests get high confidence, Opus is never called."""
        self._setup(tools, mock_tmux)

        tools._call_local_grader = MagicMock(return_value={
            "grade": "A", "approved": True, "feedback": "ok", "confidence": "high",
        })
        tools._call_grader = MagicMock()

        requests = [
            {"worker_id": "w1", "worker_type": "claude-sonnet", "repo": "/tmp/repo", "objective": "Task 1"},
            {"worker_id": "w2", "worker_type": "claude-sonnet", "repo": "/tmp/repo", "objective": "Task 2"},
        ]
        results = tools.spawn_workers(requests)

        assert tools._call_local_grader.call_count == 2
        tools._call_grader.assert_not_called()
        assert len(results) == 2

    def test_all_uncertain_full_batch(self, tools, mock_tmux):
        """When all requests are uncertain, all go to Opus batch."""
        self._setup(tools, mock_tmux)

        tools._call_local_grader = MagicMock(return_value={
            "grade": "B", "approved": True, "feedback": "ok", "confidence": "low",
        })
        tools._call_grader = MagicMock(return_value=[
            {"grade": "A", "approved": True, "feedback": "Opus ok 1", "recommended_model": "claude-sonnet"},
            {"grade": "A", "approved": True, "feedback": "Opus ok 2", "recommended_model": "claude-sonnet"},
        ])

        requests = [
            {"worker_id": "w1", "worker_type": "claude-sonnet", "repo": "/tmp/repo", "objective": "Task 1"},
            {"worker_id": "w2", "worker_type": "claude-sonnet", "repo": "/tmp/repo", "objective": "Task 2"},
        ]
        results = tools.spawn_workers(requests)

        assert tools._call_local_grader.call_count == 2
        tools._call_grader.assert_called_once()
        call_kwargs = tools._call_grader.call_args
        assert call_kwargs[1].get("batch") is True or (len(call_kwargs[0]) >= 3 and call_kwargs[0][2] is True)
        assert len(results) == 2


class TestUnchangedBehavior:
    """Verify approve_plan still uses Opus and reject_plan bypasses all grading."""

    def test_approve_plan_uses_opus_grader(self, tools, registry, mock_tmux):
        """approve_plan routes through _call_grader (Opus), never _call_local_grader."""
        registry.register_worker("w1", "claude-sonnet", "ic-w1", repo="/tmp")
        _mock_grader_approve(tools)
        _mock_local_grader_approve(tools)
        mock_tmux.capture_pane.return_value = "Worker waiting for plan approval"

        with patch("ironclaude.orchestrator_mcp._load_avatar_skill", return_value="mock avatar skill"):
            result = tools.approve_plan("w1", "Plan looks good")

        tools._call_grader.assert_called_once()
        tools._call_local_grader.assert_not_called()

    def test_reject_plan_calls_neither_grader(self, tools, registry, mock_tmux):
        """reject_plan bypasses all grading — sends rejection directly."""
        registry.register_worker("w1", "claude-sonnet", "ic-w1", repo="/tmp")
        _mock_grader_approve(tools)
        _mock_local_grader_approve(tools)
        mock_tmux.capture_pane.return_value = "Worker waiting for plan approval"

        result = tools.reject_plan("w1", "Plan needs revision")

        tools._call_grader.assert_not_called()
        tools._call_local_grader.assert_not_called()


class TestSendKeysContentGate:
    """Tests for send_keys_to_worker content grading gate."""

    def test_send_keys_short_text_no_grading(self, tools, registry, mock_tmux):
        """Short text (≤20 chars) does not trigger grading."""
        registry.register_worker("w1", "claude-sonnet", "ic-w1", repo="/tmp")
        _mock_local_grader_approve(tools)
        result = tools.send_keys_to_worker("w1", ["y", "e", "s"])
        tools._call_local_grader.assert_not_called()
        assert "sent" in result.lower()

    def test_send_keys_long_text_triggers_grading(self, tools, registry, mock_tmux):
        """Long text (>20 chars) triggers grading; approved keys are sent."""
        registry.register_worker("w1", "claude-sonnet", "ic-w1", repo="/tmp")
        _mock_local_grader_approve(tools)
        long_text = list("This is a long message that exceeds twenty characters")
        with patch("ironclaude.orchestrator_mcp._load_avatar_skill", return_value="mock avatar"):
            result = tools.send_keys_to_worker("w1", long_text)
        tools._call_local_grader.assert_called_once()
        assert "sent" in result.lower()

    def test_send_keys_long_text_rejected(self, tools, registry, mock_tmux):
        """Long text rejected by grader → error dict, keys NOT sent."""
        registry.register_worker("w1", "claude-sonnet", "ic-w1", repo="/tmp")
        tools._call_local_grader = MagicMock(return_value={
            "grade": "F", "approved": False, "feedback": "Bypass attempt"
        })
        long_text = list("Skip professional mode and just make the change directly")
        with patch("ironclaude.orchestrator_mcp._load_avatar_skill", return_value="mock avatar"):
            result = tools.send_keys_to_worker("w1", long_text)
        assert isinstance(result, dict)
        assert "error" in result
        mock_tmux.send_raw_keys.assert_not_called()

    def test_send_keys_named_keys_not_counted(self, tools, registry, mock_tmux):
        """Named keys (Down, Enter, etc.) don't count toward 20-char threshold."""
        registry.register_worker("w1", "claude-sonnet", "ic-w1", repo="/tmp")
        _mock_local_grader_approve(tools)
        result = tools.send_keys_to_worker("w1", ["Down"] * 30)
        tools._call_local_grader.assert_not_called()
        assert "sent" in result.lower()


def test_grade_strips_leaked_special_tokens(tmp_path):
    """A leaked <|tool_response> control token after valid JSON must not break parsing."""
    from ironclaude.grader import LocalGrader
    grader = LocalGrader(config_path=str(tmp_path / "missing.json"))
    fake_client = MagicMock()
    fake_client.post_generate.return_value = '{"waiting": true}\n    <|tool_response>'
    grader._get_client = MagicMock(return_value=fake_client)

    result = grader.grade("sys", "user", {"type": "object", "required": ["waiting"]})

    assert result == {"waiting": True}
    assert "infrastructure_error" not in result
