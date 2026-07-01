"""Tests for diagnostic debug logging in _do_grader_send_and_poll."""
import json
import pytest
from unittest.mock import MagicMock, patch

import ironclaude.orchestrator_mcp as orch_mod
from ironclaude.orchestrator_mcp import OrchestratorTools


_NONCE = "deadbeef01234567"
_FEEDBACK = "No degradation in wiki_tools"
_GRADER_JSON = json.dumps({
    "grade": "A",
    "approved": True,
    "feedback": _FEEDBACK,
})


def _make_tools(mock_tmux):
    tools = OrchestratorTools(MagicMock(), mock_tmux, ssh_manager=None)
    tools._grader_session = "ic-grader"
    tools._last_grader_delta = ""
    return tools


class TestDoGraderDebugLog:
    def test_debug_log_written_on_delimiter_find(self, tmp_path, monkeypatch):
        debug_log = tmp_path / "grader-debug.log"
        monkeypatch.setattr(orch_mod, "_GRADER_DEBUG", True)
        monkeypatch.setattr(orch_mod, "_GRADER_DEBUG_LOG", str(debug_log))

        pane_content = f"some prior content\nGRADER_RESPONSE_{_NONCE}\n{_GRADER_JSON}\n"

        mock_tmux = MagicMock()
        mock_tmux.capture_pane.return_value = pane_content
        mock_tmux.send_keys.return_value = True

        tools = _make_tools(mock_tmux)
        tools.GRADER_TIMEOUT_SECONDS = 5
        with patch.object(tools, "_wait_for_grader_clear", return_value=True):
            with patch("ironclaude.orchestrator_mcp.secrets.token_hex", return_value=_NONCE):
                result = tools._do_grader_send_and_poll("sys prompt", "user prompt")

        assert result is not None, "Expected grader result but got None"
        assert result["feedback"] == _FEEDBACK

        log_text = debug_log.read_text()
        assert "capture_len=" in log_text
        assert _FEEDBACK in log_text
