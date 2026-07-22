from __future__ import annotations

import hashlib
import subprocess
import time
import uuid
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app import main
from backend.app.main import SessionLocal, app
from backend.tests.test_task_sources import init_clean_repo, remove_workspace_by_path


client = TestClient(app)


def digest(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def writable_task(tmp_path: Path, mode: str = "automatic") -> tuple[dict, Path]:
    repo = tmp_path / f"execution-{uuid.uuid4().hex}"
    init_clean_repo(repo)
    task = client.post("/api/tasks", json={"source_type": "local", "local_path": str(repo), "title": "execution"}).json()
    assert task.get("id"), task
    (repo / "sample.txt").write_text("before\n", encoding="utf-8")
    with SessionLocal() as db:
        record = db.get(main.Task, task["id"])
        record.permission_mode = "full-access"
        record.execution_mode = mode
        record.current_stage = main.IMPLEMENTATION_STAGE
        db.commit()
    return task, repo


def test_patch_is_atomic_and_can_be_undone(tmp_path: Path) -> None:
    task, repo = writable_task(tmp_path)
    try:
        response = client.post(f"/api/tasks/{task['id']}/patches", json={"edits": [{
            "path": "sample.txt", "expected_hash": digest("before\n"), "old_text": "before", "new_text": "after",
        }]})
        assert response.status_code == 201
        operation = response.json()
        assert operation["status"] == "completed"
        assert repo.joinpath("sample.txt").read_text(encoding="utf-8") == "after\n"
        undone = client.post(f"/api/tasks/{task['id']}/operations/{operation['id']}/undo")
        assert undone.status_code == 200
        assert repo.joinpath("sample.txt").read_text(encoding="utf-8") == "before\n"
    finally:
        remove_workspace_by_path(repo)


def test_manual_patch_detects_concurrent_file_change(tmp_path: Path) -> None:
    task, repo = writable_task(tmp_path, "manual_confirmation")
    try:
        response = client.post(f"/api/tasks/{task['id']}/patches", json={"edits": [{
            "path": "sample.txt", "expected_hash": digest("before\n"), "old_text": "before", "new_text": "after",
        }]})
        operation = response.json()
        assert operation["status"] == "pending_approval"
        repo.joinpath("sample.txt").write_text("human edit\n", encoding="utf-8")
        approved = client.post(f"/api/tasks/{task['id']}/operations/{operation['id']}/approval", json={"approve": True})
        assert approved.status_code == 200
        assert approved.json()["status"] == "conflict"
        assert repo.joinpath("sample.txt").read_text(encoding="utf-8") == "human edit\n"
    finally:
        remove_workspace_by_path(repo)


def test_command_streams_output_and_can_be_listed(tmp_path: Path) -> None:
    task, repo = writable_task(tmp_path)
    try:
        response = client.post(f"/api/tasks/{task['id']}/commands", json={"command": "echo streamed", "timeout_seconds": 10})
        assert response.status_code == 201
        operation_id = response.json()["id"]
        for _ in range(30):
            operation = next(item for item in client.get(f"/api/tasks/{task['id']}/operations").json() if item["id"] == operation_id)
            if operation["status"] in {"completed", "failed"}: break
            time.sleep(0.1)
        assert operation["status"] == "completed"
        assert "streamed" in operation["result"]["stdout"]
    finally:
        remove_workspace_by_path(repo)


def test_git_status_and_commit_only_completed_patch_files(tmp_path: Path) -> None:
    task, repo = writable_task(tmp_path)
    try:
        subprocess.run(["git", "-C", str(repo), "config", "user.email", "agent@example.test"], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.name", "Agent"], check=True)
        with SessionLocal() as db:
            workspace = db.get(main.Workspace, db.get(main.Task, task["id"]).workspace_id)
            workspace.test_command = ["git", "status", "--porcelain"]
            db.commit()
        patch = client.post(f"/api/tasks/{task['id']}/patches", json={"edits": [{
            "path": "sample.txt", "expected_hash": digest("before\n"), "old_text": "before", "new_text": "after",
        }]})
        assert patch.status_code == 201
        status = client.get(f"/api/tasks/{task['id']}/git")
        assert status.status_code == 200
        assert "sample.txt" in status.json()["status"]
        committed = client.post(f"/api/tasks/{task['id']}/git/commit", json={"message": "Update sample"})
        assert committed.status_code == 201, committed.text
        assert "sample.txt" in committed.json()["paths"]
    finally:
        remove_workspace_by_path(repo)
