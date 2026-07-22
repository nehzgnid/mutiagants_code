from __future__ import annotations

import uuid
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.app import main
from backend.app.main import ModelProvider, SessionLocal, Task, TaskMessage, Workspace, app, read_secrets, write_secrets
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


def test_task_conversation_persists_history_and_sends_context(monkeypatch, tmp_path: Path) -> None:
    repo = tmp_path / "conversation-repo"
    init_clean_repo(repo)
    original_provider_id = active_provider_id()
    provider = client.post("/api/model-providers", json={
        "name": f"conversation-{uuid.uuid4().hex}", "kind": "external", "base_url": "https://example.test/v1", "model_name": "test-model",
    }).json()
    assert client.post(f"/api/model-providers/{provider['id']}/activate").status_code == 200
    captured: dict = {}
    responses = iter([
        '{"task_type":"development","complexity_reason":"需要设计方案。","workflow":"full","required_stages":["需求分析","概要设计","详细设计","编码实现","代码审核","单元测试"]}',
        "已记录，会继续推进设计。",
    ])

    class Response:
        def raise_for_status(self) -> None: pass
        def json(self) -> dict: return {"choices": [{"message": {"content": next(responses)}}]}

    def fake_post(*args, **kwargs):
        captured.update(kwargs["json"])
        return Response()

    monkeypatch.setattr(main.httpx, "post", fake_post)
    task_response = client.post("/api/tasks", json={
        "source_type": "local", "local_path": str(repo), "title": f"conversation-{uuid.uuid4().hex}",
    })
    task_id = task_response.json()["id"]
    try:
        reply = client.post(f"/api/tasks/{task_id}/messages", json={"content": "请先给出概要设计。"})
        assert reply.status_code == 201
        assert reply.json()["role"] == "assistant"
        tool_names = {tool["function"]["name"] for tool in captured["tools"]}
        assert {"list_files", "read_file"}.issubset(tool_names)
        history = client.get(f"/api/tasks/{task_id}/messages").json()
        assert [item["role"] for item in history] == ["user", "assistant"]
        assert captured["messages"][-1] == {"role": "user", "content": "请先给出概要设计。"}
        assert "任务标题" in captured["messages"][0]["content"]
    finally:
        remove_provider(provider["id"])
        if original_provider_id:
            client.post(f"/api/model-providers/{original_provider_id}/activate")
        remove_workspace_by_path(repo)


def test_workspace_write_tools_cannot_escape_the_task_directory(tmp_path: Path) -> None:
    repo = tmp_path / "tool-repo"
    repo.mkdir()
    task = Task(worktree_path=str(repo), permission_mode="workspace-write", current_stage=main.IMPLEMENTATION_STAGE)
    main.write_local_file(task, {"path": "notes.txt", "content": "allowed"})
    assert (repo / "notes.txt").read_text(encoding="utf-8") == "allowed"
    try:
        main.write_local_file(task, {"path": "../outside.txt", "content": "blocked"})
        assert False, "writing outside the task directory should fail"
    except ValueError as error:
        assert "only allows paths inside" in str(error)


def test_local_write_tool_rejects_non_writable_workflow_stages(tmp_path: Path) -> None:
    repo = tmp_path / "acceptance-repo"
    repo.mkdir()
    task = Task(worktree_path=str(repo), permission_mode="workspace-write", current_stage=main.AWAIT_ACCEPTANCE_STAGE)

    try:
        main.write_local_file(task, {"path": "notes.txt", "content": "blocked"})
        assert False, "acceptance stage should not allow file changes"
    except ValueError as error:
        assert "stage or permission" in str(error)
    assert not (repo / "notes.txt").exists()


def test_context_usage_and_model_compression_keep_chat_history(monkeypatch, tmp_path: Path) -> None:
    repo = tmp_path / "context-repo"
    init_clean_repo(repo)
    original_provider_id = active_provider_id()
    provider = client.post("/api/model-providers", json={
        "name": f"context-{uuid.uuid4().hex}", "kind": "external", "base_url": "https://example.test/v1", "model_name": "test-model",
    }).json()
    assert client.post(f"/api/model-providers/{provider['id']}/activate").status_code == 200

    captured: dict = {}

    class SummaryResponse:
        def raise_for_status(self) -> None: pass
        def json(self) -> dict: return {"choices": [{"message": {"content": "## 压缩摘要\n\n- 已确认的任务目标"}}]}

    def fake_post(*args, **kwargs):
        captured.update(kwargs["json"])
        return SummaryResponse()

    monkeypatch.setattr(main.httpx, "post", fake_post)
    task = client.post("/api/tasks", json={
        "source_type": "local", "local_path": str(repo), "title": f"context-{uuid.uuid4().hex}",
    }).json()
    task_id = task["id"]
    try:
        with SessionLocal() as db:
            db.add_all([
                TaskMessage(id=str(uuid.uuid4()), task_id=task_id, role="user", content="a" * 10_000, created_at=main.now()),
                TaskMessage(id=str(uuid.uuid4()), task_id=task_id, role="assistant", content="b" * 10_000, created_at=main.now()),
            ])
            db.commit()

        before = client.get(f"/api/tasks/{task_id}/context")
        assert before.status_code == 200
        assert before.json()["compressible_messages"] == 2
        assert before.json()["used_tokens"] > 5_000

        compressed = client.post(f"/api/tasks/{task_id}/context/compress/stream")
        assert compressed.status_code == 200
        assert "event: activity" in compressed.text
        assert "event: done" in compressed.text
        after = client.get(f"/api/tasks/{task_id}/context").json()
        assert after["compressible_messages"] == 0
        assert after["compacted_messages"] == 2
        assert after["used_tokens"] < before.json()["used_tokens"]
        assert "Conversation to compress" in captured["messages"][1]["content"]

        history = client.get(f"/api/tasks/{task_id}/messages")
        assert len(history.json()) == 2
        with SessionLocal() as db:
            stored = list(db.scalars(select(TaskMessage).where(TaskMessage.task_id == task_id)))
            assert all(message.context_compacted for message in stored)
            assert db.get(Task, task_id).context_summary
    finally:
        remove_provider(provider["id"])
        if original_provider_id:
            client.post(f"/api/model-providers/{original_provider_id}/activate")
        remove_workspace_by_path(repo)


def test_streamed_conversation_emits_activity_tokens_and_completion(monkeypatch, tmp_path: Path) -> None:
    repo = tmp_path / "stream-repo"
    init_clean_repo(repo)
    original_provider_id = active_provider_id()
    provider = client.post("/api/model-providers", json={
        "name": f"stream-{uuid.uuid4().hex}", "kind": "external", "base_url": "https://example.test/v1", "model_name": "test-model",
    }).json()
    assert client.post(f"/api/model-providers/{provider['id']}/activate").status_code == 200

    class RoutingResponse:
        def raise_for_status(self) -> None: pass
        def json(self) -> dict:
            return {"choices": [{"message": {"content": '{"task_type":"development","complexity_reason":"普通实现任务。","workflow":"simple","required_stages":["需求分析","编码实现","代码审核","单元测试"]}'}}]}

    class StreamResponse:
        def __enter__(self): return self
        def __exit__(self, *args): return None
        def raise_for_status(self) -> None: pass
        def iter_lines(self):
            return iter([
                'data: {"choices":[]}',
                'data: {"choices":[{"delta":{"content":"streamed answer"},"finish_reason":null}]}',
                'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}',
                "data: [DONE]",
            ])

    monkeypatch.setattr(main.httpx, "post", lambda *args, **kwargs: RoutingResponse())
    monkeypatch.setattr(main.httpx, "stream", lambda *args, **kwargs: StreamResponse())
    task = client.post("/api/tasks", json={"source_type": "local", "local_path": str(repo), "title": f"stream-{uuid.uuid4().hex}"}).json()
    try:
        response = client.post(f"/api/tasks/{task['id']}/messages/stream", json={"content": "stream this"})
        assert response.status_code == 200
        assert "event: workflow" in response.text
        assert "event: activity" in response.text
        assert "event: token" in response.text
        assert "streamed answer" in response.text
        assert "event: done" in response.text

        class ToolResponse:
            def __init__(self, lines: list[str]): self.lines = lines
            def __enter__(self): return self
            def __exit__(self, *args): return None
            def raise_for_status(self) -> None: pass
            def iter_lines(self): return iter(self.lines)

        tool_streams = iter([
            ToolResponse([
                'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call-1","function":{"name":"list_files","arguments":"{\\\"path\\\":\\\".\\\"}"}}]},"finish_reason":"tool_calls"}]}',
                "data: [DONE]",
            ]),
            ToolResponse([
                'data: {"choices":[{"delta":{"content":"tool result"},"finish_reason":"stop"}]}',
                "data: [DONE]",
            ]),
        ])
        monkeypatch.setattr(main.httpx, "stream", lambda *args, **kwargs: next(tool_streams))
        tool_reply = client.post(f"/api/tasks/{task['id']}/messages/stream", json={"content": "list files"})
        assert tool_reply.status_code == 200
        assert "list_files" in tool_reply.text
        assert "tool result" in tool_reply.text
        assert "event: done" in tool_reply.text

        class BrokenStream:
            def __enter__(self): raise main.httpx.ConnectError("connection refused")
            def __exit__(self, *args): return None

        delays: list[int] = []
        monkeypatch.setattr(main.httpx, "stream", lambda *args, **kwargs: BrokenStream())
        monkeypatch.setattr(main.time, "sleep", lambda seconds: delays.append(seconds))
        failed = client.post(f"/api/tasks/{task['id']}/messages/stream", json={"content": "retry stream"})
        assert failed.status_code == 200
        assert "event: error" in failed.text
        assert "connection refused" in failed.text
        assert "after 5 retries" in failed.text
        assert delays == [2, 4, 8, 16, 32]
    finally:
        remove_provider(provider["id"])
        if original_provider_id:
            client.post(f"/api/model-providers/{original_provider_id}/activate")
        remove_workspace_by_path(repo)
