from ironclaude.plugins import PluginRegistry


def test_no_mcp_tool_providers_by_default():
    assert PluginRegistry().get_mcp_tool_providers() == []


def test_register_and_get_mcp_tool_provider():
    reg = PluginRegistry()

    def provider(mcp, context):  # pragma: no cover - registration only
        pass

    reg.register_mcp_tool_provider(provider)
    assert reg.get_mcp_tool_providers() == [provider]
