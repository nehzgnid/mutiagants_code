from __future__ import annotations

import uuid
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.app import main
from backend.app.main import ModelProvider, SessionLocal, app, read_secrets, write_secrets
from backend.tests.test_task_sources import init_clean_repo, remove_workspace_by_path


client = TestClient(app)


def active_provider_id() -> str | None:
    with SessionLocal() as db:
        provider = db.scalar(select(ModelProvider).where(ModelProvider.is_active.is_(True)))
        return provider.id if provider else None


def remove_provider(provider_id: str) -> None:
    with SessionLocal() as db:
        provider = db.get(ModelProvider, provider_id)
        if provider:
            db.delete(provider)
            db.commit()
    secrets = read_secrets()
    secrets.pop(provider_id, None)
    write_secrets(secrets)


def test_master_agent_routes_read_only_message_and_persists_artifact(monkeypatch, tmp_path: Path) -> None:
    repo = tmp_path / "read-only-workflow"
    init_clean_repo(repo)
    original_provider_id = active_provider_id()
    provider = client.post("/api/model-providers", json={
        "name": f"workflow-{uuid.uuid4().hex}", "kind": "external", "base_url": "https://example.test/v1", "model_name": "test-model",
    }).json()
    client.post(f"/api/model-providers/{provider['id']}/activate")

    class Response:
        def raise_for_status(self) -> None: pass
        def json(self) -> dict: return {"choices": [{"message": {"content": "模块依赖已分析。"}}]}

    monkeypatch.setattr(main.httpx, "post", lambda *args, **kwargs: Response())
    task = client.post("/api/tasks", json={"source_type": "local", "local_path": str(repo), "title": "只读分析"}).json()
    try:
        response = client.post(f"/api/tasks/{task['id']}/messages", json={"content": "请分析这个模块的依赖关系"})
        assert response.status_code == 201
        current = client.get(f"/api/tasks/{task['id']}").json()
        assert current["workflow_type"] == "read_only"
        assert current["assigned_agent"] == "主 Agent"
        assert current["current_stage"] == "已完成"
        runs = client.get(f"/api/tasks/{task['id']}/stages").json()
        assert [(run["stage"], run["agent"], run["status"]) for run in runs] == [("阅读分析", "阅读 Agent", "completed")]
        assert client.get(f"/api/tasks/{task['id']}/artifacts").json()[0]["content"] == "模块依赖已分析。"
    finally:
        remove_provider(provider["id"])
        if original_provider_id:
            client.post(f"/api/model-providers/{original_provider_id}/activate")
        remove_workspace_by_path(repo)


def test_master_agent_uses_full_route_and_requires_confirmation_before_coding(tmp_path: Path) -> None:
    repo = tmp_path / "full-workflow"
    init_clean_repo(repo)
    try:
        task = client.post("/api/tasks", json={"source_type": "local", "local_path": str(repo), "title": "复杂修改"}).json()
        with SessionLocal() as db:
            record = db.get(main.Task, task["id"])
            run = main.route_message(db, record, "为登录接口增加权限校验并修改多个文件")
            assert run is not None
            assert record.workflow_type == "full"
            assert record.current_stage == "需求分析"
            main.complete_stage(db, record.id, run.id, "需求产物")
            assert record.current_stage == "概要设计"
            overview = main.start_stage(db, record, "概要设计", "继续")
            main.complete_stage(db, record.id, overview.id, "概要产物")
            detail = main.start_stage(db, record, "详细设计", "继续")
            main.complete_stage(db, record.id, detail.id, "详细产物")
            assert record.current_stage == "待编码确认"
            assert main.route_message(db, record, "确认开始编码") is None
            record.permission_mode = "workspace-write"
            assert {tool["function"]["name"] for tool in main.tools_for_task(record)} == {"list_files", "read_file"}
            implementation = main.route_message(db, record, "确认开始编码")
            assert implementation is not None
            assert implementation.stage == "编码实现"
            assert implementation.agent == "执行 Agent"
            db.commit()
    finally:
        remove_workspace_by_path(repo)
