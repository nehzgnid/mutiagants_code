"""The bundled stdio MCP Server for scoped coding operations.

The parent application supplies LOCAL_AGENT_WORKSPACE for every tools/call.
This process deliberately does not accept an arbitrary workspace argument.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP


server = FastMCP("local-coding-tools")
EXCLUDED_PARTS = {".git", "node_modules", ".venv", "__pycache__", "dist", "build"}


def workspace_root() -> Path:
    value = os.environ.get("LOCAL_AGENT_WORKSPACE")
    if not value:
        raise ValueError("No task workspace was supplied by the host application.")
    root = Path(value).resolve()
    if not root.is_dir():
        raise ValueError("The supplied task workspace does not exist.")
    return root


def workspace_path(relative_path: str) -> Path:
    root = workspace_root()
    candidate = Path(relative_path)
    if candidate.is_absolute():
        raise ValueError("Only paths relative to the task workspace are allowed.")
    target = (root / candidate).resolve()
    try:
        target.relative_to(root)
    except ValueError as error:
        raise ValueError("The requested path is outside the task workspace.") from error
    if EXCLUDED_PARTS.intersection(target.relative_to(root).parts):
        raise ValueError("The requested path is excluded from coding tools.")
    return target


@server.tool()
def list_workspace_files(path: str = ".") -> str:
    """List up to 300 files under a directory relative to the current task workspace."""
    target = workspace_path(path)
    if not target.is_dir():
        raise ValueError("The requested path is not a directory.")
    root = workspace_root()
    files: list[str] = []
    for item in target.rglob("*"):
        relative = item.relative_to(root)
        if EXCLUDED_PARTS.intersection(relative.parts) or not item.is_file():
            continue
        files.append(str(relative))
        if len(files) == 300:
            break
    return json.dumps({"path": str(target.relative_to(root)), "files": files, "truncated": len(files) == 300}, ensure_ascii=False)


@server.tool()
def read_workspace_file(path: str) -> str:
    """Read a UTF-8 text file relative to the current task workspace."""
    target = workspace_path(path)
    if not target.is_file():
        raise ValueError("The requested path is not a file.")
    content = target.read_text(encoding="utf-8", errors="replace")
    return json.dumps({"path": path, "content": content[:100_000], "truncated": len(content) > 100_000}, ensure_ascii=False)


@server.tool()
def write_workspace_file(path: str, content: str) -> str:
    """Create or replace a UTF-8 text file relative to the current task workspace."""
    target = workspace_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return json.dumps({"path": path, "written": True}, ensure_ascii=False)


if __name__ == "__main__":
    server.run(transport="stdio")
