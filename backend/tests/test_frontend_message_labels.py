from pathlib import Path


def test_each_persisted_message_renders_its_role_label() -> None:
    source = (Path(__file__).parents[2] / "frontend" / "src" / "main.tsx").read_text(encoding="utf-8")

    assert 'message.role === "user" ? "你" : "Agent"' in source
    assert '<div className="message-role">' in source


def test_persisted_agent_messages_use_markdown_renderer() -> None:
    source = (Path(__file__).parents[2] / "frontend" / "src" / "main.tsx").read_text(encoding="utf-8")

    assert '<MarkdownContent content={message.content} taskId={selected.id} />' in source
    assert "message.role === \"assistant\"" in source


def test_agent_messages_have_a_distinct_visual_container() -> None:
    source = (Path(__file__).parents[2] / "frontend" / "src" / "styles.css").read_text(encoding="utf-8")

    assert ".message.assistant {" in source
    assert "border-left: 3px solid #78a99d" in source


def test_completed_streaming_run_is_removed_after_history_refresh() -> None:
    source = (Path(__file__).parents[2] / "frontend" / "src" / "main.tsx").read_text(encoding="utf-8")

    assert "await refreshTaskWorkflow(taskId);" in source
    assert "setRuns((items) => items.filter((run) => run.id !== runId));" in source


def test_history_refresh_reloads_persisted_messages_before_removing_run() -> None:
    source = (Path(__file__).parents[2] / "frontend" / "src" / "main.tsx").read_text(encoding="utf-8")

    assert 'api<TaskMessage[]>(`/api/tasks/${taskId}/messages`)' in source
    assert "setMessages(taskMessages);" in source


def test_completed_agent_reply_renders_an_expandable_stage_trace() -> None:
    source = (Path(__file__).parents[2] / "frontend" / "src" / "main.tsx").read_text(encoding="utf-8")

    assert "function StageRunTrace" in source
    assert 'className="activity-trace completed-trace"' in source
    assert "stageRuns.find((run) => run.output === message.content)" in source


def test_stage_trace_shows_the_master_plan_and_stage_statuses() -> None:
    source = (Path(__file__).parents[2] / "frontend" / "src" / "main.tsx").read_text(encoding="utf-8")
    styles = (Path(__file__).parents[2] / "frontend" / "src" / "styles.css").read_text(encoding="utf-8")

    assert "主 Agent 协作计划" in source
    assert "workflowStageState" in source
    assert "等待编码确认" in source
    assert "workflow-plan" in styles
    assert "workflow-step" in styles
