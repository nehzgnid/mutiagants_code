from __future__ import annotations

import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.app.main import ModelProvider, SessionLocal, app, read_secrets, write_secrets


client = TestClient(app)


def active_provider_id() -> str | None:
    with SessionLocal() as db:
        provider = db.scalar(select(ModelProvider).where(ModelProvider.is_active.is_(True)))
        return provider.id if provider else None


def remove_provider(provider_id: str) -> None:
    with SessionLocal() as db:
        item = db.get(ModelProvider, provider_id)
        if item:
            db.delete(item)
            db.commit()
    secrets = read_secrets()
    secrets.pop(provider_id, None)
    write_secrets(secrets)


def test_provider_activation_masks_api_key() -> None:
    original_provider_id = active_provider_id()
    first_name = f"test-vllm-{uuid.uuid4().hex}"
    second_name = f"test-external-{uuid.uuid4().hex}"
    first = client.post("/api/model-providers", json={
        "name": first_name, "kind": "vllm", "base_url": "http://127.0.0.1:8000", "model_name": "local-model", "api_key": "local-secret",
    })
    second = client.post("/api/model-providers", json={
        "name": second_name, "kind": "external", "base_url": "https://example.test/v1", "model_name": "external-model", "api_key": "external-secret",
    })
    first_id, second_id = first.json()["id"], second.json()["id"]
    try:
        assert first.status_code == 201
        assert second.status_code == 201
        assert "api_key" not in first.json()
        assert second.json()["has_api_key"] is True
        assert first.json()["is_active"] is False
        assert second.json()["is_active"] is False
        activated = client.post(f"/api/model-providers/{first_id}/activate")
        assert activated.status_code == 200
        profiles = client.get("/api/model-providers").json()
        assert [item["id"] for item in profiles if item["is_active"]] == [first_id]
        assert client.get("/api/health").json()["active_provider"]["id"] == first_id
    finally:
        remove_provider(first_id)
        remove_provider(second_id)
        if original_provider_id:
            client.post(f"/api/model-providers/{original_provider_id}/activate")


def test_provider_rejects_non_http_url() -> None:
    response = client.post("/api/model-providers", json={
        "name": f"invalid-{uuid.uuid4().hex}", "kind": "external", "base_url": "file:///tmp/model", "model_name": "anything",
    })
    assert response.status_code == 422
