from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import uuid
from math import ceil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import JSON, DateTime, ForeignKey, String, Text, create_engine, inspect, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker


ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)
DATABASE_URL = f"sqlite:///{(DATA_DIR / 'workbench.db').as_posix()}"
EXECUTOR_IMAGE = os.getenv("EXECUTOR_IMAGE", "local-agent-python:3.12")
SECRETS_PATH = DATA_DIR / "model-secrets.json"
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
    workflow_type: Mapped[str] = mapped_column(String, default="unclassified")
    task_kind: Mapped[str] = mapped_column(String, default="unclassified")
    assigned_agent: Mapped[str] = mapped_column(String, default="主 Agent")
    branch: Mapped[str | None] = mapped_column(String, nullable=True)
    artifacts: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
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


Base.metadata.create_all(engine)


def migrate_schema() -> None:
    """Apply the small additive migrations needed by the local SQLite database."""
    columns = {column["name"] for column in inspect(engine).get_columns("tasks")}
    with engine.begin() as connection:
        if "permission_mode" not in columns:
            connection.exec_driver_sql(
                "ALTER TABLE tasks ADD COLUMN permission_mode VARCHAR NOT NULL DEFAULT 'read-only'"
            )
        for name, default in (("workflow_type", "unclassified"), ("task_kind", "unclassified"), ("assigned_agent", "主 Agent")):
            if name not in columns:
                connection.exec_driver_sql(f"ALTER TABLE tasks ADD COLUMN {name} VARCHAR NOT NULL DEFAULT '{default}'")
        if "context_summary" not in columns:
            connection.exec_driver_sql("ALTER TABLE tasks ADD COLUMN context_summary TEXT")
    message_columns = {column["name"] for column in inspect(engine).get_columns("task_messages")}
    if "context_compacted" not in message_columns:
        with engine.begin() as connection:
            connection.exec_driver_sql("ALTER TABLE task_messages ADD COLUMN context_compacted BOOLEAN NOT NULL DEFAULT 0")


migrate_schema()


def now() -> datetime:
    return datetime.now(timezone.utc)


def dump_task(item: Task) -> dict[str, Any]:
    payload = {"id": item.id, "title": item.title,
               "requirement": item.requirement,
               "permission_mode": item.permission_mode,
               "status": item.status, "current_stage": item.current_stage,
               "workflow_type": item.workflow_type, "task_kind": item.task_kind,
               "assigned_agent": item.assigned_agent,
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

STAGE_AGENTS = {
    READ_ONLY_STAGE: "阅读 Agent", REQUIREMENTS_STAGE: "主 Agent", HIGH_LEVEL_DESIGN_STAGE: "阅读 Agent",
    DETAILED_DESIGN_STAGE: "阅读 Agent", IMPLEMENTATION_STAGE: "执行 Agent", CODE_REVIEW_STAGE: "审查 Agent",
    UNIT_TESTING_STAGE: "测试 Agent", FIXING_STAGE: "执行 Agent",
}
DEVELOPMENT_KEYWORDS = ("修改", "实现", "新增", "添加", "修复", "重构", "开发", "代码", "接口", "功能", "测试", "bug", "缺陷", "优化")
COMPLEXITY_KEYWORDS = ("多文件", "多个文件", "接口", "数据库", "迁移", "架构", "跨模块", "权限", "安全", "重构", "多个", "前后端")
READ_ONLY_KEYWORDS = ("分析", "阅读", "查看", "解释", "说明", "为什么", "如何工作", "依赖", "梳理", "报错原因")
CONFIRMATION_KEYWORDS = ("确认", "继续", "执行", "开始编码", "开始实现", "同意")


def classify_workflow(content: str) -> tuple[str, str, str]:
    """Make routing deterministic so identical requests always receive the same safety boundary."""
    lowered = content.lower()
    asks_for_development = any(keyword in lowered for keyword in DEVELOPMENT_KEYWORDS)
    asks_for_reading = any(keyword in lowered for keyword in READ_ONLY_KEYWORDS)
    if asks_for_reading and not asks_for_development:
        return "read_only", "read_only_analysis", READ_ONLY_STAGE
    if any(keyword in lowered for keyword in COMPLEXITY_KEYWORDS):
        return "full", "development", REQUIREMENTS_STAGE
    return "simple", "development", REQUIREMENTS_STAGE


def next_stage(task: Task, completed_stage: str) -> str:
    if task.workflow_type == "read_only":
        return COMPLETED_STAGE
    if completed_stage == REQUIREMENTS_STAGE:
        return HIGH_LEVEL_DESIGN_STAGE if task.workflow_type == "full" else AWAIT_CODING_APPROVAL_STAGE
    if completed_stage == HIGH_LEVEL_DESIGN_STAGE:
        return DETAILED_DESIGN_STAGE
    if completed_stage == DETAILED_DESIGN_STAGE:
        return AWAIT_CODING_APPROVAL_STAGE
    if completed_stage == IMPLEMENTATION_STAGE:
        return CODE_REVIEW_STAGE
    if completed_stage == CODE_REVIEW_STAGE:
        return UNIT_TESTING_STAGE
    if completed_stage == UNIT_TESTING_STAGE:
        return AWAIT_ACCEPTANCE_STAGE
    if completed_stage == FIXING_STAGE:
        return CODE_REVIEW_STAGE
    return completed_stage


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


def route_message(db: Session, task: Task, content: str) -> StageRun | None:
    if task.current_stage in {AWAIT_CODING_APPROVAL_STAGE, AWAIT_ACCEPTANCE_STAGE}:
        if not any(keyword in content.lower() for keyword in CONFIRMATION_KEYWORDS):
            return None
        if task.current_stage == AWAIT_CODING_APPROVAL_STAGE:
            if task.permission_mode == "read-only":
                return None
            return start_stage(db, task, IMPLEMENTATION_STAGE, content)
        task.current_stage, task.status, task.assigned_agent, task.updated_at = COMPLETED_STAGE, "completed", "主 Agent", now()
        return None
    if task.workflow_type != "unclassified" and task.current_stage != COMPLETED_STAGE:
        return start_stage(db, task, task.current_stage, content)
    if task.status != "in_progress" or task.current_stage == COMPLETED_STAGE:
        workflow_type, task_kind, stage = classify_workflow(content)
        task.workflow_type, task.task_kind = workflow_type, task_kind
        return start_stage(db, task, stage, content)
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
        if len(files) == 300:
            break
    return json.dumps({"path": str(target), "files": files, "truncated": len(files) == 300}, ensure_ascii=False)


def read_local_file(task: Task, arguments: dict[str, Any]) -> str:
    target = resolve_tool_path(task.worktree_path, str(arguments["path"]), task.permission_mode)
    if not target.is_file():
        raise ValueError("The requested path is not a file.")
    content = target.read_text(encoding="utf-8", errors="replace")
    return json.dumps({"path": str(target), "content": content[:100_000], "truncated": len(content) > 100_000}, ensure_ascii=False)


def write_local_file(task: Task, arguments: dict[str, Any]) -> str:
    if task.permission_mode == "read-only":
        raise ValueError("Read-only mode does not allow file changes.")
    target = resolve_tool_path(task.worktree_path, str(arguments["path"]), task.permission_mode)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(str(arguments["content"]), encoding="utf-8")
    return json.dumps({"path": str(target), "written": True}, ensure_ascii=False)


def run_local_command(task: Task, arguments: dict[str, Any]) -> str:
    if task.permission_mode != "full-access":
        raise ValueError("Only full-access mode allows command execution.")
    working_directory = resolve_tool_path(task.worktree_path, str(arguments.get("working_directory", ".")), task.permission_mode)
    if not working_directory.is_dir():
        raise ValueError("The working directory is not a directory.")
    result = subprocess.run(str(arguments["command"]), shell=True, cwd=working_directory, text=True,
                            capture_output=True, timeout=60, check=False)
    return json.dumps({"returncode": result.returncode, "stdout": result.stdout[-20_000:], "stderr": result.stderr[-20_000:]}, ensure_ascii=False)


BASE_TOOLS = [
    {"type": "function", "function": {"name": "list_files", "description": "List files below an authorized local directory.", "parameters": {"type": "object", "properties": {"path": {"type": "string", "description": "Absolute path or path relative to the task code directory."}}, "required": []}}},
    {"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file from an authorized local path.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}},
]
WRITE_TOOL = {"type": "function", "function": {"name": "write_file", "description": "Create or replace a UTF-8 text file at an authorized local path.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}}}
COMMAND_TOOL = {"type": "function", "function": {"name": "run_command", "description": "Run a local shell command. Available only after the user selected full access.", "parameters": {"type": "object", "properties": {"command": {"type": "string"}, "working_directory": {"type": "string"}}, "required": ["command"]}}}


def tools_for(permission_mode: str) -> list[dict[str, Any]]:
    tools = list(BASE_TOOLS)
    if permission_mode in {"workspace-write", "full-access"}:
        tools.append(WRITE_TOOL)
    if permission_mode == "full-access":
        tools.append(COMMAND_TOOL)
    return tools


def tools_for_task(task: Task) -> list[dict[str, Any]]:
    """A writable task remains read-only until the orchestrator enters an execution stage."""
    if task.current_stage in {AWAIT_CODING_APPROVAL_STAGE, AWAIT_ACCEPTANCE_STAGE, COMPLETED_STAGE}:
        return list(BASE_TOOLS)
    return tools_for(task.permission_mode)


def execute_tool(task: Task, name: str, arguments: dict[str, Any]) -> str:
    handlers = {"list_files": list_local_files, "read_file": read_local_file, "write_file": write_local_file, "run_command": run_local_command}
    handler = handlers.get(name)
    if not handler:
        raise ValueError(f"Unsupported tool: {name}")
    return handler(task, arguments)


class TaskInput(BaseModel):
    source_type: str = Field(pattern="^(local|github)$")
    local_path: str | None = None
    github_url: str | None = None
    clone_path: str | None = None
    test_command: list[str] = Field(default_factory=lambda: ["python", "-m", "pytest"])
    title: str = Field(min_length=1, max_length=120)
    requirement: str = Field(default="", max_length=10000)


class PermissionInput(BaseModel):
    permission_mode: str = Field(pattern="^(read-only|workspace-write|full-access)$")


class MessageInput(BaseModel):
    content: str = Field(min_length=1, max_length=20000)


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


app = FastAPI(title="Local Agent Workbench", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["http://127.0.0.1:8787", "http://localhost:8787"], allow_methods=["*"], allow_headers=["*"])


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
                      permission_mode="read-only", artifacts={}, created_at=now(), updated_at=now())
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


def conversation_request(task: Task, provider: ModelProvider, history: list[dict[str, str]]) -> tuple[dict[str, Any], dict[str, str]]:
    permission_descriptions = {
        "read-only": "Read-only: inspect files only inside the task code directory; never modify files or run commands.",
        "workspace-write": "Workspace write: read and modify files only inside the task code directory; do not run commands.",
        "full-access": "Full access: read and modify local files and run local commands when needed.",
    }
    system_message = (
        f"You are the {task.assigned_agent} in a locally orchestrated coding workflow. Reply in Chinese. "
        "Use the supplied tools before making claims about repository contents. "
        "The application displays operational progress separately. Your response is final-answer content only: do not narrate plans, analysis steps, tool use, or future intentions. "
        "Use clean GitHub-Flavored Markdown for headings, lists, emphasis, and short code identifiers. Do not reveal private chain-of-thought. "
        "Do not paste changed source code in the final answer; summarize it and rely on changed-file links. "
        f"Task title: {task.title}; workflow: {task.workflow_type}; stage: {task.current_stage}; code directory: {task.worktree_path or 'not bound'}. "
        "Complete only the current stage. Do not claim later stages were performed. Reading Agent must not change files; "
        "Execution Agent may change files only when its provided tools permit it. "
        f"Permission: {permission_descriptions[task.permission_mode]}"
    )
    snapshot = dump_provider(provider)
    return {
        "model": snapshot["model_name"], "temperature": 0.2,
        "messages": [
            {"role": "system", "content": system_message},
            *([{"role": "system", "content": f"Compressed earlier conversation:\n{task.context_summary}"}] if task.context_summary else []),
            *history,
        ],
        "tools": tools_for_task(task), "tool_choice": "auto", "stream": True,
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


@app.post("/api/tasks/{task_id}/messages/stream")
def stream_task_message(task_id: str, payload: MessageInput) -> StreamingResponse:
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
        db.add(TaskMessage(id=str(uuid.uuid4()), task_id=task_id, role="user", content=content, created_at=now()))
        stage_run = route_message(db, task, content)
        stage_run_id = stage_run.id if stage_run else None
        task.updated_at = now()
        db.commit()
        history = [{"role": message.role, "content": message.content} for message in db.scalars(
            select(TaskMessage).where(
                TaskMessage.task_id == task_id,
                TaskMessage.context_compacted.is_(False),
            ).order_by(TaskMessage.created_at, TaskMessage.id)
        )]
        request_body, headers = conversation_request(task, provider, history)
        provider_url = api_root(provider.base_url)

    def event_stream():
        answer = ""
        yield sse("activity", {"kind": "agent", "title": task.assigned_agent, "detail": f"正在执行{task.current_stage}（{task.workflow_type}流程）"})
        try:
            for _ in range(12):
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
                                chunk = json.loads(data)
                                choices = chunk.get("choices") or []
                                if not choices:
                                    continue
                                choice = choices[0]
                                delta = choice.get("delta", {})
                                text = delta.get("content")
                                if text:
                                    answer += text
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
                    break
                calls = [tool_calls[index] for index in sorted(tool_calls)]
                request_body["messages"].append({"role": "assistant", "content": None, "tool_calls": calls})
                for call in calls:
                    function = call["function"]
                    name = function["name"]
                    try:
                        arguments = json.loads(function["arguments"] or "{}")
                        yield sse("activity", {"kind": "tool", "title": "工作阶段", "detail": f"正在执行 {name}"})
                        result = execute_tool(task, name, arguments)
                        if name == "write_file":
                            yield sse("file", {"path": json.loads(result)["path"], "action": "已更改"})
                    except (KeyError, TypeError, ValueError, OSError, json.JSONDecodeError, subprocess.SubprocessError) as error:
                        result = json.dumps({"error": str(error)})
                        yield sse("activity", {"kind": "error", "title": "工具调用失败", "detail": str(error)})
                    request_body["messages"].append({"role": "tool", "tool_call_id": call["id"], "content": result})
            else:
                raise ValueError("The model exceeded the local tool-call limit.")
            with SessionLocal() as db:
                assistant_message = TaskMessage(id=str(uuid.uuid4()), task_id=task_id, role="assistant", content=answer, created_at=now())
                db.add(assistant_message)
                updated_task = db.get(Task, task_id)
                complete_stage(db, task_id, stage_run_id, answer)
                updated_task.updated_at = now()
                db.commit()
                yield sse("done", {"message": dump_message(assistant_message)})
        except Exception as error:
            yield sse("error", {"message": str(error)})

    return StreamingResponse(event_stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


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
        user_message = TaskMessage(id=str(uuid.uuid4()), task_id=task_id, role="user", content=content, created_at=now())
        db.add(user_message)
        stage_run = route_message(db, task, content)
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
    request_body["tools"] = tools_for_task(task)
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
                    result = execute_tool(task, function["name"], arguments)
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
