# tests/test_game_tools.py
"""Tests for individual game MCP tools."""

import json
import os
import shutil
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from ironclaude.orchestrator_mcp import KEY_MAP, OrchestratorTools
from ironclaude.db import init_db
from ironclaude.worker_registry import WorkerRegistry


_has_cliclick = shutil.which("cliclick") is not None
_has_display = subprocess.run(
    ["screencapture", "-x", "/tmp/_sc_probe.png"],
    capture_output=True,
).returncode == 0


@pytest.fixture
def tools(tmp_path):
    conn = init_db(str(tmp_path / "test.db"))
    registry = WorkerRegistry(conn)
    tmux = MagicMock()
    return OrchestratorTools(registry, tmux, str(tmp_path / "ledger.json"))


class TestKeyMap:
    def test_return_key(self):
        assert KEY_MAP["Return"] == "return"

    def test_space_key(self):
        assert KEY_MAP["space"] == "space"

    def test_tab_key(self):
        assert KEY_MAP["Tab"] == "tab"

    def test_escape_key(self):
        assert KEY_MAP["Escape"] == "escape"

    def test_backspace_key(self):
        assert KEY_MAP["BackSpace"] == "delete"

    def test_arrow_up(self):
        assert KEY_MAP["Up"] == "arrow-up"

    def test_arrow_down(self):
        assert KEY_MAP["Down"] == "arrow-down"

    def test_arrow_left(self):
        assert KEY_MAP["Left"] == "arrow-left"

    def test_arrow_right(self):
        assert KEY_MAP["Right"] == "arrow-right"

    def test_shift_left(self):
        assert KEY_MAP["Shift_L"] == "shift"

    def test_shift_right(self):
        assert KEY_MAP["Shift_R"] == "shift"

    def test_control_left(self):
        assert KEY_MAP["Control_L"] == "ctrl"

    def test_control_right(self):
        assert KEY_MAP["Control_R"] == "ctrl"

    def test_alt_left(self):
        assert KEY_MAP["Alt_L"] == "alt"

    def test_alt_right(self):
        assert KEY_MAP["Alt_R"] == "alt"

    def test_meta_left(self):
        assert KEY_MAP["Meta_L"] == "cmd"

    def test_meta_right(self):
        assert KEY_MAP["Meta_R"] == "cmd"


@pytest.mark.skipif(not _has_cliclick, reason="cliclick not installed")
class TestCliclickInstalled:
    def test_cliclick_mouse_move(self):
        """Verify cliclick is installed by moving mouse to 0,0."""
        result = subprocess.run(["cliclick", "m:0,0"], capture_output=True, text=True)
        assert result.returncode == 0


class TestGameClick:
    def test_click_constructs_correct_command(self, tools):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            result = json.loads(tools.game_click(400, 300))
        mock_run.assert_called_once_with(["cliclick", "c:400,300"])
        assert result == {"action": "click", "x": 400, "y": 300, "success": True}

    def test_click_reports_failure(self, tools):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1)
            result = json.loads(tools.game_click(0, 0))
        assert result["success"] is False


class TestGameType:
    def test_type_constructs_correct_command(self, tools):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            result = json.loads(tools.game_type("hello"))
        mock_run.assert_called_once_with(["cliclick", "t:hello"])
        assert result == {"action": "type", "text": "hello", "success": True}


class TestGameKey:
    def test_mapped_key(self, tools):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            result = json.loads(tools.game_key("Return"))
        mock_run.assert_called_once_with(["cliclick", "kp:return"])
        assert result["success"] is True

    def test_unmapped_key_lowercased(self, tools):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            tools.game_key("F1")
        mock_run.assert_called_once_with(["cliclick", "kp:f1"])

    def test_escape_key(self, tools):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            tools.game_key("Escape")
        mock_run.assert_called_once_with(["cliclick", "kp:escape"])


@pytest.mark.skipif(not _has_display, reason="no display available for screencapture")
class TestGameScreenshot:
    def test_screenshot_creates_valid_png(self, tools):
        """Take a real screenshot and verify it's a valid PNG."""
        original_run = subprocess.run

        def patched_run(cmd, *args, **kwargs):
            if isinstance(cmd, list) and cmd and cmd[0] == "osascript":
                m = MagicMock()
                m.returncode = 0
                return m
            return original_run(cmd, *args, **kwargs)

        with patch("subprocess.run", side_effect=patched_run):
            result = json.loads(tools.game_screenshot())

        path = result["path"]
        try:
            assert os.path.exists(path)
            assert os.path.getsize(path) > 0
            with open(path, "rb") as f:
                header = f.read(4)
            assert header == b"\x89PNG"
        finally:
            if os.path.exists(path):
                os.unlink(path)
