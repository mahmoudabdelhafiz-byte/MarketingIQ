from fastapi.testclient import TestClient

import marketingiq.api.app as app_module
from marketingiq.api.app import create_app

AUTH_SECRET = "a-secure-test-secret-that-is-long-enough"


def test_health_endpoints_are_public_when_database_is_ready(tmp_path):
    database = tmp_path / "health.db"
    app = create_app(f"sqlite+pysqlite:///{database}", AUTH_SECRET)
    client = TestClient(app)

    live = client.get("/health/live")
    ready = client.get("/health/ready")

    assert live.status_code == 200
    assert live.json() == {"status": "ok"}
    assert ready.status_code == 200
    assert ready.json() == {"status": "ok"}


def test_readiness_returns_503_without_database_error_details(tmp_path, monkeypatch):
    database = tmp_path / "health-unavailable.db"
    app = create_app(f"sqlite+pysqlite:///{database}", AUTH_SECRET)
    client = TestClient(app)
    monkeypatch.setattr(app_module, "_database_is_ready", lambda _engine: False)

    response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}
