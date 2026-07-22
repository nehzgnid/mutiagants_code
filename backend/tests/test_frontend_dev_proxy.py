from pathlib import Path


def test_vite_development_server_proxies_api_requests_to_backend() -> None:
    source = (Path(__file__).parents[2] / "frontend" / "vite.config.ts").read_text(encoding="utf-8")

    assert '"/api"' in source
    assert 'target: "http://127.0.0.1:8789"' in source
    assert "changeOrigin: true" in source
