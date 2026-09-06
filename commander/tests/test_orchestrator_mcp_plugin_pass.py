from unittest.mock import MagicMock

from ironclaude.db import init_db
from ironclaude.orchestrator_mcp import _create_mcp_server, OrchestratorTools
from ironclaude.worker_registry import WorkerRegistry

_FIXTURE_PLUGIN = '''
def _provider(mcp, context):
    def plugin_dummy_tool() -> str:
        """Dummy tool registered by the fixture plugin."""
        return "ok"

    mcp.tool()(plugin_dummy_tool)


def register(registry):
    registry.register_mcp_tool_provider(_provider)
'''


def test_create_mcp_server_registers_plugin_mcp_tools(tmp_path):
    plug = tmp_path / "myplug"
    plug.mkdir()
    (plug / "plugin.py").write_text(_FIXTURE_PLUGIN)

    conn = init_db(str(tmp_path / "t.db"))
    tools = OrchestratorTools(WorkerRegistry(conn), MagicMock(), str(tmp_path / "ledger.json"))
    server = _create_mcp_server(tools, plugin_dirs=[str(plug)])

    names = {t.name for t in server._tool_manager.list_tools()}
    assert "plugin_dummy_tool" in names
    assert server._tool_manager.get_tool("plugin_dummy_tool").fn() == "ok"
