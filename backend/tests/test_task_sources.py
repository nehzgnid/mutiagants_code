from __future__ import annotations

import subprocess
import uuid
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.app import main
from backend.app.main import SessionLocal, Task, Workspace, app


client = TestClient(app)


def init_clean_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", str(path)], check=True, text=True, encoding="utf-8", errors="replace", capture_output=True)


def remove_workspace_by_path(path: Path) -> None:
    resolved = str(path.resolve())
    with SessionLocal() as db:
        workspace = db.scalar(select(Workspace).where(Workspace.path == resolved))
        if not workspace:
            return
        for task in db.scalars(select(Task).where(Task.workspace_id == workspace.id)):
            db.delete(task)
        db.delete(workspace)
        db.commit()


def test_create_task_from_local_source_binds_directory(tmp_path: Path) -> None:
    repo = tmp_path / "local-repo"
    init_clean_repo(repo)
    response = client.post("/api/tasks", json={
        "source_type": "local",
        "local_path": str(repo),
        "title": f"local-task-{uuid.uuid4().hex}",
        "requirement": "检查本地代码源绑定。",
    })
    try:
        assert response.status_code == 201
        body = response.json()
        assert body["title"].startswith("local-task-")
        with SessionLocal() as db:
            workspace = db.scalar(select(Workspace).where(Workspace.path == str(repo.resolve())))
            assert workspace is not None
            assert workspace.path == str(repo.resolve())
    finally:
        remove_workspace_by_path(repo)


def test_create_task_from_github_source_clones_then_binds(monkeypatch, tmp_path: Path) -> None:
    clone_target = tmp_path / "cloned-repo"

    def fake_clone(url: str, destination: str) -> Path:
        assert url == "https://github.com/example/project.git"
        assert Path(destination) == clone_target
        init_clean_repo(clone_target)
        return clone_target

    monkeypatch.setattr(main, "clone_github_repository", fake_clone)
    response = client.post("/api/tasks", json={
        "source_type": "github",
        "github_url": "https://github.com/example/project.git",
        "clone_path": str(clone_target),
        "title": f"github-task-{uuid.uuid4().hex}",
        "requirement": "检查 GitHub 克隆后的本地绑定。",
    })
    try:
        assert response.status_code == 201
        body = response.json()
        assert body["title"].startswith("github-task-")
        with SessionLocal() as db:
            workspace = db.scalar(select(Workspace).where(Workspace.path == str(clone_target.resolve())))
            assert workspace is not None
    finally:
        remove_workspace_by_path(clone_target)


def test_workspace_management_and_existing_source_are_not_public(tmp_path: Path) -> None:
    repo = tmp_path / "local-repo"
    init_clean_repo(repo)
    try:
        assert client.get("/api/workspaces").status_code == 404
        response = client.post("/api/tasks", json={
            "source_type": "existing", "title": "unsupported-source",
        })
        assert response.status_code == 422
        assert client.post("/api/tasks/missing/run-analysis").status_code in {404, 405}
        assert client.post("/api/tasks/missing/approvals/retry").status_code in {404, 405}
        assert client.post("/api/tasks/missing/patch", json={"patch": "not used anymore"}).status_code in {404, 405}
        assert client.post("/api/tasks/missing/test").status_code in {404, 405}
        assert client.get("/api/tasks/missing/events").status_code == 404
    finally:
        remove_workspace_by_path(repo)


def test_task_starts_read_only_and_permission_changes_in_conversation(tmp_path: Path) -> None:
    repo = tmp_path / "permission-repo"
    init_clean_repo(repo)
    try:
        response = client.post("/api/tasks", json={
            "source_type": "local", "local_path": str(repo), "title": f"permission-{uuid.uuid4().hex}",
        })
        assert response.status_code == 201
        assert response.json()["permission_mode"] == "read-only"
        update = client.patch(f"/api/tasks/{response.json()['id']}/permission", json={"permission_mode": "workspace-write"})
        assert update.status_code == 200
        assert update.json()["permission_mode"] == "workspace-write"
        assert client.get(f"/api/tasks/{response.json()['id']}").json()["permission_mode"] == "workspace-write"
    finally:
        remove_workspace_by_path(repo)
