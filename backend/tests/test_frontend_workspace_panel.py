from pathlib import Path


def test_execution_records_live_in_a_toggleable_workspace_panel() -> None:
    root = Path(__file__).parents[2]
    source = (root / "frontend" / "src" / "main.tsx").read_text(encoding="utf-8")
    styles = (root / "frontend" / "src" / "styles.css").read_text(encoding="utf-8")

    assert 'setShowWorkPanel' in source
    assert 'className="work-panel"' in source
    assert 'aria-label="工作区"' in source
    assert '<ExecutionPanel operations={operations}' in source
    assert 'title={showWorkPanel ? "隐藏工作区" : "显示工作区"}' in source
    assert '.layout.work-panel-open' in styles
    assert '.work-panel-toggle' in styles
