from pathlib import Path


def test_only_agent_messages_render_a_role_label() -> None:
    source = (Path(__file__).parents[2] / "frontend" / "src" / "main.tsx").read_text(encoding="utf-8")

    assert '{message.role === "assistant" && <div className="message-role">Agent</div>}' in source
    assert 'message.role === "user" ? "你" : "Agent"' not in source


def test_persisted_agent_messages_use_markdown_renderer() -> None:
    source = (Path(__file__).parents[2] / "frontend" / "src" / "main.tsx").read_text(encoding="utf-8")

    assert '<MarkdownContent content={message.content} taskId={selected.id} />' in source
    assert "message.role === \"assistant\"" in source


def test_agent_messages_have_a_distinct_visual_container() -> None:
    source = (Path(__file__).parents[2] / "frontend" / "src" / "styles.css").read_text(encoding="utf-8")

    assert ".message.assistant {" in source
    assert "border-left: 3px solid #78a99d" in source


def test_completed_streaming_run_is_restored_after_history_refresh() -> None:
    source = (Path(__file__).parents[2] / "frontend" / "src" / "main.tsx").read_text(encoding="utf-8")

    assert "await refreshTaskWorkflow(taskId);" in source
    assert "setRuns(restoreAgentRuns(agentRuns));" in source


def test_frontend_does_not_render_a_workflow_write_status() -> None:
    source = (Path(__file__).parents[2] / "frontend" / "src" / "main.tsx").read_text(encoding="utf-8")
    styles = (Path(__file__).parents[2] / "frontend" / "src" / "styles.css").read_text(encoding="utf-8")

    assert "writeStatus" not in source
    assert "write-status" not in styles


def test_history_refresh_reloads_persisted_messages_before_removing_run() -> None:
    source = (Path(__file__).parents[2] / "frontend" / "src" / "main.tsx").read_text(encoding="utf-8")

    assert 'api<TaskMessage[]>(`/api/tasks/${taskId}/messages`)' in source
    assert "setMessages(taskMessages);" in source


def test_completed_agent_reply_renders_an_expandable_stage_trace() -> None:
    source = (Path(__file__).parents[2] / "frontend" / "src" / "main.tsx").read_text(encoding="utf-8")

    assert "function StageRunTrace" in source
    assert 'className="activity-trace completed-trace"' in source
    assert "stageRuns.find((run) => run.output === message.content)" in source


def test_stage_trace_shows_only_the_agents_in_an_arrow_flow() -> None:
    source = (Path(__file__).parents[2] / "frontend" / "src" / "main.tsx").read_text(encoding="utf-8")
    styles = (Path(__file__).parents[2] / "frontend" / "src" / "styles.css").read_text(encoding="utf-8")

    assert "协作流程" in source
    assert "const agentFlow" in source
    assert "workflow-arrow" in source
    assert "workflow-plan" in styles
    assert "workflow-agent-flow" in styles


def test_streaming_run_receives_and_renders_the_master_workflow_before_completion() -> None:
    source = (Path(__file__).parents[2] / "frontend" / "src" / "main.tsx").read_text(encoding="utf-8")

    assert 'if (eventName === "workflow")' in source
    assert "workflow: payload" in source
    assert "function AgentFlow" in source
    assert "{run.workflow && <AgentFlow decision={run.workflow} activeAgent={run.activeAgent} />}" in source


def test_agent_flow_highlights_only_the_current_agent() -> None:
    source = (Path(__file__).parents[2] / "frontend" / "src" / "main.tsx").read_text(encoding="utf-8")
    styles = (Path(__file__).parents[2] / "frontend" / "src" / "styles.css").read_text(encoding="utf-8")

    assert 'agent === activeAgent ? "active" : ""' in source
    assert ".workflow-agent.active { font-weight: 700; }" in styles
    assert ".workflow-agent-flow" in styles and "font-weight: 400" in styles


def test_failed_agent_runs_are_restored_after_a_refresh() -> None:
    source = (Path(__file__).parents[2] / "frontend" / "src" / "main.tsx").read_text(encoding="utf-8")

    assert "function restoreAgentRuns" in source
    assert 'api<AgentRun[]>(`/api/tasks/${selected.id}/agent-runs`)' in source
    assert '.filter((run) => run.status !== "completed" || (run.result.stages?.length ?? 0) > 0)' in source
