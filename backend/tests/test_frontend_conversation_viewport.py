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
    assert "const renderedMessages = messages.slice(-renderedMessageCount);" in source
    assert "{renderedMessages.map((message) => {" in source
    assert 'className="load-earlier-messages"' in source
