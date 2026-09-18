from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

import marketingiq.api.app as app_module
from marketingiq.api.app import create_app
from marketingiq.infrastructure.schema_status import expected_schema_heads

AUTH_SECRET = "a-secure-test-secret-that-is-long-enough"


def _stamp_current_schema(database_url: str) -> None:
    head = expected_schema_heads()
    assert len(head) == 1
    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
        )
        connection.execute(
            text("INSERT INTO alembic_version (version_num) VALUES (:version)"),
            {"version": head[0]},
        )


def test_health_endpoints_are_public_when_database_and_schema_are_ready(tmp_path):
    database = tmp_path / "health.db"
    url = f"sqlite+pysqlite:///{database}"
    _stamp_current_schema(url)
    app = create_app(url, AUTH_SECRET)
    client = TestClient(app)

    live = client.get("/health/live")
    ready = client.get("/health/ready")

    assert live.status_code == 200
    assert live.json() == {"status": "ok"}
    assert ready.status_code == 200
    assert ready.json() == {"status": "ok"}


def test_readiness_returns_503_for_reachable_unmigrated_database(tmp_path):
    database = tmp_path / "health-unmigrated.db"
    app = create_app(f"sqlite+pysqlite:///{database}", AUTH_SECRET)
    client = TestClient(app)

    response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}


def test_readiness_returns_503_without_database_error_details(tmp_path, monkeypatch):
    database = tmp_path / "health-unavailable.db"
    app = create_app(f"sqlite+pysqlite:///{database}", AUTH_SECRET)
    client = TestClient(app)
    monkeypatch.setattr(app_module, "_database_is_ready", lambda _engine: False)

    response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}
