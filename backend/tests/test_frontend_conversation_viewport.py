from pathlib import Path


def test_task_selection_resets_the_message_window_and_scrolls_to_the_latest_message() -> None:
    source = (Path(__file__).parents[2] / "frontend" / "src" / "main.tsx").read_text(encoding="utf-8")

    assert "const selectTask = (task: Task) =>" in source
    assert "setRenderedMessageCount(INITIAL_RENDERED_MESSAGES);" in source
    assert "messageList.scrollTop = messageList.scrollHeight;" in source
    assert 'onClick={() => selectTask(task)}' in source


def test_long_conversations_render_a_recent_window_with_incremental_history_loading() -> None:
    source = (Path(__file__).parents[2] / "frontend" / "src" / "main.tsx").read_text(encoding="utf-8")

    assert "const INITIAL_RENDERED_MESSAGES = 20;" in source
    assert "const renderedTimelineItems = timelineItems.slice(-renderedMessageCount);" in source
    assert "{renderedTimelineItems.map((item) => {" in source
    assert 'className="load-earlier-messages"' in source


def test_conversation_interleaves_messages_and_agent_runs_in_timestamp_order() -> None:
    source = (Path(__file__).parents[2] / "frontend" / "src" / "main.tsx").read_text(encoding="utf-8")

    assert "type ConversationItem" in source
    assert "Date.parse(left.created_at) - Date.parse(right.created_at)" in source
    assert "...runs.filter((run) => !attachedRunIds.has(run.id)).map((run) => ({ kind: \"run\" as const, created_at: run.created_at, run }))" in source


def test_automatic_workflow_does_not_start_a_second_client_stream() -> None:
    source = (Path(__file__).parents[2] / "frontend" / "src" / "main.tsx").read_text(encoding="utf-8")

    assert "setAutoContinueTaskId" not in source
    assert "sendMessage(undefined, undefined, true)" not in source
