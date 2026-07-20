from pathlib import Path


def test_composer_contains_context_ring_and_compress_action() -> None:
    source = (Path(__file__).parents[2] / "frontend" / "src" / "main.tsx").read_text(encoding="utf-8")

    assert 'className="context-ring"' in source
    assert 'aria-label="上下文用量"' in source
    assert "压缩上下文" in source
    assert "/context/compress/stream" in source
    assert 'title: "正在压缩上下文"' in source
    assert 'title: "上下文压缩完成"' in source
    assert 'title: "上下文压缩失败"' in source


def test_context_monitor_styles_define_ring_and_hover_popover() -> None:
    source = (Path(__file__).parents[2] / "frontend" / "src" / "styles.css").read_text(encoding="utf-8")

    assert ".context-ring" in source
    assert "conic-gradient" in (Path(__file__).parents[2] / "frontend" / "src" / "main.tsx").read_text(encoding="utf-8")
    assert ".context-monitor:hover .context-popover" in source
