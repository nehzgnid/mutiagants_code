from pathlib import Path


def test_task_sidebar_exposes_hover_configuration_and_delete_actions() -> None:
    root = Path(__file__).parents[2]
    source = (root / "frontend" / "src" / "main.tsx").read_text(encoding="utf-8")
    styles = (root / "frontend" / "src" / "styles.css").read_text(encoding="utf-8")

    assert 'className="task-menu-button"' in source
    assert "更改配置" in source
    assert "删除任务" in source
    assert 'method: "DELETE"' in source
    assert "task-config-modal" in source
    assert "初始需求" not in source
    assert ".task-row:hover" in styles
    assert ".task-row:hover .task-menu-button" in styles
