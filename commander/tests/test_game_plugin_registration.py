import logging
import pathlib
from unittest.mock import MagicMock

import pytest

from ironclaude.db import init_db
from ironclaude.orchestrator_mcp import _create_mcp_server, OrchestratorTools
from ironclaude.worker_registry import WorkerRegistry

GAME_TOOL_NAMES = {"game_launch", "game_screenshot", "game_click", "game_type", "game_key", "game_kill"}
_GAME_PLUGIN_DIR = pathlib.Path(__file__).resolve().parents[1] / "src" / "ironclaude" / "plugins" / "game"


@pytest.mark.skipif(
    not _GAME_PLUGIN_DIR.exists(),
    reason="game plugin symlink absent — private Ironclaude-plugins repo not checked out",
)
def test_game_plugin_registers_exactly_six_game_tools(tmp_path, caplog):
    conn = init_db(str(tmp_path / "t.db"))
    tools = OrchestratorTools(WorkerRegistry(conn), MagicMock(), str(tmp_path / "ledger.json"))
    with caplog.at_level(logging.WARNING):
        server = _create_mcp_server(tools, plugin_dirs=[str(_GAME_PLUGIN_DIR)])
    names = {t.name for t in server._tool_manager.list_tools()}
    missing = GAME_TOOL_NAMES - names
    assert not missing, f"game tools not registered: {missing}"
    # ToolManager dedups silently (first-wins) — a double registration is only
    # observable as this WARNING (mcp/server/fastmcp/tools/tool_manager.py:70).
    dups = [r for r in caplog.records if "Tool already exists" in r.getMessage()]
    assert dups == [], [r.getMessage() for r in dups]
    # fail-soft swallows must not have fired
    swallowed = [r for r in caplog.records
                 if "Failed to load plugin" in r.getMessage()
                 or "MCP-tool provider registration failed" in r.getMessage()]
    assert swallowed == [], [r.getMessage() for r in swallowed]
