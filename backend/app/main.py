from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import uuid
import asyncio
import hashlib
import difflib
import threading
import queue
import signal
from math import ceil
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, create_engine, inspect, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker


ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)
DATABASE_URL = f"sqlite:///{(DATA_DIR / 'workbench.db').as_posix()}"
EXECUTOR_IMAGE = os.getenv("EXECUTOR_IMAGE", "local-agent-python:3.12")
SECRETS_PATH = DATA_DIR / "model-secrets.json"
# A fast coding turn should normally inspect, edit/verify, then finish.  Further
# exploration is still available by continuing the task after the handoff.
CONTINUOUS_TOOL_LOOP_LIMIT = 3
CONTINUOUS_WORKING_CONTEXT_MAX_CHARS = 28_000
CONTINUOUS_TOOL_RESULT_MAX_CHARS = 6_000
MODEL_STREAM_MAX_ATTEMPTS = 2
MODEL_STREAM_RETRY_DELAY_SECONDS = 0.5
FILE_LIST_MAX_ENTRIES = 120
FILE_READ_DEFAULT_LINES = 400
FILE_READ_MAX_LINES = 2_000
FILE_READ_MAX_CHARS = 24_000
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class Workspace(Base):
    __tablename__ = "workspaces"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String)
    path: Mapped[str] = mapped_column(String, unique=True)
    branch: Mapped[str] = mapped_column(String)
    test_command: Mapped[list[str]] = mapped_column(JSON, default=lambda: ["python", "-m", "pytest"])
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    tasks: Mapped[list["Task"]] = relationship(back_populates="workspace")


class Task(Base):
    __tablename__ = "tasks"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"))
    title: Mapped[str] = mapped_column(String)
    requirement: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String)
    current_stage: Mapped[str] = mapped_column(String)
    worktree_path: Mapped[str | None] = mapped_column(String, nullable=True)
    permission_mode: Mapped[str] = mapped_column(String, default="read-only")
    execution_mode: Mapped[str] = mapped_column(String, default="confirm_before_coding")
    workflow_type: Mapped[str] = mapped_column(String, default="unclassified")
    task_kind: Mapped[str] = mapped_column(String, default="unclassified")
    assigned_agent: Mapped[str] = mapped_column(String, default="主 Agent")
    branch: Mapped[str | None] = mapped_column(String, nullable=True)
    artifacts: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    routing_decision: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    context_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    workspace: Mapped[Workspace] = relationship(back_populates="tasks")
    messages: Mapped[list["TaskMessage"]] = relationship(back_populates="task", cascade="all, delete-orphan")
    stage_runs: Mapped[list["StageRun"]] = relationship(back_populates="task", cascade="all, delete-orphan")


class TaskMessage(Base):
    __tablename__ = "task_messages"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"))
    role: Mapped[str] = mapped_column(String)
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    context_compacted: Mapped[bool] = mapped_column(default=False)
    task: Mapped[Task] = relationship(back_populates="messages")


class StageRun(Base):
    __tablename__ = "stage_runs"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"))
    stage: Mapped[str] = mapped_column(String)
    agent: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String)
    input_summary: Mapped[str] = mapped_column(Text)
    output: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    task: Mapped[Task] = relationship(back_populates="stage_runs")


class ModelProvider(Base):
    __tablename__ = "model_providers"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, unique=True)
    kind: Mapped[str] = mapped_column(String)
    base_url: Mapped[str] = mapped_column(String)
    model_name: Mapped[str] = mapped_column(String)
    is_active: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class McpServer(Base):
    __tablename__ = "mcp_servers"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, unique=True)
    command: Mapped[str] = mapped_column(String)
    arguments: Mapped[list[str]] = mapped_column(JSON, default=list)
    enabled: Mapped[bool] = mapped_column(default=True)
    tools: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class TaskMcpTool(Base):
    __tablename__ = "task_mcp_tools"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"))
    server_id: Mapped[str] = mapped_column(ForeignKey("mcp_servers.id"))
    tool_name: Mapped[str] = mapped_column(String)
    access_mode: Mapped[str] = mapped_column(String)


class AgentRun(Base):
    __tablename__ = "agent_runs"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"))
    status: Mapped[str] = mapped_column(String)
    result: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ExecutionOperation(Base):
    __tablename__ = "execution_operations"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"))
    agent_run_id: Mapped[str | None] = mapped_column(ForeignKey("agent_runs.id"), nullable=True)
    kind: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String)
    request: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ExecutionEvent(Base):
    __tablename__ = "execution_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"))
    operation_id: Mapped[str | None] = mapped_column(ForeignKey("execution_operations.id"), nullable=True)
    kind: Mapped[str] = mapped_column(String)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class TaskAccessGrant(Base):
    __tablename__ = "task_access_grants"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"))
    path: Mapped[str] = mapped_column(String)
    access_mode: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


Base.metadata.create_all(engine)


def is_legacy_coding_mcp(server: McpServer) -> bool:
    """Identify the removed built-in workspace server regardless of where its script path was stored."""
    values = [server.command, *(server.arguments or [])]
    return any(Path(str(value)).name.lower() == "builtin_coding_mcp.py" for value in values)


def migrate_schema() -> None:
    """Apply the small additive migrations needed by the local SQLite database."""
    columns = {column["name"] for column in inspect(engine).get_columns("tasks")}
    with engine.begin() as connection:
        if "permission_mode" not in columns:
            connection.exec_driver_sql(
                "ALTER TABLE tasks ADD COLUMN permission_mode VARCHAR NOT NULL DEFAULT 'read-only'"
            )
        if "execution_mode" not in columns:
            connection.exec_driver_sql(
                "ALTER TABLE tasks ADD COLUMN execution_mode VARCHAR NOT NULL DEFAULT 'confirm_before_coding'"
            )
        for name, default in (("workflow_type", "unclassified"), ("task_kind", "unclassified"), ("assigned_agent", "主 Agent")):
            if name not in columns:
                connection.exec_driver_sql(f"ALTER TABLE tasks ADD COLUMN {name} VARCHAR NOT NULL DEFAULT '{default}'")
        if "context_summary" not in columns:
            connection.exec_driver_sql("ALTER TABLE tasks ADD COLUMN context_summary TEXT")
        if "routing_decision" not in columns:
            connection.exec_driver_sql("ALTER TABLE tasks ADD COLUMN routing_decision JSON")
    message_columns = {column["name"] for column in inspect(engine).get_columns("task_messages")}
    if "context_compacted" not in message_columns:
        with engine.begin() as connection:
            connection.exec_driver_sql("ALTER TABLE task_messages ADD COLUMN context_compacted BOOLEAN NOT NULL DEFAULT 0")
    agent_run_columns = {column["name"] for column in inspect(engine).get_columns("agent_runs")}
    if "result" not in agent_run_columns:
        with engine.begin() as connection:
            connection.exec_driver_sql("ALTER TABLE agent_runs ADD COLUMN result JSON NOT NULL DEFAULT '{}'")
    # The built-in coding MCP was replaced by the host executor. Remove stale records
    # left in existing local databases so they cannot launch a deleted script.
    with SessionLocal() as db:
        legacy_servers = [
            server for server in db.scalars(select(McpServer))
            if is_legacy_coding_mcp(server)
        ]
        for server in legacy_servers:
            for binding in db.scalars(select(TaskMcpTool).where(TaskMcpTool.server_id == server.id)):
                db.delete(binding)
            db.delete(server)
        if legacy_servers:
            db.commit()


migrate_schema()


def now() -> datetime:
    return datetime.now(timezone.utc)


def recover_interrupted_operations() -> None:
    """A process registry is in-memory, so persisted in-flight work cannot survive a restart."""
    with SessionLocal() as db:
        interrupted = list(db.scalars(select(ExecutionOperation).where(ExecutionOperation.status.in_({"queued", "running"}))))
        for operation in interrupted:
            operation.status = "failed"; operation.updated_at = now()
            operation.result = {**(operation.result or {}), "interrupted": True, "message": "Host restarted before the operation completed."}
            record_execution_event(db, operation.task_id, "failed", operation.result, operation.id)
        interrupted_runs = list(db.scalars(select(AgentRun).where(AgentRun.status.in_({"running", "awaiting_approval"}))))
        for run in interrupted_runs:
            run.status, run.updated_at = "failed", now()
            run.result = {**(run.result or {}), "error": "Host restarted before the Agent run completed."}
            task = db.get(Task, run.task_id)
            if task and task.status == "in_progress":
                task.status, task.updated_at = "failed", now()
            record_execution_event(db, run.task_id, "agent_run", {"run_id": run.id, "status": run.status, "result": run.result})
        if interrupted or interrupted_runs: db.commit()


def dump_task(item: Task) -> dict[str, Any]:
    payload = {"id": item.id, "title": item.title,
               "requirement": item.requirement,
               "permission_mode": item.permission_mode,
               "write_enabled": write_enabled(item),
               "execution_mode": item.execution_mode,
               "execution_mode_locked": execution_mode_locked(item),
               "status": item.status, "current_stage": item.current_stage,
               "workflow_type": item.workflow_type, "task_kind": item.task_kind,
               "assigned_agent": item.assigned_agent,
               "routing_decision": item.routing_decision,
               "created_at": item.created_at.isoformat(), "updated_at": item.updated_at.isoformat()}
    return payload


def dump_message(item: TaskMessage) -> dict[str, Any]:
    return {"id": item.id, "role": item.role, "content": item.content, "created_at": item.created_at.isoformat()}


def dump_stage_run(item: StageRun) -> dict[str, Any]:
    return {"id": item.id, "stage": item.stage, "agent": item.agent, "status": item.status,
            "input_summary": item.input_summary, "output": item.output,
            "created_at": item.created_at.isoformat(),
            "completed_at": item.completed_at.isoformat() if item.completed_at else None}


READ_ONLY_STAGE = "阅读分析"
REQUIREMENTS_STAGE = "需求分析"
HIGH_LEVEL_DESIGN_STAGE = "概要设计"
DETAILED_DESIGN_STAGE = "详细设计"
AWAIT_CODING_APPROVAL_STAGE = "待编码确认"
IMPLEMENTATION_STAGE = "编码实现"
CODE_REVIEW_STAGE = "代码审核"
UNIT_TESTING_STAGE = "单元测试"
FIXING_STAGE = "修复"
AWAIT_ACCEPTANCE_STAGE = "待用户验收"
COMPLETED_STAGE = "已完成"


def execution_mode_locked(task: Task) -> bool:
    """Prevent mode changes only while a write-capable stage is running."""
    return task.status == "in_progress" and task.current_stage in {
        IMPLEMENTATION_STAGE, FIXING_STAGE,
    }


def write_enabled(task: Task) -> bool:
    """Report whether the current workflow stage exposes the local write tool."""
    return task.permission_mode in {"workspace-write", "full-access"} and task.current_stage in {
        IMPLEMENTATION_STAGE,
        FIXING_STAGE,
    }


def task_can_write(task: Task) -> bool:
    """Permission gate for the continuous main-Agent run.

    Legacy stage workflows still use write_enabled(); new runs intentionally do
    not need to enter an implementation stage before applying a patch.
    """
    return task.permission_mode in {"workspace-write", "full-access"}

STAGE_AGENTS = {
    READ_ONLY_STAGE: "阅读 Agent", REQUIREMENTS_STAGE: "主 Agent", HIGH_LEVEL_DESIGN_STAGE: "阅读 Agent",
    DETAILED_DESIGN_STAGE: "阅读 Agent", IMPLEMENTATION_STAGE: "执行 Agent", CODE_REVIEW_STAGE: "审查 Agent",
    UNIT_TESTING_STAGE: "测试 Agent", FIXING_STAGE: "执行 Agent",
}
CONFIRMATION_KEYWORDS = ("确认", "继续", "执行", "开始编码", "开始实现", "同意")


class RoutingDecision(BaseModel):
    """The validated contract between the master agent and the stage orchestrator."""
    task_type: Literal["read_only_analysis", "development"]
    complexity_reason: str = Field(min_length=1, max_length=500)
    workflow: Literal["read_only", "simple", "full"]
    required_stages: list[str] = Field(min_length=1, max_length=6)


WORKFLOW_STAGES = {
    "read_only": [READ_ONLY_STAGE],
    "simple": [REQUIREMENTS_STAGE, IMPLEMENTATION_STAGE, CODE_REVIEW_STAGE, UNIT_TESTING_STAGE],
    "full": [REQUIREMENTS_STAGE, HIGH_LEVEL_DESIGN_STAGE, DETAILED_DESIGN_STAGE, IMPLEMENTATION_STAGE, CODE_REVIEW_STAGE, UNIT_TESTING_STAGE],
}
DEVELOPMENT_STAGES = WORKFLOW_STAGES["full"]


def validate_routing_decision(payload: Any) -> RoutingDecision:
    decision = RoutingDecision.model_validate(payload)
    if (decision.workflow == "read_only") != (decision.task_type == "read_only_analysis"):
        raise ValueError("task_type and workflow must describe the same task category.")
    if decision.workflow == "read_only":
        if decision.required_stages != [READ_ONLY_STAGE]:
            raise ValueError("read_only tasks may contain only the read-only analysis stage.")
        return decision
    if not all(stage in DEVELOPMENT_STAGES for stage in decision.required_stages):
        raise ValueError("development tasks may contain only approved development stages.")
    if len(set(decision.required_stages)) != len(decision.required_stages):
        raise ValueError("required_stages must not repeat a stage.")
    ordered_positions = [DEVELOPMENT_STAGES.index(stage) for stage in decision.required_stages]
    if ordered_positions != sorted(ordered_positions):
        raise ValueError("required_stages must preserve the approved stage order.")
    return decision


class RoutingError(Exception):
    """The master agent did not produce a safe routing decision."""


def routing_context(db: Session, task: Task) -> str:
    """Give the router enough persisted state to omit work already completed."""
    recent_messages = list(db.scalars(
        select(TaskMessage).where(TaskMessage.task_id == task.id).order_by(TaskMessage.created_at.desc(), TaskMessage.id.desc()).limit(8)
    ))
    recent_messages.reverse()
    artifacts = list((task.artifacts or {}).values())[-6:]
    artifact_summary = [
        {"stage": artifact.get("stage"), "content": str(artifact.get("content", ""))[:500]}
        for artifact in artifacts if isinstance(artifact, dict)
    ]
    return json.dumps({
        "current_stage": task.current_stage,
        "previous_workflow": task.workflow_type,
        "previous_plan": task.routing_decision,
        "compressed_context": task.context_summary,
        "completed_artifacts": artifact_summary,
        "recent_messages": [{"role": message.role, "content": message.content[:1_000]} for message in recent_messages],
    }, ensure_ascii=False)


def master_agent_route(provider: ModelProvider, task: Task, content: str, context: str = "") -> RoutingDecision:
    """Ask the master agent for a JSON-only routing decision, then enforce its contract locally."""
    contract = {
        "task_type": "read_only_analysis | development",
        "complexity_reason": "non-empty concise Chinese reason",
        "workflow": "read_only | simple | full (a descriptive collaboration mode, not a fixed sequence)",
        "required_stages": "the necessary non-repeating stages, selected independently in approved order",
    }
    prompt = (
        "You are the master agent that routes a local coding task. Return only one JSON object, with no Markdown. "
        f"Its exact keys and constraints are: {json.dumps(contract, ensure_ascii=False)}. "
        f"Allowed read-only stages: {json.dumps([READ_ONLY_STAGE], ensure_ascii=False)}. "
        f"Allowed development stages, in the only valid order: {json.dumps(DEVELOPMENT_STAGES, ensure_ascii=False)}. "
        "Plan only the stages still needed for this request. Do not add requirements or design stages when the supplied context "
        "already contains adequate completed planning; start at implementation, review, testing, or another necessary later stage. "
        "Requests to create, modify, compile, run, deploy, or otherwise execute code must include 编码实现 before review or testing. "
        "You may choose any ordered subset of the development stages, including a planning-only sequence. The workflow label is "
        "descriptive only: simple and full do not impose fixed stage lists. Use read_only only for requests that do not ask for a change. "
        f"Task title: {task.title}\nTask requirement: {task.requirement}\nPersisted task context: {context or '(none)'}\nIncoming message: {content}"
    )
    try:
        response = httpx.post(
            f"{api_root(provider.base_url)}/chat/completions",
            json={"model": provider.model_name, "temperature": 0, "messages": [
                {"role": "system", "content": "Return valid JSON only. Do not call tools."},
                {"role": "user", "content": prompt},
            ]},
            headers={"Authorization": f"Bearer {read_secrets().get(provider.id, '')}"}, timeout=30,
        )
        response.raise_for_status()
        raw = response.json()["choices"][0]["message"]["content"]
        if not isinstance(raw, str):
            raise ValueError("The master agent did not return JSON text.")
        decision = validate_routing_decision(json.loads(raw))
        return decision
    except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise RoutingError(f"主 Agent 路由失败：{type(error).__name__}: {error}") from error


def apply_routing_decision(task: Task, decision: RoutingDecision) -> str:
    task.workflow_type = decision.workflow
    task.task_kind = decision.task_type
    task.routing_decision = decision.model_dump()
    return decision.required_stages[0]


def next_stage(task: Task, completed_stage: str) -> str:
    decision = task.routing_decision or {}
    required_stages = decision.get("required_stages") if isinstance(decision, dict) else None
    if isinstance(required_stages, list) and completed_stage in required_stages:
        position = required_stages.index(completed_stage)
        if position + 1 < len(required_stages):
            following = required_stages[position + 1]
            if following == IMPLEMENTATION_STAGE and task.execution_mode != "automatic":
                return AWAIT_CODING_APPROVAL_STAGE
            return following
        return COMPLETED_STAGE if task.workflow_type == "read_only" else AWAIT_ACCEPTANCE_STAGE
    if task.workflow_type == "read_only":
        return COMPLETED_STAGE
    if completed_stage == REQUIREMENTS_STAGE:
        if task.workflow_type == "full":
            return HIGH_LEVEL_DESIGN_STAGE
        return IMPLEMENTATION_STAGE if task.execution_mode == "automatic" else AWAIT_CODING_APPROVAL_STAGE
    if completed_stage == HIGH_LEVEL_DESIGN_STAGE:
        return DETAILED_DESIGN_STAGE
    if completed_stage == DETAILED_DESIGN_STAGE:
        return IMPLEMENTATION_STAGE if task.execution_mode == "automatic" else AWAIT_CODING_APPROVAL_STAGE
    if completed_stage == IMPLEMENTATION_STAGE:
        return CODE_REVIEW_STAGE
    if completed_stage == CODE_REVIEW_STAGE:
        return UNIT_TESTING_STAGE
    if completed_stage == UNIT_TESTING_STAGE:
        return AWAIT_ACCEPTANCE_STAGE
    if completed_stage == FIXING_STAGE:
        return CODE_REVIEW_STAGE
    return completed_stage


def can_continue_automatically(task: Task) -> bool:
    decision = task.routing_decision or {}
    stages = decision.get("required_stages") if isinstance(decision, dict) else None
    return (
        task.execution_mode == "automatic"
        and task.status == "awaiting_input"
        and isinstance(stages, list)
        and task.current_stage in stages
    )


def start_stage(db: Session, task: Task, stage: str, content: str) -> StageRun:
    task.current_stage = stage
    task.assigned_agent = STAGE_AGENTS.get(stage, "主 Agent")
    task.status = "in_progress"
    task.updated_at = now()
    run = StageRun(id=str(uuid.uuid4()), task_id=task.id, stage=stage, agent=task.assigned_agent,
                   status="in_progress", input_summary=content[:2_000], output=None,
                   created_at=now(), completed_at=None)
    db.add(run)
    return run


def should_replan(task: Task, content: str) -> bool:
    """Every new instruction is master-routed; explicit approval keeps its guarded transition."""
    return not (
        task.current_stage in {AWAIT_CODING_APPROVAL_STAGE, AWAIT_ACCEPTANCE_STAGE}
        and any(keyword in content.lower() for keyword in CONFIRMATION_KEYWORDS)
    )


def route_message(db: Session, task: Task, content: str, decision: RoutingDecision | None = None) -> StageRun | None:
    if decision:
        stage = apply_routing_decision(task, decision)
        if stage == IMPLEMENTATION_STAGE and task.execution_mode != "automatic":
            task.current_stage = AWAIT_CODING_APPROVAL_STAGE
            task.assigned_agent = "主 Agent"
            task.status = "awaiting_input"
            task.updated_at = now()
            return None
        return start_stage(db, task, stage, content)
    if task.current_stage in {AWAIT_CODING_APPROVAL_STAGE, AWAIT_ACCEPTANCE_STAGE}:
        if not any(keyword in content.lower() for keyword in CONFIRMATION_KEYWORDS):
            return None
        if task.current_stage == AWAIT_CODING_APPROVAL_STAGE:
            if task.permission_mode == "read-only":
                return None
            return start_stage(db, task, IMPLEMENTATION_STAGE, content)
        task.current_stage, task.status, task.assigned_agent, task.updated_at = COMPLETED_STAGE, "completed", "主 Agent", now()
        return None
    if task.workflow_type not in {None, "unclassified"} and task.current_stage != COMPLETED_STAGE:
        return start_stage(db, task, task.current_stage, content)
    if task.status != "in_progress" or task.current_stage == COMPLETED_STAGE:
        raise RoutingError("主 Agent 路由失败：未提供可验证的路由决策。")
    return start_stage(db, task, task.current_stage, content)


def complete_stage(db: Session, task_id: str, run_id: str | None, output: str) -> None:
    task = db.get(Task, task_id)
    if not task or not run_id:
        return
    run = db.get(StageRun, run_id)
    if not run:
        return
    run.status, run.output, run.completed_at = "completed", output, now()
    artifacts = dict(task.artifacts or {})
    artifacts[run.id] = {"stage": run.stage, "agent": run.agent, "content": output}
    task.artifacts = artifacts
    following_stage = next_stage(task, run.stage)
    task.current_stage = following_stage
    task.assigned_agent = STAGE_AGENTS.get(following_stage, "主 Agent")
    task.status = "completed" if following_stage == COMPLETED_STAGE else "awaiting_input"
    task.updated_at = now()


def read_secrets() -> dict[str, str]:
    if not SECRETS_PATH.is_file():
        return {}
    try:
        return json.loads(SECRETS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def write_secrets(secrets: dict[str, str]) -> None:
    SECRETS_PATH.write_text(json.dumps(secrets, ensure_ascii=False), encoding="utf-8")


def dump_provider(item: ModelProvider) -> dict[str, Any]:
    return {"id": item.id, "name": item.name, "kind": item.kind, "base_url": item.base_url,
            "model_name": item.model_name, "is_active": item.is_active,
            "has_api_key": bool(read_secrets().get(item.id)), "created_at": item.created_at.isoformat()}


MCP_ACCESS_MODES = {"read-only", "workspace-write", "full-access"}
MCP_TIMEOUT_SECONDS = 30


def dump_mcp_server(item: McpServer) -> dict[str, Any]:
    return {
        "id": item.id, "name": item.name, "command": item.command,
        "arguments": item.arguments or [], "enabled": item.enabled,
        "tools": item.tools or [], "created_at": item.created_at.isoformat(),
        "updated_at": item.updated_at.isoformat(),
    }


def dump_task_mcp_tool(item: TaskMcpTool, server: McpServer) -> dict[str, Any]:
    tool = next((candidate for candidate in (server.tools or []) if candidate.get("name") == item.tool_name), {})
    return {"server_id": server.id, "server_name": server.name, "tool_name": item.tool_name,
            "description": tool.get("description", ""), "input_schema": tool.get("input_schema", {}),
            "access_mode": item.access_mode, "function_name": mcp_function_name(server, item.tool_name)}


def mcp_function_name(server: McpServer, tool_name: str) -> str:
    safe_name = "".join(character if character.isalnum() else "_" for character in tool_name)
    tool_hash = hashlib.sha256(tool_name.encode("utf-8")).hexdigest()[:12]
    return f"mcp_{server.id.replace('-', '')[:8]}_{tool_hash}_{safe_name[:32]}"


async def mcp_list_tools_async(server: McpServer) -> list[dict[str, Any]]:
    parameters = StdioServerParameters(command=server.command, args=list(server.arguments or []), env=dict(os.environ))
    async with stdio_client(parameters) as (reader, writer):
        async with ClientSession(reader, writer) as session:
            await asyncio.wait_for(session.initialize(), timeout=MCP_TIMEOUT_SECONDS)
            result = await asyncio.wait_for(session.list_tools(), timeout=MCP_TIMEOUT_SECONDS)
            return [{"name": tool.name, "description": tool.description or "", "input_schema": tool.inputSchema}
                    for tool in result.tools]


async def mcp_call_tool_async(server: McpServer, tool_name: str, arguments: dict[str, Any], environment: dict[str, str] | None = None) -> str:
    process_environment = dict(os.environ)
    if environment:
        process_environment.update(environment)
    parameters = StdioServerParameters(command=server.command, args=list(server.arguments or []), env=process_environment)
    async with stdio_client(parameters) as (reader, writer):
        async with ClientSession(reader, writer) as session:
            await asyncio.wait_for(session.initialize(), timeout=MCP_TIMEOUT_SECONDS)
            result = await asyncio.wait_for(
                session.call_tool(tool_name, arguments, read_timeout_seconds=timedelta(seconds=MCP_TIMEOUT_SECONDS)),
                timeout=MCP_TIMEOUT_SECONDS,
            )
            payload = result.model_dump(mode="json")
            if payload.get("isError"):
                raise ValueError(json.dumps(payload, ensure_ascii=False))
            return json.dumps(payload, ensure_ascii=False)


def discover_mcp_tools(server: McpServer) -> list[dict[str, Any]]:
    try:
        return asyncio.run(mcp_list_tools_async(server))
    except (OSError, asyncio.TimeoutError, ValueError) as error:
        raise ValueError(f"MCP Server 连接或工具发现失败：{error}") from error


def classify_mcp_tools(server: McpServer, discovered: list[dict[str, Any]]) -> list[dict[str, Any]]:
    existing_modes = {str(tool.get("name")): mcp_tool_access_mode(server, tool) for tool in (server.tools or [])}
    return [{**tool, "access_mode": existing_modes.get(tool["name"], "read-only")} for tool in discovered]


def api_root(base_url: str) -> str:
    return base_url.rstrip("/") if base_url.rstrip("/").endswith("/v1") else f"{base_url.rstrip('/')}/v1"


def git(path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", str(path), *args], text=True, capture_output=True, check=False)


def ensure_workspace(path: str) -> tuple[Path, str]:
    root = Path(path).expanduser().resolve()
    if not root.is_dir() or git(root, "rev-parse", "--is-inside-work-tree").returncode != 0:
        raise HTTPException(422, "路径必须是一个可访问的 Git 工作区")
    if git(root, "status", "--porcelain").stdout.strip():
        raise HTTPException(409, "Git 工作区存在未提交改动；请先清理后再注册")
    branch = git(root, "branch", "--show-current").stdout.strip() or "HEAD"
    return root, branch


def validate_github_url(url: str) -> None:
    parsed = urlparse(url)
    is_https_github = parsed.scheme in {"http", "https"} and parsed.netloc.lower() == "github.com"
    has_owner_and_repo = len([part for part in parsed.path.split("/") if part]) >= 2
    is_ssh_github = url.startswith("git@github.com:") and "/" in url.removeprefix("git@github.com:")
    if not ((is_https_github and has_owner_and_repo) or is_ssh_github):
        raise HTTPException(422, "GitHub 地址必须是 github.com 上的仓库 URL")


def clone_github_repository(url: str, destination: str) -> Path:
    validate_github_url(url)
    target = Path(destination).expanduser().resolve()
    if target.exists() and any(target.iterdir()):
        raise HTTPException(409, "GitHub 保存目录必须不存在或为空目录")
    target.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(["git", "clone", url, str(target)], text=True, capture_output=True, check=False)
    if result.returncode != 0:
        raise HTTPException(409, result.stderr.strip() or "GitHub 仓库克隆失败")
    return target


def workspace_display_name(path: Path, fallback: str | None = None) -> str:
    name = (fallback or "").strip() or path.name or "Local repository"
    return name[:80]


def register_workspace(db: Session, root: Path, branch: str, name: str | None, test_command: list[str]) -> Workspace:
    existing = db.scalar(select(Workspace).where(Workspace.path == str(root)))
    if existing:
        existing.branch = branch
        existing.test_command = test_command
        return existing
    record = Workspace(id=str(uuid.uuid4()), name=workspace_display_name(root, name), path=str(root), branch=branch,
                       test_command=test_command, created_at=now())
    db.add(record)
    return record


def resolve_tool_path(workspace_path: str | None, requested_path: str, permission_mode: str) -> Path:
    if not workspace_path:
        raise ValueError("The task does not have a local code directory.")
    root = Path(workspace_path).resolve()
    candidate = Path(requested_path).expanduser()
    target = (candidate if candidate.is_absolute() else root / candidate).resolve()
    if permission_mode != "full-access" and not target.is_relative_to(root):
        raise ValueError("This permission mode only allows paths inside the task code directory.")
    return target


def file_digest(content: str | None) -> str | None:
    return hashlib.sha256(content.encode("utf-8")).hexdigest() if content is not None else None


def operation_payload(operation: ExecutionOperation) -> dict[str, Any]:
    return {"id": operation.id, "task_id": operation.task_id, "kind": operation.kind,
            "status": operation.status, "request": operation.request or {}, "result": operation.result,
            "created_at": operation.created_at.isoformat(), "updated_at": operation.updated_at.isoformat()}


def record_execution_event(db: Session, task_id: str, kind: str, payload: dict[str, Any], operation_id: str | None = None) -> None:
    db.add(ExecutionEvent(task_id=task_id, operation_id=operation_id, kind=kind, payload=payload, created_at=now()))


def update_agent_run(run_id: str, task_id: str, *, activity: dict[str, Any] | None = None,
                     token: str | None = None, file: dict[str, Any] | None = None,
                     error: str | None = None, workflow: dict[str, Any] | None = None,
                     status: str | None = None) -> None:
    """Persist the user-visible stream so a disconnect or refresh can recover it."""
    with SessionLocal() as db:
        run = db.get(AgentRun, run_id)
        if not run:
            return
        result = dict(run.result or {})
        if activity:
            result["activities"] = [*(result.get("activities") or []), activity]
        if token:
            result["content"] = f"{result.get('content', '')}{token}"
        if file:
            files = [*(result.get("files") or [])]
            if not any(item.get("path") == file.get("path") and item.get("action") == file.get("action") for item in files):
                files.append(file)
            result["files"] = files
        if workflow:
            result["workflow"] = workflow
        if error:
            result["error"] = error
        run.result = result
        if status:
            run.status = status
        run.updated_at = now()
        record_execution_event(db, task_id, "agent_run", {"run_id": run_id, "status": run.status, "result": result})
        db.commit()


def record_agent_run_timing(run_id: str, agent: str | None = None, metric: str | None = None,
                            elapsed_ms: float = 0) -> dict[str, Any]:
    """Accumulate observable wall-clock timings without storing model reasoning content."""
    with SessionLocal() as db:
        run = db.get(AgentRun, run_id)
        if not run:
            return {}
        result = dict(run.result or {})
        timing = dict(result.get("timing") or {})
        timing.setdefault("started_at", run.created_at.isoformat())
        started_at = run.created_at.replace(tzinfo=timezone.utc) if run.created_at.tzinfo is None else run.created_at
        timing["total_ms"] = max(0, round((now() - started_at).total_seconds() * 1000))
        agents = dict(timing.get("agents") or {})
        if agent and metric:
            agent_timing = dict(agents.get(agent) or {})
            agent_timing[metric] = round(float(agent_timing.get(metric, 0)) + elapsed_ms)
            agents[agent] = agent_timing
        timing["agents"] = agents
        result["timing"] = timing
        run.result, run.updated_at = result, now()
        db.commit()
        return timing


def save_agent_run_context(run_id: str, task_id: str, messages: list[dict[str, Any]], waiting_operation_id: str | None = None) -> None:
    """Persist the tool conversation needed to explain or resume a paused run."""
    with SessionLocal() as db:
        run = db.get(AgentRun, run_id)
        if not run:
            return
        result = dict(run.result or {})
        result["conversation"] = messages
        result["waiting_operation_id"] = waiting_operation_id
        run.result = result
        run.updated_at = now()
        record_execution_event(db, task_id, "agent_run", {"run_id": run_id, "status": run.status, "result": result})
        db.commit()


def update_agent_run_stage(run_id: str, task_id: str, stage: str, agent: str, status: str,
                           output: str | None = None) -> None:
    with SessionLocal() as db:
        run = db.get(AgentRun, run_id)
        if not run:
            return
        result = dict(run.result or {})
        stages = list(result.get("stages") or [])
        entry = {"stage": stage, "agent": agent, "status": status}
        if output is not None:
            entry["output"] = output
        if stages and stages[-1].get("stage") == stage and stages[-1].get("status") == "running":
            stages[-1] = {**stages[-1], **entry}
        else:
            stages.append(entry)
        result["stages"] = stages
        run.result = result
        run.updated_at = now()
        record_execution_event(db, task_id, "agent_stage", {"run_id": run_id, **entry})
        db.commit()


recover_interrupted_operations()


def task_accessible_path(db: Session, task: Task, requested_path: str, write: bool = False) -> Path:
    root = Path(task.worktree_path or "").resolve()
    candidate = Path(requested_path).expanduser()
    target = (candidate if candidate.is_absolute() else root / candidate).resolve()
    if target.is_relative_to(root):
        return target
    required_mode = "workspace-write" if write else "read-only"
    for grant in db.scalars(select(TaskAccessGrant).where(TaskAccessGrant.task_id == task.id)):
        granted_root = Path(grant.path).resolve()
        if target.is_relative_to(granted_root) and (grant.access_mode == "full-access" or grant.access_mode == required_mode):
            return target
    raise ValueError("The requested external path has not been explicitly authorized for this task.")


def patch_preview(task: Task, db: Session, edits: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    prepared: list[dict[str, Any]] = []
    snapshots: dict[str, Any] = {}
    for edit in edits:
        path = str(edit["path"])
        target = task_accessible_path(db, task, path, write=True)
        before = target.read_text(encoding="utf-8") if target.exists() else None
        expected_hash = edit.get("expected_hash")
        if expected_hash is not None and expected_hash != file_digest(before):
            raise RuntimeError(json.dumps({"path": path, "current_hash": file_digest(before), "reason": "base_changed"}))
        old_text = edit.get("old_text")
        new_text = edit.get("new_text", "")
        if old_text is None:
            if before is not None:
                raise ValueError(f"File already exists: {path}. Use exact old_text/new_text edits for existing files; old_text=null is only for creating new files.")
            after = new_text
        else:
            if before is None or old_text not in before:
                raise RuntimeError(json.dumps({"path": path, "current_hash": file_digest(before), "reason": "text_not_found"}))
            if before.count(old_text) != 1:
                raise ValueError(f"Patch text is ambiguous in {path}.")
            after = before.replace(old_text, new_text, 1)
        diff = "".join(difflib.unified_diff((before or "").splitlines(keepends=True), after.splitlines(keepends=True),
                                            fromfile=f"a/{path}", tofile=f"b/{path}"))
        prepared.append({"path": path, "target": target, "before": before, "after": after, "diff": diff})
        snapshots[path] = {"before": before, "before_hash": file_digest(before), "after_hash": file_digest(after)}
    return prepared, snapshots


def apply_prepared_patch(prepared: list[dict[str, Any]]) -> None:
    for item in prepared:
        target: Path = item["target"]
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
        temporary.write_text(item["after"], encoding="utf-8")
        temporary.replace(target)


def execute_patch_operation(db: Session, operation: ExecutionOperation) -> None:
    task = db.get(Task, operation.task_id)
    assert task is not None
    try:
        prepared, snapshot = patch_preview(task, db, list((operation.request or {}).get("edits", [])))
    except RuntimeError as error:
        operation.status = "conflict"; operation.result = json.loads(str(error)); operation.updated_at = now()
        record_execution_event(db, task.id, "conflict", operation.result, operation.id)
        return
    except ValueError as error:
        operation.status = "failed"; operation.result = {"message": str(error)}; operation.updated_at = now()
        record_execution_event(db, task.id, "failed", operation.result, operation.id)
        return
    apply_prepared_patch(prepared)
    operation.status = "completed"; operation.snapshot = snapshot
    operation.result = {"files": [{"path": item["path"], "diff": item["diff"]} for item in prepared]}
    operation.updated_at = now()
    record_execution_event(db, task.id, "completed", operation.result, operation.id)


PROCESS_LOCK = threading.Lock()
ACTIVE_PROCESSES: dict[str, subprocess.Popen[str]] = {}


def append_command_output(operation_id: str, task_id: str, stream: str, content: str) -> None:
    with SessionLocal() as db:
        operation = db.get(ExecutionOperation, operation_id)
        if not operation or operation.status == "canceled":
            return
        result = dict(operation.result or {})
        result[stream] = (result.get(stream, "") + content)[-100_000:]
        operation.result = result; operation.updated_at = now()
        record_execution_event(db, task_id, "output", {"stream": stream, "content": content}, operation_id)
        db.commit()


def run_command_operation(operation_id: str) -> None:
    with SessionLocal() as db:
        operation = db.get(ExecutionOperation, operation_id)
        if not operation or operation.status == "canceled": return
        task = db.get(Task, operation.task_id)
        assert task is not None
        request = operation.request or {}; cwd = task_accessible_path(db, task, str(request.get("working_directory", ".")))
        command = str(request["command"])
        operation.status = "running"; operation.result = {"stdout": "", "stderr": ""}; operation.updated_at = now()
        record_execution_event(db, task.id, "started", {"command": command}, operation.id); db.commit()
    shell_command = ["cmd.exe", "/d", "/s", "/c", command] if os.name == "nt" else ["/bin/sh", "-lc", command]
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    process = subprocess.Popen(shell_command, cwd=cwd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               creationflags=flags, start_new_session=os.name != "nt")
    with PROCESS_LOCK: ACTIVE_PROCESSES[operation_id] = process
    readers = [threading.Thread(target=lambda pipe, name: [append_command_output(operation_id, task.id, name, line) for line in iter(pipe.readline, "")], args=(process.stdout, "stdout")),
               threading.Thread(target=lambda pipe, name: [append_command_output(operation_id, task.id, name, line) for line in iter(pipe.readline, "")], args=(process.stderr, "stderr"))]
    for reader in readers: reader.start()
    timeout = min(max(int(request.get("timeout_seconds", 60)), 1), 1800)
    try:
        returncode = process.wait(timeout=timeout); status = "completed" if returncode == 0 else "failed"
    except subprocess.TimeoutExpired:
        process.kill(); returncode = None; status = "failed"
    for reader in readers: reader.join(timeout=2)
    with PROCESS_LOCK: ACTIVE_PROCESSES.pop(operation_id, None)
    with SessionLocal() as db:
        operation = db.get(ExecutionOperation, operation_id)
        if not operation: return
        if operation.status != "canceled":
            result = dict(operation.result or {}); result.update({"returncode": returncode, "timed_out": returncode is None})
            operation.status = status; operation.result = result; operation.updated_at = now()
            record_execution_event(db, operation.task_id, status, result, operation.id)
        db.commit()


def list_local_files(task: Task, arguments: dict[str, Any]) -> str:
    target = resolve_tool_path(task.worktree_path, str(arguments.get("path", ".")), task.permission_mode)
    if not target.is_dir():
        raise ValueError("The requested path is not a directory.")
    excluded = {".git", "node_modules", ".venv", "__pycache__", "dist", "build"}
    files: list[str] = []
    for path in target.rglob("*"):
        if excluded.intersection(path.parts) or not path.is_file():
            continue
        files.append(str(path.relative_to(target)))
        if len(files) == FILE_LIST_MAX_ENTRIES:
            break
    return json.dumps({"path": str(target), "files": files, "truncated": len(files) == FILE_LIST_MAX_ENTRIES}, ensure_ascii=False)


def read_local_file(task: Task, arguments: dict[str, Any]) -> str:
    target = resolve_tool_path(task.worktree_path, str(arguments["path"]), task.permission_mode)
    if not target.is_file():
        raise ValueError("The requested path is not a file.")
    content = target.read_text(encoding="utf-8", errors="replace")
    lines = content.splitlines(keepends=True)
    start_line = max(1, int(arguments.get("start_line", 1)))
    requested_end = int(arguments.get("end_line", start_line + FILE_READ_DEFAULT_LINES - 1))
    end_line = min(len(lines), max(start_line, min(requested_end, start_line + FILE_READ_MAX_LINES - 1)))
    selected = "".join(lines[start_line - 1:end_line])
    if len(selected) > FILE_READ_MAX_CHARS:
        selected = selected[:FILE_READ_MAX_CHARS]
        truncated = True
    else:
        truncated = end_line < len(lines)
    return json.dumps({
        "path": str(target), "sha256": file_digest(content), "start_line": start_line,
        "end_line": end_line, "total_lines": len(lines), "content": selected, "truncated": truncated,
    }, ensure_ascii=False)


def compact_tool_result(content: str) -> str:
    """Bound tool output sent back to the model; the full output remains in AgentRun."""
    if len(content) <= CONTINUOUS_TOOL_RESULT_MAX_CHARS:
        return content
    try:
        payload = json.loads(content)
        if isinstance(payload, dict) and isinstance(payload.get("content"), str):
            payload = dict(payload)
            payload["content"] = payload["content"][:CONTINUOUS_TOOL_RESULT_MAX_CHARS]
            payload["truncated"] = True
            return json.dumps(payload, ensure_ascii=False)
    except json.JSONDecodeError:
        pass
    return content[:CONTINUOUS_TOOL_RESULT_MAX_CHARS] + "\n[Tool output truncated in working context.]"


def compact_continuous_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep recent tool protocol intact while replacing older turns with local state."""
    copied = [{**message} for message in messages]
    for message in copied:
        if message.get("role") == "tool" and isinstance(message.get("content"), str):
            message["content"] = compact_tool_result(message["content"])
    if sum(len(str(message.get("content") or "")) for message in copied) <= CONTINUOUS_WORKING_CONTEXT_MAX_CHARS:
        return copied

    prefix_end = next((index for index, message in enumerate(copied) if message.get("role") != "system"), len(copied))
    tool_turns = [index for index, message in enumerate(copied) if message.get("role") == "assistant" and message.get("tool_calls")]
    keep_start = tool_turns[-2] if len(tool_turns) >= 2 else max(prefix_end, len(copied) - 4)
    prior = copied[prefix_end:keep_start]
    state: list[str] = []
    for message in prior:
        role, content = str(message.get("role", "unknown")), str(message.get("content") or "")
        if role == "assistant" and message.get("tool_calls"):
            names = ", ".join(str(call.get("function", {}).get("name", "tool")) for call in message["tool_calls"])
            state.append(f"assistant called: {names}")
        elif content:
            state.append(f"{role}: {content[:1_000]}")
    summary = "\n".join(state)[-6_000:] or "Earlier working turns were compacted locally."
    return [
        *copied[:prefix_end],
        {"role": "system", "content": f"Working-state summary from earlier turns:\n{summary}"},
        *copied[keep_start:],
    ]


def apply_local_patch(task: Task, arguments: dict[str, Any], db: Session) -> str:
    if not task_can_write(task):
        raise ValueError("The task permission does not allow file changes.")
    edits = arguments.get("edits")
    if not isinstance(edits, list) or not edits:
        raise ValueError("apply_patch requires at least one edit.")
    operation = ExecutionOperation(id=str(uuid.uuid4()), task_id=task.id, kind="patch", status="queued",
                                   request={"edits": edits}, created_at=now(), updated_at=now())
    db.add(operation)
    if task.execution_mode == "manual_confirmation":
        try:
            prepared, _ = patch_preview(task, db, edits)
            operation.status = "pending_approval"
            operation.result = {"files": [{"path": item["path"], "diff": item["diff"]} for item in prepared]}
            record_execution_event(db, task.id, "approval_required", operation.result, operation.id)
        except RuntimeError as error:
            operation.status = "conflict"; operation.result = json.loads(str(error))
            record_execution_event(db, task.id, "conflict", operation.result, operation.id)
    else:
        execute_patch_operation(db, operation)
    db.commit()
    return json.dumps(operation_payload(operation), ensure_ascii=False)


BASE_TOOLS = [
    {"type": "function", "function": {"name": "list_files", "description": "List up to 120 files below an authorized local directory. Use a focused path when possible.", "parameters": {"type": "object", "properties": {"path": {"type": "string", "description": "Absolute path or path relative to the task code directory."}}, "required": []}}},
    {"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file range and return its SHA-256 hash. Defaults to 400 lines; request a focused range before expanding.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "start_line": {"type": "integer", "minimum": 1}, "end_line": {"type": "integer", "minimum": 1}}, "required": ["path"]}}},
]
PATCH_TOOL = {"type": "function", "function": {"name": "apply_patch", "description": "Atomically apply precise text edits. For existing files, always use small exact old_text/new_text replacements like a diff; do not replace the whole file. Use old_text=null only to create a brand-new file that does not already exist. Read files first and supply their SHA-256 content hash as expected_hash. In manual confirmation mode the patch waits for user approval.", "parameters": {"type": "object", "properties": {"edits": {"type": "array", "items": {"type": "object", "properties": {"path": {"type": "string"}, "expected_hash": {"type": ["string", "null"], "description": "SHA-256 hash of the file content read before editing. Required for existing files."}, "old_text": {"type": ["string", "null"], "description": "Existing files: exact text snippet to replace. New files only: null."}, "new_text": {"type": "string", "description": "Replacement text for old_text, or complete content only when creating a new file."}}, "required": ["path", "expected_hash", "old_text", "new_text"]}}}, "required": ["edits"]}}}
COMMAND_TOOL = {"type": "function", "function": {"name": "run_command", "description": "Start a local command with streamed output. Available only after the user selected full access.", "parameters": {"type": "object", "properties": {"command": {"type": "string"}, "working_directory": {"type": "string"}, "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 1800}}, "required": ["command"]}}}


def tools_for(permission_mode: str) -> list[dict[str, Any]]:
    tools = list(BASE_TOOLS)
    if permission_mode in {"workspace-write", "full-access"}:
        tools.append(PATCH_TOOL)
    if permission_mode == "full-access":
        tools.append(COMMAND_TOOL)
    return tools


def mcp_tool_allowed(task: Task, access_mode: str) -> bool:
    if access_mode == "read-only":
        return True
    if access_mode == "workspace-write":
        return task_can_write(task)
    return task.permission_mode == "full-access"


def mcp_tool_access_mode(server: McpServer, tool: dict[str, Any]) -> str:
    """Unknown third-party tools are read-only unless their profile explicitly classifies them."""
    configured = tool.get("access_mode")
    return configured if configured in MCP_ACCESS_MODES else "read-only"


def enabled_mcp_servers(db: Session) -> list[McpServer]:
    return list(db.scalars(select(McpServer).where(McpServer.enabled.is_(True))))


def task_mcp_tools(db: Session, task: Task) -> list[tuple[TaskMcpTool, McpServer]]:
    records = db.scalars(select(TaskMcpTool).where(TaskMcpTool.task_id == task.id)).all()
    result: list[tuple[TaskMcpTool, McpServer]] = []
    for record in records:
        server = db.get(McpServer, record.server_id)
        if server and server.enabled:
            result.append((record, server))
    return result


def tools_for_task(task: Task, db: Session | None = None, continuous: bool = False) -> list[dict[str, Any]]:
    """Expose globally configured MCP tools that satisfy the task access policy."""
    can_write = task_can_write(task) if continuous else write_enabled(task)
    selected_tools = tools_for(task.permission_mode) if can_write else list(BASE_TOOLS)
    if not db:
        return selected_tools
    for server in enabled_mcp_servers(db):
        for discovered in server.tools or []:
            if not mcp_tool_allowed(task, mcp_tool_access_mode(server, discovered)):
                continue
            tool_name = str(discovered.get("name", ""))
            if not tool_name:
                continue
            selected_tools.append({"type": "function", "function": {
                "name": mcp_function_name(server, tool_name),
                "description": f"MCP {server.name}: {discovered.get('description') or tool_name}",
                "parameters": discovered.get("input_schema") or {"type": "object", "properties": {}},
            }})
    return selected_tools


def execute_tool(task: Task, name: str, arguments: dict[str, Any], db: Session | None = None) -> str:
    if name == "apply_patch":
        if not db: raise ValueError("apply_patch requires a database session.")
        return apply_local_patch(task, arguments, db)
    if name == "run_command":
        if not db: raise ValueError("run_command requires a database session.")
        if task.permission_mode != "full-access": raise ValueError("Only full-access mode allows command execution.")
        operation = ExecutionOperation(id=str(uuid.uuid4()), task_id=task.id, kind="command", status="queued",
                                       request=dict(arguments), created_at=now(), updated_at=now())
        db.add(operation); record_execution_event(db, task.id, "queued", {"command": arguments.get("command", "")}, operation.id); db.commit()
        threading.Thread(target=run_command_operation, args=(operation.id,), daemon=True).start()
        return json.dumps(operation_payload(operation), ensure_ascii=False)
    handlers = {"list_files": list_local_files, "read_file": read_local_file}
    handler = handlers.get(name)
    if handler:
        return handler(task, arguments)
    if not db:
        raise ValueError(f"Unsupported tool: {name}")
    for server in enabled_mcp_servers(db):
        for discovered in server.tools or []:
            tool_name = str(discovered.get("name", ""))
            if mcp_function_name(server, tool_name) != name:
                continue
            if not mcp_tool_allowed(task, mcp_tool_access_mode(server, discovered)):
                raise ValueError("The current task stage or permission does not allow this MCP tool.")
            try:
                return asyncio.run(mcp_call_tool_async(server, tool_name, arguments, {
                    "LOCAL_AGENT_WORKSPACE": task.worktree_path or "",
                }))
            except Exception as error:
                raise ValueError(f"MCP Server {server.name} 调用失败：{error}") from error
    raise ValueError(f"Unsupported tool: {name}")


def tool_activity_detail(db: Session, task: Task, function_name: str) -> str:
    for server in enabled_mcp_servers(db):
        for tool in server.tools or []:
            if mcp_function_name(server, str(tool.get("name", ""))) == function_name:
                return f"正在调用 MCP Server {server.name} 的 {tool.get('name')}"
    return f"正在执行 {function_name}"


def continuous_tool_activity(db: Session, task: Task, function_name: str) -> dict[str, str]:
    agent_by_tool = {
        "list_files": "阅读 Agent",
        "read_file": "阅读 Agent",
        "apply_patch": "执行 Agent",
        "run_command": "执行 Agent",
    }
    return {"kind": "tool", "title": agent_by_tool.get(function_name, "工具 Agent"), "detail": tool_activity_detail(db, task, function_name)}


class TaskInput(BaseModel):
    source_type: str = Field(pattern="^(local|github)$")
    local_path: str | None = None
    github_url: str | None = None
    clone_path: str | None = None
    test_command: list[str] = Field(default_factory=lambda: ["python", "-m", "pytest"])
    title: str = Field(min_length=1, max_length=120)
    requirement: str = Field(default="", max_length=10000)
    permission_mode: str = Field(default="full-access", pattern="^(read-only|workspace-write|full-access)$")
    execution_mode: Literal["confirm_before_coding", "automatic", "manual_confirmation"] = "automatic"


class PermissionInput(BaseModel):
    permission_mode: str = Field(pattern="^(read-only|workspace-write|full-access)$")


class ExecutionModeInput(BaseModel):
    execution_mode: Literal["confirm_before_coding", "automatic", "manual_confirmation"]


class TaskMcpToolInput(BaseModel):
    server_id: str = Field(min_length=1, max_length=80)
    tool_name: str = Field(min_length=1, max_length=160)
    access_mode: Literal["read-only", "workspace-write", "full-access"]


class TaskUpdateInput(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    permission_mode: str = Field(pattern="^(read-only|workspace-write|full-access)$")
    execution_mode: Literal["confirm_before_coding", "automatic", "manual_confirmation"] | None = None
    mcp_tools: list[TaskMcpToolInput] | None = Field(default=None, max_length=100)


class MessageInput(BaseModel):
    content: str = Field(default="", max_length=20000)
    continuation: bool = False


class PatchInput(BaseModel):
    edits: list[dict[str, Any]] = Field(min_length=1, max_length=100)


class CommandInput(BaseModel):
    command: str = Field(min_length=1, max_length=20_000)
    working_directory: str = "."
    timeout_seconds: int = Field(default=60, ge=1, le=1800)


class ApprovalInput(BaseModel):
    approve: bool


class AccessGrantInput(BaseModel):
    path: str = Field(min_length=1, max_length=2000)
    access_mode: Literal["read-only", "workspace-write", "full-access"]


class CommitInput(BaseModel):
    message: str = Field(min_length=1, max_length=500)


CONTEXT_TOKEN_LIMIT = 128_000
SYSTEM_CONTEXT_TOKENS = 800
CONTEXT_SUMMARY_MAX_CHARS = 4_000


def estimate_tokens(content: str) -> int:
    """Use a stable local approximation until providers expose tokenizer metadata."""
    return max(1, ceil(len(content) / 4)) if content else 0


def context_usage(db: Session, task: Task) -> dict[str, int]:
    messages = list(db.scalars(
        select(TaskMessage).where(
            TaskMessage.task_id == task.id,
            TaskMessage.context_compacted.is_(False),
        )
    ))
    compacted_messages = list(db.scalars(
        select(TaskMessage).where(
            TaskMessage.task_id == task.id,
            TaskMessage.context_compacted.is_(True),
        )
    ))
    message_tokens = sum(estimate_tokens(message.content) for message in messages)
    used_tokens = min(
        CONTEXT_TOKEN_LIMIT,
        SYSTEM_CONTEXT_TOKENS + estimate_tokens(task.context_summary or "") + message_tokens,
    )
    return {
        "used_tokens": used_tokens,
        "total_tokens": CONTEXT_TOKEN_LIMIT,
        "compacted_messages": len(compacted_messages),
        "compressible_messages": len(messages),
    }


def save_compressed_context(task: Task, messages: list[TaskMessage], summary: str) -> None:
    task.context_summary = summary[:CONTEXT_SUMMARY_MAX_CHARS]
    for message in messages:
        message.context_compacted = True


class ProviderInput(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    kind: str = Field(pattern="^(vllm|external)$")
    base_url: str = Field(min_length=8, max_length=500)
    model_name: str = Field(min_length=1, max_length=160)
    api_key: str | None = Field(default=None, max_length=1000)
    # Creating an interface profile must not silently change the model used by an active conversation.
    activate: bool = False

    def valid_url(self) -> None:
        parsed = urlparse(self.base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise HTTPException(422, "模型地址必须是 http 或 https URL")


class McpServerInput(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    command: str = Field(min_length=1, max_length=500)
    arguments: list[str] = Field(default_factory=list, max_length=40)
    enabled: bool = True


app = FastAPI(title="Local Agent Workbench", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["http://127.0.0.1:5173", "http://localhost:5173", "http://127.0.0.1:8787", "http://localhost:8787"], allow_methods=["*"], allow_headers=["*"])


@app.get("/api/health")
def health() -> dict[str, Any]:
    with SessionLocal() as db:
        active = db.scalar(select(ModelProvider).where(ModelProvider.is_active.is_(True)))
    return {"status": "ok", "python": os.sys.version.split()[0], "docker_available": shutil.which("docker") is not None,
            "model_configured": active is not None, "active_provider": dump_provider(active) if active else None}


@app.get("/api/model-providers")
def list_model_providers() -> list[dict[str, Any]]:
    with SessionLocal() as db:
        return [dump_provider(x) for x in db.scalars(select(ModelProvider).order_by(ModelProvider.created_at.desc()))]


@app.post("/api/model-providers", status_code=201)
def create_model_provider(payload: ProviderInput) -> dict[str, Any]:
    payload.valid_url()
    with SessionLocal() as db:
        if db.scalar(select(ModelProvider).where(ModelProvider.name == payload.name)):
            raise HTTPException(409, "模型档案名称已存在")
        if payload.activate:
            for provider in db.scalars(select(ModelProvider).where(ModelProvider.is_active.is_(True))):
                provider.is_active = False
        record = ModelProvider(id=str(uuid.uuid4()), name=payload.name, kind=payload.kind, base_url=payload.base_url.rstrip("/"),
                               model_name=payload.model_name, is_active=payload.activate, created_at=now())
        db.add(record); db.commit(); db.refresh(record)
        if payload.api_key:
            secrets = read_secrets(); secrets[record.id] = payload.api_key; write_secrets(secrets)
        return dump_provider(record)


@app.post("/api/model-providers/{provider_id}/activate")
def activate_model_provider(provider_id: str) -> dict[str, Any]:
    with SessionLocal() as db:
        provider = db.get(ModelProvider, provider_id)
        if not provider:
            raise HTTPException(404, "模型档案不存在")
        for item in db.scalars(select(ModelProvider).where(ModelProvider.is_active.is_(True))):
            item.is_active = False
        provider.is_active = True; db.commit()
        return dump_provider(provider)


@app.post("/api/model-providers/{provider_id}/diagnose")
def diagnose_model_provider(provider_id: str) -> dict[str, Any]:
    with SessionLocal() as db:
        provider = db.get(ModelProvider, provider_id)
        if not provider:
            raise HTTPException(404, "模型档案不存在")
        snapshot = dump_provider(provider)
    headers = {"Authorization": f"Bearer {read_secrets().get(provider_id, '')}"}
    try:
        response = httpx.get(f"{api_root(snapshot['base_url'])}/models", headers=headers, timeout=10)
        return {"ok": response.is_success, "status_code": response.status_code,
                "message": "连接正常" if response.is_success else response.text[:500]}
    except httpx.HTTPError as error:
        return {"ok": False, "status_code": None, "message": str(error)}


@app.get("/api/mcp-servers")
def list_mcp_servers() -> list[dict[str, Any]]:
    with SessionLocal() as db:
        return [dump_mcp_server(server) for server in db.scalars(select(McpServer).order_by(McpServer.created_at.desc()))]


@app.post("/api/mcp-servers", status_code=201)
def create_mcp_server(payload: McpServerInput) -> dict[str, Any]:
    with SessionLocal() as db:
        if db.scalar(select(McpServer).where(McpServer.name == payload.name.strip())):
            raise HTTPException(409, "MCP Server 名称已存在")
        server = McpServer(id=str(uuid.uuid4()), name=payload.name.strip(), command=payload.command.strip(),
                           arguments=payload.arguments, enabled=payload.enabled, tools=[], created_at=now(), updated_at=now())
        db.add(server)
        try:
            server.tools = classify_mcp_tools(server, discover_mcp_tools(server))
        except ValueError as error:
            db.rollback()
            raise HTTPException(422, str(error)) from error
        db.commit()
        return dump_mcp_server(server)


@app.put("/api/mcp-servers/{server_id}")
def update_mcp_server(server_id: str, payload: McpServerInput) -> dict[str, Any]:
    with SessionLocal() as db:
        server = db.get(McpServer, server_id)
        if not server:
            raise HTTPException(404, "MCP Server 不存在")
        duplicate = db.scalar(select(McpServer).where(McpServer.name == payload.name.strip(), McpServer.id != server_id))
        if duplicate:
            raise HTTPException(409, "MCP Server 名称已存在")
        server.name, server.command, server.arguments, server.enabled = payload.name.strip(), payload.command.strip(), payload.arguments, payload.enabled
        try:
            server.tools = classify_mcp_tools(server, discover_mcp_tools(server))
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        server.updated_at = now(); db.commit()
        return dump_mcp_server(server)


@app.post("/api/mcp-servers/{server_id}/diagnose")
def diagnose_mcp_server(server_id: str) -> dict[str, Any]:
    with SessionLocal() as db:
        server = db.get(McpServer, server_id)
        if not server:
            raise HTTPException(404, "MCP Server 不存在")
        try:
            server.tools = classify_mcp_tools(server, discover_mcp_tools(server))
            server.updated_at = now(); db.commit()
            return {"ok": True, "message": "连接正常", "tools": server.tools}
        except ValueError as error:
            return {"ok": False, "message": str(error), "tools": []}


@app.delete("/api/mcp-servers/{server_id}", status_code=204)
def delete_mcp_server(server_id: str) -> None:
    with SessionLocal() as db:
        server = db.get(McpServer, server_id)
        if not server:
            raise HTTPException(404, "MCP Server 不存在")
        for authorization in db.scalars(select(TaskMcpTool).where(TaskMcpTool.server_id == server_id)):
            db.delete(authorization)
        db.delete(server); db.commit()


def replace_task_mcp_tools(db: Session, task: Task, tools: list[TaskMcpToolInput]) -> None:
    unique = {(item.server_id, item.tool_name) for item in tools}
    if len(unique) != len(tools):
        raise HTTPException(422, "同一 MCP 工具只能授权一次")
    for item in tools:
        server = db.get(McpServer, item.server_id)
        if not server or not server.enabled:
            raise HTTPException(422, "MCP Server 不存在或未启用")
        if not any(tool.get("name") == item.tool_name for tool in (server.tools or [])):
            raise HTTPException(422, "MCP 工具不在已发现的工具清单中")
    for authorization in db.scalars(select(TaskMcpTool).where(TaskMcpTool.task_id == task.id)):
        db.delete(authorization)
    db.add_all([TaskMcpTool(id=str(uuid.uuid4()), task_id=task.id, server_id=item.server_id,
                            tool_name=item.tool_name, access_mode=item.access_mode) for item in tools])


@app.get("/api/tasks/{task_id}/mcp-tools")
def list_task_mcp_tools(task_id: str) -> list[dict[str, Any]]:
    with SessionLocal() as db:
        task = db.get(Task, task_id)
        if not task:
            raise HTTPException(404, "任务不存在")
        return [dump_task_mcp_tool(authorization, server) for authorization, server in task_mcp_tools(db, task)]


@app.get("/api/tasks")
def list_tasks() -> list[dict[str, Any]]:
    with SessionLocal() as db:
        return [dump_task(x) for x in db.scalars(select(Task).order_by(Task.updated_at.desc()))]


@app.post("/api/tasks", status_code=201)
def create_task(payload: TaskInput) -> dict[str, Any]:
    if payload.source_type == "local":
        if not payload.local_path:
            raise HTTPException(422, "请选择本地 Git 文件夹")
        source_root, source_branch = ensure_workspace(payload.local_path)
    elif payload.source_type == "github":
        if not payload.github_url or not payload.clone_path:
            raise HTTPException(422, "请填写 GitHub 仓库地址和本地保存目录")
        cloned = clone_github_repository(payload.github_url, payload.clone_path)
        source_root, source_branch = ensure_workspace(str(cloned))
    with SessionLocal() as db:
        workspace = register_workspace(db, source_root, source_branch, payload.title, payload.test_command)
        record = Task(id=str(uuid.uuid4()), workspace_id=workspace.id, title=payload.title, requirement=payload.requirement,
                      status="awaiting_model", current_stage="需求分析", worktree_path=str(source_root), branch=source_branch,
                      permission_mode=payload.permission_mode, execution_mode=payload.execution_mode, artifacts={}, created_at=now(), updated_at=now())
        db.add(record); db.commit()
        task = dump_task(record)
    return task


@app.get("/api/tasks/{task_id}")
def get_task(task_id: str) -> dict[str, Any]:
    with SessionLocal() as db:
        task = db.get(Task, task_id)
        if not task:
            raise HTTPException(404, "任务不存在")
        return dump_task(task)


@app.get("/api/tasks/{task_id}/stages")
def list_task_stages(task_id: str) -> list[dict[str, Any]]:
    with SessionLocal() as db:
        if not db.get(Task, task_id):
            raise HTTPException(404, "任务不存在")
        runs = db.scalars(select(StageRun).where(StageRun.task_id == task_id).order_by(StageRun.created_at, StageRun.id))
        return [dump_stage_run(run) for run in runs]


@app.get("/api/tasks/{task_id}/artifacts")
def list_task_artifacts(task_id: str) -> list[dict[str, Any]]:
    with SessionLocal() as db:
        task = db.get(Task, task_id)
        if not task:
            raise HTTPException(404, "任务不存在")
        return list((task.artifacts or {}).values())


@app.patch("/api/tasks/{task_id}/permission")
def update_task_permission(task_id: str, payload: PermissionInput) -> dict[str, Any]:
    with SessionLocal() as db:
        task = db.get(Task, task_id)
        if not task:
            raise HTTPException(404, "Task not found")
        task.permission_mode = payload.permission_mode
        task.updated_at = now()
        db.commit()
        return dump_task(task)


@app.patch("/api/tasks/{task_id}/execution-mode")
def update_task_execution_mode(task_id: str, payload: ExecutionModeInput) -> dict[str, Any]:
    with SessionLocal() as db:
        task = db.get(Task, task_id)
        if not task:
            raise HTTPException(404, "Task not found")
        if execution_mode_locked(task):
            raise HTTPException(409, "Execution mode is locked after coding has started")
        task.execution_mode = payload.execution_mode
        task.updated_at = now()
        db.commit()
        return dump_task(task)


@app.patch("/api/tasks/{task_id}")
def update_task(task_id: str, payload: TaskUpdateInput) -> dict[str, Any]:
    with SessionLocal() as db:
        task = db.get(Task, task_id)
        if not task:
            raise HTTPException(404, "Task not found")
        task.title = payload.title.strip()
        task.permission_mode = payload.permission_mode
        if payload.execution_mode is not None and payload.execution_mode != task.execution_mode and execution_mode_locked(task):
            raise HTTPException(409, "Execution mode is locked after coding has started")
        if payload.execution_mode is not None:
            task.execution_mode = payload.execution_mode
        if payload.mcp_tools is not None:
            replace_task_mcp_tools(db, task, payload.mcp_tools)
        task.updated_at = now()
        db.commit()
        return dump_task(task)


@app.get("/api/tasks/{task_id}/operations")
def list_execution_operations(task_id: str) -> list[dict[str, Any]]:
    with SessionLocal() as db:
        if not db.get(Task, task_id): raise HTTPException(404, "Task not found")
        return [operation_payload(item) for item in db.scalars(select(ExecutionOperation).where(
            ExecutionOperation.task_id == task_id).order_by(ExecutionOperation.created_at.desc()))]


@app.get("/api/tasks/{task_id}/agent-runs")
def list_agent_runs(task_id: str) -> list[dict[str, Any]]:
    with SessionLocal() as db:
        if not db.get(Task, task_id): raise HTTPException(404, "Task not found")
        return [{"id": run.id, "status": run.status, "result": run.result or {}, "created_at": run.created_at.isoformat(), "updated_at": run.updated_at.isoformat()}
                for run in db.scalars(select(AgentRun).where(AgentRun.task_id == task_id).order_by(AgentRun.created_at.desc()))]


@app.get("/api/tasks/{task_id}/events")
def stream_execution_events(task_id: str, after_id: int = 0) -> StreamingResponse:
    with SessionLocal() as db:
        if not db.get(Task, task_id):
            raise HTTPException(404, "Task not found")
    def event_stream():
        cursor = after_id
        idle = 0
        while idle < 150:
            with SessionLocal() as db:
                events = list(db.scalars(select(ExecutionEvent).where(ExecutionEvent.task_id == task_id,
                    ExecutionEvent.id > cursor).order_by(ExecutionEvent.id)))
            if events:
                idle = 0
                for event in events:
                    cursor = event.id
                    yield sse("execution", {"id": event.id, "operation_id": event.operation_id, "kind": event.kind,
                                            "payload": event.payload or {}, "created_at": event.created_at.isoformat()})
            else:
                idle += 1; yield ": keepalive\n\n"; time.sleep(1)
    return StreamingResponse(event_stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


@app.post("/api/tasks/{task_id}/patches", status_code=201)
def create_patch_operation(task_id: str, payload: PatchInput) -> dict[str, Any]:
    with SessionLocal() as db:
        task = db.get(Task, task_id)
        if not task: raise HTTPException(404, "Task not found")
        if not write_enabled(task): raise HTTPException(409, "The current task stage or permission does not allow file changes")
        operation = ExecutionOperation(id=str(uuid.uuid4()), task_id=task.id, kind="patch", status="queued",
                                       request={"edits": payload.edits}, created_at=now(), updated_at=now())
        db.add(operation)
        if task.execution_mode == "manual_confirmation":
            try:
                prepared, _ = patch_preview(task, db, payload.edits)
                operation.status = "pending_approval"; operation.result = {"files": [{"path": item["path"], "diff": item["diff"]} for item in prepared]}
                record_execution_event(db, task.id, "approval_required", operation.result, operation.id)
            except RuntimeError as error:
                operation.status = "conflict"; operation.result = json.loads(str(error)); record_execution_event(db, task.id, "conflict", operation.result, operation.id)
        else:
            execute_patch_operation(db, operation)
        db.commit(); return operation_payload(operation)


@app.post("/api/tasks/{task_id}/commands", status_code=201)
def create_command_operation(task_id: str, payload: CommandInput) -> dict[str, Any]:
    with SessionLocal() as db:
        task = db.get(Task, task_id)
        if not task: raise HTTPException(404, "Task not found")
        if task.permission_mode != "full-access": raise HTTPException(409, "Only full-access mode allows command execution")
        try: task_accessible_path(db, task, payload.working_directory)
        except ValueError as error: raise HTTPException(403, str(error)) from error
        operation = ExecutionOperation(id=str(uuid.uuid4()), task_id=task.id, kind="command", status="queued",
                                       request=payload.model_dump(), created_at=now(), updated_at=now())
        db.add(operation); record_execution_event(db, task.id, "queued", {"command": payload.command}, operation.id); db.commit()
        threading.Thread(target=run_command_operation, args=(operation.id,), daemon=True).start()
        return operation_payload(operation)


@app.post("/api/tasks/{task_id}/operations/{operation_id}/approval")
def approve_operation(task_id: str, operation_id: str, payload: ApprovalInput) -> dict[str, Any]:
    with SessionLocal() as db:
        operation = db.get(ExecutionOperation, operation_id)
        if not operation or operation.task_id != task_id: raise HTTPException(404, "Operation not found")
        if operation.status != "pending_approval": raise HTTPException(409, "Operation is not awaiting approval")
        if not payload.approve:
            operation.status = "canceled"; operation.updated_at = now(); record_execution_event(db, task_id, "canceled", {}, operation.id); db.commit(); return operation_payload(operation)
        execute_patch_operation(db, operation); db.commit(); return operation_payload(operation)


@app.post("/api/tasks/{task_id}/operations/{operation_id}/cancel")
def cancel_operation(task_id: str, operation_id: str) -> dict[str, Any]:
    with SessionLocal() as db:
        operation = db.get(ExecutionOperation, operation_id)
        if not operation or operation.task_id != task_id: raise HTTPException(404, "Operation not found")
        if operation.status not in {"queued", "running", "pending_approval"}: raise HTTPException(409, "Operation cannot be canceled")
        with PROCESS_LOCK:
            process = ACTIVE_PROCESSES.get(operation.id)
        if process and process.poll() is None:
            if os.name == "nt": process.send_signal(signal.CTRL_BREAK_EVENT)
            else: os.killpg(process.pid, signal.SIGTERM)
        operation.status = "canceled"; operation.updated_at = now(); record_execution_event(db, task_id, "canceled", {}, operation.id); db.commit()
        return operation_payload(operation)


@app.post("/api/tasks/{task_id}/operations/{operation_id}/undo")
def undo_patch_operation(task_id: str, operation_id: str) -> dict[str, Any]:
    with SessionLocal() as db:
        operation = db.get(ExecutionOperation, operation_id)
        task = db.get(Task, task_id)
        if not operation or not task or operation.task_id != task_id or operation.kind != "patch": raise HTTPException(404, "Patch operation not found")
        if operation.status != "completed" or not operation.snapshot: raise HTTPException(409, "Only completed patch operations can be undone")
        conflicts = []
        for path, state in operation.snapshot.items():
            current = task_accessible_path(db, task, path, write=True)
            content = current.read_text(encoding="utf-8") if current.exists() else None
            if file_digest(content) != state["after_hash"]: conflicts.append(path)
        if conflicts: raise HTTPException(409, {"message": "Files changed after this operation", "paths": conflicts})
        for path, state in operation.snapshot.items():
            target = task_accessible_path(db, task, path, write=True)
            if state["before"] is None:
                target.unlink(missing_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True); target.write_text(state["before"], encoding="utf-8")
        operation.status = "undone"; operation.updated_at = now(); record_execution_event(db, task_id, "undone", {"paths": list(operation.snapshot)}, operation.id); db.commit()
        return operation_payload(operation)


@app.post("/api/tasks/{task_id}/access-grants", status_code=201)
def create_access_grant(task_id: str, payload: AccessGrantInput) -> dict[str, Any]:
    with SessionLocal() as db:
        task = db.get(Task, task_id)
        if not task: raise HTTPException(404, "Task not found")
        path = Path(payload.path).expanduser().resolve()
        if not path.exists() or not path.is_dir(): raise HTTPException(422, "Authorized path must be an existing directory")
        grant = TaskAccessGrant(id=str(uuid.uuid4()), task_id=task_id, path=str(path), access_mode=payload.access_mode, created_at=now())
        db.add(grant); record_execution_event(db, task_id, "access_granted", {"path": str(path), "access_mode": payload.access_mode}); db.commit()
        return {"id": grant.id, "path": grant.path, "access_mode": grant.access_mode}


@app.get("/api/tasks/{task_id}/git")
def get_task_git_status(task_id: str) -> dict[str, Any]:
    with SessionLocal() as db:
        task = db.get(Task, task_id)
        if not task: raise HTTPException(404, "Task not found")
        root = Path(task.worktree_path or "").resolve()
        status = git(root, "status", "--porcelain=v1", "--branch")
        diff = git(root, "diff", "--no-ext-diff", "--binary")
        if status.returncode != 0: raise HTTPException(409, status.stderr.strip() or "Git status failed")
        return {"status": status.stdout, "diff": diff.stdout, "branch": task.branch}


@app.post("/api/tasks/{task_id}/git/commit", status_code=201)
def create_task_git_commit(task_id: str, payload: CommitInput) -> dict[str, Any]:
    with SessionLocal() as db:
        task = db.get(Task, task_id); workspace = db.get(Workspace, task.workspace_id) if task else None
        if not task or not workspace: raise HTTPException(404, "Task not found")
        root = Path(task.worktree_path or "").resolve()
        changed_paths = sorted({path for operation in db.scalars(select(ExecutionOperation).where(
            ExecutionOperation.task_id == task_id, ExecutionOperation.kind == "patch", ExecutionOperation.status == "completed"))
            for path in (operation.snapshot or {}).keys()})
        if not changed_paths: raise HTTPException(409, "No completed agent patch is available to commit")
        test = subprocess.run(workspace.test_command, cwd=root, text=True, capture_output=True, check=False, timeout=300)
        if test.returncode != 0: raise HTTPException(409, {"message": "Tests failed; commit was not created", "stdout": test.stdout[-20_000:], "stderr": test.stderr[-20_000:]})
        staged = git(root, "add", "--", *changed_paths)
        if staged.returncode != 0: raise HTTPException(409, staged.stderr.strip() or "Git staging failed")
        committed = git(root, "commit", "-m", payload.message)
        if committed.returncode != 0: raise HTTPException(409, committed.stderr.strip() or "Git commit failed")
        record_execution_event(db, task_id, "git_committed", {"paths": changed_paths, "message": payload.message}); db.commit()
        return {"message": payload.message, "paths": changed_paths, "output": committed.stdout}


@app.delete("/api/tasks/{task_id}", status_code=204)
def delete_task(task_id: str) -> None:
    with SessionLocal() as db:
        task = db.get(Task, task_id)
        if not task:
            raise HTTPException(404, "Task not found")
        for authorization in db.scalars(select(TaskMcpTool).where(TaskMcpTool.task_id == task_id)):
            db.delete(authorization)
        db.delete(task)
        db.commit()


@app.get("/api/tasks/{task_id}/messages")
def list_task_messages(task_id: str) -> list[dict[str, Any]]:
    with SessionLocal() as db:
        if not db.get(Task, task_id):
            raise HTTPException(404, "任务不存在")
        messages = db.scalars(select(TaskMessage).where(TaskMessage.task_id == task_id).order_by(TaskMessage.created_at, TaskMessage.id))
        return [dump_message(message) for message in messages]


@app.get("/api/tasks/{task_id}/context")
def get_task_context(task_id: str) -> dict[str, int]:
    with SessionLocal() as db:
        task = db.get(Task, task_id)
        if not task:
            raise HTTPException(404, "Task not found")
        return context_usage(db, task)


@app.post("/api/tasks/{task_id}/context/compress/stream")
def compress_task_context(task_id: str) -> StreamingResponse:
    with SessionLocal() as db:
        task = db.get(Task, task_id)
        if not task:
            raise HTTPException(404, "Task not found")
        provider = db.scalar(select(ModelProvider).where(ModelProvider.is_active.is_(True)))
        if not provider:
            raise HTTPException(409, "Configure and activate a model profile first")
        messages = list(db.scalars(
            select(TaskMessage).where(
                TaskMessage.task_id == task_id,
                TaskMessage.context_compacted.is_(False),
            ).order_by(TaskMessage.created_at, TaskMessage.id)
        ))
        if not messages:
            raise HTTPException(409, "There is no new conversation context to compress")
        message_ids = [message.id for message in messages]
        provider_snapshot = dump_provider(provider)
        source = "\n\n".join(
            f"{message.role}: {message.content.strip()}" for message in messages
        )
        prior_summary = task.context_summary or "(none)"
        task_snapshot = {"title": task.title, "stage": task.current_stage, "workflow": task.workflow_type}

    def event_stream():
        yield sse("activity", {"kind": "agent", "title": "上下文压缩", "detail": "正在收集对话和已有摘要"})
        request_body = {
            "model": provider_snapshot["model_name"],
            "temperature": 0.1,
            "messages": [
                {"role": "system", "content": (
                    "You are a precise conversation compactor for a coding agent. Return only a concise Chinese Markdown summary. "
                    "Preserve the task goal, confirmed requirements, technical decisions, changed files, commands and results, current workflow stage, "
                    "open questions, and exact identifiers or values needed to continue work. Do not invent facts or include conversational filler."
                )},
                {"role": "user", "content": (
                    f"Task: {task_snapshot['title']}\nWorkflow: {task_snapshot['workflow']}\nStage: {task_snapshot['stage']}\n\n"
                    f"Existing compressed summary:\n{prior_summary}\n\nConversation to compress:\n{source}"
                )},
            ],
        }
        try:
            yield sse("activity", {"kind": "network", "title": "模型正在整理", "detail": "正在提炼可继续执行的长期记忆"})
            response = httpx.post(
                f"{api_root(provider_snapshot['base_url'])}/chat/completions",
                json=request_body,
                headers={"Authorization": f"Bearer {read_secrets().get(provider_snapshot['id'], '')}"},
                timeout=120,
            )
            response.raise_for_status()
            summary = response.json()["choices"][0]["message"].get("content")
            if not isinstance(summary, str) or not summary.strip():
                raise ValueError("The model returned an empty context summary.")
            yield sse("activity", {"kind": "tool", "title": "正在保存摘要", "detail": "原始对话会继续保留在界面中"})
            with SessionLocal() as db:
                task = db.get(Task, task_id)
                compressed = list(db.scalars(select(TaskMessage).where(TaskMessage.id.in_(message_ids))))
                save_compressed_context(task, compressed, summary.strip())
                task.updated_at = now()
                db.commit()
                usage = context_usage(db, task)
            yield sse("done", {"context": usage})
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as error:
            yield sse("error", {"message": str(error)})

    return StreamingResponse(event_stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def sse(event: str, payload: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def conversation_request(db: Session, task: Task, provider: ModelProvider, history: list[dict[str, Any]], continuous: bool = False) -> tuple[dict[str, Any], dict[str, str]]:
    permission_descriptions = {
        "read-only": "Read-only: inspect files only inside the task code directory; never modify files or run commands.",
        "workspace-write": "Workspace write: read and modify files only inside the task code directory; do not run commands.",
        "full-access": "Full access: read and modify local files and run local commands when needed.",
    }
    system_message = (
        "You are the single main coding agent for this task. Reply in Chinese. "
        "Continuously decide the next useful action from the user goal and tool results: inspect, edit, run verification, "
        "review, or finish. There are no mandatory workflow stages and no separate agents. "
        "Use supplied tools before making claims about repository contents. The UI records tool activity separately, so the final "
        "answer must only report the completed result, verification, and genuine blockers; do not expose chain-of-thought. "
        "Use concise GitHub-Flavored Markdown and do not paste full changed source files. "
        "For a coding task, batch independent reads or searches in one tool response. After inspection, combine related edits in one "
        "apply_patch call and, when safe, place dependent verification commands after it in the same tool response. "
        f"Task title: {task.title}; task requirement: {task.requirement}; code directory: {task.worktree_path or 'not bound'}. "
        "When editing existing files, use precise small old_text/new_text replacements; never use old_text=null or whole-file replacement for existing files. "
        "When a patch requires user approval, wait for its tool result before deciding what to do next. "
        "Do not commit, grant external path access, or elevate permission without an explicit user action. "
        f"Permission: {permission_descriptions[task.permission_mode]}"
    )
    snapshot = dump_provider(provider)
    return {
        "model": snapshot["model_name"], "temperature": 0.2,
        "messages": [
            {"role": "system", "content": system_message},
            *([{"role": "system", "content": f"Compressed earlier conversation:\n{task.context_summary}"}] if task.context_summary else []),
            *(compact_continuous_messages(history) if continuous else history),
        ],
        "tools": tools_for_task(task, db, continuous=continuous), "tool_choice": "auto", "stream": True,
    }, {"Authorization": f"Bearer {read_secrets().get(snapshot['id'], '')}"}


@app.get("/api/tasks/{task_id}/files")
def view_task_file(task_id: str, path: str = Query(min_length=1)) -> FileResponse:
    with SessionLocal() as db:
        task = db.get(Task, task_id)
        if not task:
            raise HTTPException(404, "Task not found")
        target = resolve_tool_path(task.worktree_path, path, task.permission_mode)
    if not target.is_file():
        raise HTTPException(404, "File not found")
    return FileResponse(target, media_type="text/plain; charset=utf-8")


@app.post("/api/tasks/{task_id}/legacy-messages/stream")
def stream_task_message(task_id: str, payload: MessageInput) -> StreamingResponse:
    content = payload.content.strip()
    if not content and not payload.continuation:
        raise HTTPException(422, "Message cannot be empty")
    with SessionLocal() as db:
        task = db.get(Task, task_id)
        provider = db.scalar(select(ModelProvider).where(ModelProvider.is_active.is_(True)))
        if not task:
            raise HTTPException(404, "Task not found")
        if not provider:
            raise HTTPException(409, "Configure and activate a model profile first")
        if payload.continuation:
            if not can_continue_automatically(task):
                raise HTTPException(409, "This task cannot continue automatically")
            decision = None
        else:
            try:
                decision = master_agent_route(provider, task, content, routing_context(db, task)) if should_replan(task, content) else None
            except RoutingError as error:
                message = str(error)
                def routing_error_stream():
                    yield sse("error", {"message": message, "retryable": True})
                return StreamingResponse(routing_error_stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})
            db.add(TaskMessage(id=str(uuid.uuid4()), task_id=task_id, role="user", content=content, created_at=now()))
        stage_run = route_message(db, task, content, decision)
        stage_run_id = stage_run.id if stage_run else None
        agent_run = AgentRun(id=str(uuid.uuid4()), task_id=task_id, status="running", created_at=now(), updated_at=now())
        db.add(agent_run)
        agent_run_id = agent_run.id
        task.updated_at = now()
        db.commit()
        workflow_payload = task.routing_decision if isinstance(task.routing_decision, dict) else None
        history = [{"role": message.role, "content": message.content} for message in db.scalars(
            select(TaskMessage).where(
                TaskMessage.task_id == task_id,
                TaskMessage.context_compacted.is_(False),
            ).order_by(TaskMessage.created_at, TaskMessage.id)
        )]
        request_body, headers = conversation_request(db, task, provider, history, continuous=True)
        provider_url = api_root(provider.base_url)

    def event_stream():
        nonlocal task, request_body, headers, stage_run_id
        def stream_stage() -> Any:
            answer = ""
            for _ in range(12):
                update_agent_run(agent_run_id, task_id, activity={"kind": "network", "title": "model", "detail": "请求模型响应"})
                yield sse("activity", {"kind": "network", "title": "网络调用", "detail": "正在请求模型响应"})
                tool_calls: dict[int, dict[str, Any]] = {}
                for retry in range(6):
                    try:
                        with httpx.stream("POST", f"{provider_url}/chat/completions", json=request_body, headers=headers, timeout=120) as response:
                            response.raise_for_status()
                            for line in response.iter_lines():
                                if not line or not line.startswith("data:"):
                                    continue
                                data = line.removeprefix("data:").strip()
                                if data == "[DONE]":
                                    continue
                                choices = (json.loads(data).get("choices") or [])
                                if not choices:
                                    continue
                                delta = choices[0].get("delta", {})
                                text = delta.get("content")
                                if text:
                                    answer += text
                                    update_agent_run(agent_run_id, task_id, token=text)
                                    yield sse("token", {"content": text})
                                for call in delta.get("tool_calls", []):
                                    index = call.get("index", 0)
                                    stored = tool_calls.setdefault(index, {"id": call.get("id"), "function": {"name": "", "arguments": ""}})
                                    stored["id"] = call.get("id") or stored["id"]
                                    function = call.get("function", {})
                                    stored["function"]["name"] += function.get("name", "")
                                    stored["function"]["arguments"] += function.get("arguments", "")
                        break
                    except httpx.HTTPStatusError as error:
                        detail = error.response.text[:1_000].strip() or error.response.reason_phrase
                        raise ConnectionError(f"Model endpoint {provider_url} returned HTTP {error.response.status_code}: {detail}") from error
                    except httpx.TransportError as error:
                        if retry == 5:
                            raise ConnectionError(f"Model endpoint {provider_url} remained unavailable after 5 retries: {type(error).__name__}: {error}") from error
                        delay = 2 ** (retry + 1)
                        yield sse("activity", {"kind": "network", "title": "网络重连", "detail": f"第 {retry + 1}/5 次重试将在 {delay} 秒后开始：{type(error).__name__}: {error}"})
                        time.sleep(delay)
                if not tool_calls:
                    if not answer.strip():
                        raise ValueError("The model returned an empty response.")
                    return answer
                request_body["messages"].append({"role": "assistant", "content": None, "tool_calls": [tool_calls[index] for index in sorted(tool_calls)]})
                for call in request_body["messages"][-1]["tool_calls"]:
                    name = call["function"]["name"]
                    try:
                        arguments = json.loads(call["function"]["arguments"] or "{}")
                        with SessionLocal() as tool_db:
                            current_task = tool_db.get(Task, task_id)
                            update_agent_run(agent_run_id, task_id, activity={"kind": "tool", "title": name, "detail": "正在调用工具"})
                            yield sse("activity", {"kind": "tool", "title": "工作阶段", "detail": tool_activity_detail(tool_db, current_task, name)})
                            result = execute_tool(current_task, name, arguments, tool_db)
                        if name == "apply_patch":
                            operation = json.loads(result)
                            if operation.get("status") == "completed":
                                for item in (operation.get("result") or {}).get("files", []):
                                    file_event = {"path": item.get("path", ""), "action": "modified"}
                                    update_agent_run(agent_run_id, task_id, file=file_event)
                                    yield sse("file", file_event)
                    except (KeyError, TypeError, ValueError, OSError, json.JSONDecodeError, subprocess.SubprocessError) as error:
                        result = json.dumps({"error": str(error)})
                        yield sse("activity", {"kind": "error", "title": "工具调用失败", "detail": str(error)})
                    request_body["messages"].append({"role": "tool", "tool_call_id": call["id"], "content": result})
            raise ValueError("The model exceeded the local tool-call limit.")

        if workflow_payload:
            update_agent_run(agent_run_id, task_id, workflow=workflow_payload)
            yield sse("workflow", workflow_payload)
        try:
            while True:
                stage_name, stage_agent = task.current_stage, task.assigned_agent
                update_agent_run_stage(agent_run_id, task_id, stage_name, stage_agent, "running")
                yield sse("stage", {"stage": stage_name, "agent": stage_agent, "status": "running"})
                update_agent_run(agent_run_id, task_id, activity={"kind": "agent", "title": stage_agent, "detail": f"执行阶段：{stage_name}"})
                yield sse("activity", {"kind": "agent", "title": stage_agent, "detail": f"正在执行{stage_name}（{task.workflow_type}流程）"})
                answer = yield from stream_stage()
                update_agent_run_stage(agent_run_id, task_id, stage_name, stage_agent, "completed", answer)
                yield sse("stage", {"stage": stage_name, "agent": stage_agent, "status": "completed", "output": answer})
                history.append({"role": "assistant", "content": answer})
                with SessionLocal() as db:
                    current_task = db.get(Task, task_id)
                    complete_stage(db, task_id, stage_run_id, answer)
                    continue_automatically = can_continue_automatically(current_task)
                    if continue_automatically:
                        next_run = start_stage(db, current_task, current_task.current_stage, "自动续跑")
                        next_stage_run_id = next_run.id
                    else:
                        assistant_message = TaskMessage(id=str(uuid.uuid4()), task_id=task_id, role="assistant", content=answer, created_at=now())
                        db.add(assistant_message)
                        completed_run = db.get(AgentRun, agent_run_id)
                        if completed_run:
                            completed_run.status = "completed"
                            completed_run.result = {**(completed_run.result or {}), "content": answer}
                            completed_run.updated_at = now()
                    db.commit()
                    task = current_task
                if not continue_automatically:
                    yield sse("done", {"message": dump_message(assistant_message)})
                    break
                stage_run_id = next_stage_run_id
                with SessionLocal() as db:
                    request_body, headers = conversation_request(db, task, provider, history)
        except Exception as error:
            update_agent_run(agent_run_id, task_id, error=str(error), status="failed")
            yield sse("error", {"message": str(error)})

    return StreamingResponse(event_stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/tasks/{task_id}/messages/stream")
def stream_continuous_task_message(task_id: str, payload: MessageInput) -> StreamingResponse:
    content = payload.content.strip()
    if not content:
        raise HTTPException(422, "Message cannot be empty")
    with SessionLocal() as db:
        task = db.get(Task, task_id)
        provider = db.scalar(select(ModelProvider).where(ModelProvider.is_active.is_(True)))
        if not task:
            raise HTTPException(404, "Task not found")
        if not provider:
            raise HTTPException(409, "Configure and activate a model profile first")
        active = db.scalar(select(AgentRun).where(AgentRun.task_id == task_id, AgentRun.status.in_({"running", "awaiting_approval"})))
        if active:
            raise HTTPException(409, "This task already has an active Agent run")
        db.add(TaskMessage(id=str(uuid.uuid4()), task_id=task_id, role="user", content=content, created_at=now()))
        task.status, task.current_stage, task.assigned_agent = "in_progress", "continuous_run", "Main Agent"
        task.workflow_type, task.task_kind, task.routing_decision, task.updated_at = "continuous", "development", None, now()
        run_created_at = now()
        run = AgentRun(id=str(uuid.uuid4()), task_id=task_id, status="running", result={
            "timing": {"started_at": run_created_at.isoformat(), "total_ms": 0, "agents": {}},
        }, created_at=run_created_at, updated_at=run_created_at)
        db.add(run); db.commit()
        history = [{"role": message.role, "content": message.content} for message in db.scalars(
            select(TaskMessage).where(TaskMessage.task_id == task_id, TaskMessage.context_compacted.is_(False)).order_by(TaskMessage.created_at, TaskMessage.id)
        )]
        request_body, headers = conversation_request(db, task, provider, history, continuous=True)
        provider_url, run_id = api_root(provider.base_url), run.id

    def event_stream():
        # Audit history is complete; request_body carries only the bounded working context.
        audit_messages = list(request_body["messages"])

        def publish_timing(agent: str | None = None, metric: str | None = None, started_at: float | None = None):
            elapsed_ms = (time.perf_counter() - started_at) * 1000 if started_at is not None else 0
            timing = record_agent_run_timing(run_id, agent, metric, elapsed_ms)
            yield sse("timing", timing)

        def complete_run(answer: str, status: str = "completed", stage: str = COMPLETED_STAGE) -> TaskMessage:
            with SessionLocal() as db:
                task = db.get(Task, task_id)
                if task:
                    task.status, task.current_stage = status, stage
                message = TaskMessage(id=str(uuid.uuid4()), task_id=task_id, role="assistant", content=answer, created_at=now())
                db.add(message)
                stored = db.get(AgentRun, run_id)
                if stored:
                    stored.status = "completed"
                    stored.result = {
                        **(stored.result or {}),
                        "content": answer,
                        "message_id": message.id,
                        "conversation": audit_messages,
                    }
                    stored.updated_at = now()
                db.commit()
                return message

        def summarize_after_tool_limit() -> str:
            activity = {"kind": "agent", "title": "Main Agent", "detail": "整理阶段结果"}
            update_agent_run(run_id, task_id, activity=activity)
            yield sse("activity", activity)
            final_request = {key: value for key, value in request_body.items() if key not in {"tools", "tool_choice"}}
            final_request["messages"] = [
                *request_body["messages"],
                {"role": "system", "content": (
                    "The local continuous-run tool budget for this response has been reached. Do not call tools. "
                    "Reply in Chinese with a concise status update: what was completed, what was changed or verified if known, "
                    "and what the user can ask you to continue next. Do not claim unfinished work is complete."
                )},
            ]
            answer = ""
            model_started_at = time.perf_counter()
            for attempt in range(MODEL_STREAM_MAX_ATTEMPTS):
                try:
                    with httpx.stream("POST", f"{provider_url}/chat/completions", json=final_request, headers=headers, timeout=120) as response:
                        response.raise_for_status()
                        for line in response.iter_lines():
                            if not line or not line.startswith("data:"):
                                continue
                            data = line.removeprefix("data:").strip()
                            if data == "[DONE]":
                                continue
                            choices = (json.loads(data).get("choices") or [])
                            if not choices:
                                continue
                            text = choices[0].get("delta", {}).get("content")
                            if text:
                                answer += text
                                update_agent_run(run_id, task_id, token=text)
                                yield sse("token", {"content": text})
                    break
                except httpx.TransportError as error:
                    if answer or attempt + 1 == MODEL_STREAM_MAX_ATTEMPTS:
                        raise ConnectionError(f"Model stream failed: {type(error).__name__}: {error}") from error
                    activity = {"kind": "network", "title": "Main Agent", "detail": "模型连接中断，正在重新连接"}
                    update_agent_run(run_id, task_id, activity=activity)
                    yield sse("activity", activity)
                    time.sleep(MODEL_STREAM_RETRY_DELAY_SECONDS)
            yield from publish_timing("Main Agent", "model_ms", model_started_at)
            if answer.strip():
                return answer
            return "本轮已达到本地工具调用上限。已保留当前工作记录，请继续发送指令让我接着处理。"

        def wait_for_operation(operation_id: str, agent: str):
            announced = False
            wait_started_at = time.perf_counter()
            approval_started_at: float | None = None
            while True:
                with SessionLocal() as operation_db:
                    operation = operation_db.get(ExecutionOperation, operation_id)
                    if not operation:
                        yield from publish_timing(agent, "operation_wait_ms", wait_started_at)
                        return {"error": "Operation no longer exists"}
                    snapshot = operation_payload(operation)
                    if operation.status not in {"queued", "running", "pending_approval"}:
                        yield from publish_timing(agent, "operation_wait_ms", wait_started_at)
                        if approval_started_at is not None:
                            yield from publish_timing("等待确认", "approval_wait_ms", approval_started_at)
                        return snapshot
                    if operation.status == "pending_approval" and not announced:
                        announced = True
                        approval_started_at = time.perf_counter()
                        update_agent_run(run_id, task_id, status="awaiting_approval", activity={"kind": "pause", "title": "等待补丁确认", "detail": "确认后会自动继续"})
                        save_agent_run_context(run_id, task_id, audit_messages, operation_id)
                        yield sse("pause", {"operation_id": operation_id, "reason": "approval_required"})
                time.sleep(0.2)

        try:
            yield sse("run", {"id": run_id, "status": "running"})
            update_agent_run(run_id, task_id, activity={"kind": "agent", "title": "Main Agent", "detail": "连续执行任务"})
            yield sse("activity", {"kind": "agent", "title": "Main Agent", "detail": "连续执行任务"})
            for _ in range(CONTINUOUS_TOOL_LOOP_LIMIT):
                answer = ""
                calls: dict[int, dict[str, Any]] = {}
                activity = {"kind": "network", "title": "Main Agent", "detail": "等待模型响应"}
                update_agent_run(run_id, task_id, activity=activity)
                yield sse("activity", activity)
                model_started_at = time.perf_counter()
                for attempt in range(MODEL_STREAM_MAX_ATTEMPTS):
                    try:
                        with httpx.stream("POST", f"{provider_url}/chat/completions", json=request_body, headers=headers, timeout=120) as response:
                            response.raise_for_status()
                            for line in response.iter_lines():
                                if not line or not line.startswith("data:"):
                                    continue
                                data = line.removeprefix("data:").strip()
                                if data == "[DONE]":
                                    continue
                                choices = (json.loads(data).get("choices") or [])
                                if not choices:
                                    continue
                                delta = choices[0].get("delta", {})
                                text = delta.get("content")
                                if text:
                                    answer += text; update_agent_run(run_id, task_id, token=text); yield sse("token", {"content": text})
                                for call in delta.get("tool_calls", []):
                                    index = call.get("index", 0)
                                    stored = calls.setdefault(index, {"id": call.get("id"), "function": {"name": "", "arguments": ""}})
                                    stored["id"] = call.get("id") or stored["id"]
                                    function = call.get("function", {})
                                    stored["function"]["name"] += function.get("name", "")
                                    stored["function"]["arguments"] += function.get("arguments", "")
                        break
                    except httpx.TransportError as error:
                        if answer or calls or attempt + 1 == MODEL_STREAM_MAX_ATTEMPTS:
                            raise ConnectionError(f"Model stream failed: {type(error).__name__}: {error}") from error
                        activity = {"kind": "network", "title": "Main Agent", "detail": "模型连接中断，正在重新连接"}
                        update_agent_run(run_id, task_id, activity=activity)
                        yield sse("activity", activity)
                        time.sleep(MODEL_STREAM_RETRY_DELAY_SECONDS)
                yield from publish_timing("Main Agent", "model_ms", model_started_at)
                if not calls:
                    if not answer.strip():
                        raise ValueError("The model returned an empty response")
                    message = complete_run(answer)
                    yield sse("done", {"message": dump_message(message)})
                    return
                tool_calls = [calls[index] for index in sorted(calls)]
                assistant_tool_message = {"role": "assistant", "content": None, "tool_calls": tool_calls}
                request_body["messages"].append(assistant_tool_message)
                audit_messages.append(assistant_tool_message)
                for call in tool_calls:
                    name = call["function"]["name"]
                    try:
                        arguments = json.loads(call["function"]["arguments"] or "{}")
                        yield sse("tool_call", {"name": name, "arguments": arguments})
                        tool_started_at = time.perf_counter()
                        with SessionLocal() as db:
                            current_task = db.get(Task, task_id)
                            activity = continuous_tool_activity(db, current_task, name)
                            update_agent_run(run_id, task_id, activity=activity)
                            yield sse("activity", activity)
                            result = execute_tool(current_task, name, arguments, db)
                        yield from publish_timing(activity["title"], "tool_ms", tool_started_at)
                        if name in {"apply_patch", "run_command"}:
                            operation = json.loads(result)
                            if operation["status"] in {"queued", "running", "pending_approval"}:
                                result = json.dumps((yield from wait_for_operation(operation["id"], activity["title"])), ensure_ascii=False)
                                update_agent_run(run_id, task_id, status="running")
                                operation = json.loads(result)
                        if name == "apply_patch":
                            if operation.get("status") == "completed":
                                for item in (operation.get("result") or {}).get("files", []):
                                    file_event = {"path": item.get("path", ""), "action": "modified"}
                                    update_agent_run(run_id, task_id, file=file_event)
                                    yield sse("file", file_event)
                    except (KeyError, TypeError, ValueError, OSError, json.JSONDecodeError, subprocess.SubprocessError) as error:
                        result = json.dumps({"error": str(error)}, ensure_ascii=False)
                        yield sse("activity", {"kind": "error", "title": "tool failed", "detail": str(error)})
                    tool_message = {"role": "tool", "tool_call_id": call["id"], "content": result}
                    request_body["messages"].append(tool_message)
                    audit_messages.append(tool_message)
                    save_agent_run_context(run_id, task_id, audit_messages)
                request_body["messages"] = compact_continuous_messages(request_body["messages"])
            answer = yield from summarize_after_tool_limit()
            message = complete_run(answer, status="awaiting_input", stage="continuous_run")
            yield sse("done", {"message": dump_message(message)})
        except Exception as error:
            update_agent_run(run_id, task_id, error=str(error), status="failed")
            with SessionLocal() as db:
                failed_task = db.get(Task, task_id)
                if failed_task:
                    failed_task.status, failed_task.updated_at = "failed", now()
                    db.commit()
            yield sse("error", {"message": str(error)})

    event_queue: queue.Queue[str | None] = queue.Queue()

    def run_in_background() -> None:
        try:
            for event in event_stream():
                event_queue.put(event)
        finally:
            event_queue.put(None)

    threading.Thread(target=run_in_background, daemon=True).start()

    def subscribe() -> Any:
        while True:
            event = event_queue.get()
            if event is None:
                return
            yield event

    return StreamingResponse(subscribe(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/tasks/{task_id}/messages", status_code=201)
def send_task_message(task_id: str, payload: MessageInput) -> dict[str, Any]:
    content = payload.content.strip()
    if not content:
        raise HTTPException(422, "消息不能为空")
    with SessionLocal() as db:
        task = db.get(Task, task_id)
        provider = db.scalar(select(ModelProvider).where(ModelProvider.is_active.is_(True)))
        if not task:
            raise HTTPException(404, "任务不存在")
        if not provider:
            raise HTTPException(409, "请先配置并激活模型档案")
        try:
            decision = master_agent_route(provider, task, content, routing_context(db, task)) if should_replan(task, content) else None
        except RoutingError as error:
            raise HTTPException(502, str(error)) from error
        user_message = TaskMessage(id=str(uuid.uuid4()), task_id=task_id, role="user", content=content, created_at=now())
        db.add(user_message)
        stage_run = route_message(db, task, content, decision)
        stage_run_id = stage_run.id if stage_run else None
        task.updated_at = now()
        db.commit()
        history = [
            {"role": message.role, "content": message.content}
            for message in db.scalars(select(TaskMessage).where(
                TaskMessage.task_id == task_id,
                TaskMessage.context_compacted.is_(False),
            ).order_by(TaskMessage.created_at, TaskMessage.id))
        ]
        provider_snapshot = dump_provider(provider)
        task_snapshot = {"title": task.title, "requirement": task.requirement, "stage": task.current_stage,
                         "path": task.worktree_path, "permission_mode": task.permission_mode,
                         "context_summary": task.context_summary}
    request_body = {
        "model": provider_snapshot["model_name"],
        "temperature": 0.2,
        "messages": [{"role": "system", "content": (
            f"你是本地代码任务工作流中的{task.assigned_agent}。使用中文回答，仅完成当前阶段，不要声称已经完成后续阶段。"
            f"任务标题：{task_snapshot['title']}；当前阶段：{task_snapshot['stage']}；代码目录：{task_snapshot['path'] or '尚未绑定'}。"
            "清楚说明已完成的分析或建议，不要虚构文件修改、命令执行或测试结果。"
        )},
        *([{"role": "system", "content": f"Compressed earlier conversation:\n{task_snapshot['context_summary']}"}] if task_snapshot["context_summary"] else []),
        *history],
    }
    permission_descriptions = {
        "read-only": "Read-only: inspect files only inside the task code directory; never modify files or run commands.",
        "workspace-write": "Workspace write: read and modify files only inside the task code directory; do not run commands.",
        "full-access": "Full access: read and modify local files and run local commands when needed.",
    }
    request_body["messages"][0]["content"] += (
        f"\nPermission: {permission_descriptions[task_snapshot['permission_mode']]} "
        "Use the provided local tools to inspect files before making claims about repository contents."
    )
    with SessionLocal() as db:
        current_task = db.get(Task, task_id)
        request_body["tools"] = tools_for_task(current_task, db)
    request_body["tool_choice"] = "auto"
    headers = {"Authorization": f"Bearer {read_secrets().get(provider_snapshot['id'], '')}"}
    try:
        for _ in range(12):
            response = httpx.post(f"{api_root(provider_snapshot['base_url'])}/chat/completions", json=request_body, headers=headers, timeout=120)
            response.raise_for_status()
            assistant = response.json()["choices"][0]["message"]
            tool_calls = assistant.get("tool_calls") or []
            if not tool_calls:
                answer = assistant.get("content")
                if not isinstance(answer, str) or not answer.strip():
                    raise ValueError("The model returned an empty response.")
                break
            request_body["messages"].append({"role": "assistant", "content": assistant.get("content"), "tool_calls": tool_calls})
            for call in tool_calls:
                function = call.get("function", {})
                try:
                    arguments = json.loads(function.get("arguments", "{}"))
                    with SessionLocal() as db:
                        current_task = db.get(Task, task_id)
                        result = execute_tool(current_task, function["name"], arguments, db)
                except (KeyError, TypeError, ValueError, OSError, json.JSONDecodeError, subprocess.SubprocessError) as error:
                    result = json.dumps({"error": str(error)})
                request_body["messages"].append({"role": "tool", "tool_call_id": call["id"], "content": result})
        else:
            raise ValueError("The model exceeded the local tool-call limit.")
    except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as error:
        raise HTTPException(502, f"模型调用失败：{error}") from error
    with SessionLocal() as db:
        assistant_message = TaskMessage(id=str(uuid.uuid4()), task_id=task_id, role="assistant", content=answer, created_at=now())
        db.add(assistant_message)
        task = db.get(Task, task_id)
        complete_stage(db, task_id, stage_run_id, answer)
        task.updated_at = now()
        db.commit()
    return dump_message(assistant_message)


@app.get("/{path:path}")
def frontend(path: str):
    if path.startswith("api/"):
        raise HTTPException(404, "API 路径不存在")
    dist = ROOT / "frontend" / "dist"
    candidate = dist / path
    if path and candidate.is_file(): return FileResponse(candidate)
    if (dist / "index.html").is_file(): return FileResponse(dist / "index.html")
    return {"message": "前端尚未构建。请执行 npm --prefix frontend install 和 npm --prefix frontend run build"}
