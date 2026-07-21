from __future__ import annotations

import sys
import uuid
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app import main
from backend.app.main import SessionLocal, app
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


def test_builtin_coding_mcp_is_global_and_scoped_to_task_workspace(tmp_path: Path) -> None:
    response = client.post("/api/mcp-servers/presets/coding")
    assert response.status_code == 201, response.text
    preset = response.json()
    server = preset["server"]
    assert {tool["name"] for tool in server["tools"]} == {
        "list_workspace_files", "read_workspace_file", "write_workspace_file",
    }
    assert next(tool for tool in server["tools"] if tool["name"] == "write_workspace_file")["access_mode"] == "workspace-write"

    repo = tmp_path / "coding-mcp-workspace"
    init_clean_repo(repo)
    task = client.post("/api/tasks", json={
        "source_type": "local", "local_path": str(repo), "title": f"coding-mcp-{uuid.uuid4().hex}",
    }).json()
    try:
        with SessionLocal() as db:
            record = db.get(main.Task, task["id"])
            tools = [tool["function"] for tool in main.tools_for_task(record, db)]
            assert any(function["name"].endswith("read_workspace_file") for function in tools)
            assert not any(function["name"].endswith("write_workspace_file") for function in tools)
            record.permission_mode = "workspace-write"
            record.current_stage = main.IMPLEMENTATION_STAGE
            write_function = next(function for function in main.tools_for_task(record, db)
                                  if function["function"]["name"].endswith("write_workspace_file"))["function"]
            result = main.execute_tool(record, write_function["name"], {"path": "created.txt", "content": "workspace only"}, db)
            assert "created.txt" in result
        assert (repo / "created.txt").read_text(encoding="utf-8") == "workspace only"
    finally:
        remove_workspace_by_path(repo)
        if preset["created"]:
            client.delete(f"/api/mcp-servers/{server['id']}")
