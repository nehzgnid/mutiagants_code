from __future__ import annotations

import sys
import uuid
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app import main
from backend.app.main import McpServer, SessionLocal, TaskMcpTool, app
from backend.tests.test_task_sources import init_clean_repo, remove_workspace_by_path


client = TestClient(app)


def write_test_server(path: Path) -> None:
    path.write_text(
        "from mcp.server.fastmcp import FastMCP\n"
        "server = FastMCP('test-server')\n"
        "@server.tool()\n"
        "def echo(text: str) -> str:\n"
        "    return f'echo:{text}'\n"
        "server.run(transport='stdio')\n",
        encoding="utf-8",
    )


def test_stdio_mcp_discovery_is_global_and_call(tmp_path: Path) -> None:
    script = tmp_path / "mcp_server.py"
    write_test_server(script)
    created = client.post("/api/mcp-servers", json={
        "name": f"stdio-{uuid.uuid4().hex}", "command": sys.executable,
        "arguments": [str(script)], "enabled": True,
    })
    assert created.status_code == 201, created.text
    server = created.json()
    assert server["tools"][0]["name"] == "echo"
    duplicate = client.post("/api/mcp-servers", json={
        "name": server["name"], "command": sys.executable, "arguments": [str(script)],
    })
    assert duplicate.status_code == 409

    repo = tmp_path / "mcp-workspace"
    init_clean_repo(repo)
    task = client.post("/api/tasks", json={
        "source_type": "local", "local_path": str(repo), "title": f"mcp-task-{uuid.uuid4().hex}",
    }).json()
    try:
        with SessionLocal() as db:
            record = db.get(main.Task, task["id"])
            function = next(tool["function"] for tool in main.tools_for_task(record, db)
                            if tool["function"]["name"] == main.mcp_function_name(db.get(main.McpServer, server["id"]), "echo"))
            assert function["parameters"]["properties"]["text"]["type"] == "string"
            result = main.execute_tool(record, function["name"], {"text": "hello"}, db)
            assert "echo:hello" in result
    finally:
        remove_workspace_by_path(repo)
        assert client.delete(f"/api/mcp-servers/{server['id']}").status_code == 204


def test_stdio_mcp_rejects_unavailable_command() -> None:
    response = client.post("/api/mcp-servers", json={
        "name": f"broken-{uuid.uuid4().hex}", "command": "not-a-real-mcp-command", "arguments": [],
    })
    assert response.status_code == 422
    assert "MCP Server" in response.text


def test_builtin_coding_mcp_preset_is_removed() -> None:
    assert client.post("/api/mcp-servers/presets/coding").status_code in {404, 405}


def test_legacy_builtin_coding_mcp_is_removed_when_script_is_an_argument() -> None:
    server_id = str(uuid.uuid4())
    task_id = str(uuid.uuid4())
    with SessionLocal() as db:
        db.add(McpServer(
            id=server_id, name=f"legacy-coding-{server_id}", command=sys.executable,
            arguments=["C:/legacy/backend/app/builtin_coding_mcp.py"], enabled=True,
            tools=[], created_at=main.now(), updated_at=main.now(),
        ))
        db.add(TaskMcpTool(
            id=str(uuid.uuid4()), task_id=task_id, server_id=server_id,
            tool_name="write_workspace_file", access_mode="workspace-write",
        ))
        db.commit()
        legacy_servers = [server for server in db.scalars(main.select(McpServer)) if main.is_legacy_coding_mcp(server)]
        for server in legacy_servers:
            for binding in db.scalars(main.select(TaskMcpTool).where(TaskMcpTool.server_id == server.id)):
                db.delete(binding)
            db.delete(server)
        db.commit()
        assert db.get(McpServer, server_id) is None
        assert not list(db.scalars(main.select(TaskMcpTool).where(TaskMcpTool.server_id == server_id)))
