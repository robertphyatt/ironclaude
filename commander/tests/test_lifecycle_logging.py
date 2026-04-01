# tests/test_lifecycle_logging.py
"""Tests for log_worker_event helper in main.py."""
import json
import logging

import pytest

from ironclaude.main import log_worker_event


class TestLogWorkerEvent:
    def test_emits_valid_json_via_logger(self, caplog):
        """log_worker_event emits a JSON-parseable string via ironclaude logger."""
        with caplog.at_level(logging.INFO, logger="ironclaude"):
            log_worker_event("WORKER_SPAWNED", worker_id="w1", repo="/repo")
        assert len(caplog.records) == 1
        payload = json.loads(caplog.records[0].message)
        assert payload["event_type"] == "WORKER_SPAWNED"
        assert "timestamp" in payload
        assert payload["worker_id"] == "w1"
        assert payload["repo"] == "/repo"

    def test_timestamp_is_iso8601(self, caplog):
        """Timestamp field is parseable as ISO 8601."""
        from datetime import datetime
        with caplog.at_level(logging.INFO, logger="ironclaude"):
            log_worker_event("WORKER_TEST")
        payload = json.loads(caplog.records[0].message)
        datetime.fromisoformat(payload["timestamp"])  # raises ValueError if malformed

    def test_all_extra_fields_included(self, caplog):
        """All kwargs are included in the JSON payload."""
        with caplog.at_level(logging.INFO, logger="ironclaude"):
            log_worker_event("WORKER_KILLED", worker_id="w2", had_evidence=True, runtime_seconds=42.5)
        payload = json.loads(caplog.records[0].message)
        assert payload["had_evidence"] is True
        assert payload["runtime_seconds"] == 42.5
