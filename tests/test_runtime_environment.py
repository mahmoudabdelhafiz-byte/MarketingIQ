import pytest
from fastapi.testclient import TestClient

from marketingiq.api.app import create_app

SECRET = "a-secure-test-secret-that-is-long-enough"
EXAMPLE_SECRET = "replace-with-a-random-development-value"


def test_invalid_app_environment_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_ENV", "prodution")
    url = f"sqlite+pysqlite:///{tmp_path / 'invalid-env.db'}"

    with pytest.raises(RuntimeError, match="APP_ENV must be one of"):
        create_app(url, SECRET)


def test_example_auth_secret_is_rejected_at_runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    url = f"sqlite+pysqlite:///{tmp_path / 'example-secret.db'}"

    with pytest.raises(RuntimeError, match="example development value"):
        create_app(url, EXAMPLE_SECRET)


def test_production_disables_interactive_api_documentation(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    url = f"sqlite+pysqlite:///{tmp_path / 'production.db'}"
    client = TestClient(create_app(url, SECRET))

    assert client.get("/docs").status_code == 404
    assert client.get("/redoc").status_code == 404
    assert client.get("/openapi.json").status_code == 404


def test_non_production_keeps_api_documentation_available(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_ENV", "staging")
    url = f"sqlite+pysqlite:///{tmp_path / 'staging.db'}"
    client = TestClient(create_app(url, SECRET))

    assert client.get("/docs").status_code == 200
    assert client.get("/openapi.json").status_code == 200


def test_production_responses_include_security_and_no_store_headers(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    url = f"sqlite+pysqlite:///{tmp_path / 'production-headers.db'}"
    client = TestClient(create_app(url, SECRET))

    response = client.get("/health/live")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["permissions-policy"] == "camera=(), microphone=(), geolocation=()"


def test_non_production_does_not_force_production_security_headers(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_ENV", "staging")
    url = f"sqlite+pysqlite:///{tmp_path / 'staging-headers.db'}"
    client = TestClient(create_app(url, SECRET))

    response = client.get("/health/live")

    assert "x-frame-options" not in response.headers
    assert "permissions-policy" not in response.headers
