"""Tests for IroncladeDaemon._validate_brain_message and _detect_prompt_waiting."""
import time
import pytest
from unittest.mock import MagicMock

from ironclaude.main import IroncladeDaemon, PROMPT_WAITING_CACHE_TTL


def _make_daemon():
    daemon = IroncladeDaemon.__new__(IroncladeDaemon)
    daemon._grader = MagicMock()
    daemon._prompt_waiting_cache = {}
    return daemon


class TestValidateBrainMessage:
    def test_empty_message_short_circuits(self):
        daemon = _make_daemon()
        valid, reason = daemon._validate_brain_message("")
        assert valid is False
        assert reason == "Empty message"
        daemon._grader.grade.assert_not_called()

    def test_whitespace_only_short_circuits(self):
        daemon = _make_daemon()
        valid, reason = daemon._validate_brain_message("   \n  ")
        assert valid is False
        assert reason == "Empty message"
        daemon._grader.grade.assert_not_called()

    def test_grader_valid_true_returns_true(self):
        daemon = _make_daemon()
        daemon._grader.grade.return_value = {"valid": True}
        valid, reason = daemon._validate_brain_message("d1083 completed — fixed the bug")
        assert valid is True
        assert reason == ""

    def test_grader_valid_false_with_reason_returns_false_and_reason(self):
        daemon = _make_daemon()
        daemon._grader.grade.return_value = {"valid": False, "reason": "Missing reason clause"}
        # Include a directive ref so the message passes the pre-filter and the
        # grader verdict (and its reason) is what flows back.
        valid, reason = daemon._validate_brain_message("#42 everything is good")
        assert valid is False
        assert reason == "Missing reason clause"

    def test_grader_valid_false_no_reason_uses_fallback(self):
        daemon = _make_daemon()
        daemon._grader.grade.return_value = {"valid": False}
        valid, reason = daemon._validate_brain_message("some message here")
        assert valid is False
        assert len(reason) > 0

    def test_infrastructure_error_fails_open(self):
        daemon = _make_daemon()
        daemon._grader.grade.return_value = {"infrastructure_error": True, "error_detail": "Ollama unreachable"}
        valid, reason = daemon._validate_brain_message("d1083 completed work")
        assert valid is True
        assert reason == ""


class TestDetectPromptWaiting:
    def test_waiting_true_returned(self):
        daemon = _make_daemon()
        daemon._grader.grade.return_value = {"waiting": True}
        result = daemon._detect_prompt_waiting("AskUserQuestion\nsome log output")
        assert result is True

    def test_waiting_false_returned(self):
        daemon = _make_daemon()
        daemon._grader.grade.return_value = {"waiting": False}
        result = daemon._detect_prompt_waiting("Worker is coding normally")
        assert result is False

    def test_infrastructure_error_returns_false(self):
        daemon = _make_daemon()
        daemon._grader.grade.return_value = {"infrastructure_error": True, "error_detail": "timeout"}
        result = daemon._detect_prompt_waiting("some log tail")
        assert result is False

    def test_infrastructure_error_not_cached(self):
        daemon = _make_daemon()
        daemon._grader.grade.return_value = {"infrastructure_error": True, "error_detail": "timeout"}
        log_tail = "some log tail"
        daemon._detect_prompt_waiting(log_tail)
        daemon._detect_prompt_waiting(log_tail)
        assert daemon._grader.grade.call_count == 2

    def test_cache_hit_skips_grader(self):
        daemon = _make_daemon()
        daemon._grader.grade.return_value = {"waiting": True}
        log_tail = "AskUserQuestion displayed"
        daemon._detect_prompt_waiting(log_tail)
        daemon._detect_prompt_waiting(log_tail)
        assert daemon._grader.grade.call_count == 1

    def test_cache_expires_after_ttl(self):
        daemon = _make_daemon()
        daemon._grader.grade.return_value = {"waiting": False}
        log_tail = "Worker working normally"
        cache_key = hash(log_tail)
        daemon._prompt_waiting_cache[cache_key] = (time.time() - PROMPT_WAITING_CACHE_TTL - 1, False)
        daemon._detect_prompt_waiting(log_tail)
        assert daemon._grader.grade.call_count == 1

    def test_log_tail_truncated_to_2000_chars(self):
        daemon = _make_daemon()
        daemon._grader.grade.return_value = {"waiting": False}
        long_tail = "x" * 3000
        daemon._detect_prompt_waiting(long_tail)
        call_args = daemon._grader.grade.call_args
        user_prompt = call_args[0][1]
        assert "x" * 2001 not in user_prompt
        assert len(user_prompt) <= 2020
