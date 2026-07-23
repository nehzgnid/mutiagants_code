from __future__ import annotations

import json
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
    try:
        main.resolve_tool_path(str(repo), "../outside.txt", "workspace-write")
        assert False, "workspace path resolution should reject paths outside the task directory"
    except ValueError as error:
        assert "only allows paths inside" in str(error)


def test_read_file_returns_a_bounded_range_and_hash(tmp_path: Path) -> None:
    repo = tmp_path / "read-range-repo"
    repo.mkdir()
    source = "".join(f"line {index}\n" for index in range(1, 701))
    (repo / "sample.txt").write_text(source, encoding="utf-8")
    task = Task(worktree_path=str(repo), permission_mode="read-only")

    result = json.loads(main.read_local_file(task, {"path": "sample.txt", "start_line": 20, "end_line": 25}))

    assert result["start_line"] == 20
    assert result["end_line"] == 25
    assert result["content"] == "".join(f"line {index}\n" for index in range(20, 26))
    assert result["sha256"] == main.file_digest(source)
    assert result["truncated"] is True


def test_continuous_working_context_compacts_old_tool_output() -> None:
    messages: list[dict] = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "goal " + "x" * 20_000},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "old", "function": {"name": "read_file", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "old", "content": json.dumps({"content": "a" * 12_000})},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "recent", "function": {"name": "read_file", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "recent", "content": json.dumps({"content": "b" * 12_000})},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "latest", "function": {"name": "read_file", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "latest", "content": json.dumps({"content": "c" * 12_000})},
    ]

    compacted = main.compact_continuous_messages(messages)

    assert any(message.get("content", "").startswith("Working-state summary") for message in compacted)
    assert any(message.get("tool_call_id") == "recent" for message in compacted)
    assert any(message.get("tool_call_id") == "latest" for message in compacted)
    assert not any(message.get("tool_call_id") == "old" for message in compacted)
    recent_result = next(message["content"] for message in compacted if message.get("tool_call_id") == "recent")
    assert len(recent_result) <= main.CONTINUOUS_TOOL_RESULT_MAX_CHARS + 100
    assert main.CONTINUOUS_TOOL_LOOP_LIMIT == 3


def test_local_write_tool_rejects_non_writable_workflow_stages(tmp_path: Path) -> None:
    repo = tmp_path / "acceptance-repo"
    repo.mkdir()
    task = Task(worktree_path=str(repo), permission_mode="workspace-write", current_stage=main.AWAIT_ACCEPTANCE_STAGE)

    assert not main.write_enabled(task)


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

    captured_stream_requests: list[dict] = []
    monkeypatch.setattr(main.httpx, "post", lambda *args, **kwargs: RoutingResponse())
    monkeypatch.setattr(main.httpx, "stream", lambda *args, **kwargs: (captured_stream_requests.append(kwargs["json"]) or StreamResponse()))
    task = client.post("/api/tasks", json={"source_type": "local", "local_path": str(repo), "title": f"stream-{uuid.uuid4().hex}"}).json()
    try:
        response = client.post(f"/api/tasks/{task['id']}/messages/stream", json={"content": "stream this"})
        assert response.status_code == 200
        assert "event: run" in response.text
        assert "event: activity" in response.text
        assert "等待模型响应" in response.text
        assert "event: timing" in response.text
        assert "event: token" in response.text
        assert "streamed answer" in response.text
        assert "event: done" in response.text
        tool_names = {tool["function"]["name"] for tool in captured_stream_requests[-1]["tools"]}
        assert {"list_files", "read_file", "apply_patch", "run_command"}.issubset(tool_names)
        patch_tool = next(tool["function"] for tool in captured_stream_requests[-1]["tools"] if tool["function"]["name"] == "apply_patch")
        assert "do not replace the whole file" in patch_tool["description"]
        assert "old_text=null only to create a brand-new file" in patch_tool["description"]
        timed_run = client.get(f"/api/tasks/{task['id']}/agent-runs").json()[0]
        assert timed_run["result"]["timing"]["total_ms"] >= 0
        assert timed_run["result"]["timing"]["agents"]["Main Agent"]["model_ms"] >= 0

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
        assert "event: activity" in tool_reply.text
        assert "阅读 Agent" in tool_reply.text
        assert "正在执行 list_files" in tool_reply.text
        assert "tool result" in tool_reply.text
        assert "event: done" in tool_reply.text
        timed_tool_run = client.get(f"/api/tasks/{task['id']}/agent-runs").json()[0]
        assert timed_tool_run["result"]["timing"]["agents"]["阅读 Agent"]["tool_ms"] >= 0

        class BrokenStream:
            def __enter__(self): raise main.httpx.ConnectError("connection refused")
            def __exit__(self, *args): return None

        recovery_streams = iter([BrokenStream(), StreamResponse()])
        recovery_delays: list[float] = []
        monkeypatch.setattr(main.httpx, "stream", lambda *args, **kwargs: next(recovery_streams))
        monkeypatch.setattr(main.time, "sleep", lambda seconds: recovery_delays.append(seconds))
        recovered = client.post(f"/api/tasks/{task['id']}/messages/stream", json={"content": "recover stream"})
        assert recovered.status_code == 200
        assert "模型连接中断，正在重新连接" in recovered.text
        assert "streamed answer" in recovered.text
        assert "event: done" in recovered.text
        assert recovery_delays == [main.MODEL_STREAM_RETRY_DELAY_SECONDS]

        delays: list[float] = []
        attempts = 0
        def broken_stream(*args, **kwargs):
            nonlocal attempts
            attempts += 1
            return BrokenStream()
        monkeypatch.setattr(main.httpx, "stream", broken_stream)
        monkeypatch.setattr(main.time, "sleep", lambda seconds: delays.append(seconds))
        failed = client.post(f"/api/tasks/{task['id']}/messages/stream", json={"content": "retry stream"})
        assert failed.status_code == 200
        assert "event: error" in failed.text
        assert "connection refused" in failed.text
        assert attempts == main.MODEL_STREAM_MAX_ATTEMPTS
        assert delays == [main.MODEL_STREAM_RETRY_DELAY_SECONDS]
        agent_runs = client.get(f"/api/tasks/{task['id']}/agent-runs").json()
        failed_run = next(run for run in agent_runs if run["status"] == "failed")
        assert failed_run["result"]["error"]
        assert failed_run["result"]["activities"]
    finally:
        remove_provider(provider["id"])
        if original_provider_id:
            client.post(f"/api/model-providers/{original_provider_id}/activate")
        remove_workspace_by_path(repo)


def test_continuous_tool_activity_reports_agent_roles(tmp_path: Path) -> None:
    repo = tmp_path / "tool-activity-repo"
    init_clean_repo(repo)
    task = Task(id=str(uuid.uuid4()), title="tool activity", worktree_path=str(repo), permission_mode="full-access")
    with SessionLocal() as db:
        assert main.continuous_tool_activity(db, task, "list_files") == {
            "kind": "tool", "title": "阅读 Agent", "detail": "正在执行 list_files"
        }
        assert main.continuous_tool_activity(db, task, "apply_patch") == {
            "kind": "tool", "title": "执行 Agent", "detail": "正在执行 apply_patch"
        }
        assert main.continuous_tool_activity(db, task, "custom_tool") == {
            "kind": "tool", "title": "工具 Agent", "detail": "正在执行 custom_tool"
        }


def test_continuous_run_summarizes_instead_of_failing_at_tool_limit(monkeypatch, tmp_path: Path) -> None:
    repo = tmp_path / "tool-limit-repo"
    init_clean_repo(repo)
    original_provider_id = active_provider_id()
    provider = client.post("/api/model-providers", json={
        "name": f"tool-limit-{uuid.uuid4().hex}", "kind": "external", "base_url": "https://example.test/v1", "model_name": "test-model",
    }).json()
    client.post(f"/api/model-providers/{provider['id']}/activate")

    class ToolResponse:
        def __enter__(self): return self
        def __exit__(self, *args): return None
        def raise_for_status(self) -> None: pass
        def iter_lines(self):
            return iter([
                'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call-1","function":{"name":"list_files","arguments":"{\\\"path\\\":\\\".\\\"}"}}]},"finish_reason":"tool_calls"}]}',
                "data: [DONE]",
            ])

    class SummaryResponse:
        def __enter__(self): return self
        def __exit__(self, *args): return None
        def raise_for_status(self) -> None: pass
        def iter_lines(self):
            return iter([
                'data: {"choices":[{"delta":{"content":"本轮工具预算已用完，可以继续处理。"},"finish_reason":"stop"}]}',
                "data: [DONE]",
            ])

    captured_requests: list[dict] = []
    streams = iter([ToolResponse(), SummaryResponse()])
    monkeypatch.setattr(main, "CONTINUOUS_TOOL_LOOP_LIMIT", 1)
    monkeypatch.setattr(main.httpx, "stream", lambda *args, **kwargs: (captured_requests.append(kwargs["json"]) or next(streams)))
    task = client.post("/api/tasks", json={"source_type": "local", "local_path": str(repo), "title": f"tool-limit-{uuid.uuid4().hex}"}).json()
    try:
        response = client.post(f"/api/tasks/{task['id']}/messages/stream", json={"content": "inspect until limit"})
        assert response.status_code == 200
        assert "event: error" not in response.text
        assert "event: done" in response.text
        assert "整理阶段结果" in response.text
        assert "本轮工具预算已用完" in response.text
        assert "tools" not in captured_requests[-1]
        current = client.get(f"/api/tasks/{task['id']}").json()
        assert current["status"] == "awaiting_input"
        agent_run = client.get(f"/api/tasks/{task['id']}/agent-runs").json()[0]
        assert agent_run["status"] == "completed"
    finally:
        remove_provider(provider["id"])
        if original_provider_id:
            client.post(f"/api/model-providers/{original_provider_id}/activate")
        remove_workspace_by_path(repo)


def test_continuous_run_completes_without_creating_stage_records(monkeypatch, tmp_path: Path) -> None:
    repo = tmp_path / "automatic-continuation-repo"
    init_clean_repo(repo)
    original_provider_id = active_provider_id()
    provider = client.post("/api/model-providers", json={
        "name": f"continuation-{uuid.uuid4().hex}", "kind": "external", "base_url": "https://example.test/v1", "model_name": "test-model",
    }).json()
    client.post(f"/api/model-providers/{provider['id']}/activate")

    class RoutingResponse:
        def raise_for_status(self) -> None: pass
        def json(self) -> dict:
            return {"choices": [{"message": {"content": json.dumps({
                "task_type": "development", "complexity_reason": "multi-stage", "workflow": "full",
                "required_stages": [main.REQUIREMENTS_STAGE, main.HIGH_LEVEL_DESIGN_STAGE],
            })}}]}

    class StreamResponse:
        def __enter__(self): return self
        def __exit__(self, *args): return None
        def raise_for_status(self) -> None: pass
        def iter_lines(self):
            return iter(['data: {"choices":[{"delta":{"content":"stage complete"},"finish_reason":"stop"}]}', "data: [DONE]"])

    monkeypatch.setattr(main.httpx, "post", lambda *args, **kwargs: RoutingResponse())
    monkeypatch.setattr(main.httpx, "stream", lambda *args, **kwargs: StreamResponse())
    task = client.post("/api/tasks", json={"source_type": "local", "local_path": str(repo), "title": f"continuation-{uuid.uuid4().hex}"}).json()
    try:
        assert client.patch(f"/api/tasks/{task['id']}/execution-mode", json={"execution_mode": "automatic"}).status_code == 200
        assert client.post(f"/api/tasks/{task['id']}/messages/stream", json={"content": "implement this"}).status_code == 200
        current = client.get(f"/api/tasks/{task['id']}").json()
        assert current["current_stage"] == main.COMPLETED_STAGE
        history = client.get(f"/api/tasks/{task['id']}/messages").json()
        assert [message["role"] for message in history] == ["user", "assistant"]
        stages = client.get(f"/api/tasks/{task['id']}/stages").json()
        assert stages == []
        agent_run = client.get(f"/api/tasks/{task['id']}/agent-runs").json()[0]
        assert agent_run["status"] == "completed"
        assert agent_run["result"]["content"] == "stage complete"
        assert agent_run["result"]["message_id"] == history[-1]["id"]
    finally:
        remove_provider(provider["id"])
        if original_provider_id:
            client.post(f"/api/model-providers/{original_provider_id}/activate")
        remove_workspace_by_path(repo)
