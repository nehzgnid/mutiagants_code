from __future__ import annotations

import json
import uuid
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.app import main
from backend.app.main import ModelProvider, SessionLocal, TaskMessage, app, read_secrets, write_secrets
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

    responses = iter([
        '{"task_type":"read_only_analysis","complexity_reason":"用户只要求分析依赖。","workflow":"read_only","required_stages":["阅读分析"]}',
        "模块依赖已分析。",
    ])

    class Response:
        def raise_for_status(self) -> None: pass
        def json(self) -> dict: return {"choices": [{"message": {"content": next(responses)}}]}

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
            assert record.execution_mode == "confirm_before_coding"
            decision = main.RoutingDecision(
                task_type="development", complexity_reason="复杂变更", workflow="full",
                required_stages=main.WORKFLOW_STAGES["full"],
            )
            run = main.route_message(db, record, "为登录接口增加权限校验并修改多个文件", decision)
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
            assert main.write_enabled(record)
            assert {tool["function"]["name"] for tool in main.tools_for_task(record)} == {"list_files", "read_file", "apply_patch"}
            db.commit()
    finally:
        remove_workspace_by_path(repo)


def test_automatic_mode_locks_only_while_coding_is_running(tmp_path: Path) -> None:
    repo = tmp_path / "automatic-workflow"
    init_clean_repo(repo)
    try:
        task = client.post("/api/tasks", json={"source_type": "local", "local_path": str(repo), "title": "自动执行"}).json()
        updated = client.patch(f"/api/tasks/{task['id']}/execution-mode", json={"execution_mode": "automatic"})
        assert updated.status_code == 200
        assert updated.json()["execution_mode"] == "automatic"
        with SessionLocal() as db:
            record = db.get(main.Task, task["id"])
            decision = main.RoutingDecision(task_type="development", complexity_reason="复杂变更", workflow="full", required_stages=main.WORKFLOW_STAGES["full"])
            requirements = main.route_message(db, record, "改造多个模块", decision)
            assert requirements is not None
            main.complete_stage(db, record.id, requirements.id, "需求")
            overview = main.start_stage(db, record, main.HIGH_LEVEL_DESIGN_STAGE, "继续")
            main.complete_stage(db, record.id, overview.id, "概要")
            detail = main.start_stage(db, record, main.DETAILED_DESIGN_STAGE, "继续")
            main.complete_stage(db, record.id, detail.id, "详细")
            assert record.current_stage == main.IMPLEMENTATION_STAGE
            assert not main.execution_mode_locked(record)
            implementation = main.start_stage(db, record, main.IMPLEMENTATION_STAGE, "自动继续")
            assert main.execution_mode_locked(record)
            main.complete_stage(db, record.id, implementation.id, "实现")
            assert record.current_stage == main.CODE_REVIEW_STAGE
            assert not main.execution_mode_locked(record)
            review = main.start_stage(db, record, main.CODE_REVIEW_STAGE, "自动继续")
            main.complete_stage(db, record.id, review.id, "审核")
            assert record.current_stage == main.UNIT_TESTING_STAGE
            tests = main.start_stage(db, record, main.UNIT_TESTING_STAGE, "自动继续")
            main.complete_stage(db, record.id, tests.id, "测试")
            assert record.current_stage == main.AWAIT_ACCEPTANCE_STAGE
            db.commit()
        updated = client.patch(f"/api/tasks/{task['id']}/execution-mode", json={"execution_mode": "confirm_before_coding"})
        assert updated.status_code == 200
        assert updated.json()["execution_mode"] == "confirm_before_coding"
    finally:
        remove_workspace_by_path(repo)


def test_master_agent_json_route_controls_workflow_and_persists_contract(monkeypatch, tmp_path: Path) -> None:
    repo = tmp_path / "master-route"
    init_clean_repo(repo)
    provider = ModelProvider(
        id=str(uuid.uuid4()), name="router", kind="external", base_url="https://example.test/v1",
        model_name="test-model", is_active=True, created_at=main.now(),
    )

    class Response:
        def raise_for_status(self) -> None: pass
        def json(self) -> dict:
            return {"choices": [{"message": {"content": """{
                \"task_type\": \"development\",
                \"complexity_reason\": \"涉及权限与多个模块，需要完整设计。\",
                \"workflow\": \"full\",
                \"required_stages\": [\"需求分析\", \"概要设计\", \"详细设计\", \"编码实现\", \"代码审核\", \"单元测试\"]
            }"""}}]}

    monkeypatch.setattr(main.httpx, "post", lambda *args, **kwargs: Response())
    try:
        with SessionLocal() as db:
            workspace = main.register_workspace(db, repo, "main", "router-test", ["python", "-m", "pytest"])
            task = main.Task(id=str(uuid.uuid4()), workspace_id=workspace.id, title="权限改造", requirement="", status="created",
                             current_stage="", worktree_path=str(repo), created_at=main.now(), updated_at=main.now())
            db.add(task)
            decision = main.master_agent_route(provider, task, "为登录增加权限校验")
            run = main.route_message(db, task, "为登录增加权限校验", decision)
            assert run is not None
            assert task.workflow_type == "full"
            assert task.routing_decision == decision.model_dump()
            assert task.current_stage == "需求分析"
    finally:
        remove_workspace_by_path(repo)


def test_master_agent_can_plan_only_remaining_stages_from_persisted_context(monkeypatch, tmp_path: Path) -> None:
    repo = tmp_path / "context-aware-route"
    init_clean_repo(repo)
    provider = ModelProvider(id=str(uuid.uuid4()), name="context-router", kind="external", base_url="https://example.test/v1",
                             model_name="test-model", is_active=True, created_at=main.now())
    captured: dict = {}

    class Response:
        def raise_for_status(self) -> None: pass
        def json(self) -> dict:
            return {"choices": [{"message": {"content": json.dumps({
                "task_type": "development", "complexity_reason": "已有设计产物，只需实施、审查和测试。", "workflow": "simple",
                "required_stages": [main.IMPLEMENTATION_STAGE, main.CODE_REVIEW_STAGE, main.UNIT_TESTING_STAGE],
            }, ensure_ascii=False)}}]}

    def fake_post(*args, **kwargs):
        captured.update(kwargs["json"])
        return Response()

    monkeypatch.setattr(main.httpx, "post", fake_post)
    try:
        with SessionLocal() as db:
            workspace = main.register_workspace(db, repo, "main", "context-router", ["python", "-m", "pytest"])
            task = main.Task(id=str(uuid.uuid4()), workspace_id=workspace.id, title="已有方案的改动", requirement="", status="created",
                             current_stage="", worktree_path=str(repo), context_summary="概要和详细设计已经确认。",
                             artifacts={"design": {"stage": main.DETAILED_DESIGN_STAGE, "content": "已确认接口和数据结构。"}},
                             execution_mode="automatic", created_at=main.now(), updated_at=main.now())
            db.add(task)
            db.add(TaskMessage(id=str(uuid.uuid4()), task_id=task.id, role="user", content="设计已完成，开始实现。", created_at=main.now()))
            db.flush()
            decision = main.master_agent_route(provider, task, "请按既有方案实现", main.routing_context(db, task))
            run = main.route_message(db, task, "请按既有方案实现", decision)
            assert run is not None
            assert run.stage == main.IMPLEMENTATION_STAGE
            assert decision.required_stages == [main.IMPLEMENTATION_STAGE, main.CODE_REVIEW_STAGE, main.UNIT_TESTING_STAGE]
            prompt = captured["messages"][1]["content"]
            assert "概要和详细设计已经确认" in prompt
            assert main.DETAILED_DESIGN_STAGE in prompt
    finally:
        remove_workspace_by_path(repo)


def test_master_agent_plan_is_not_changed_by_keyword_heuristics(monkeypatch, tmp_path: Path) -> None:
    repo = tmp_path / "write-intent-route"
    init_clean_repo(repo)
    provider = ModelProvider(id=str(uuid.uuid4()), name="write-intent-router", kind="external", base_url="https://example.test/v1",
                             model_name="test-model", is_active=True, created_at=main.now())

    class Response:
        def raise_for_status(self) -> None: pass
        def json(self) -> dict:
            return {"choices": [{"message": {"content": json.dumps({
                "task_type": "development", "complexity_reason": "误判为只需审核", "workflow": "simple",
                "required_stages": [main.CODE_REVIEW_STAGE],
            }, ensure_ascii=False)}}]}

    monkeypatch.setattr(main.httpx, "post", lambda *args, **kwargs: Response())
    try:
        task = main.Task(id=str(uuid.uuid4()), workspace_id="workspace", title="Java 示例", requirement="", status="created",
                         current_stage=main.CODE_REVIEW_STAGE, created_at=main.now(), updated_at=main.now())
        decision = main.master_agent_route(provider, task, "使用 Java 写一个 HelloWorld，并编译")
        assert decision.required_stages == [main.CODE_REVIEW_STAGE]
    finally:
        remove_workspace_by_path(repo)


def test_new_instruction_replans_an_existing_task_instead_of_reusing_test_stage(tmp_path: Path) -> None:
    repo = tmp_path / "replan-existing-task"
    init_clean_repo(repo)
    try:
        task = client.post("/api/tasks", json={"source_type": "local", "local_path": str(repo), "title": "已有流程的任务"}).json()
        with SessionLocal() as db:
            record = db.get(main.Task, task["id"])
            record.workflow_type = "full"
            record.task_kind = "development"
            record.execution_mode = "automatic"
            record.current_stage = main.UNIT_TESTING_STAGE
            record.status = "awaiting_input"
            record.assigned_agent = "测试 Agent"
            record.routing_decision = {
                "task_type": "development", "complexity_reason": "旧计划", "workflow": "full",
                "required_stages": main.WORKFLOW_STAGES["full"],
            }
            new_instruction = "解决 JWT 密钥可能不安全问题"
            assert main.should_replan(record, new_instruction)
            decision = main.RoutingDecision(
                task_type="development", complexity_reason="需要直接修复 JWT 配置并验证。", workflow="simple",
                required_stages=[main.IMPLEMENTATION_STAGE, main.CODE_REVIEW_STAGE, main.UNIT_TESTING_STAGE],
            )
            run = main.route_message(db, record, new_instruction, decision)
            assert run is not None
            assert run.stage == main.IMPLEMENTATION_STAGE
            assert run.agent == "执行 Agent"
            assert record.current_stage == main.IMPLEMENTATION_STAGE
            assert record.routing_decision == decision.model_dump()
            record.current_stage = main.AWAIT_CODING_APPROVAL_STAGE
            assert not main.should_replan(record, "确认继续")
    finally:
        remove_workspace_by_path(repo)


def test_master_agent_rejects_invalid_stage_contract_without_starting_task(monkeypatch, tmp_path: Path) -> None:
    repo = tmp_path / "invalid-master-route"
    init_clean_repo(repo)
    provider = ModelProvider(id=str(uuid.uuid4()), name="router-invalid", kind="external", base_url="https://example.test/v1",
                             model_name="test-model", is_active=True, created_at=main.now())

    class Response:
        def raise_for_status(self) -> None: pass
        def json(self) -> dict:
            return {"choices": [{"message": {"content": '{"task_type":"development","complexity_reason":"x","workflow":"simple","required_stages":["代码审核","编码实现"]}'}}]}

    monkeypatch.setattr(main.httpx, "post", lambda *args, **kwargs: Response())
    try:
        task = main.Task(id=str(uuid.uuid4()), workspace_id="workspace", title="测试", requirement="", status="created",
                         current_stage="", created_at=main.now(), updated_at=main.now())
        try:
            main.master_agent_route(provider, task, "修改一个按钮")
        except main.RoutingError as error:
            assert "主 Agent 路由失败" in str(error)
        else:
            raise AssertionError("invalid routing contract must not have a fallback")
        assert task.workflow_type is None
        assert task.routing_decision is None
    finally:
        remove_workspace_by_path(repo)


def test_routing_failure_does_not_persist_message_or_start_stage(monkeypatch, tmp_path: Path) -> None:
    repo = tmp_path / "routing-failure"
    init_clean_repo(repo)
    original_provider_id = active_provider_id()
    provider = client.post("/api/model-providers", json={
        "name": f"routing-failure-{uuid.uuid4().hex}", "kind": "external", "base_url": "https://example.test/v1", "model_name": "test-model",
    }).json()
    client.post(f"/api/model-providers/{provider['id']}/activate")

    class Response:
        def raise_for_status(self) -> None: pass
        def json(self) -> dict:
            return {"choices": [{"message": {"content": "not JSON"}}]}

    monkeypatch.setattr(main.httpx, "post", lambda *args, **kwargs: Response())
    task = client.post("/api/tasks", json={"source_type": "local", "local_path": str(repo), "title": "路由失败"}).json()
    try:
        response = client.post(f"/api/tasks/{task['id']}/messages", json={"content": "修改按钮"})
        assert response.status_code == 502
        current = client.get(f"/api/tasks/{task['id']}").json()
        assert current["workflow_type"] == "unclassified"
        assert client.get(f"/api/tasks/{task['id']}/messages").json() == []
        assert client.get(f"/api/tasks/{task['id']}/stages").json() == []
    finally:
        remove_provider(provider["id"])
        if original_provider_id:
            client.post(f"/api/model-providers/{original_provider_id}/activate")
        remove_workspace_by_path(repo)
