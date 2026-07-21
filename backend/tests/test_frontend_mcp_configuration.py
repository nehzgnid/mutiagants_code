from pathlib import Path


def test_frontend_exposes_mcp_server_and_task_tool_configuration() -> None:
    source = (Path(__file__).parents[2] / "frontend" / "src" / "main.tsx").read_text(encoding="utf-8")

    assert '"/api/mcp-servers"' in source
    assert '"/api/mcp-servers/presets/coding"' in source
    assert "添加预制编码 MCP Server" in source
    assert "所有任务现在都可自动使用其工具" in source
    assert "MCP 工具授权" not in source
