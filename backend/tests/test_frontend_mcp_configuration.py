from pathlib import Path


def test_frontend_exposes_mcp_server_and_task_tool_configuration() -> None:
    source = (Path(__file__).parents[2] / "frontend" / "src" / "main.tsx").read_text(encoding="utf-8")

    assert '"/api/mcp-servers"' in source
    assert '"/api/mcp-servers/presets/coding"' in source
    assert "添加预制编码 MCP Server" in source
    assert "所有任务现在都可自动使用其工具" in source
    assert "MCP 工具授权" not in source


def test_frontend_groups_related_configuration_controls() -> None:
    source = (Path(__file__).parents[2] / "frontend" / "src" / "main.tsx").read_text(encoding="utf-8")
    styles = (Path(__file__).parents[2] / "frontend" / "src" / "styles.css").read_text(encoding="utf-8")

    assert '<div className="header-actions">' in source
    assert source.index('title="配置模型接口"') < source.index('title="配置本地 MCP Server"')
    assert ".composer-footer > .permission-picker { order: 1; }" in styles
    assert ".composer-footer > .execution-mode-picker { order: 2; }" in styles
    assert ".composer-actions { order: 3; gap: 8px; margin-left: auto; }" in styles
